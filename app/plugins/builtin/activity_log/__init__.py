"""Activity log (built-in plugin, issue #279): who did what, and when, for the admins.

The app writes an entry with activity.log() (app/activity.py) where something an admin may want to know
happens; this plugin hears each one (on_activity, in the background, so logging never slows a request)
and keeps it in a table of its own, `activity`. Switched off, nothing is kept: log() costs nothing.

- store.py: the table, writing an entry, reading them back
- its API, admins only: GET /api/plugins/activity-log/api/entries?before=&user=&kind=&group=&limit=,
  newest first; `before` is the id of the last entry the page has, for the next page
"""
from fastapi import APIRouter, Depends, Query

from app.plugins import Context, Plugin
from app.plugins.web import plugin_ctx
from app.users import manage

from . import store

router = APIRouter(dependencies=[manage])


@router.get("/entries")
def entries(before: int | None = Query(None, ge=1), user: str | None = None, kind: str | None = None,
            group: str | None = None, limit: int = Query(50, ge=1, le=store.MAX_PAGE),
            ctx: Context = Depends(plugin_ctx)):
    """A page of entries, newest first. `user`: an id, or "system" for the app's own work; `kind` one kind
    ("series.deleted") or a prefix ending in a dot ("account."); `group` one of activity.GROUPS."""
    return store.page(ctx.db, before=before, user=user, kind=kind, group=group, limit=limit)


class ActivityLog(Plugin):
    id = "activity-log"
    category = "system"
    name = "Activity log"
    version = "1.0"
    description = ("Keeps a log of who did what, and when: logins, accounts, folders and scans, deleted series, "
                   "plugins and settings. Only admins can see it. Built in.")
    permissions = ["Keeps the log in MangaVibe's database; connects to nothing"]
    router = router

    def on_start(self, ctx):
        store.ensure_table(ctx.db)

    def on_activity(self, ctx, entry):
        store.add(ctx.db, entry)
