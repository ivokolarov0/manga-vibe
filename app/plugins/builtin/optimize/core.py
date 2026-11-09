"""Optimize: converts a book's PNG, JPEG, BMP and TIFF pages to JPEG XL (or AVIF, WebP), lossy, to
make the files smaller. The owner starts it for one series or for every series.

The only part of the app besides Delete series that changes the owner's files, so it is careful:
- A book is converted into a new file next to the old one (a hidden `.mvopt-…` name, which scans and
  the folder watcher skip). It replaces the old file only once every page decoded, the page count
  and order are the same and the old file didn't change meanwhile. No copy is kept.
- A page that gets less than KEEP_BELOW smaller stays as it is (no quality lost for nothing). Pages
  already in a modern format, nested archives, EPUB and PDF are left alone.
- RAR and 7z can't be written: they become a .cbz with the same name.
- The swap holds the scanner's lock and updates the book's row (path, size, mtime) at once, so the
  next scan sees nothing new: reading progress, manual numbers and plugins' per-page data (e.g.
  translations) stay, since the pages are the same pages in the same order.
"""
import io
import logging
import os
import shutil
import threading
import time
import warnings
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image, ImageChops, ImageCms, ImageOps

from app import config
from app.db import SessionLocal, get_setting, set_setting
from app.models import Book, Series
from app.scanner import archives, walker
from app.scanner.deleting import _read_only_mount
from app.scanner.parser import natural_key

log = logging.getLogger("mangavibe.plugin.optimize")

FORMATS = {"jxl": ("JXL", ".jxl"), "avif": ("AVIF", ".avif"), "webp": ("WEBP", ".webp")}
QUALITY = {  # each encoder's own scale: these give pages of about the same look
    "jxl": {"high": 92, "normal": 85, "small": 75},
    "avif": {"high": 80, "normal": 65, "small": 50},
    "webp": {"high": 92, "normal": 85, "small": 75},
}
CONVERT = {".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".bmp", ".tif", ".tiff"}
BOOK_FORMATS = {"zip", "rar", "7z", "folder"}
KEEP_BELOW = 0.9  # a converted page is used only if it's under 90% of the original's size
TEMP = ".mvopt-"  # hidden: the scanner and the folder watcher skip names starting with a dot
QUEUE_KEY, TOTALS_KEY = "optimize_queue", "optimize_totals"
THREADS = max(1, (os.cpu_count() or 2) // 2)  # leave the rest for reading

READ_ONLY = ("The manga folder is mounted read-only, so MangaVibe can't change files. To allow it, "
             "remove `:ro` from the manga folder in the container's settings and recreate the container.")
NO_PERMISSION = ("MangaVibe's user can't write in this folder. Give it write access to the manga folder, "
                 "or set PUID/PGID to the user that owns it, and restart the container.")

# what the worker is doing, for status() and the badges; one worker at a time
state = {"thread": None, "series": None, "series_id": None, "book_id": None, "book": 0, "books": 0,
         "stop": False, "restart": False, "starter": None}
_state_lock = threading.Lock()
_queue_lock = threading.Lock()


class Skip(Exception):
    """Why a book is left as it is, in words for the owner."""


class Blocked(Skip):
    """Nothing in this folder can be changed right now (read-only, no permission, drive full). Not
    remembered as the book's result: once the owner fixes it, the books are tried again."""


# ---------------------------------------------------------------- one page

def _prepare(im):
    """The page as it looks (EXIF rotation applied), in a mode the encoders take. Colours with an
    embedded profile (Adobe RGB, CMYK…) become sRGB, since the new file has no profile. Grey pages
    saved as colour (most scans) become grey, and an alpha channel nothing uses is dropped. None:
    leave the page alone (16-bit and float images, a profile that can't be read)."""
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)
    if im.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        return None
    if icc:
        alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        try:
            if im.mode not in ("RGB", "RGBA", "L", "CMYK"):
                im = im.convert("RGBA" if alpha else "RGB")
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                           ImageCms.createProfile("sRGB"),
                                           outputMode="RGBA" if alpha and im.mode == "RGBA" else "RGB")
        except Exception as e:  # a broken or unusual profile: keep the page rather than guess
            log.info("Page not converted: its colour profile can't be used (%s)", e)
            return None
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema() == (255, 255):
        im = im.convert(im.mode[:-1])
    if im.mode == "RGB":
        # every pixel, not a thumbnail: a small coloured title must not be averaged away
        r, g, b = im.split()
        if max(ImageChops.difference(r, g).getextrema()[1], ImageChops.difference(g, b).getextrema()[1],
               ImageChops.difference(r, b).getextrema()[1]) <= 8:
            im = im.convert("L")
    return im


