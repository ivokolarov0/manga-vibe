"""Export everything MangaVibe knows into one file, and import it again (Settings → Backup, issue #262).

The file is a .zip:
- manifest.json: {"app": "MangaVibe", "format": 1, "schema": <migrations the database has had>,
  "version": <the app's version>, "created": <ISO date>, "tokens": <tokens included>, counts}
- mangavibe.db: a copy of the database (SQLite's own backup, consistent while the app writes). It holds
  the library's metadata and edits, every user's progress and reading status, the accounts, the
  libraries, and the app's and the plugins' settings.
- covers/: the posters (downloaded, uploaded or picked from a page). An uploaded one exists nowhere else.
- custom.css: the owner's own styles.
The manga files are not in it, nor the caches (thumbnails, resized pages: made again) or the plugins
installed into /data/plugins (their settings are in the database; install them again).

Tokens (the GitHub token, plugin settings marked secret) are left out unless asked for. Password hashes
(accounts, locked libraries) and the keys that sign logins always go along: without them nobody could log
in after an import.

Import replaces everything. The file's database is checked and brought up to date on a copy first
(migrations), a file from a newer MangaVibe is refused; then the current database is copied to
/data/backups/before-import-<date>.db and the file's is copied over it, while the app keeps running.
Books are matched by path: the next scan reads every file again (new thumbnails, this server's dates);
progress of books that aren't there is kept like any missing file's (hidden, removed after 30 days; a
library folder that isn't there at all is left alone, so mount it and scan).
"""
import json
import logging
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from app import config

log = logging.getLogger("mangavibe")

FORMAT = 1  # the file's layout; a newer one is refused
MANIFEST = "manifest.json"
DB_NAME = "mangavibe.db"
COVERS = "covers/"
CSS_NAME = "custom.css"
KEEP_SAFETY_COPIES = 3
_SAFE_NAME = re.compile(r"^[\w.-]+$")


class ImportRefused(Exception):
    """The file can't be imported; the message says why, for the owner."""


class Busy(Exception):
    """A scan is running: try again when it has finished."""


def backups_dir() -> Path:
    return config.DATA_DIR / "backups"


@contextmanager
def _work_dir():
    """A folder on the data volume (an export can be big; /tmp may be small) that is removed after."""
    root = config.DATA_DIR / ".backup-work"
    root.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=root))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _copy_db(src, dst):
    """SQLite's own backup: a consistent copy even while the app writes. Replaces what dst holds."""
    a = sqlite3.connect(src, timeout=30)
    try:
        b = sqlite3.connect(dst, timeout=30)
        try:
            a.backup(b)
        finally:
            b.close()
    finally:
        a.close()


def _schema_now() -> int:
    from app.db import MIGRATIONS
    return len(MIGRATIONS)


def _version() -> str | None:
    from app import updater
    cur = updater.current()
    return cur.get("version") or (cur.get("sha") or "")[:7] or None


# ---------------------------------------------------------------- export

def _secret_keys():
    """{settings key: [keys inside it]} of the plugin settings marked secret, for the loaded plugins."""
    from app.plugins import manager as plugins
    out = {}
    for p in plugins.plugins.values():
        keys = [f.key for f in getattr(p, "settings_fields", None) or [] if f.secret]
        if keys:
            out[f"plugin:{p.id}"] = keys
    return out


def _strip_tokens(path):
    """Take the tokens out of an exported copy (and VACUUM, so they aren't left in free pages)."""
    from app.plugin_updates import TOKEN_KEY
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM setting WHERE key = ?", (TOKEN_KEY,))
        for key, secret in _secret_keys().items():
            row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
            try:
                values = json.loads(row[0]) if row and row[0] else None
            except ValueError:
                continue
            if isinstance(values, dict) and any(k in values for k in secret):
                values = {k: v for k, v in values.items() if k not in secret}
                conn.execute("UPDATE setting SET value = ? WHERE key = ?", (json.dumps(values), key))
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def _counts(path) -> dict:
    conn = sqlite3.connect(path)
    try:
        return {name: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for name, table in (("series", "series"), ("books", "book"), ("progress", "progress"))}
    finally:
        conn.close()


def export_to(dest: Path, include_tokens=False) -> dict:
    """Write the export to `dest` (a .zip). Returns its manifest."""
    from app import db as database
    with _work_dir() as work:
        copy = work / DB_NAME
        _copy_db(Path(database.engine.url.database), copy)
        if not include_tokens:
            _strip_tokens(copy)
        conn = sqlite3.connect(copy)
        try:
            schema = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        finally:
            conn.close()
        manifest = {"app": "MangaVibe", "format": FORMAT, "schema": schema, "version": _version(),
                    "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "tokens": bool(include_tokens), **_counts(copy)}
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, indent=2))
            z.write(copy, DB_NAME)
            if config.COVERS_DIR.is_dir():
                for f in sorted(config.COVERS_DIR.iterdir()):
                    if f.is_file() and _SAFE_NAME.match(f.name):
                        # JPEG and WebP are compressed already
                        z.write(f, COVERS + f.name, compress_type=zipfile.ZIP_STORED)
            if config.CUSTOM_CSS.is_file():
                z.write(config.CUSTOM_CSS, CSS_NAME)
    return manifest


