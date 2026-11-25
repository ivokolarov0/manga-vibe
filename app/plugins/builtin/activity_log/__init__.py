"""Activity log (built-in plugin, issue #279): who did what, and when, for the admins.

The app writes an entry with activity.log() (app/activity.py) where something an admin may want to know
happens; this plugin hears each one (on_activity, in the background, so logging never slows a request)
and keeps it in a table of its own, `activity`. Switched off, nothing is kept: log() costs nothing.

- store.py: the table, writing an entry, reading them back, removing old ones
- export.py: the entries as a CSV file
- its API, admins only, at /api/plugins/activity-log/api/:
  - GET entries?before=&user=&kind=&group=&q=&since=&until=&limit=: newest first; `before` is the id of
    the last entry the page has, for the next page. Each entry says whether its target is still there
    (`target_exists`), so the page links only to what it can open
  - GET people: who has entries, for the page's filter
  - GET / PUT options: {"keep_days": 30 | 90 | 365 | 0 (forever)}, how long entries are kept
  - POST clear: remove every entry (that is logged itself)
  - GET export.csv?<the filters>&tz=&offset=: what the filters show, as CSV
- activity_log.js, settings.html, activity_log.css: Settings → Activity, admins only (MangaVibe.settingsPage
  in the "start" event, so switching the plugin on or off adds or removes it without a reload)

Old entries are removed by a thread of the plugin's own: at start, then once a day, and at once when the
time is made shorter.
"""
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.plugins import Context, Plugin, activity, manager
from app.plugins.sdk import Library, Series, Shelf
from app.plugins.web import plugin_ctx
from app.users import manage

from . import export, store

HERE = Path(__file__).parent
router = APIRouter(dependencies=[manage])
KEEP_CHOICES = (30, 90, 365, 0)  # days; 0: forever
KEEP_DEFAULT = 90
CLEAN_EVERY = 24 * 3600  # seconds


def _read(name):
    return (HERE / name).read_text(encoding="utf-8")


def keep_days(ctx) -> int:
    """How many days entries are kept (0: forever)."""
    try:
        days = int(ctx.settings.get("keep_days", KEEP_DEFAULT))
    except (TypeError, ValueError):
        return KEEP_DEFAULT
    return days if days in KEEP_CHOICES else KEEP_DEFAULT


def clean_up(ctx) -> int:
    """Remove the entries older than the time they're kept. Not committed. Returns how many went."""
    days = keep_days(ctx)
    return store.remove_older_than(ctx.db, days) if days else 0


def _cleaner(ctx):
    """The plugin's thread: clean up now, then once a day, until the plugin stops (reloaded, switched off)."""
    while not ctx.should_stop():
        try:
            gone = clean_up(ctx)
            ctx.db.commit()
            if gone:
                ctx.log.info("Removed %d activity entries older than %d days", gone, keep_days(ctx))
        except Exception:  # noqa: BLE001 - e.g. the database is busy: next time
            ctx.db.rollback()
            ctx.log.exception("Could not remove old activity entries")
        until = time.monotonic() + CLEAN_EVERY
        while time.monotonic() < until and not ctx.should_stop():
            time.sleep(1)  # short: stopping (a backup import waits for it) mustn't wait long


def _ids(entries, kind):
    out = set()
    for e in entries:
        if e["target"] and e["target"][0] == kind and str(e["target"][1] or "").isdigit():
            out.add(int(e["target"][1]))
    return out


def mark_targets(db, entries):
    """Set `target_exists` on each entry: its series, account, plugin, folder or library is still there."""
    there = {}
    for kind, model in (("series", Series), ("folder", Library), ("library", Shelf)):
        ids = _ids(entries, kind)
        there[kind] = {str(i) for (i,) in db.query(model.id).filter(model.id.in_(ids))} if ids else set()
    there["plugin"] = set(manager.plugins)
    there["account"] = {str(i) for i in _ids(entries, "account") if manager.find_user(db, i) is not None}
    for e in entries:
        t = e["target"]
        e["target_exists"] = bool(t) and str(t[1]) in there.get(t[0], set())
    return entries


