"""Library scanner: finds series and books, updates the database incrementally."""
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import activity, config, images
from app.api.common import book_numbers, numbering
from app.db import SessionLocal
from app.models import Book, Library, PluginPage, Series
from app.plugins import manager as plugins
from app.scanner import archives
from app.scanner.parser import Parsed, natural_key, order_parsed, parse

log = logging.getLogger("mangavibe.scan")

# Synology/Windows/macOS housekeeping folders that must never become books
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", "__MACOSX", ".AppleDouble", "@Recycle"}
KEEP_MISSING_DAYS = 30

# library_id is set when only one library is scanned, None for a full scan
status = {"running": False, "library_id": None, "library": None, "current": None, "done": 0, "total": 0,
          "started_at": None, "finished_at": None, "new_books": 0, "errors": 0,
          # what the last scan changed, for the activity log (the series and books found, re-read, gone)
          "new_series": 0, "changed_books": 0, "missing_series": 0, "missing_books": 0}
CHANGE_COUNTS = ("new_series", "new_books", "changed_books", "missing_series", "missing_books")
_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc)


def _skip(name):
    return name in SKIP_DIRS or name.startswith(".")


def find_books(series_dir: Path):
    """Yield (path, format) for every book inside a series folder (recursive)."""
    for dirpath, dirnames, filenames in os.walk(series_dir):
        dirnames[:] = sorted(d for d in dirnames if not _skip(d))
        if any(archives.is_image(f) for f in filenames):
            yield Path(dirpath), "folder"
        for f in filenames:
            fmt = archives.ARCHIVE_FORMATS.get(Path(f).suffix.lower())
            if fmt and not f.startswith("."):
                yield Path(dirpath) / f, fmt


def signature(path: Path, fmt: str):
    """(size, mtime) used to detect changed books."""
    if fmt != "folder":
        st = path.stat()
        return st.st_size, st.st_mtime
    size, mtime = 0, 0.0
    for e in os.scandir(path):
        if e.is_file() and archives.is_image(e.name):
            st = e.stat()
            size += st.st_size
            mtime = max(mtime, st.st_mtime)
    return size, mtime


def start_scan_in_background(lib_id: int | None = None):
    """Scan every library, or only one when lib_id is given. False if a scan is already running."""
    if status["running"]:
        return False
    # someone pressed Scan (or added a folder): logged as theirs, and its end too. The app's own scans
    # (at start, every few hours) only log their end, when they changed something
    by = activity.in_request()
    if by:
        activity.log("scan.started", _library_name(lib_id), target=("folder", lib_id) if lib_id else None)
    threading.Thread(target=scan_all, args=(lib_id, by), daemon=True, name="scanner").start()
    return True


def _counts() -> dict:
    """The scan's counts, to put back after a scan of one series (they describe the last full scan)."""
    return {k: status[k] for k in (*CHANGE_COUNTS, "errors")}


def _library_name(lib_id):
    """The folder's path, or "" for every folder."""
    if lib_id is None:
        return ""
    with SessionLocal() as db:
        lib = db.get(Library, lib_id)
        return lib.root_path if lib else ""


def scan_all(lib_id: int | None = None, by_person=False):
    if not _lock.acquire(blocking=False):
        return
    started = time.monotonic()
    try:
        status.update(running=True, library_id=lib_id, library=None, started_at=now().isoformat(),
                      finished_at=None, done=0, total=0, errors=0, **dict.fromkeys(CHANGE_COUNTS, 0))
        db = SessionLocal()
        try:
            libs = db.query(Library)
            if lib_id is not None:
                libs = libs.filter(Library.id == lib_id)
            for lib in libs.all():
                scan_library(db, lib)
        finally:
            db.close()
    except Exception:
        log.exception("Scan failed")
    finally:
        status.update(running=False, current=None, finished_at=now().isoformat())
        _lock.release()
    if by_person or any(status[k] for k in CHANGE_COUNTS):
        activity.log("scan.finished", _library_name(lib_id), target=("folder", lib_id) if lib_id else None,
                     details={**_counts(), "series": status["total"], "seconds": round(time.monotonic() - started)})
    from app import db as database
    database.refresh_stats()  # the library may have grown a lot: the query planner's numbers again
    try:
        from app.metadata import worker
        worker.enqueue_unmatched()  # new series get metadata in the background
    except Exception:
        log.exception("Could not queue metadata matching")


