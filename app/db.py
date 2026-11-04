"""SQLite engine, sessions, first-start setup and small hand-written migrations."""
import logging
import sys

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app import config
from app.models import BOOK_NUMBERS_COLUMNS, Base, Setting

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
    # 3: a reading direction per library folder, for its series that aren't matched
    lambda conn: conn.execute(text("ALTER TABLE library ADD COLUMN reader_mode VARCHAR")),
    # 4: no more right to left: every paged series reads like a digital comic (next page on the right)
    lambda conn: [conn.execute(text(q)) for q in (
        "UPDATE series SET reader_mode='paged' WHERE reader_mode IN ('rtl', 'ltr')",
        "UPDATE library SET reader_mode='paged' WHERE reader_mode IN ('rtl', 'ltr')",
        "UPDATE setting SET value='\"paged\"' WHERE key='reader_mode' AND value IN ('\"rtl\"', '\"ltr\"')",
        "DELETE FROM setting WHERE key='reader_tap_invert'")],
    # 5: indexes for big libraries (see Book and Progress in app/models.py)
    lambda conn: [conn.execute(text(q)) for q in (
        f"CREATE INDEX IF NOT EXISTS ix_book_numbers ON book ({', '.join(BOOK_NUMBERS_COLUMNS)})",
        "CREATE INDEX IF NOT EXISTS ix_book_error ON book (series_id) WHERE error IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_book_duplicate ON book (series_id) WHERE duplicate_flag IS 1",
        "CREATE INDEX IF NOT EXISTS ix_progress_book_state ON progress (book_id, completed, page)")],
    # 6: the books' numbering stored per series (filled in by fill_numbering() at start)
    lambda conn: conn.execute(text("ALTER TABLE series ADD COLUMN numbering JSON")),
    # 7: a collection marked complete by hand (omnibus editions)
    lambda conn: conn.execute(text("ALTER TABLE series ADD COLUMN collection_complete BOOLEAN DEFAULT 0")),
    # 8: editions (omnibus, kanzenban...): the list from MangaUpdates, the owner's pick, and the
    # pages per file in the numbering (cleared so fill_numbering() works it out again)
    lambda conn: [conn.execute(text(q)) for q in (
        "ALTER TABLE series ADD COLUMN editions JSON",
        "ALTER TABLE series ADD COLUMN edition_volumes INTEGER",
        "UPDATE series SET numbering = NULL",
        "DROP INDEX IF EXISTS ix_book_numbers",
        f"CREATE INDEX ix_book_numbers ON book ({', '.join(BOOK_NUMBERS_COLUMNS)})")],
    # 9: chapter titles no longer make a file an extra ("Chapter 333 - Something Special"):
    # the next scan reads the files marked extra again
    lambda conn: conn.execute(text("UPDATE book SET mtime = -1 WHERE extra = 1")),
    # 10: extras are no longer guessed from file names (only the owner marks them): the next scan
    # reads the files that were guessed as extras again, to give them their chapter number
    lambda conn: conn.execute(text("UPDATE book SET mtime = -1 WHERE extra = 1")),
    # 11: extras with a chapter or volume number stay in their place: the next scan sorts the
    # series that have extras again (a sort_index of 0 makes it)
    lambda conn: conn.execute(text("UPDATE book SET sort_index = 0 "
                                   "WHERE COALESCE(override_extra, extra) = 1")),
    # 12: each library folder's path outside Docker, for the series page's "Copy path"
    lambda conn: conn.execute(text("ALTER TABLE library ADD COLUMN host_path VARCHAR")),
    # 13: the numbering marks one-file series (one-shots) for the collection check, so it's worked
    # out again; and the next scan reads the files without a number again: ones read before the
    # parser knew names like "001 Read Online" kept no number, as unchanged files aren't re-read
    lambda conn: [conn.execute(text(q)) for q in (
        "UPDATE series SET numbering = NULL",
        "UPDATE book SET mtime = -1 WHERE volume IS NULL AND chapter IS NULL")],
    # 14: libraries (Manga, Comics...) apart from the folders: the shelf table comes from create_all;
    # each folder gets one, named like it and with its reading mode (shelves.ensure_folder_shelves())
    lambda conn: [conn.execute(text(q)) for q in (
        "ALTER TABLE library ADD COLUMN shelf_id INTEGER REFERENCES shelf(id) ON DELETE SET NULL",
        "ALTER TABLE series ADD COLUMN shelf_id INTEGER REFERENCES shelf(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS ix_series_shelf_id ON series (shelf_id)")],
    # 15: accounts (app/users.py). The account tables come from create_all; see _migrate_accounts()
    lambda conn: _migrate_accounts(conn),
]