def _filters(user: str | None = None, kind: str | None = None, group: str | None = None,
             q: str | None = Query(None, max_length=200), since: datetime | None = None,
             until: datetime | None = None) -> dict:
    """The page's filters, the same for the list and the export."""
    return {"user": user, "kind": kind, "group": group, "q_text": q, "since": since, "until": until}


@router.get("/entries")
def entries(before: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=store.MAX_PAGE),
            filters: dict = Depends(_filters), ctx: Context = Depends(plugin_ctx)):
    """A page of entries, newest first. `user`: an id, or "system" for the app's own work; `kind` one kind
    ("series.deleted") or a prefix ending in a dot ("account."); `group` one or more of activity.GROUPS
    (comma-separated); `q` words to find; `since` / `until` ISO times (from, and before)."""
    found = store.page(ctx.db, before=before, limit=limit, **filters)
    mark_targets(ctx.db, found["entries"])
    return found


@router.get("/people")
def people(ctx: Context = Depends(plugin_ctx)):
    """Who has entries: [{id, name}] (id None: System, 0: not logged in)."""
    return store.people(ctx.db)


class Options(BaseModel):
    keep_days: int


@router.get("/options")
def get_options(ctx: Context = Depends(plugin_ctx)):
    return {"keep_days": keep_days(ctx), "choices": list(KEEP_CHOICES)}


@router.put("/options")
def put_options(body: Options, ctx: Context = Depends(plugin_ctx)):
    """How long entries are kept. A shorter time removes the older entries at once."""
    if body.keep_days not in KEEP_CHOICES:
        raise HTTPException(400, "Keep entries for 30, 90 or 365 days, or forever (0)")
    old = keep_days(ctx)
    ctx.save_settings({**ctx.settings, "keep_days": body.keep_days})
    gone = clean_up(ctx)
    ctx.db.commit()
    if old != body.keep_days:
        activity.log("setting.changed", "activity-log.keep_days", target=("plugin", ActivityLog.id),
                     details={"plugin": ActivityLog.id, "setting": "keep_days", "old": old, "new": body.keep_days})
    return {"keep_days": body.keep_days, "removed": gone}


@router.post("/clear")
def clear(ctx: Context = Depends(plugin_ctx)):
    """Remove every entry. The clearing is the log's next entry, so it's never silently empty."""
    gone = store.clear(ctx.db)
    ctx.db.commit()
    activity.log("activity.cleared", "", details={"entries": gone})
    return {"removed": gone}


@router.get("/export.csv")
def export_csv(filters: dict = Depends(_filters), tz: str | None = Query(None, max_length=60),
               offset: int = Query(0, ge=-900, le=900)):
    """What the filters show, as CSV (newest first): the time in UTC and in the person's time zone (`tz`, an
    IANA name, else `offset`: minutes east of UTC), who, what, the target and the details as JSON. Streamed
    from a session of its own, so a long log never sits in memory."""
    rows = export.csv_rows(filters, export.zone(tz, offset))
    name = f"mangavibe-activity-{datetime.now().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(rows, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"',
                                      "Cache-Control": "no-store"})


class ActivityLog(Plugin):
    id = "activity-log"
    category = "system"
    name = "Activity log"
    version = "1.0"
    description = ("Keeps a log of who did what, and when: logins, accounts, folders and scans, deleted series, "
                   "plugins and settings. Only admins can see it. Built in.")
    permissions = ["Adds Settings → Activity, for admins only",
                   "Keeps the log in MangaVibe's database; connects to nothing"]
    router = router
    js = _read("activity_log.js")
    css = _read("activity_log.css")
    html = {"settings": _read("settings.html")}

    def on_start(self, ctx):
        store.ensure_table(ctx.db)
        ctx.run_in_thread(_cleaner)

    def on_activity(self, ctx, entry):
        store.add(ctx.db, entry)
