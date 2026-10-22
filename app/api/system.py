"""Health, libraries, scanning, settings and plugins."""
import os
import string
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sqlalchemy import func

from app import config
from app.api.common import get_or_404
from app.db import get_db, get_setting, set_setting
from app.models import Library, Series
from app.plugins import manager as plugins
from app.scanner import walker
from app.scanner.parser import natural_key

router = APIRouter(prefix="/api")

DEFAULT_SETTINGS = {
    "title_language": "english",   # english | romaji | native
    "theme": "dark",               # dark | light
    "card_size": "medium",         # small | medium | large
    "reader_mode": "rtl",          # rtl | ltr | vertical
    "reader_fit": "screen",        # width | height | screen | original
    "auto_fullscreen": True,
    "preload": 4,
    "reader_autohide": 5,          # seconds until the reader controls hide; 0 = never
    "reader_drawer_side": "right", # right | left: which side the reader options slide in from
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


@router.get("/plugins")
def list_plugins():
    return {
        "plugins": [{"id": p.id, "name": p.name, "version": p.version,
                     "description": p.description, "enabled": p.id not in plugins.disabled,
                     "provider": p.is_provider, "fields": [f.label for f in p.fields],
                     "actions": [a.label for a in p.actions]}
                    for p in plugins.plugins.values()],
        "errors": plugins.errors,
    }


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
    return list_plugins()