# the settings that became each account's own in migration 15 (the rest stay the server's)
_PERSONAL_IN_15 = ("title_language", "theme", "card_size", "nav_position", "library_listing",
                   "library_page_size", "reader_mode", "reader_fit", "auto_fullscreen", "preload",
                   "reader_autohide", "reader_drawer_side", "reader_tap_layout", "reader_volume_keys",
                   "overlay_opacity", "overlay_outline", "reader_brightness", "reader_contrast",
                   "reader_colors", "saved_searches")


def _migrate_accounts(conn):
    """The one admin account (ADMIN_USER / ADMIN_PASSWORD, else "Admin" without a password, so nothing
    changes until a second account or a password is added) gets everything there was: the progress,
    reading statuses, favorites, reading modes, personal settings, saved searches and every library."""
    from app.models import Progress, User
    from app.shelves import hash_password
    password = config.ADMIN_PASSWORD
    conn.execute(User.__table__.insert().values(
        id=1, name=config.ADMIN_USER or "Admin", is_admin=True, session_version=0,
        password_hash=hash_password(password) if password else None,
        password_kind=("pin" if password.isdigit() else "password") if password else None))
    for q in (
            # progress per account: the old table had one row per book (book_id unique)
            "DROP INDEX IF EXISTS ix_progress_book_state",
            "DROP INDEX IF EXISTS ix_progress_updated_at",
            "ALTER TABLE progress RENAME TO progress_old"):
        conn.execute(text(q))
    Progress.__table__.create(conn)
    for q in (
            "INSERT INTO progress (id, user_id, book_id, page, completed, updated_at) "
            "SELECT id, 1, book_id, page, completed, updated_at FROM progress_old",
            "DROP TABLE progress_old",
            "INSERT INTO user_series (user_id, series_id, reading_status, reader_mode, favorite, last_read_at) "
            "SELECT 1, id, reading_status, reader_mode, COALESCE(favorite, 0), last_read_at FROM series "
            "WHERE reading_status IS NOT NULL OR reader_mode IS NOT NULL OR favorite = 1 OR last_read_at IS NOT NULL"):
        conn.execute(text(q))
    # upgrading from before libraries (schema 13 or older), create_all made the shelf table just now,
    # owner_id included
    if "owner_id" not in {row[1] for row in conn.execute(text("PRAGMA table_info(shelf)"))}:
        conn.execute(text("ALTER TABLE shelf ADD COLUMN owner_id INTEGER REFERENCES account(id) ON DELETE SET NULL"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_shelf_owner_id ON shelf (owner_id)"))
    conn.execute(text("UPDATE shelf SET owner_id = 1"))
    keys = ", ".join(f"'{k}'" for k in _PERSONAL_IN_15)
    conn.execute(text(f"INSERT INTO user_setting (user_id, key, value) SELECT 1, key, value FROM setting "
                      f"WHERE key IN ({keys})"))
    conn.execute(text(f"DELETE FROM setting WHERE key IN ({keys})"))


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
            "  Fix: give the user the container runs as (PUID:PGID, or root) read and write access to the\n"
            "  folder mounted as /data, e.g. `chown -R 1000:1000 <folder>`, or on a NAS in the folder's\n"
            "  permission settings. Then restart the container.",
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
    # Every request holds a connection while it runs, and the web server runs up to 40 at once
    # (anyio's thread limit), plus the scanner, metadata and plugin threads. SQLAlchemy's default
    # pool (5 + 10 overflow) ran dry when 15 requests were slow, and every other request then waited
    # 30 s for a connection and failed: the site froze. SQLite connections are cheap, so the pool
    # keeps as many as the threads that can use them (opening extra ones per burst re-ran the PRAGMAs
    # below each time), with room for the background threads on top.
    engine = create_engine(f"sqlite:///{config.DB_PATH}", pool_size=40, max_overflow=20,
                           connect_args={"check_same_thread": False, "timeout": 30})

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


def get_user_setting(db, user_id, key, default=None):
    """One account's own setting (theme, reader...), see app/users.py."""
    from app.models import UserSetting
    row = db.get(UserSetting, (user_id, key)) if user_id is not None else None
    return default if row is None else row.value


def set_user_setting(db, user_id, key, value):
    from app.models import UserSetting
    row = db.get(UserSetting, (user_id, key))
    if row is None:
        db.add(UserSetting(user_id=user_id, key=key, value=value))
    else:
        row.value = value


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