def scan_library(db, lib: Library):
    root = Path(lib.root_path)
    status["library"] = lib.name
    if not root.is_dir():
        # don't mark anything missing: the share may just be unmounted for a moment
        log.warning("Library folder %s not found; is it mounted in the container?", root)
        return
    t0 = time.time()
    dirs = sorted((e for e in os.scandir(root) if e.is_dir() and not _skip(e.name)),
                  key=lambda e: natural_key(e.name))
    status["total"] += len(dirs)
    seen = set()
    reparse = _parser_switched(db, lib)
    for entry in dirs:
        status["current"] = entry.name
        try:
            s = scan_series(db, lib, Path(entry.path), reparse=reparse)
            if s is not None:
                seen.add(s.id)
            db.commit()
        except Exception:
            db.rollback()
            status["errors"] += 1
            log.exception("Failed to scan series folder %s", entry.path)
        status["done"] += 1

    for s in db.query(Series).filter(Series.library_id == lib.id):
        if s.id not in seen and s.missing_since is None:
            s.missing_since = now()
            status["missing_series"] += 1
    gone = _purge_missing(db, lib.id)
    _parser_done(db, lib)
    db.commit()
    _remove_purged_files(*gone)
    log.info("Scanned library %s: %d series in %.1fs", lib.name, len(dirs), time.time() - t0)


PARSER_USED = "jobs:scanner:parser-used"  # {library id: "plugin@version"} that read its books' numbers


def _parser_now() -> str:
    p = plugins.provider_of("scanner:parser") or plugins.builtin_for("scanner:parser")
    return f"{p.id}@{p.version}" if p else ""


def _parser_switched(db, lib) -> bool:
    """True when another plugin (or version) does the scanner:parser job than the one that read this
    library's books (#210): the scan reads every book's numbers again."""
    from app.db import get_setting
    before = (get_setting(db, PARSER_USED, {}) or {}).get(str(lib.id))
    if before is not None and before != _parser_now():
        log.info("The file name parser is %s now (was %s): reading the numbers of %s again",
                 _parser_now(), before, lib.name)
        return True
    return False


def _parser_done(db, lib):
    """Remember the parser that read this library (after its scan, so one cut short reads them again)."""
    from app.db import get_setting, set_setting
    used = dict(get_setting(db, PARSER_USED, {}) or {})
    if used.get(str(lib.id)) != _parser_now():
        set_setting(db, PARSER_USED, {**used, str(lib.id): _parser_now()})


# A series' new books count as an update (books_added_at) once it has been here this long: the books of a
# folder still being copied in, found by the scans right after the first, are part of adding it
JUST_ADDED = timedelta(hours=1)


def _settled(series) -> bool:
    added = series.added_at
    if added is None:
        return False
    return now() - (added if added.tzinfo else added.replace(tzinfo=timezone.utc)) > JUST_ADDED