# ---------------------------------------------------------------- import

def _manifest(z: zipfile.ZipFile) -> dict:
    try:
        m = json.loads(z.read(MANIFEST))
    except (KeyError, ValueError):
        m = None
    if not isinstance(m, dict) or m.get("app") != "MangaVibe" or DB_NAME not in z.namelist():
        raise ImportRefused("This isn't a MangaVibe export: pick a file made by Settings → Backup → Export.")
    newer = (f"This file comes from a newer MangaVibe ({m.get('version') or 'unknown version'}) than this "
             "one. Update MangaVibe first, then import it.")
    if not isinstance(m.get("format"), int) or m["format"] > FORMAT:
        raise ImportRefused(newer)
    if not isinstance(m.get("schema"), int) or m["schema"] > _schema_now():
        raise ImportRefused(newer)
    return m


def _check_db(path: Path, manifest: dict):
    """The file's database opens, isn't damaged and isn't newer than this app."""
    try:
        conn = sqlite3.connect(path)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ImportRefused("The database in this file is damaged.")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise ImportRefused(f"The database in this file can't be read ({e}).")
    if row is None or not isinstance(row[0], int):
        raise ImportRefused("The database in this file has no version.")
    if row[0] > _schema_now():
        raise ImportRefused(f"This file comes from a newer MangaVibe ({manifest.get('version') or 'unknown version'}) "
                            "than this one. Update MangaVibe first, then import it.")


def _migrate_copy(path: Path):
    """Bring the file's database up to this version's tables, on its own (not the running one)."""
    from app.db import migrate
    eng = create_engine(f"sqlite:///{path}")
    try:
        migrate(eng)
    finally:
        eng.dispose()


def safety_copy() -> Path:
    """Copy the running database to /data/backups/before-import-<date>.db (the newest few are kept)."""
    from app import db as database
    d = backups_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"before-import-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.db"
    tmp = dest.with_name(dest.name + ".tmp")
    _copy_db(Path(database.engine.url.database), tmp)
    tmp.replace(dest)
    for old in sorted(d.glob("before-import-*.db"), reverse=True)[KEEP_SAFETY_COPIES:]:
        old.unlink(missing_ok=True)
    return dest


def _restore_files(z: zipfile.ZipFile):
    """The posters (the old ones go: series ids now mean the file's series) and custom.css."""
    covers = [n for n in z.namelist() if n.startswith(COVERS) and _SAFE_NAME.match(n[len(COVERS):])]
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    for f in config.COVERS_DIR.iterdir():
        if f.is_file():
            f.unlink(missing_ok=True)
    for n in covers:
        with z.open(n) as src, open(config.COVERS_DIR / n[len(COVERS):], "wb") as dst:
            shutil.copyfileobj(src, dst)
    if CSS_NAME in z.namelist():
        config.CUSTOM_CSS.write_bytes(z.read(CSS_NAME))


def _clear_caches():
    """Thumbnails and resized pages are kept by book id, which now means other books."""
    for d in (config.THUMBS_DIR, config.PAGES_DIR):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


def import_from(src: Path) -> dict:
    """Replace the database, posters and custom.css with the export in `src`. Raises ImportRefused
    (a file that can't be imported: nothing changed) or Busy (a scan is running)."""
    from app import db as database
    from app.metadata import worker
    from app.plugins import manager as plugins
    from app.scanner import walker
    try:
        z = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError):
        raise ImportRefused("This isn't a MangaVibe export (not a .zip file).")
    with z, _work_dir() as work:
        manifest = _manifest(z)
        copy = work / DB_NAME
        with z.open(DB_NAME) as f, open(copy, "wb") as out:
            shutil.copyfileobj(f, out)
        _check_db(copy, manifest)
        _migrate_copy(copy)

        if not walker._lock.acquire(timeout=5):  # no scan writes the old ids into the new database
            raise Busy("A library scan is running. Try again when it has finished.")
        try:
            with worker.busy:  # nor a metadata match
                saved = safety_copy()
                database.engine.dispose()
                _copy_db(copy, Path(database.engine.url.database))
                database.engine.dispose()  # no connection keeps what it read of the old one
                worker.clear()
                _restore_files(z)
                _clear_caches()
                with database.SessionLocal() as db:
                    # read every file again: thumbnails, and this server's file dates
                    db.execute(text("UPDATE book SET mtime = -1"))
                    db.commit()
                    walker.ensure_libraries(db, config.LIBRARY_PATHS)  # this server's folders
                    plugins.load()
                    plugins.load_switches(db)
                    plugins.start(db)
                    counts = _counts(Path(database.engine.url.database))
        finally:
            walker._lock.release()
    log.info("Imported an export from %s (MangaVibe %s); the database before it is in %s",
             manifest.get("created"), manifest.get("version"), saved)
    walker.start_scan_in_background()
    return {"ok": True, "safety_copy": str(saved), "from": {k: manifest.get(k) for k in ("version", "created")},
            **counts}
