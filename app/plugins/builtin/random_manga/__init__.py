"""Random manga (built-in plugin, issue #275): for when you can't decide what to read next.

A shuffle button on the library page (and in the Sidebar's column) opens a series picked at random from
what the library shows (the picked library; never a locked one); its page then has a Pick another button
to pick again without going back. The settings leave out series whose reading status is completed or
dropped (both on by default), using the status the app shows (app/plugins/query.py). When nothing is left
to pick, it says so.

The pick is GET /api/plugins/random-manga/api/pick (current=<id>: never this one; recent=<ids>: these
only when nothing else is left); the UI is random_manga.js and the markup in *.html (one file per slot).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func

from app.plugins import McpTool, Plugin, SettingDef
from app.plugins.query import ListQuery, values
from app.plugins.sdk import Series, display_title, t, title_lang, visible_series
from app.plugins.web import plugin_ctx

HERE = Path(__file__).parent
YES_NO = [("yes", "Yes"), ("no", "No")]
MAX_RECENT = 50

router = APIRouter()


def excluded(ctx) -> list[str]:
    """The reading statuses the settings leave out."""
    return [status for key, status in (("exclude_completed", "completed"), ("exclude_dropped", "dropped"))
            if ctx.setting(key) != "no"]


def _candidates(db, base, leave_out):
    """The series of `base` whose reading status isn't one of `leave_out`."""
    lq = ListQuery(db, base, title_lang(db))
    if leave_out:
        lq.query = lq.query.filter(lq.reading_status().not_in(leave_out))
    return lq.result()


def pick(ctx, request, current: int | None = None, recent=()) -> dict:
    """A random series this request may see, by the settings: {"series": {id, title}, "message": ""},
    or {"series": None, "message": why there's nothing to pick}."""
    db, leave_out = ctx.db, excluded(ctx)
    base = db.query(Series).filter(Series.missing_since.is_(None), *visible_series(db, request))
    query = _candidates(db, base, leave_out)
    if current:
        query = query.filter(Series.id != current)
    found = None
    if recent:  # not one picked lately, while others are left
        found = query.filter(Series.id.not_in(list(recent))).order_by(func.random()).first()
    found = found or query.order_by(func.random()).first()
    if found:
        return {"series": {"id": found.id, "title": display_title(found, title_lang(db))}, "message": ""}
    if current and _candidates(db, base, leave_out).filter(Series.id == current).count():
        message = t("This is the only series left to pick.")
    elif not base.count():
        message = t("Nothing to pick: there are no series here.")
    elif leave_out == ["completed", "dropped"]:
        message = t("Nothing to pick: every series here is read or dropped.")
    elif leave_out == ["completed"]:
        message = t("Nothing to pick: every series here is read.")
    else:
        message = t("Nothing to pick: every series here is dropped.")
    return {"series": None, "message": message}


@router.get("/pick")
def pick_route(request: Request, current: int | None = None, ctx=Depends(plugin_ctx)):
    """Pick a series. The lists' scope comes along (e.g. the libraries plugin's shelf=3), so it picks
    from the library shown."""
    recent = [int(x) for x in values(request.query_params, "recent") if x.isdigit()][:MAX_RECENT]
    return pick(ctx, request, current, recent)


class RandomManga(Plugin):
    id = "random-manga"
    name = "Random manga"
    category = "library"
    version = "1.0"
    description = ("Can't decide what to read next? A button on the library page opens a series picked at "
                   "random, and Pick another picks again. Built in.")
    permissions = ["Adds a Random series button to the library page, the Sidebar and the picked series' page",
                   "Reads the library and your reading status; keeps nothing and connects to nothing"]
    settings_fields = [
        SettingDef("exclude_completed", "Exclude read manga", default="yes", options=YES_NO,
                   help="Don't pick series whose reading status is Completed."),
        SettingDef("exclude_dropped", "Exclude dropped manga", default="yes", options=YES_NO,
                   help="Don't pick series whose reading status is Dropped."),
    ]
    router = router
    mcp_tools = [McpTool("pick", "A series picked at random from the library, for when you can't decide what to "
                                 "read next (read and dropped series are left out, unless the plugin's settings "
                                 "say otherwise)")]

    def run_mcp_tool(self, ctx, name, args):
        if name == "pick":
            # no browser behind it: locked libraries stay locked
            return pick(ctx, Request({"type": "http", "query_string": b"", "headers": []}))
        raise ValueError(f"No tool {name}")

    js = (HERE / "random_manga.js").read_text(encoding="utf-8")
    css = (HERE / "random_manga.css").read_text(encoding="utf-8")
    html = {"library-tools": (HERE / "tool.html").read_text(encoding="utf-8"),
            # in the Sidebar's column, right after Library and Continue (before the libraries)
            "nav-items": ((HERE / "nav.html").read_text(encoding="utf-8"), 10),
            "series-actions": (HERE / "series_action.html").read_text(encoding="utf-8")}