def scan_series(db, lib, folder: Path, recheck_errors=True, reparse=False) -> Series | None:
    """recheck_errors=False leaves unchanged broken books alone (scans after a change, see scan_folder).
    reparse: read the numbers of unchanged books again too (the parser plugin was switched)."""
    path_str = str(folder)
    series = db.query(Series).filter_by(folder_path=path_str).one_or_none()
    book_paths = list(find_books(folder))
    if series is None and not book_paths:
        return None  # empty folder: not a series (yet)
    is_new = series is None  # its first books are part of adding it, not an update
    if series is None:
        series = Series(library_id=lib.id, folder_path=path_str, folder_name=folder.name,
                        sort_title=folder.name.lower())
        db.add(series)
        db.flush()
        status["new_series"] += 1
        log.info("New series: %s", folder.name)
    series.missing_since = None

    existing = {b.path: b for b in series.books}
    found = set()
    changed = False
    for path, fmt in book_paths:
        key = str(path)
        found.add(key)
        try:
            size, mtime = signature(path, fmt)
        except OSError:
            continue
        book = existing.get(key)
        if (book and book.file_size == size and book.mtime == mtime and book.missing_since is None
                and (book.error is None or not recheck_errors
                     or archives.not_yet_supported(book.format, book.error))):
            if reparse:
                before = (book.volume, book.chapter, book.chapter_end, book.extra, book.extra_index, book.revision)
                _parse_name(db, series, book, path)
                changed |= before != (book.volume, book.chapter, book.chapter_end, book.extra,
                                      book.extra_index, book.revision)
            continue  # unchanged; damaged books are re-checked on every scan, light novels aren't
        if book is None:
            book = Book(series_id=series.id, path=key, file_name=path.name, format=fmt)
            db.add(book)
            series.books.append(book)
            status["new_books"] += 1
            if not is_new and _settled(series):
                series.books_added_at = now()  # Home's Recently updated (#291)
        elif book.mtime != -1 and (book.file_size, book.mtime) != (size, mtime):
            status["changed_books"] += 1  # the file changed (not a forced re-read, not a broken one re-checked)
        before = (book.file_size, book.mtime, book.missing_since, book.error, book.page_count, book.format)
        if book.id is not None and before[1] != -1 and (book.file_size, book.mtime) != (size, mtime):
            # the file was replaced: what plugins saved per page (e.g. translations) belongs to the old one.
            # mtime -1 is a forced re-read of an unchanged file (see below), which keeps them.
            db.query(PluginPage).filter_by(book_id=book.id).delete(synchronize_session=False)
        book.missing_since = None
        book.file_size, book.mtime = size, mtime
        db.flush()  # need book.id for thumbnail file names
        _read_book(db, series, book, path)
        if before != (size, mtime, None, book.error, book.page_count, book.format):
            changed = True  # a re-checked broken file that is still broken changes nothing

    for key, book in existing.items():
        if key not in found and book.missing_since is None:
            book.missing_since = now()
            status["missing_books"] += 1
            changed = True

    if changed or any(b.sort_index == 0 for b in series.books):
        apply_order(series)
        series.updated_at = now()
        plugins.run_hook(db, "on_series_scanned", series, active_books(series))
    return series


def _parse_name(db, series, book, path: Path):
    """The book's numbers from its file name (the scanner:parser job), then on_book_scanned's fixes."""
    p = parse(path.name, series.folder_name)
    plugins.run_hook(db, "on_book_scanned", book, p)
    book.volume, book.chapter, book.chapter_end = p.volume, p.chapter, p.chapter_end
    book.extra, book.extra_index, book.revision = p.extra, p.extra_index, p.version


def _read_book(db, series, book, path: Path):
    """Count pages, make a thumbnail, parse the filename, run plugin hooks."""
    _parse_name(db, series, book, path)

    book.error = None
    book.thumbnail_path = None
    book.format = archives.sniff(path, book.format)  # a ZIP named .rar still opens as ZIP
    if book.format not in archives.SUPPORTED:
        book.error = f"{(book.format or 'unknown').upper()} files are not supported yet (coming in a later version)"
        book.page_count = 0
        return
    try:
        pages = archives.list_pages(str(path), book.format)
    except archives.TextBook as e:
        # a light novel: its sections count as pages, a plugin's reader shows it (Plugin API 14)
        book.format, book.page_count = archives.TEXT_BOOK, len(e.sections)
        cover = archives.epub_cover(str(path))
        if cover:
            try:
                book.thumbnail_path = images.make_thumbnail(cover, book.id)
            except Exception as err:
                log.info("No thumbnail from the cover of %s: %s", path.name, err)
        return
    except archives.ArchiveError as e:
        book.error, book.page_count = str(e), 0
        status["errors"] += 1
        log.warning("Unreadable book %s: %s", path, e)
        return
    except Exception as e:
        book.error, book.page_count = f"Could not read file: {type(e).__name__}: {e}", 0
        status["errors"] += 1
        log.warning("Unreadable book %s: %s", path, e)
        return
    book.page_count = len(pages)
    # thumbnail from the first page that decodes; one broken image must not break the book
    for i in range(min(5, len(pages))):
        try:
            data, _ = archives.read_page(str(path), book.format, i)
            book.thumbnail_path = images.make_thumbnail(data, book.id)
            break
        except Exception as e:
            log.info("No thumbnail from page %d of %s: %s", i + 1, path.name, e)


