"""SQLite engine, sessions, first-start setup and small hand-written migrations."""
import logging
import sys

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import config
from app.models import Base, Setting

log = logging.getLogger("mangavibe")

engine = None
SessionLocal = sessionmaker(expire_on_commit=False)

# Each entry runs once, in order. Add new ones at the end, never edit old ones.
# Example: lambda conn: conn.execute(text("ALTER TABLE series ADD COLUMN foo TEXT"))
MIGRATIONS = [
    # 1: metadata matching (phase 2)
    lambda conn: [conn.execute(text(f"ALTER TABLE series ADD COLUMN {c}")) for c in
                  ("match_candidates JSON", "metadata_source JSON", "banner_url VARCHAR")],
    # 2: manual chapter ranges per file
    lambda conn: conn.execute(text("ALTER TABLE book ADD COLUMN override_chapter_end FLOAT")),
]


CUSTOM_CSS_TEMPLATE = """/*
 * Your own styles for MangaVibe. Edit this file (docker/mangavibe/custom.css on the NAS),
 * then reload the page. No restart needed.
 *
 * Rules here always win over the built-in styles, so you don't need !important.
 * The easiest changes are the design tokens (see app/web/static/css/tokens.css), for example:
 *
 * :root {
 *   --accent: #4f8cff;        main colour (buttons, active tabs)
 *   --card: 130px;            minimum width of library cards
 *   --poster-width: 340px;    series page poster on desktop
 *   --radius: 4px;            rounder or sharper corners
 * }
 *
 * .card-title {
 *   font-weight: 600;
 * }
 */
"""


def ensure_data_dir():
    """Create /data folders. Exit with a plain-English hint if /data is not writable."""
    try:
        for d in (config.DATA_DIR, config.COVERS_DIR, config.THUMBS_DIR, config.PAGES_DIR,
                  config.PLUGINS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        if not config.CUSTOM_CSS.exists():
            config.CUSTOM_CSS.write_text(CUSTOM_CSS_TEMPLATE, encoding="utf-8")
        probe = config.DATA_DIR / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        log.error(
            "Cannot write to the data folder %s (%s).\n"
            "  Fix on Synology: open File Station, right-click the docker/mangavibe folder ->\n"
            "  Properties -> Permission -> Create -> User or group: Everyone -> tick Read and Write ->\n"
            "  tick 'Apply to this folder, sub-folders and files' -> Done -> Save. Then restart the container.",
            config.DATA_DIR, e)
        sys.exit(1)


def _adopt_old_database():
    """The app used to be called MangaShelf: keep its database (progress, metadata) on upgrade."""
    old = config.DATA_DIR / "mangashelf.db"
    if config.DB_PATH.exists() or not old.exists():
        return
    for suffix in ("", "-wal", "-shm"):  # SQLite's side files must move with the database
        src = old.with_name(old.name + suffix)
        if src.exists():
            src.rename(config.DB_PATH.with_name(config.DB_PATH.name + suffix))
    log.info("Renamed the old MangaShelf database to %s", config.DB_PATH.name)


def init_db():
    global engine
    ensure_data_dir()
    _adopt_old_database()
    first = not config.DB_PATH.exists()
    engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)"))
        row = conn.execute(text("SELECT version FROM schema_version")).fetchone()
        if row is None:
            # brand-new DB already has the latest tables from create_all
            conn.execute(text("INSERT INTO schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
        else:
            for i in range(row[0], len(MIGRATIONS)):
                log.info("Running database migration %d", i + 1)
                MIGRATIONS[i](conn)
                conn.execute(text("UPDATE schema_version SET version=:v"), {"v": i + 1})
    log.info("%s database at %s", "Created" if first else "Opened", config.DB_PATH)


def get_setting(db, key, default=None):
    row = db.get(Setting, key)
    return default if row is None else row.value


def set_setting(db, key, value):
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
