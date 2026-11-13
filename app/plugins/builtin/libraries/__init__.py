"""Libraries (built-in plugin): Manga, Comics... Each shows its own series, picked with the tabs on the
home screen; a folder's series show in its library, one series can be moved to another. Optional
password per library. Switch it off in Settings -> Plugins (not while a library has a password) to have
one list of everything, or replace it with a plugin of your own.

- core.py: libraries ("shelves" in the code), passwords, the unlock cookie and LockGuard
- api.py: its API at /api/plugins/libraries/api/...
- libraries.js, *.html, libraries.css: the tabs, the password dialog, Settings -> Libraries, the
  library pickers (series Edit, multi-select, Settings -> Library folders)

The data stays in the core's tables (models.Shelf, Series.shelf_id, Library.shelf_id), unused while the
plugin is off.
"""
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func

from app import config
from app.models import Library, Series, Shelf
from app.plugins import Plugin

from . import api, core

HERE = Path(__file__).parent
_cleared = False  # CLEAR_LIBRARY_PASSWORD is applied once per start, not on every Reload plugins


def _read(name):
    return (HERE / name).read_text(encoding="utf-8")


def _shelf_param(request):
    value = request.query_params.get("shelf", "")
    return int(value) if value.isdigit() and int(value) > 0 else None


class Libraries(Plugin):
    id = "libraries"
    name = "Libraries"
    category = ["library", "system"]  # listed under Library; tagged System (passwords, who sees what)
    version = "1.0"
    description = ("Separate libraries (Manga, Comics...) with tabs on the home screen, a reading mode "
                   "and an optional password each. Built in.")
    permissions = ["Adds the library tabs, Settings → Libraries and the library pickers",
                   "Can hide a library's series behind a password; connects to nothing"]
    router = api.router_all
    middleware = core.LockGuard  # a locked library's series, books, pages and images are refused
    js = _read("libraries.js")
    css = _read("libraries.css")
    html = {"home-top": _read("tabs.html"),
            "select-menu": _read("select_menu.html"),
            "library-end": _read("move_dialog.html"),
            "series-edit": _read("edit_field.html"),
            "settings": _read("settings.html"),
            "folder-row": _read("folder_row.html"),
            "attention-note": _read("attention_note.html"),
            "account-row": _read("account_row.html"),  # with the accounts plugin
            "sheets": _read("unlock_dialog.html")}

    # --- what a request may see -------------------------------------------------------------------
    def series_filter(self, ctx, request):
        """Never a locked library's series; with shelf=<id>, only that library's (the home tabs).
        A locked one asked for is refused."""
        locked = core.locked_for(ctx.db, request)
        shelf = _shelf_param(request)
        if shelf in locked:
            raise HTTPException(403, "This library is locked")
        conds = [core.visible(locked)] if locked else []
        if shelf:
            conds.append(core.shelf_column() == shelf)
        return conds

    def series_info(self, ctx, series):
        """The library it shows in: its own (moved by hand) or its folder's."""
        db = ctx.db
        folder = db.get(Library, series.library_id)

        def shelf_of(shelf_id):
            x = db.get(Shelf, shelf_id) if shelf_id is not None else None
            return {"id": x.id, "name": x.name} if x else None

        return {"shelf": shelf_of(core.shelf_id_of(db, series)), "shelf_override": series.shelf_id is not None,
                "folder_shelf": shelf_of(folder.shelf_id if folder else None)}

    def reading_mode(self, ctx, series):
        """Its library's reading mode, for series without their own."""
        shelf_id = core.shelf_id_of(ctx.db, series)
        shelf = ctx.db.get(Shelf, shelf_id) if shelf_id is not None else None
        if shelf is not None and shelf.reader_mode:
            return shelf.reader_mode, f"the mode of {shelf.name}"
        return None

    # --- folders and switching on/off ----------------------------------------------------------------
    def on_start(self, ctx):
        """Every folder gets a library (also the ones added while the plugin was off)."""
        global _cleared
        if not _cleared:
            _cleared = True
            core.clear_from_env(ctx.db, config.CLEAR_LIBRARY_PASSWORD)
        core.ensure_folder_shelves(ctx.db)

    def on_folders_changed(self, ctx):
        core.ensure_folder_shelves(ctx.db)

    def on_folder_removed(self, ctx, folder):
        """The library the app made for this folder goes too, if the owner never set it up and it's
        empty now."""
        db = ctx.db
        shelf = db.get(Shelf, folder.shelf_id) if folder.shelf_id else None
        if (shelf and shelf.made_for_folder == folder.id and not shelf.password_hash
                and db.query(Shelf).count() > 1
                and not db.query(Library).filter(Library.shelf_id == shelf.id).first()
                and not db.query(Series).filter(Series.shelf_id == shelf.id).first()):
            db.delete(shelf)
        core.invalidate()

    def can_switch_off(self, ctx):
        """Not while a library has a password: with libraries off, its series would show to everyone."""
        names = [n for (n,) in ctx.db.query(Shelf.name).filter(Shelf.password_hash.is_not(None))
                 .order_by(func.lower(Shelf.name))]
        if names:
            return (f"Remove the password of {', '.join(names)} first (Settings → Libraries): with "
                    "Libraries off, every series shows to everyone.")
        return None
