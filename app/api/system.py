"""Health, libraries, scanning, settings and plugins."""
import html
import os
import string
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from sqlalchemy import func

from app import config
from app.api.common import get_or_404
from app.db import get_db, get_setting, set_setting
from app.models import Library, Series
from app.plugins import Context, OpenUrl, action_result, implements, manager as plugins
from app.scanner import walker
from app.scanner.parser import natural_key

router = APIRouter(prefix="/api")

DEFAULT_SETTINGS = {
    "title_language": "english",   # english | romaji | native
    "theme": "dark",               # dark | light
    "card_size": "medium",         # small | medium | large
    "library_listing": "lazy",     # lazy (infinite scroll) | paged (numbered pages)
    "library_page_size": 60,       # series per page in paged mode
    "reader_mode": "rtl",          # rtl | ltr | vertical
    "reader_fit": "screen",        # width | height | screen | original
    "auto_fullscreen": True,
    "preload": 4,
    "reader_autohide": 5,          # seconds until the reader controls hide; 0 = never
    "reader_drawer_side": "right", # right | left: which side the reader options slide in from
    "reader_tap_layout": "default",  # default | one-handed | off: what tapping the page does
    "reader_tap_invert": False,    # swap the tap sides; swipe and page order keep the reading direction
    "auto_match": True,            # match new series automatically
    "match_threshold": 90,         # auto-accept score (0-100)
    "include_adult": False,        # include adult results from AniList
    "auto_update": True,           # check GitHub for a new version once a day
}


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/libraries")
def libraries(db=Depends(get_db)):
    counts = dict(db.query(Series.library_id, func.count(Series.id))
                  .filter(Series.missing_since.is_(None)).group_by(Series.library_id).all())
    return [{"id": l.id, "name": l.name, "root_path": l.root_path, "series": counts.get(l.id, 0),
             "available": Path(l.root_path).is_dir()} for l in db.query(Library)]


class LibraryBody(BaseModel):
    root_path: str
    name: str | None = None


@router.post("/libraries")
def add_library(body: LibraryBody, db=Depends(get_db)):
    root = Path(body.root_path)
    if not root.is_absolute() or not root.is_dir():
        raise HTTPException(400, f"Folder {body.root_path} does not exist inside the container. "
                                 "Mount it as a volume first.")
    resolved = root.resolve()
    data = config.DATA_DIR.resolve()
    if resolved == data or data in resolved.parents:
        raise HTTPException(400, "That is MangaVibe's own data folder, not a manga folder")
    if db.query(Library).filter_by(root_path=str(root)).one_or_none():
        raise HTTPException(400, "Library already exists")
    lib = Library(name=body.name or root.name, root_path=str(root))
    db.add(lib)
    removed = set(get_setting(db, "removed_libraries", []) or [])
    removed.discard(str(root))
    set_setting(db, "removed_libraries", sorted(removed))
    db.commit()
    walker.start_scan_in_background(lib.id)  # only the new folder; the others are up to date
    return {"id": lib.id}


@router.delete("/libraries/{lib_id}")
def delete_library(lib_id: int, db=Depends(get_db)):
    """Removes the library from the app's database only. Files are never touched.
    Remembered, so a folder from LIBRARY_PATHS isn't added back on the next start."""
    lib = get_or_404(db, Library, lib_id)
    removed = set(get_setting(db, "removed_libraries", []) or [])
    removed.add(lib.root_path)
    set_setting(db, "removed_libraries", sorted(removed))
    db.delete(lib)
    db.commit()
    return {"ok": True}


# folders that are part of the container system, never a manga library
SYSTEM_DIRS = {"bin", "boot", "dev", "etc", "lib", "lib32", "lib64", "libx32", "proc", "run", "sbin", "sys",
               "usr", "var", "tmp", "app", "root", "srv", "opt", "home", "snap", "lost+found"}


@router.get("/folders")
def folders(path: str = "", db=Depends(get_db)):
    """Folders inside the container, for the 'Add folder' picker. Only names of folders, nothing else."""
    libs = {l.root_path for l in db.query(Library)}
    data = config.DATA_DIR.resolve()
    if not path:
        if os.name == "nt":  # local development on Windows: list drives
            roots = [Path(f"{d}:/") for d in string.ascii_uppercase if Path(f"{d}:/").exists()]
            return {"path": "", "parent": None,
                    "dirs": [{"name": str(r), "path": str(r), "library": False} for r in roots]}
        path = "/"
    base = Path(path)
    if not base.is_absolute() or not base.is_dir():
        raise HTTPException(400, "Folder not found")
    out = []
    try:
        entries = sorted(os.scandir(base), key=lambda e: natural_key(e.name))
    except OSError as e:
        raise HTTPException(403, f"Can't open this folder: {e.strerror}")
    for e in entries:
        try:
            if not e.is_dir() or e.name.startswith((".", "@", "#")):
                continue
        except OSError:
            continue
        if str(base) == "/" and e.name in SYSTEM_DIRS:
            continue
        p = Path(e.path)
        if p.resolve() == data:
            continue
        out.append({"name": e.name, "path": str(p), "library": str(p) in libs})
    parent = None if base.parent == base else str(base.parent)
    return {"path": str(base), "parent": parent, "dirs": out}


@router.post("/scan")
def scan():
    return {"started": walker.start_scan_in_background(), **walker.status}