def active_books(series):
    return [b for b in series.books if b.missing_since is None]


def to_parsed(book: Book) -> Parsed:
    """Parsed values for ordering, with the owner's overrides applied."""
    return Parsed(
        name=book.file_name,
        volume=book.eff_volume,
        chapter=book.eff_chapter,
        chapter_end=book.eff_chapter_end,
        version=book.revision,
        extra=book.eff_extra,
        extra_index=book.extra_index,
        ref=book,
    )


def apply_order(series):
    """Compute sort_index, hidden and duplicate flags for all books of a series."""
    books = active_books(series)
    kept, skipped, dups = order_parsed([to_parsed(b) for b in books])
    if any(p.ref.sort_override is not None for p in kept):
        # manual order from the owner wins; books without one keep their relative place
        pos = {id(p): (p.ref.sort_override if p.ref.sort_override is not None else i)
               for i, p in enumerate(kept, start=1)}
        kept.sort(key=lambda p: pos[id(p)])
    dup_ids = {p.ref.id for group in dups for p in group}
    for i, p in enumerate(kept, start=1):
        p.ref.sort_index = i
        p.ref.hidden = p.ref.hidden_by_owner
    for i, p in enumerate(skipped, start=len(kept) + 1):
        p.ref.sort_index = i
        p.ref.hidden = True  # older revision
    for b in books:
        b.duplicate_flag = b.id in dup_ids
    series.has_duplicates = bool(dup_ids)
    series.numbering = numbering(books)  # hidden books are left out by numbering()


def fill_numbering(db):
    """Store numbering() for the series that don't have it yet (all of them, after the upgrade
    that added it). Reads only the books' number columns, in batches."""
    todo = [sid for (sid,) in db.query(Series.id).filter(Series.numbering.is_(None))]
    for i in range(0, len(todo), 500):
        ids = todo[i:i + 500]
        books = book_numbers(db, Book.series_id.in_(ids))
        for sid in ids:
            db.query(Series).filter(Series.id == sid).update(
                {Series.numbering: numbering(books.get(sid, []))}, synchronize_session=False)
        db.commit()
    if todo:
        log.info("Worked out the numbering of %d series", len(todo))


class Busy(Exception):
    pass


def rescan_series(series_id: int):
    """Re-read every file of one series (ignores 'unchanged'), re-parse and re-order it."""
    if not _lock.acquire(timeout=2):
        raise Busy("A library scan is running. Try again when it has finished.")
    try:
        db = SessionLocal()
        try:
            series = db.get(Series, series_id)
            if series is None:
                raise KeyError(series_id)
            folder = Path(series.folder_path)
            lib = db.get(Library, series.library_id)
            if not folder.is_dir():
                if lib is None or not Path(lib.root_path).is_dir():
                    # the whole share is gone (e.g. not mounted): don't hide anything
                    raise FileNotFoundError(f"Library folder not found: {lib.root_path if lib else '?'}")
                # the series folder was deleted: hide it now, like a full scan would
                if series.missing_since is None:
                    series.missing_since = now()
                db.commit()
                return {"removed": True, "books": 0, "errors": 0}
            for b in series.books:
                b.mtime = -1  # forces each file to be opened and parsed again
            archives._pages_cached.cache_clear()
            before = _counts()  # the last full scan's counts must not change
            try:
                scan_series(db, lib, folder)
            finally:
                status.update(before)
            apply_order(series)
            db.commit()
            books = active_books(series)
            return {"removed": False, "books": len(books), "errors": sum(1 for b in books if b.error)}
        finally:
            db.close()
    finally:
        _lock.release()