def convert_page(data: bytes, fmt: str, quality: int) -> bytes | None:
    """The page in the new format, or None when it doesn't get at least 10% smaller (or can't be
    converted, e.g. a WebP over 16383 pixels). The result has been decoded once to check it."""
    try:
        with Image.open(io.BytesIO(data)) as src:
            if getattr(src, "n_frames", 1) > 1:
                return None  # animated
            src.load()
            im = _prepare(src)
            if im is None:
                return None
            out = io.BytesIO()
            name, _ = FORMATS[fmt]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if fmt == "jxl":
                    im.save(out, name, quality=quality, effort=7, lossless_jpeg=False, num_threads=THREADS)
                elif fmt == "avif":
                    im.save(out, name, quality=quality, speed=6, max_threads=THREADS)
                else:
                    im.save(out, name, quality=quality, method=5)
            size = im.size
    except Exception as e:
        log.info("Page not converted: %s: %s", type(e).__name__, e)
        return None
    new = out.getvalue()
    if len(new) >= len(data) * KEEP_BELOW:
        return None
    with Image.open(io.BytesIO(new)) as check:  # a page that doesn't decode is never written
        check.load()
        if check.size != size:
            return None
    return new


def _convertible(name, fmt):
    ext = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    return ext in CONVERT and ext != FORMATS[fmt][1]


def _renamed(name, fmt):
    p = name.rsplit(".", 1)[0]
    return p + FORMATS[fmt][1]


# ---------------------------------------------------------------- one book

def candidate(book):
    """True when the book is of a kind this can optimize (its pages decide the rest)."""
    return book.format in BOOK_FORMATS and not book.error and book.missing_since is None


def writable(folder: Path):
    """Raise Blocked when files in this folder can't be replaced."""
    if _read_only_mount(folder):
        raise Blocked(READ_ONLY)
    if not os.access(folder, os.W_OK):
        raise Blocked(NO_PERMISSION)


def _plan(book, fmt):
    """(pages, {old name: new name}) for the pages that may be converted. Raises Skip."""
    try:
        pages = archives.list_pages(book.path, book.format)
    except archives.ArchiveError as e:
        raise Skip(str(e))
    if any(archives.NESTED in p for p in pages):
        raise Skip("It has archives inside it")
    entries = pages if book.format == "folder" else archives.list_entries(book.path, book.format)
    taken = {e.lower() for e in entries}
    names = {}
    for p in pages:
        if _convertible(p, fmt):
            new = _renamed(p, fmt)
            if new.lower() not in taken:  # "001.png" and "001.jpg" can't both become "001.jxl"
                names[p] = new
                taken.add(new.lower())
    if not names:
        raise Skip("Nothing to convert")
    order = [names.get(p, p) for p in pages]
    if sorted(order, key=natural_key) != order:  # a new extension must not move a page
        raise Skip("The new page names would change the page order")
    return pages, names


def optimize_book(book, fmt, quality, should_stop=lambda: False):
    """Convert one book into hidden file(s) next to the old one. Returns (result, the old file's
    signature) for swap_in(), which puts it in place; raises Skip when there's nothing to do."""
    path = Path(book.path)
    writable(path.parent if book.format != "folder" else path)
    pages, names = _plan(book, fmt)
    before = walker.signature(path, book.format)
    # an archive is written whole; a folder's converted pages all wait as hidden files until the swap
    need = before[0] * 1.1 if book.format != "folder" else sum((path / p).stat().st_size for p in names)
    if shutil.disk_usage(path if book.format == "folder" else path.parent).free < need:
        raise Blocked("Not enough free space on the drive for the new file")

    if book.format == "folder":
        return _convert_folder(path, names, fmt, quality, should_stop), before
    return _convert_archive(book, path, pages, names, fmt, quality, should_stop), before