@router.post("/libraries/{lib_id}/scan")
def scan_one_library(lib_id: int, db=Depends(get_db)):
    """Scan only this library folder. Only one scan runs at a time: started is False if one is running."""
    get_or_404(db, Library, lib_id)
    return {"started": walker.start_scan_in_background(lib_id), **walker.status}


@router.get("/scan/status")
def scan_status():
    return walker.status


@router.get("/settings")
def get_settings(db=Depends(get_db)):
    return {k: get_setting(db, k, v) for k, v in DEFAULT_SETTINGS.items()}


@router.put("/settings")
def put_settings(body: dict, db=Depends(get_db)):
    for k, v in body.items():
        if k in DEFAULT_SETTINGS:
            set_setting(db, k, v)
    db.commit()
    return get_settings(db)


def _plugin_status(p, db):
    if p.id in plugins.disabled or not implements(p, "status"):
        return ""
    try:
        return str(p.status(Context(p, db)) or "")
    except Exception as e:  # a broken status line must not break the Settings page
        return f"Error: {type(e).__name__}: {e}"


def _plugin_dict(p, db):
    ctx = Context(p, db)
    saved = ctx.settings
    return {"id": p.id, "name": p.name, "version": p.version,
            "description": p.description, "enabled": p.id not in plugins.disabled,
            "provider": p.is_provider, "fields": [f.label for f in p.fields],
            "actions": [a.label for a in p.actions],
            "settings": [{"key": f.key, "label": f.label, "help": f.help, "secret": f.secret,
                          "options": [[str(v), str(label)] for v, label in f.options],
                          "value": "" if f.secret else str(ctx.setting(f.key) or ""),
                          "saved": bool(saved.get(f.key))} for f in p.settings_fields],
            "global_actions": [{"key": a.key, "label": a.label, "confirm": a.confirm}
                               for a in p.global_actions],
            "status": _plugin_status(p, db)}


@router.get("/plugins")
def list_plugins(db=Depends(get_db)):
    return {"plugins": [_plugin_dict(p, db) for p in plugins.plugins.values()],
            "errors": plugins.errors}


def _enabled_plugin(plugin_id):
    p = plugins.get(plugin_id)
    if p is None:
        raise HTTPException(404, "Plugin not found or switched off")
    return p


@router.put("/plugins/{plugin_id}/settings")
def save_plugin_settings(plugin_id: str, body: dict, db=Depends(get_db)):
    """Save the plugin's Settings boxes. Only the keys it declared; "" clears a value."""
    p = _enabled_plugin(plugin_id)
    ctx = Context(p, db)
    values = ctx.settings
    for f in p.settings_fields:
        if f.key in body:
            v = str(body[f.key] or "").strip()
            if v and f.options and v not in {str(o) for o, _ in f.options}:
                raise HTTPException(400, f"{f.label}: not one of the options")
            if v:
                values[f.key] = v
            else:
                values.pop(f.key, None)
    ctx.save_settings(values)
    db.commit()
    return _plugin_dict(p, db)


@router.post("/plugins/{plugin_id}/actions/{action_key}")
def run_global_action(plugin_id: str, action_key: str, db=Depends(get_db)):
    p = _enabled_plugin(plugin_id)
    if action_key not in {a.key for a in p.global_actions}:
        raise HTTPException(404, "Action not found")
    try:
        result = p.run_global_action(Context(p, db), action_key)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Plugin error: {type(e).__name__}: {e}")
    return action_result(result)


@router.get("/plugins/{plugin_id}/web/{path:path}")
def plugin_web(plugin_id: str, path: str, request: Request, db=Depends(get_db)):
    """A page a plugin answers itself, e.g. where a website sends you back after logging in."""
    p = _enabled_plugin(plugin_id)
    if not implements(p, "handle_web"):
        raise HTTPException(404, "This plugin has no web pages")
    try:
        result = p.handle_web(Context(p, db), path, dict(request.query_params))
        db.commit()
    except Exception as e:
        db.rollback()
        result = f"{p.name} failed: {type(e).__name__}: {e}"
    if isinstance(result, OpenUrl):
        return RedirectResponse(result.url, status_code=303)
    if result is None:
        raise HTTPException(404, "Not found")
    return HTMLResponse(_message_page(p.name, str(result)))


def _message_page(title, message):
    t, m = html.escape(title), html.escape(message)
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{t}</title><body style='font:16px system-ui;max-width:32rem;margin:3rem auto;"
            f"padding:0 1rem'><h1 style='font-size:1.3rem'>{t}</h1><p>{m}</p>"
            f"<p><a href='/#/settings/plugins'>Back to MangaVibe</a></p></body>")


class PluginToggle(BaseModel):
    enabled: bool


@router.put("/plugins/{plugin_id}")
def toggle_plugin(plugin_id: str, body: PluginToggle, db=Depends(get_db)):
    if plugin_id not in plugins.plugins:
        raise HTTPException(404, "Plugin not found")
    (plugins.disabled.discard if body.enabled else plugins.disabled.add)(plugin_id)
    set_setting(db, "plugins_disabled", sorted(plugins.disabled))
    db.commit()
    return {"ok": True}


@router.post("/plugins/reload")
def reload_plugins(db=Depends(get_db)):
    plugins.load()
    plugins.disabled = set(get_setting(db, "plugins_disabled", []) or [])
    return list_plugins(db)
