"""Series cards (built-in plugin, issue #205): the `view:card` job.

How a series looks in the library grid, the library's list view and Continue reading: the poster with
its badges, the title and a line under it. The markup is templates/views/card.html, a Jinja template of
Alpine markup. A plugin that replaces this one (provides = {"view:card": 1}) gives its own
templates/views/card.html, and can start from this one: {% extends "@cards/views/card.html" %}, then
change a block (poster, badges, text, details). See docs/PLUGINS.md, "Replacing a view", and
examples/plugins/compact_cards/.
"""
from app.plugins import Plugin


class Cards(Plugin):
    id = "cards"
    name = "Series cards"
    category = ["appearance", "library"]
    version = "1.0"
    description = ("How a series looks in the library and Continue reading: poster, badges, title and "
                   "progress. Built in; replace it with a plugin that draws the cards its own way.")
    permissions = ["Draws the series cards in the library and Continue reading; connects to nothing"]
    provides = {"view:card": 1}
