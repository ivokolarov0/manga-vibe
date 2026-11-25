"""The activity log's table, `activity`: one row per entry (app/activity.py entry_of()).

The plugin's own table, not one of the app's models: made when the plugin starts (at app start, when
switched on, after a backup import), and again if it's missing when an entry comes. It keeps the user's
name at the time, so the entries of an account that was removed still read well. Indexes on `at`, on
`user_id` and on `kind`: the page lists the newest first and filters by person and by kind; the
clean-up removes by `at`.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Column, DateTime, Index, Integer, MetaData, String, Table, cast, func, or_, select
from sqlalchemy.exc import OperationalError

from app.plugins import activity

MAX_PAGE = 200

metadata = MetaData()
table = Table(
    "activity", metadata,
    Column("id", Integer, primary_key=True),
    Column("at", DateTime, nullable=False),     # UTC
    Column("user_id", Integer),                 # None: System (the app's own work); 0: not logged in
    Column("user_name", String, nullable=False, default=""),
    Column("ip", String),
    Column("via", String),                      # "key": a request that came with a key (the MCP server)
    Column("kind", String, nullable=False),     # e.g. series.deleted
    Column("grp", String, nullable=False),      # activity.GROUPS
    Column("summary", String, nullable=False, default=""),
    Column("target_type", String),
    Column("target_id", String),
    Column("details", JSON),
    Index("ix_activity_at", "at"),
    Index("ix_activity_user_id", "user_id", "id"),
    Index("ix_activity_kind", "kind", "id"),
)


def ensure_table(db):
    """Make the table if this database doesn't have it yet."""
    metadata.create_all(db.get_bind(), checkfirst=True)


def _row(entry: dict) -> dict:
    target = entry.get("target") or [None, None]
    at = datetime.fromisoformat(entry["at"].replace("Z", "+00:00")).replace(tzinfo=None)
    return {"at": at, "user_id": entry.get("user_id"), "user_name": entry.get("user_name") or "",
            "ip": entry.get("ip"), "via": entry.get("via"), "kind": entry["kind"],
            "grp": entry.get("group") or activity.group_of(entry["kind"]), "summary": entry.get("summary") or "",
            "target_type": target[0], "target_id": target[1], "details": entry.get("details") or {}}


def add(db, entry: dict):
    """Keep one entry. Not committed (the plugin worker commits; its session has nothing else)."""
    row = _row(entry)
    try:
        db.execute(table.insert().values(**row))
    except OperationalError:  # no table: e.g. it was dropped, or the database replaced under us
        db.rollback()
        ensure_table(db)
        db.execute(table.insert().values(**row))


def as_dict(row) -> dict:
    return {"id": row.id, "at": row.at.isoformat(timespec="seconds") + "Z",
            "user_id": row.user_id, "user_name": row.user_name, "ip": row.ip, "via": row.via,
            "kind": row.kind, "group": row.grp, "summary": row.summary,
            "target": [row.target_type, row.target_id] if row.target_type else None,
            "details": row.details or {}}


def _utc(value: datetime | None):
    """A time as the table keeps it: UTC, without a time zone."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


class NoMatch(Exception):
    """A filter nothing can match (e.g. a user that isn't a number)."""


def filtered(user=None, kind=None, group=None, q_text=None, since=None, until=None):
    """The entries the filters let through, as a query. `user` an id or "system"; `kind` one kind, or a
    prefix ending in "." ("account."); `group` one or more, comma-separated; `q_text` words that must all be
    in the summary, the name, the kind, the address or the details; `since` / `until` times (from, and
    before). Raises NoMatch."""
    q = select(table)
    if since is not None:
        q = q.where(table.c.at >= _utc(since))
    if until is not None:
        q = q.where(table.c.at < _utc(until))
    for word in (q_text or "").split()[:8]:
        like = "%" + "".join("\\" + c if c in "\\%_" else c for c in word) + "%"
        q = q.where(or_(table.c.summary.ilike(like, escape="\\"), table.c.user_name.ilike(like, escape="\\"),
                        table.c.kind.ilike(like, escape="\\"), table.c.ip.ilike(like, escape="\\"),
                        cast(table.c.details, String).ilike(like, escape="\\")))
    if user == "system":
        q = q.where(table.c.user_id.is_(None))
    elif user not in (None, ""):
        try:
            q = q.where(table.c.user_id == int(user))
        except ValueError:
            raise NoMatch from None
    if kind:
        q = q.where(table.c.kind.startswith(kind, autoescape=True) if kind.endswith(".")
                    else table.c.kind == kind)
    if group:
        q = q.where(or_(*[table.c.grp == g for g in group.split(",")]))
    return q


def page(db, before=None, limit=50, **filters) -> dict:
    """Entries, newest first: {"entries": [...], "more": whether there are older ones}. `before` an
    entry id; the filters as filtered() takes them."""
    try:
        q = filtered(**filters)
    except NoMatch:
        return {"entries": [], "more": False}
    if before:
        q = q.where(table.c.id < before)
    try:
        rows = db.execute(q.order_by(table.c.id.desc()).limit(limit + 1)).all()
    except OperationalError:  # no table yet: nothing was logged
        return {"entries": [], "more": False}
    return {"entries": [as_dict(r) for r in rows[:limit]], "more": len(rows) > limit}


def each(db, batch=500, **filters):
    """Every entry the filters let through, newest first, a batch at a time (an export of a long log
    never holds it all in memory)."""
    try:
        base = filtered(**filters)
    except NoMatch:
        return
    before = None
    while True:
        q = base.where(table.c.id < before) if before else base
        try:
            rows = db.execute(q.order_by(table.c.id.desc()).limit(batch)).all()
        except OperationalError:
            return
        yield from rows
        if len(rows) < batch:
            return
        before = rows[-1].id


def remove_older_than(db, days: int) -> int:
    """Remove the entries older than `days` days. Not committed. Returns how many went."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    try:
        return db.execute(table.delete().where(table.c.at < cutoff)).rowcount or 0
    except OperationalError:
        return 0


def clear(db) -> int:
    """Remove every entry. Not committed. Returns how many went."""
    try:
        return db.execute(table.delete()).rowcount or 0
    except OperationalError:
        return 0


def people(db) -> list[dict]:
    """Everyone with entries, for the page's filter: [{id, name}], the latest name each had; System
    (id None) and not logged in (id 0) included when they have entries."""
    latest = (select(table.c.user_id, func.max(table.c.id).label("last"))
              .group_by(table.c.user_id).subquery())
    q = (select(table.c.user_id, table.c.user_name)
         .join(latest, table.c.id == latest.c.last).order_by(table.c.user_name))
    try:
        return [{"id": r.user_id, "name": r.user_name} for r in db.execute(q)]
    except OperationalError:
        return []