def _convert_folder(folder: Path, names, fmt, quality, should_stop):
    """Each converted page as a hidden file in the folder: {old name: (hidden temp, new name)}."""
    done = {}
    try:
        for old, new in names.items():
            if should_stop():
                raise Skip("Stopped")
            out = convert_page((folder / old).read_bytes(), fmt, quality)
            if out is None:
                continue
            temp = folder / f"{TEMP}{new}"
            temp.write_bytes(out)
            done[old] = (temp, new)
    except BaseException:
        for temp, _ in done.values():
            temp.unlink(missing_ok=True)
        raise
    if not done:
        raise Skip("No page got smaller")
    return {"pages": done, "path": folder, "format": "folder"}


def _convert_archive(book, path: Path, pages, names, fmt, quality, should_stop):
    """The whole book as a new hidden .cbz next to the old file."""
    target = path if book.format == "zip" else path.with_suffix(".cbz")
    if target != path and target.exists():
        raise Skip(f"{target.name} is there already")
    temp = path.parent / f"{TEMP}{target.name}"
    page_set, converted = set(pages), 0
    try:
        with zipfile.ZipFile(temp, "w") as z:
            for name in archives.list_entries(str(path), book.format):
                if should_stop():
                    raise Skip("Stopped")
                data = archives.read_entry(str(path), book.format, name)
                out = convert_page(data, fmt, quality) if name in names else None
                if out is not None:
                    z.writestr(names[name], out, zipfile.ZIP_STORED)  # pictures don't compress further
                    converted += 1
                elif name in page_set:
                    z.writestr(name, data, zipfile.ZIP_STORED)
                else:
                    z.writestr(name, data, zipfile.ZIP_DEFLATED)  # e.g. ComicInfo.xml
                if out is None and name in names:
                    names = {k: v for k, v in names.items() if k != name}  # kept as it was
        if not converted:
            raise Skip("No page got smaller")
        with zipfile.ZipFile(temp) as z:  # read back what was written
            if z.testzip() is not None:
                raise Skip("The new file didn't read back correctly")
            written = sorted((n for n in z.namelist() if archives.is_image(n)), key=natural_key)
        if written != [names.get(p, p) for p in pages]:
            raise Skip("The new file doesn't have the same pages")
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return {"temp": temp, "path": target, "format": "zip"}


def swap_in(db, book, result, before, should_stop=lambda: False):
    """Put the converted file(s) in place of the old ones and update the book's row, holding the
    scanner's lock so no scan reads the book halfway. Returns (bytes before, bytes after)."""
    while not walker._lock.acquire(timeout=5):  # a full scan is running: wait for it
        if should_stop():
            _discard(result)
            raise Skip("Stopped")
    try:
        old = Path(book.path)
        try:
            same = walker.signature(old, book.format) == before
        except OSError:
            same = False
        if not same:
            _discard(result)
            raise Skip("The file changed while it was being converted")
        if result["format"] == "folder":
            _swap_pages(db, book, old, result)
        else:
            new_path = result["path"]
            _retry(os.replace, result["temp"], new_path)
            if new_path != old:
                try:
                    _retry(old.unlink)  # the .cbr/.cb7 the .cbz replaces
                except OSError:
                    new_path.unlink(missing_ok=True)  # never leave the book there twice
                    raise
        new_path = result["path"]
        book.path, book.file_name, book.format = str(new_path), new_path.name, result["format"]
        book.file_size, book.mtime = walker.signature(new_path, book.format)
        db.commit()  # the scan that the change starts finds the book unchanged
    finally:
        walker._lock.release()
    shutil.rmtree(config.PAGES_DIR / str(book.id), ignore_errors=True)  # cached pages of the old file
    archives._pages_cached.cache_clear()
    return before[0], book.file_size  # the whole book (for a folder: all its pages)


