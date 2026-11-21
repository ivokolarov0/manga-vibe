"""Continue reading page (built-in plugin, issue #207): the `view:continue` job.

The series in progress, the last read first, each with the book and page to go on with. Its markup is
templates/views/continue.html, drawn from MangaVibe.continue. A plugin that replaces this one
(provides = {"view:continue": 1}) draws the page its own way. See docs/PLUGINS.md, "The view:continue job".
"""
from app.plugins import Plugin


class ContinuePage(Plugin):
    id = "continue-page"
    name = "Continue reading page"
    category = ["appearance", "library"]
    version = "1.0"
    description = ("The series in progress, the last read first. Built in; replace it with a plugin that "
                   "draws the page its own way.")
    permissions = ["Draws the Continue reading page; connects to nothing"]
    provides = {"view:continue": 1}
