"""The sidebar (built-in plugin): another `view:nav`, in place of the top bar.

Library, Continue and Settings in a column on the left of the page; the logo, the search box and the topbar
slot (e.g. the account menu) stay in a bar at the top. On phones and narrow windows the column is the
usual Library / Continue bar, with Settings as its third entry (Settings → Display → Navigation bar still
puts it at the bottom or under the top bar). Its markup is templates/views/nav.html, its look sidebar.css.

Starts switched off: the top bar is the app's own navigation; pick this one in Settings → Plugins
(Navigation: pick one). See docs/PLUGINS.md, "The view:nav job".
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
    enabled_by_default = False  # the top bar (nav) is the default; this one is picked
    css = (HERE / "sidebar.css").read_text(encoding="utf-8")