def _swap_pages(db, book, folder: Path, result):
    """A folder book: each new page in, then its old one out. A page whose old file can't be removed
    loses its new one again, so no page is ever there twice. If that happens after some pages were
    swapped, the book's row is updated to the folder as it is (those pages stay converted)."""
    moved = 0
    try:
        for name, (temp, new) in result["pages"].items():
            _retry(os.replace, temp, folder / new)
            try:
                _retry((folder / name).unlink)
            except OSError:
                (folder / new).unlink(missing_ok=True)
                raise
            moved += 1
    except OSError as e:
        if moved:
            book.file_size, book.mtime = walker.signature(folder, "folder")
            db.commit()
        raise Skip(f"{moved} of {len(result['pages'])} pages were replaced; one couldn't be ({e})")
    finally:
        _discard(result)  # whatever wasn't moved in


def _retry(fn, *args, tries=5):
    """fn(*args), waiting a moment if Windows still has the file open (a page being read)."""
    for i in range(tries):
        try:
            return fn(*args)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(1)


def _discard(result):
    if result["format"] == "folder":
        for temp, _ in result["pages"].values():
            temp.unlink(missing_ok=True)
    else:
        result["temp"].unlink(missing_ok=True)


def clean_leftovers(folder: Path):
    """Hidden files a crash left behind in a series folder."""
    for dirpath, _, files in os.walk(folder):
        for f in files:
            if f.startswith(TEMP):
                Path(dirpath, f).unlink(missing_ok=True)


# ---------------------------------------------------------------- the queue

def records(ctx, series):
    """{book id: what happened to it} for a series: {"size", "mtime", "before", "after"} or "error"."""
    return ctx.get_series_data(series, "books", {}) or {}


def todo(book, rec, retry=False):
    """Whether a book still needs optimizing: never tried at its current file, or it failed and the
    owner asks again (retry). A book that was optimized or had nothing to convert is done."""
    if not candidate(book):
        return False
    if rec is None or rec.get("size") != book.file_size or rec.get("mtime") != book.mtime:
        return True
    return retry and "error" in rec


def queue():
    """The series ids waiting, read fresh (not from a long-lived session's cache)."""
    with SessionLocal() as db:
        return list(get_setting(db, QUEUE_KEY, []) or [])


def _edit_queue(change):
    """Change the queue in one step: the worker and the buttons both change it, so each reads it
    fresh and writes it back under one lock (no change of the other is lost)."""
    with _queue_lock, SessionLocal() as db:
        q = change(list(get_setting(db, QUEUE_KEY, []) or []))
        set_setting(db, QUEUE_KEY, q)
        db.commit()
        return q


def add_to_queue(series_ids, first=False):
    """Queue series (first: before the others). Returns how many weren't queued yet."""
    added = []

    def change(q):
        added.extend(s for s in series_ids if s not in q)
        if first:
            return list(series_ids) + [s for s in q if s not in series_ids]
        return q + added

    _edit_queue(change)
    return len(added)


def totals(db):
    return get_setting(db, TOTALS_KEY, {"books": 0, "before": 0, "after": 0}) or {}


def series_to_optimize(db):
    """Ids of series with books that may still be optimized (not done at their current file), in
    title order. Two queries, however big the library."""
    from app.models import PluginSeriesData
    recs = dict(db.query(PluginSeriesData.series_id, PluginSeriesData.value)
                .filter(PluginSeriesData.plugin == "optimize", PluginSeriesData.key == "books"))
    rows = (db.query(Book.id, Book.series_id, Book.format, Book.error, Book.missing_since, Book.file_size,
                     Book.mtime)
            .join(Series, Book.series_id == Series.id).filter(Series.missing_since.is_(None))
            .order_by(Series.sort_title, Series.id))
    out, seen = [], set()
    for b in rows:
        if b.series_id not in seen and todo(b, (recs.get(b.series_id) or {}).get(str(b.id))):
            seen.add(b.series_id)
            out.append(b.series_id)
    return out


def start(plugin_ctx):
    """Start the worker. One that is still running may be past its last look at the queue, or
    stopping (Stop, Reload plugins): it starts a new one for the queue when it ends."""
    with _state_lock:
        state["starter"] = plugin_ctx
        t = state["thread"]
        if t is not None and t.is_alive():
            state["restart"] = True
            return
        state["stop"] = False
        state["thread"] = plugin_ctx.run_in_thread(_work)


def stop():
    with _state_lock:
        state["stop"], state["restart"] = True, False
    _edit_queue(lambda q: [])


def running():
    t = state["thread"]
    return t is not None and t.is_alive()


