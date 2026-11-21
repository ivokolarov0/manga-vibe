"""The top bar (built-in plugin, issue #208): the `view:nav` job.

The logo, the search box, plugins' buttons (the topbar slot, e.g. the account menu), the Settings button
and the Library / Continue bar. Its markup is templates/views/nav.html. A top bar of one's own must keep
{{ settings_link() }}, the Settings button, or the built-in bar is drawn instead: Settings -> Plugins, where
a plugin is switched off, is always one tap away. See docs/PLUGINS.md, "The view:nav job".
"""
from app.plugins import Plugin


class Nav(Plugin):
    id = "nav"
    name = "Top bar"
    category = "appearance"
    version = "1.0"
    description = "The top bar with the search box and Settings, and the Library / Continue bar. Built in."
    permissions = ["Draws the top bar; connects to nothing"]
    provides = {"view:nav": 1}
