"""Library scanner: finds series and books, updates the database incrementally."""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import images
from app.db import SessionLocal
from app.models import Book, Library, Series
from app.plugins import manager as plugins
from app.scanner import archives
from app.scanner.parser import Parsed, natural_key, order_parsed, parse

log = logging.getLogger("mangavibe.scan")

# Synology/Windows/macOS housekeeping folders that must never become books
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", "__MACOSX", ".AppleDouble", "@Recycle"}
KEEP_MISSING_DAYS = 30

status = {"running": False, "library": None, "current": None, "done": 0, "total": 0,
          "started_at": None, "finished_at": None, "new_books": 0, "errors": 0}
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


def start_scan_in_background():
    if status["running"]:
        return False
    threading.Thread(target=scan_all, daemon=True, name="scanner").start()
    return True


def scan_all():
    if not _lock.acquire(blocking=False):
        return
    try:
        status.update(running=True, started_at=now().isoformat(), finished_at=None, done=0,
                      total=0, new_books=0, errors=0)
        db = SessionLocal()
        try:
            for lib in db.query(Library).all():
                scan_library(db, lib)
        finally:
            db.close()
    except Exception:
        log.exception("Scan failed")
    finally:
        status.update(running=False, current=None, finished_at=now().isoformat())
        _lock.release()
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
    for entry in dirs:
        status["current"] = entry.name
        try:
            s = scan_series(db, lib, Path(entry.path))
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
    _purge_missing(db)
    db.commit()
    log.info("Scanned library %s: %d series in %.1fs", lib.name, len(dirs), time.time() - t0)


def scan_series(db, lib, folder: Path) -> Series | None:
    path_str = str(folder)
    series = db.query(Series).filter_by(folder_path=path_str).one_or_none()
    book_paths = list(find_books(folder))
    if series is None and not book_paths:
        return None  # empty folder: not a series (yet)
    if series is None:
        series = Series(library_id=lib.id, folder_path=path_str, folder_name=folder.name,
                        sort_title=folder.name.lower())
        db.add(series)
        db.flush()
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
        if book and book.file_size == size and book.mtime == mtime and book.missing_since is None                 and book.error is None:
            continue  # unchanged; books with errors are re-checked on every scan
        if book is None:
            book = Book(series_id=series.id, path=key, file_name=path.name, format=fmt)
            db.add(book)
            series.books.append(book)
            status["new_books"] += 1
        before = (book.file_size, book.mtime, book.missing_since, book.error, book.page_count, book.format)
        book.missing_since = None
        book.file_size, book.mtime = size, mtime
        db.flush()  # need book.id for thumbnail file names
        _read_book(db, series, book, path)
        if before != (size, mtime, None, book.error, book.page_count, book.format):
            changed = True  # a re-checked broken file that is still broken changes nothing

    for key, book in existing.items():
        if key not in found and book.missing_since is None:
            book.missing_since = now()
            changed = True

    if changed or any(b.sort_index == 0 for b in series.books):
        apply_order(series)
        series.updated_at = now()
        plugins.run_hook(db, "on_series_scanned", series, active_books(series))
    return series


def _read_book(db, series, book, path: Path):
    """Count pages, make a thumbnail, parse the filename, run plugin hooks."""
    p = parse(path.name, series.folder_name)
    plugins.run_hook(db, "on_book_scanned", book, p)
    book.volume, book.chapter, book.chapter_end = p.volume, p.chapter, p.chapter_end
    book.extra, book.extra_index, book.revision = p.extra, p.extra_index, p.version

    book.error = None
    book.thumbnail_path = None
    book.format = archives.sniff(path, book.format)  # a ZIP named .rar still opens as ZIP
    if book.format not in archives.SUPPORTED:
        book.error = f"{(book.format or 'unknown').upper()} files are not supported yet (coming in a later version)"
        book.page_count = 0
        return
    try:
        pages = archives.list_pages(str(path), book.format)
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
            errors_before = status["errors"]  # the last full scan's count must not change
            try:
                scan_series(db, lib, folder)
            finally:
                status["errors"] = errors_before
            apply_order(series)
            db.commit()
            books = active_books(series)
            return {"removed": False, "books": len(books), "errors": sum(1 for b in books if b.error)}
        finally:
            db.close()
    finally:
        _lock.release()


def _purge_missing(db):
    cutoff = now() - timedelta(days=KEEP_MISSING_DAYS)
    for b in db.query(Book).filter(Book.missing_since.is_not(None)):
        if _aware(b.missing_since) < cutoff:
            db.delete(b)
    for s in db.query(Series).filter(Series.missing_since.is_not(None)):
        if _aware(s.missing_since) < cutoff:
            db.delete(s)


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_libraries(db, paths):
    """Add LIBRARY_PATHS as libraries (existing ones are kept, ones the owner removed stay removed)."""
    from app.db import get_setting
    removed = set(get_setting(db, "removed_libraries", []) or [])
    for p in paths:
        root = str(Path(p))
        if root in removed:
            continue
        if not db.query(Library).filter_by(root_path=root).one_or_none():
            db.add(Library(name=Path(root).name or root, root_path=root))
            log.info("Added library %s", root)
    db.commit()
