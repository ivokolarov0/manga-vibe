"""Saved searches (built-in plugin): a name for the library page's filters and sort ("Isekai":
genre=Isekai), picked from the Saved button next to Filter / Sort / Letter. Only the query string is
kept, the same one the page's URL has; it applies to whichever library is shown.

The list is the saved-searches job's, not this plugin's (Plugin API 22, #209), so a replacement finds it:
the jobs:saved-searches setting, [{"id": 1, "name": "Isekai", "query": "genre=Isekai&sort=year"}]. With
accounts, each person has their own list (user:<id>:jobs:saved-searches, app/users.py).

The UI is saved_searches.js, the markup in *.html (one file per slot of the page) and the styles in
saved_searches.css. Switch it off in Settings -> Plugins, or replace it with a plugin of your own.
"""
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import users
from app.plugins import Context, McpTool, Plugin
from app.plugins.query import search_params
from app.plugins.sdk import get_setting, set_setting, t
from app.plugins.web import plugin_ctx

HERE = Path(__file__).parent
KEY = "jobs:saved-searches"  # the job's data, its format in app/contracts (the saved-searches job)
MAX = 200
# never kept: where the list was, not what it shows
NOT_KEPT = {"page", "cursor", "limit"}

router = APIRouter(prefix="/searches")


def _key():
    """Each user's own list (app/users.py); the owner's is jobs:saved-searches itself."""
    return users.personal_key(KEY)


def _all(db):
    return list(get_setting(db, _key(), []) or [])


def _clean(db, query: str) -> str:
    """What a saved search keeps: the library's filters and sort, the parameters the library-query
    plugin names (#204), so a replacement's own filters are kept too. Not the page."""
    keep = search_params(db)
    return urlencode([(k, v) for k, v in parse_qsl(query.lstrip("?"))
                      if v and (k in keep if keep is not None else k not in NOT_KEPT)])


@router.get("")
def list_saved(ctx: Context = Depends(plugin_ctx)):
    return _all(ctx.db)


class SavedBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(max_length=4000)


@router.post("")
def add_saved(body: SavedBody, ctx: Context = Depends(plugin_ctx)):
    """Save a search. The same name again replaces that one's filters."""
    name, query = body.name.strip(), _clean(ctx.db, body.query)
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
            raise HTTPException(400, t("At most {n} saved searches", n=MAX))
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
    category = "library"
    version = "1.0"
    description = ("Keep the library page's filters and sort under a name (e.g. Isekai) and pick them "
                   "again from the Saved button. Built in.")
    permissions = ["Adds the Saved and Save search buttons to the library page",
                   "Keeps its searches in MangaVibe's database; connects to nothing"]
    router = router
    # it keeps the library's filters, which are that job's (#204): without a plugin doing it, it doesn't run
    requires = {"library-query": 1}
    # the job whose data it keeps (#209): a replacement doing it reads and writes the same list
    provides = {"saved-searches": 1}
    # for assistants (the Remote control plugin); "show" is a screen command in saved_searches.js
    mcp_tools = [McpTool("list", "Your saved searches: each one's name, its filters as words, and its query")]

    def run_mcp_tool(self, ctx, name, args):
        if name == "list":
            return {"saved_searches": [{"name": x["name"], "query": x["query"]} for x in _all(ctx.db)]}
        raise ValueError(f"No tool {name}")

    js = _read("saved_searches.js")
    css = _read("saved_searches.css")
    html = {"library-tools": _read("tool.html"),
            "library-filters": _read("save_button.html"),
            "sheets": _read("sheet.html")}
