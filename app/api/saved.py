"""Saved searches: a name for a set of library filters and sort ("Isekai": genre=Isekai), picked from
the library page's Saved button. Only the query string is kept, the same one the page's URL has; it
applies to whichever library is shown. Each account has its own, in its saved_searches setting:
[{"id": 1, "name": "Isekai", "query": "genre=Isekai&sort=year"}]."""
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.access import Viewer, viewer
from app.db import get_db, get_user_setting, set_user_setting

router = APIRouter(prefix="/api/saved-searches")
KEY = "saved_searches"
MAX = 200
# what a saved search keeps: the library page's filters and sort, not the page number
PARAMS = {"q", "genre", "exclude", "pub", "read", "match", "format", "have", "fav", "letter",
          "year_min", "year_max", "score_min", "sort", "order"}


def _all(db, user_id):
    return list(get_user_setting(db, user_id, KEY, []) or [])


def _clean(query: str) -> str:
    return urlencode([(k, v) for k, v in parse_qsl(query.lstrip("?")) if k in PARAMS and v])


@router.get("")
def list_saved(db=Depends(get_db), v: Viewer = Depends(viewer)):
    return _all(db, v.id)


class SavedBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(max_length=4000)


@router.post("")
def add_saved(body: SavedBody, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """Save a search. The same name again replaces that one's filters."""
    name, query = body.name.strip(), _clean(body.query)
    if not name:
        raise HTTPException(400, "Give the search a name")
    if not query:
        raise HTTPException(400, "Pick some filters first")
    items = _all(db, v.id)
    same = next((x for x in items if x["name"].lower() == name.lower()), None)
    if same:
        same["query"] = query
    else:
        if len(items) >= MAX:
            raise HTTPException(400, f"At most {MAX} saved searches")
        same = {"id": max((x["id"] for x in items), default=0) + 1, "name": name, "query": query}
        items.append(same)
    set_user_setting(db, v.id, KEY, items)
    db.commit()
    return same


@router.delete("/{saved_id}")
def delete_saved(saved_id: int, db=Depends(get_db), v: Viewer = Depends(viewer)):
    items = _all(db, v.id)
    if not any(x["id"] == saved_id for x in items):
        raise HTTPException(404, "Saved search not found")
    set_user_setting(db, v.id, KEY, [x for x in items if x["id"] != saved_id])
    db.commit()
    return {"ok": True}