def scan_folder(lib_id: int, folder: Path):
    """Scan one series folder of a library after its files changed (app/scanner/watcher.py). Like a
    full scan, only new and changed files are opened. A deleted folder hides its series. Raises Busy
    while another scan is running; returns True when a new series was added."""
    if not _lock.acquire(blocking=False):
        raise Busy("A library scan is running")
    new = False
    try:
        with SessionLocal() as db:
            lib = db.get(Library, lib_id)
            if lib is None or not Path(lib.root_path).is_dir():
                return False  # folder removed from the app, or the share isn't mounted: change nothing
            counts = _counts()  # they describe the last full scan
            try:
                if folder.is_dir():
                    known = db.query(Series.id).filter_by(folder_path=str(folder)).first() is not None
                    # broken books aren't reopened: on Windows reading a file is a change too, and
                    # each reopen would start the next scan
                    new = scan_series(db, lib, folder, recheck_errors=False) is not None and not known
                else:
                    series = db.query(Series).filter_by(folder_path=str(folder)).one_or_none()
                    if series is not None and series.missing_since is None:
                        series.missing_since = now()
                db.commit()
            finally:
                status.update(counts)
    finally:
        _lock.release()
    if new:
        from app.metadata import worker
        worker.enqueue_unmatched()
    return new


def _purge_missing(db, lib_id: int):
    """Delete books and series of this library that have been missing for too long. Returns what
    to remove from /data once that is committed: {series_id: [book ids]} of deleted series, and
    the ids of books deleted from series that stay."""
    from app.scanner.deleting import forget
    cutoff = now() - timedelta(days=KEEP_MISSING_DAYS)
    gone_series, gone_books = {}, []
    for s in db.query(Series).filter(Series.library_id == lib_id, Series.missing_since.is_not(None)):
        if _aware(s.missing_since) < cutoff:
            gone_series[s.id] = [b.id for b in s.books]
            forget(db, s.id, gone_series[s.id])
            db.delete(s)  # its books go with it
    for b in (db.query(Book).join(Series, Book.series_id == Series.id)
              .filter(Series.library_id == lib_id, Book.missing_since.is_not(None))):
        if b.series_id not in gone_series and _aware(b.missing_since) < cutoff:
            gone_books.append(b.id)
            db.delete(b)
    return gone_series, gone_books


def _remove_purged_files(gone_series, gone_books):
    """Posters, thumbnails and cached pages of what _purge_missing() deleted: they stayed in /data."""
    from app.scanner.deleting import remove_cached_files
    for sid, book_ids in gone_series.items():
        remove_cached_files(sid, book_ids)
    for b in gone_books:
        (config.THUMBS_DIR / f"{b}.webp").unlink(missing_ok=True)
        shutil.rmtree(config.PAGES_DIR / str(b), ignore_errors=True)
    if gone_series or gone_books:
        log.info("Removed %d series and %d books missing for over %d days",
                 len(gone_series), len(gone_books), KEEP_MISSING_DAYS)


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_libraries(db, paths):
    """Add LIBRARY_PATHS as library folders (existing ones are kept, ones the owner removed stay
    removed). Plugins see them in on_start (e.g. libraries: every folder gets one)."""
    from app.db import get_setting
    removed = set(get_setting(db, "removed_libraries", []) or [])
    for p in paths:
        root = str(Path(p))
        if root in removed:
            continue
        if not db.query(Library).filter_by(root_path=root).one_or_none():
            db.add(Library(name=Path(root).name or root, root_path=root))
            log.info("Added library folder %s", root)
    db.commit()
