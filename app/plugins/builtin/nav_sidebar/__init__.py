"""The sidebar (built-in plugin): another `view:nav`, in place of the top bar.

Library, Continue and Settings in a column on the left of the page; the logo, the search box and the topbar
slot (e.g. the account menu) stay in a bar at the top. On phones and narrow windows the column is the
usual Library / Continue bar, with Settings as its third entry (Settings → Display → Navigation bar still
puts it at the bottom or under the top bar). Its markup is templates/views/nav.html, its look sidebar.css.

The app's navigation since the redesign (issue #303): on by default, and switched on once on the update
where the top bar was the built-in default the owner never changed (NEW_DEFAULTS in app/plugins). The top
bar is the alternative, picked in Settings → Plugins (Navigation: pick one). See docs/PLUGINS.md, "The
view:nav job".
"""
from pathlib import Path

from app.plugins import Plugin

HERE = Path(__file__).parent


class NavSidebar(Plugin):
    id = "nav-sidebar"
    name = "Sidebar"
    category = "appearance"
    version = "1.0"
    description = ("Library, Continue and Settings in a sidebar on the left, the search box at the top. "
                   "On phones, the usual bar. Built in.")
    permissions = ["Draws the sidebar and the top bar; connects to nothing"]
    provides = {"view:nav": 1}
    css = (HERE / "sidebar.css").read_text(encoding="utf-8")
