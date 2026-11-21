"""Series page (built-in plugin, issue #206): the `view:series` job.

The page a series opens on: poster, titles, Continue, the reading status, the details, links, tags, the
books and files. Its markup is templates/views/series.html and its component series_page.js; the data
and every action (marking read, deleting, the reading mode...) are the app's, MangaVibe.series. A
plugin that replaces this one (provides = {"view:series": 1}) draws the page its own way from the same
MangaVibe.series, or extends this one: {% extends "@series-page/views/series.html" %}. See
docs/PLUGINS.md, "The view:series job".
"""
from pathlib import Path

from app.plugins import Plugin

HERE = Path(__file__).parent


class SeriesPage(Plugin):
    id = "series-page"
    name = "Series page"
    category = ["appearance", "library"]
    version = "1.0"
    description = ("The page a series opens on: poster, details, tags, books and files, with its buttons. "
                   "Built in; replace it with a plugin that draws the page its own way.")
    permissions = ["Draws the series page; connects to nothing"]
    provides = {"view:series": 1}
    js = (HERE / "series_page.js").read_text(encoding="utf-8")
