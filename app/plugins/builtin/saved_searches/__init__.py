"""Saved searches (built-in plugin): a name for the library page's filters and sort ("Isekai":
genre=Isekai), picked from the Saved button next to Filter / Sort / Letter. Only the query string is
kept, the same one the page's URL has; it applies to whichever library is shown.

Kept in the saved_searches setting, as before it was a plugin:
[{"id": 1, "name": "Isekai", "query": "genre=Isekai&sort=year"}]. With accounts, each person has their
own list (user:<id>:saved_searches, app/users.py).

The UI is saved_searches.js, the markup in *.html (one file per slot of the page) and the styles in
saved_searches.css. Switch it off in Settings -> Plugins, or replace it with a plugin of your own.
"""
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import users
from app.db import get_setting, set_setting
from app.plugins import Context, Plugin
from app.plugins.web import plugin_ctx

HERE = Path(__file__).parent
KEY = "saved_searches"
MAX = 200
# what a saved search keeps: the library page's filters and sort, not the page number
PARAMS = {"q", "genre", "exclude", "pub", "read", "match", "format", "have", "fav", "letter",
          "year_min", "year_max", "score_min", "sort", "order"}

router = APIRouter(prefix="/searches")


def _key():
    """Each user's own list (app/users.py); the owner's is the saved_searches setting, as before."""
    return users.personal_key(KEY)


def _all(db):
    return list(get_setting(db, _key(), []) or [])


def _clean(query: str) -> str:
    return urlencode([(k, v) for k, v in parse_qsl(query.lstrip("?")) if k in PARAMS and v])


@router.get("")
def list_saved(ctx: Context = Depends(plugin_ctx)):
    return _all(ctx.db)


class SavedBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(max_length=4000)


@router.post("")
def add_saved(body: SavedBody, ctx: Context = Depends(plugin_ctx)):
    """Save a search. The same name again replaces that one's filters."""
    name, query = body.name.strip(), _clean(body.query)
    if not name:
        raise HTTPException(400, "Give the search a name")
    if not query:
        raise HTTPException(400, "Pick some filters first")
    items = _all(ctx.db)
    same = next((x for x in items if x["name"].lower() == name.lower()), None)
    if same:
        same["query"] = query
    else:
        if len(items) >= MAX:
            raise HTTPException(400, f"At most {MAX} saved searches")
        same = {"id": max((x["id"] for x in items), default=0) + 1, "name": name, "query": query}
        items.append(same)
    set_setting(ctx.db, _key(), items)
    ctx.db.commit()
    return same


@router.delete("/{saved_id}")
def delete_saved(saved_id: int, ctx: Context = Depends(plugin_ctx)):
    items = _all(ctx.db)
    if not any(x["id"] == saved_id for x in items):
        raise HTTPException(404, "Saved search not found")
    set_setting(ctx.db, _key(), [x for x in items if x["id"] != saved_id])
    ctx.db.commit()
    return {"ok": True}


def _read(name):
    return (HERE / name).read_text(encoding="utf-8")


class SavedSearches(Plugin):
    id = "saved-searches"
    name = "Saved searches"
    version = "1.0"
    description = ("Keep the library page's filters and sort under a name (e.g. Isekai) and pick them "
                   "again from the Saved button. Built in.")
    permissions = ["Adds the Saved and Save search buttons to the library page",
                   "Keeps its searches in MangaVibe's database; connects to nothing"]
    router = router
    js = _read("saved_searches.js")
    css = _read("saved_searches.css")
    html = {"library-tools": _read("tool.html"),
            "library-filters": _read("save_button.html"),
            "sheets": _read("sheet.html")}
