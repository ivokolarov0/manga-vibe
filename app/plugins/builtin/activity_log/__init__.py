"""Activity log (built-in plugin, issue #279): who did what, and when, for the admins.

The app writes an entry with activity.log() (app/activity.py) where something an admin may want to know
happens; this plugin hears each one (on_activity, in the background, so logging never slows a request)
and keeps it in a table of its own, `activity`. Switched off, nothing is kept: log() costs nothing.

- store.py: the table, writing an entry, reading them back
- its API, admins only, at /api/plugins/activity-log/api/:
  - GET entries?before=&user=&kind=&group=&q=&since=&until=&limit=: newest first; `before` is the id of
    the last entry the page has, for the next page. Each entry says whether its target is still there
    (`target_exists`), so the page links only to what it can open
  - GET people: who has entries, for the page's filter
- activity_log.js, settings.html, activity_log.css: Settings → Activity, admins only (MangaVibe.settingsPage
  in the "start" event, so switching the plugin on or off adds or removes it without a reload)
"""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from app.plugins import Context, Plugin, manager
from app.plugins.sdk import Library, Series, Shelf
from app.plugins.web import plugin_ctx
from app.users import manage

from . import store

HERE = Path(__file__).parent
router = APIRouter(dependencies=[manage])


def _read(name):
    return (HERE / name).read_text(encoding="utf-8")


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


@router.get("/entries")
def entries(before: int | None = Query(None, ge=1), user: str | None = None, kind: str | None = None,
            group: str | None = None, q: str | None = Query(None, max_length=200),
            since: datetime | None = None, until: datetime | None = None,
            limit: int = Query(50, ge=1, le=store.MAX_PAGE), ctx: Context = Depends(plugin_ctx)):
    """A page of entries, newest first. `user`: an id, or "system" for the app's own work; `kind` one kind
    ("series.deleted") or a prefix ending in a dot ("account."); `group` one or more of activity.GROUPS
    (comma-separated); `q` words to find; `since` / `until` ISO times (from, and before)."""
    found = store.page(ctx.db, before=before, user=user, kind=kind, group=group, limit=limit, q_text=q,
                       since=since, until=until)
    mark_targets(ctx.db, found["entries"])
    return found


@router.get("/people")
def people(ctx: Context = Depends(plugin_ctx)):
    """Who has entries: [{id, name}] (id None: System, 0: not logged in)."""
    return store.people(ctx.db)


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

    def on_activity(self, ctx, entry):
        store.add(ctx.db, entry)
