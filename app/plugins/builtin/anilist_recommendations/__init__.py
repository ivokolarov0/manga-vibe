"""AniList recommendations (built-in plugin, issue #244): "If you liked this", at the end of the series page.

For a series matched on AniList (Series.anilist_id), the series AniList's readers recommend, best rated
first, each with its cover. One already in the library links to its page here, with its own poster; the
others link to AniList. Asked for once per series and kept for the "days" setting (core.py); the panel is
panel.html in the series-end slot, drawn by recs.js. Covers are saved on the server by default (ctx.cache_dir),
or with Covers: "Loaded from AniList", the browser loads them from AniList itself. Switched off, the
series page shows nothing extra. See docs/PLUGINS.md, "AniList recommendations".
"""
import shutil
from pathlib import Path

from app.plugins import Action, Plugin, SettingDef
from app.plugins.sdk import PluginSeriesData

from . import core
from .api import router as api_router

HERE = Path(__file__).parent


class AniListRecommendations(Plugin):
    id = "anilist-recommendations"
    name = "AniList recommendations"
    category = ["library", "metadata"]
    version = "1.0"
    description = ("“If you liked this” at the end of a series page: what AniList's readers recommend, "
                   "with covers. Series you have open here; the others on AniList. Built in.")
    permissions = ["Connects to graphql.anilist.co for the series matched on AniList",
                   "Downloads covers from AniList's image server (s4.anilist.co) and keeps them on the server; "
                   "with Covers: Loaded from AniList, your browser loads them from there instead"]
    settings_fields = [
        SettingDef("covers", "Covers", default="cache",
                   options=[("cache", "Saved on this server"), ("hotlink", "Loaded from AniList")],
                   help="Saved: downloaded once and kept, so they show offline and your browser only talks "
                        "to MangaVibe. Loaded from AniList: no disk space, but your browser asks AniList "
                        "for every cover."),
        SettingDef("count", "How many", default="10",
                   options=[("5", "Up to 5"), ("10", "Up to 10"), ("15", "Up to 15"), ("25", "Up to 25")]),
        SettingDef("days", "Ask AniList again after", default="7",
                   options=[("1", "A day"), ("7", "A week"), ("30", "A month")],
                   help="Until then a series shows the recommendations it got last time."),
    ]
    global_actions = [Action("clear", "Forget saved recommendations and covers",
                             confirm="Forget every saved recommendation and cover? They are asked for again "
                                     "when a series page opens.")]
    router = api_router
    html = {"series-end": ((HERE / "panel.html").read_text(encoding="utf-8"), 50)}
    js = (HERE / "recs.js").read_text(encoding="utf-8")
    css = (HERE / "recs.css").read_text(encoding="utf-8")

    def status(self, ctx):
        series = ctx.db.query(PluginSeriesData).filter_by(plugin=self.id, key=core.KEY).count()
        covers, size = core.cache_size(ctx)
        if not series and not covers:
            return "Nothing saved yet"
        return f"Saved for {series} series · {covers} covers ({size / 1048576:.1f} MB)"

    def run_global_action(self, ctx, action_key):
        if action_key == "clear":
            ctx.db.query(PluginSeriesData).filter_by(plugin=self.id).delete(synchronize_session=False)
            shutil.rmtree(core.covers_dir(ctx), ignore_errors=True)
            return "Forgotten"
        return None
