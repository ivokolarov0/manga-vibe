"""Compact cards: series cards with just the poster and the title. An example of replacing a view
(Plugin API 17, docs/PLUGINS.md "Replacing a view").

It's a folder plugin: copy the whole compact_cards folder into your plugins folder. It does the view:card
job, so switching it on switches off the built-in "Series cards". Its card is templates/views/card.html,
which starts from the built-in one ({% extends "@cards/views/card.html" %}) and changes two blocks: no
line under the title, and no details in the list view. The badges, the reading bar and other plugins'
badges (the card-badges slot) stay as they are.
"""
from app.plugins import Plugin


class CompactCards(Plugin):
    id = "compact-cards"
    name = "Compact cards"
    category = "appearance"
    version = "1.0"
    description = "Series cards with just the poster and the title."
    permissions = ["Draws the series cards in the library and Continue reading; connects to nothing"]
    provides = {"view:card": 1}
    css = ".compact-title .card-title, .card-title.compact-title { font-size: 11px; }"  # the built-in: 13px
