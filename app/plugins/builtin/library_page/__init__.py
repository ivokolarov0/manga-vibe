"""Library page (built-in plugin, issue #207): the `view:library` job.

The library: Filter / Sort / Letter and the quick buttons, the filters that are on, the page size and
the grid/list switch, the multi-select bar, the cards and the pages. Its markup is
templates/views/library.html; the data and every action are the app's, MangaVibe.library, and
libraryPage() (library_page.js) gives their names to the markup. A plugin that replaces this one
(provides = {"view:library": 1}) draws the page its own way, or extends this one:
{% extends "@library-page/views/library.html" %}. See docs/PLUGINS.md, "The view:library job".
"""
from pathlib import Path

from app.plugins import Plugin

HERE = Path(__file__).parent


class LibraryPage(Plugin):
    id = "library-page"
    name = "Library page"
    category = ["appearance", "library"]
    version = "1.0"
    description = ("The library: its buttons and filters, the cards, pages and multi-select. Built in; "
                   "replace it with a plugin that draws the library its own way.")
    permissions = ["Draws the library page; connects to nothing"]
    provides = {"view:library": 1}
    js = (HERE / "library_page.js").read_text(encoding="utf-8")
