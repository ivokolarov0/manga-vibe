"""Settings' sections (built-in plugins, issue #208): Library folders, Display, Reader (with the Android
app's page), Metadata and Updates, each a `settings:<section>` job of a plugin of its own.

A section is a page in Settings' menu (settings_pages) and its markup (templates/views/settings-<section>.html),
which runs in Settings' component: `st` (the settings, as strings), `save(key)`, `rows` (SETTING_ROWS in
app.js) and the rest it uses. Its contract is the setting keys it changes: a replacement changes the same
ones (GET / PUT /api/settings), so the library and the reader keep reading them. Switched off, a section and
its menu entry are gone; the keys keep their values. Settings -> Plugins is the core's and can't be
replaced. See docs/PLUGINS.md, "Settings' sections".
"""
from app.plugins import Plugin
from app.plugins.sdk import N_

KEEP = "Draws a Settings page; connects to nothing"


class FoldersPage(Plugin):
    id = "settings-folders"
    name = "Settings: Library folders"
    category = ["appearance", "system"]
    version = "1.0"
    description = "Settings → Library folders: add and remove folders, scan, the path outside Docker. Built in."
    permissions = [KEEP]
    provides = {"settings:library": 1}
    settings_pages = [{"key": "library", "label": N_("Library folders"), "icon": "folder", "order": 10}]


class DisplayPage(Plugin):
    id = "settings-display"
    name = "Settings: Display"
    category = ["appearance", "system"]
    version = "1.0"
    description = "Settings → Display: theme, cards, page width, the library's listing and titles. Built in."
    permissions = [KEEP]
    provides = {"settings:display": 1}
    settings_pages = [{"key": "display", "label": N_("Display"), "icon": "palette", "order": 20, "personal": True}]


class ReaderPage(Plugin):
    id = "settings-reader"
    name = "Settings: Reader"
    category = ["appearance", "reader"]
    version = "1.0"
    description = "Settings → Reader: the reading mode, fit, taps, overlays, the page's look; and the Android app. Built in."
    permissions = [KEEP]
    provides = {"settings:reader": 1}
    settings_pages = [{"key": "app", "label": N_("Android app"), "icon": "phone", "order": 25, "personal": True,
                       "android": True},
                      {"key": "reader", "label": N_("Reader"), "icon": "book-open", "order": 30, "personal": True}]


class MetadataPage(Plugin):
    id = "settings-metadata"
    name = "Settings: Metadata"
    category = ["appearance", "metadata"]
    version = "1.0"
    description = "Settings → Metadata: the sources, automatic matching and its threshold. Built in."
    permissions = [KEEP]
    provides = {"settings:metadata": 1}
    settings_pages = [{"key": "metadata", "label": N_("Metadata"), "icon": "tag", "order": 40}]


class UpdatesPage(Plugin):
    id = "settings-updates"
    name = "Settings: Updates"
    category = ["appearance", "system"]
    version = "1.0"
    description = "Settings → Updates: the running version, checking, installing and going back. Built in."
    permissions = [KEEP]
    provides = {"settings:updates": 1}
    settings_pages = [{"key": "updates", "label": N_("Updates"), "icon": "download", "order": 60}]
