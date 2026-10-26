"""Deleting a series: its folder on the drive, then everything the app keeps about it.

This is the only place the app removes the owner's files, and only when the owner asks for one
series. It refuses anything that isn't a series folder directly inside a library folder, and it
needs the manga folder mounted read-write (a `:ro` mount makes deleting impossible on purpose).
"""
import logging
import os
import shutil
import stat
from pathlib import Path

from app import config
from app.db import SessionLocal, get_setting, set_setting
from app.models import Library, Series
from app.plugins import manager as plugins
from app.scanner import walker

log = logging.getLogger("mangavibe.delete")

READ_ONLY = ("The manga folder is mounted read-only, so MangaVibe can't delete files. To allow it, "
             "remove `:ro` from the manga folder in the container's settings (on a Synology, untick "
             "Read-only) and recreate the container.")
NO_PERMISSION = ("MangaVibe's user can't delete files in this folder. Give it write access to the manga "
                 "folder, or set PUID/PGID to the user that owns it, and restart the container.")


def _read_only_mount(path):
    """True when the folder is on a read-only mount (Linux: the container's `:ro`)."""
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except (AttributeError, OSError):  # Windows has no statvfs
        return False


class CannotDelete(Exception):
    """Why this series' folder can't be deleted, in words the owner can act on."""


def series_folder(db, series):
    """The folder to delete, or raise CannotDelete."""
    lib = db.get(Library, series.library_id)
    if lib is None:
        raise CannotDelete("This series' library folder was removed from MangaVibe.")
    raw = Path(series.folder_path)
    if raw.is_symlink():
        raise CannotDelete("The series folder is a link to another place. Delete it by hand.")
    if not raw.is_dir():
        raise CannotDelete("The series folder isn't there any more. Use Rescan this series to remove it.")
    folder, root = raw.resolve(), Path(lib.root_path).resolve()
    if folder.parent != root:  # series are always folders directly inside a library folder
        raise CannotDelete("Only a series folder directly inside a library folder can be deleted.")
    if _read_only_mount(folder):
        raise CannotDelete(READ_ONLY)
    if not (os.access(root, os.W_OK) and os.access(folder, os.W_OK)):
        raise CannotDelete(NO_PERMISSION)
    return folder


def _size(folder: Path):
    files = size = 0
    for dirpath, _, filenames in os.walk(folder):
        for f in filenames:
            files += 1
            try:
                size += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return files, size


def info(db, series):
    """What the confirmation shows: the folder, its files and size, and whether it can be deleted."""
    out = {"folder": series.folder_path, "files": 0, "size": 0, "can_delete": True, "reason": None}
    try:
        folder = series_folder(db, series)
    except CannotDelete as e:
        out.update(can_delete=False, reason=str(e))
        if Path(series.folder_path).is_dir():
            out["files"], out["size"] = _size(Path(series.folder_path))
        return out
    out["files"], out["size"] = _size(folder)
    return out


def delete_series(series_id: int):
    """Delete the series' folder, then the series from the database.
    Returns {"deleted": True} or, when some files couldn't be removed, {"deleted": False, "left": n,
    "error": "..."}: the series then stays, rescanned, so what is left still shows up."""
    if not walker._lock.acquire(timeout=2):
        raise walker.Busy("A library scan is running. Try again when it has finished.")
    rescan = False
    try:
        with SessionLocal() as db:
            series = db.get(Series, series_id)
            if series is None:
                raise KeyError(series_id)
            folder = series_folder(db, series)
            name = series.folder_name
            # plugins see the series (and their own data for it) one last time
            plugins.run_hook(db, "on_series_deleted", series)
            db.commit()
            errors = []
            shutil.rmtree(folder, onexc=lambda f, p, e: _collect(errors, f, p, e))
            if folder.exists():
                left, _ = _size(folder)
                log.warning("Deleting %s left %d files: %s", folder, left, errors[0] if errors else "?")
                rescan = True
                return {"deleted": False, "left": left,
                        "error": errors[0] if errors else "Some files could not be deleted."}
            book_ids = [b.id for b in series.books]
            forget(db, series_id, book_ids)
            db.delete(series)  # books, progress, tags, links and plugin data go with it (ON DELETE CASCADE)
            db.commit()
            remove_cached_files(series_id, book_ids)
            log.info("Deleted series %s (%s) and its folder", name, folder)
            return {"deleted": True}
    finally:
        walker._lock.release()
        if rescan:
            try:
                walker.rescan_series(series_id)
            except Exception:
                log.exception("Rescan after a partial delete failed")


def _collect(errors, func, path, exc):
    """rmtree error handler: a file marked read-only (common on Windows shares) is made writable
    and tried once more; anything else is noted and the rest of the folder is still deleted."""
    if isinstance(exc, PermissionError):
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if os.path.isdir(path) else 0))
            func(path)
            return
        except OSError as e:
            exc = e
    errors.append(f"{Path(path).name}: {getattr(exc, 'strerror', None) or exc}")


def forget(db, series_id, book_ids):
    """Drop the series' rows from the Needs attention ignore list (they would never match again)."""
    keys = set(get_setting(db, "attention_ignored", []) or [])
    sid, books = str(series_id), {f"err:{b}" for b in book_ids}

    def belongs(k):
        kind, _, rest = k.partition(":")
        if kind in ("dup", "gap"):
            return rest.split(":")[0] == sid
        if kind == "same":
            return sid in rest.split("-")  # a hashed key (a big group) can't be read; it's left
        return k in books

    kept = {k for k in keys if not belongs(k)}
    if kept != keys:
        set_setting(db, "attention_ignored", sorted(kept))


def remove_cached_files(series_id, book_ids):
    """The poster, book thumbnails and resized pages the app made in /data."""
    for p in (config.COVERS_DIR / f"{series_id}.jpg", config.COVERS_DIR / f"{series_id}_sm.webp"):
        p.unlink(missing_ok=True)
    for b in book_ids:
        (config.THUMBS_DIR / f"{b}.webp").unlink(missing_ok=True)
        shutil.rmtree(config.PAGES_DIR / str(b), ignore_errors=True)