def _work(ctx):
    should_stop = lambda: state["stop"] or ctx.should_stop()  # noqa: E731
    try:
        while not should_stop():
            q = queue()
            if not q:
                break
            sid = q[0]
            again = False
            try:
                again = _optimize_series(ctx, sid, should_stop)
            except Exception:
                ctx.db.rollback()
                log.exception("Optimizing series %s failed", sid)
            if should_stop():
                break
            if not again:
                _edit_queue(lambda q: [x for x in q if x != sid])
    finally:
        state.update(series=None, series_id=None, book_id=None, book=0, books=0)
        with _state_lock:
            starter, restart = state.get("starter"), state["restart"]
            state["restart"] = False
            # the plugin object that asked must still be the one switched on (not reloaded or off)
            if restart and starter is not None and plugins_manager().get("optimize") is starter.plugin:
                state["stop"] = False
                state["thread"] = starter.run_in_thread(_work)


def plugins_manager():
    from app.plugins import manager
    return manager


def _optimize_series(ctx, series_id, should_stop):
    """Optimize the series' books that need it. True when the owner asked for it again while it ran
    (Optimize this series, to retry failed books): it is then done once more."""
    from app.plugins.builtin.optimize import settings_of
    series = ctx.db.get(Series, series_id)
    if series is None:
        return False
    fmt, quality = settings_of(ctx)
    folder = Path(series.folder_path)
    if folder.is_dir():
        clean_leftovers(folder)
    recs = records(ctx, series)
    retry = bool(ctx.get_series_data(series, "retry"))  # "Optimize this series" retries failed books
    books = [b for b in series.books if todo(b, recs.get(str(b.id)), retry)]
    books.sort(key=lambda b: b.sort_index)
    state.update(series=series.folder_name, series_id=series.id, books=len(books), book=0)
    for i, book in enumerate(books, start=1):
        if should_stop():
            return False
        state.update(book=i, book_id=book.id)
        rec = _optimize_one(ctx, book, fmt, quality, should_stop)
        if rec is None:  # stopped: try it again next time
            return False
        if rec is BLOCKED:  # read-only, no permission, drive full: the other books won't do better
            break
        recs[str(book.id)] = rec
        ctx.set_series_data(series, "books", dict(recs))
        ctx.db.commit()
    ctx.db.commit()  # a fresh read: the button may have asked again meanwhile
    if ctx.get_series_data(series, "retry") and not retry:
        return True
    ctx.set_series_data(series, "retry", None)
    ctx.db.commit()
    return False


BLOCKED = object()


def _optimize_one(ctx, book, fmt, quality, should_stop):
    """Optimize a book and say what happened, as its record. None when it was stopped, BLOCKED when
    nothing can be written there now (not remembered, so it's tried again later)."""
    t0 = time.monotonic()
    try:
        result, before = optimize_book(book, fmt, quality, should_stop)
        old, new = swap_in(ctx.db, book, result, before, should_stop)
    except Blocked as e:
        ctx.db.rollback()
        log.warning("Not optimized: %s: %s", book.path, e)
        return BLOCKED
    except Skip as e:
        if str(e) == "Stopped":
            return None
        ctx.db.rollback()
        ctx.db.refresh(book)
        if str(e) in ("Nothing to convert", "No page got smaller"):
            return {"size": book.file_size, "mtime": book.mtime, "before": book.file_size or 0,
                    "after": book.file_size or 0}
        log.info("Not optimized: %s: %s", book.path, e)
        return {"size": book.file_size, "mtime": book.mtime, "error": str(e)}
    except Exception as e:
        ctx.db.rollback()
        log.exception("Optimizing %s failed", book.path)
        return {"size": book.file_size, "mtime": book.mtime, "error": f"{type(e).__name__}: {e}"}
    t = totals(ctx.db)
    set_setting(ctx.db, TOTALS_KEY, {"books": t.get("books", 0) + 1, "before": t.get("before", 0) + old,
                                     "after": t.get("after", 0) + new})
    log.info("Optimized %s: %s -> %s in %.0fs", book.file_name, _size(old), _size(new), time.monotonic() - t0)
    return {"size": book.file_size, "mtime": book.mtime, "before": old, "after": new}


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
