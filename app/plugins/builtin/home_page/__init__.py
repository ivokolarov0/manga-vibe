"""Home page (built-in plugin, issue #291): the `view:home` job.

The first page: rows of what's new (Continue reading, Recently added, Recently updated, and what plugins add,
e.g. the libraries), each a carousel of the newest 10 with "See all". Its markup is templates/views/home.html,
drawn from MangaVibe.home (GET /api/home); home_page.js gives the carousels their arrow buttons. A plugin
that replaces this one (provides = {"view:home": 1}) draws the page its own way. The rows themselves are
the app's and the plugins' (app/api/home.py, Plugin.home_sections). See docs/PLUGINS.md, "The view:home job".
"""
from pathlib import Path

from app.plugins import Plugin

HERE = Path(__file__).parent


class HomePage(Plugin):
    id = "home-page"
    name = "Home page"
    category = ["appearance", "library"]
    version = "1.0"
    description = ("The first page: rows of what's new, each a carousel of series. Built in; replace it "
                   "with a plugin that draws Home its own way.")
    permissions = ["Draws the Home page; connects to nothing"]
    provides = {"view:home": 1}
    js = (HERE / "home_page.js").read_text(encoding="utf-8")
