"""The series' dialogs (built-in plugins, issue #208): Edit (`dialog:edit`) and Fix match
(`dialog:fix-match`).

Their markup is templates/views/edit.html and templates/views/fix-match.html; what they do is the app's,
the components editSeries() and fixMatch() (app.js), which a dialog of one's own uses the same way: the
markup is replaceable, the saving and matching aren't. See docs/PLUGINS.md, "The dialogs".
"""
from app.plugins import Plugin


class EditDialog(Plugin):
    id = "edit-dialog"
    name = "Edit dialog"
    category = ["appearance", "library"]
    version = "1.0"
    description = "The series' Edit dialog: titles, description, numbers, tags, links and the poster. Built in."
    permissions = ["Draws the Edit dialog; connects to nothing"]
    provides = {"dialog:edit": 1}


class FixMatchDialog(Plugin):
    id = "fix-match-dialog"
    name = "Fix match dialog"
    category = ["appearance", "metadata"]
    version = "1.0"
    description = "Picking the right metadata for a series: search the sources, or paste a link. Built in."
    permissions = ["Draws the Fix match dialog; the searches are the metadata sources'"]
    provides = {"dialog:fix-match": 1}
