"""The MCP server's tools.

Two kinds (see DECISIONS.md, "Remote control: an MCP server"):
- **Data** tools answer from the server. They call the app's own API, as the person whose key it is,
  through the whole app (its plugins' guards too), so they see and may do exactly what that person can
  in the app: a locked library stays locked, owner-only settings stay owner-only.
- **Screen** tools act on an open app the person is looking at (screens.py): they navigate, turn pages,
  press buttons and say what is on the screen. They work in any part of the UI, because screen_describe
  lists the controls the page shows and screen_press presses one, whatever it is.

Plugins add tools of both kinds: `mcp_tools` + run_mcp_tool() on the server, and
MangaVibe.screenCommand() in their script (Plugin API 13). Their names are `<plugin id>__<name>`.
"""
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app import users
from app.api.common import READING_STATUSES
from app.api.system import DEFAULT_SETTINGS, PERSONAL_SETTINGS
from app.plugins import Context, manager as plugins

from .screens import NoScreen, ScreenTimeout, hub


class ToolError(Exception):
    """What went wrong, for the assistant (the call's result is an error, not a crash)."""


@dataclass
class Tool:
    name: str
    description: str
    params: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    run: object = None          # async fn(args) -> result
    screen: bool = False        # works on an open app: gets a `screen` parameter
    admin: bool = False         # only for people who manage the app
    plugin: str = ""            # a plugin's tool: its id
    read_only: bool = False     # changes nothing (a hint for the assistant)

    def schema(self):
        props = dict(self.params)
        if self.screen:
            props["screen"] = {"type": "string", "description": SCREEN_PARAM}
        out = {"name": self.name, "description": self.description,
               "inputSchema": {"type": "object", "properties": props,
                               **({"required": list(self.required)} if self.required else {})}}
        if self.read_only:
            out["annotations"] = {"readOnlyHint": True}
        return out


SCREEN_PARAM = ("Which open app to use: its screen id or part of its name (screens_list). Leave it out "
                "for the one used last.")
BUILTIN: dict[str, Tool] = {}


def tool(name, description, params=None, required=(), screen=False, read_only=False):
    def add(fn):
        BUILTIN[name] = Tool(name, description, params or {}, list(required), fn, screen, read_only=read_only)
        return fn
    return add


# ---------------------------------------------------------------- calling the app's own API

async def call_api(method, path, params=None, body=None, raw=False):
    """A request to the app as the current user, through every middleware (the plugins' guards
    too). Returns the JSON answer; raises ToolError with the app's message on an error."""
    from app.main import app
    user = users.current()
    query = urlencode([(k, v) for k, v in (params.items() if isinstance(params, dict) else params or [])
                       for v in (v if isinstance(v, (list, tuple)) else [v]) if v is not None])
    data = b"" if body is None else json.dumps(body).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(),
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "client": ("127.0.0.1", 0), "server": ("mangavibe", 80),
             "headers": [(b"host", b"mangavibe"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(data)).encode())],
             users.SCOPE_KEY: user, "mangavibe.internal": True}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}
        return {"type": "http.disconnect"}

    out = {"status": 500, "headers": {}, "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    with users.acting_as(user):
        await app(scope, receive, send)
    kind = out["headers"].get("content-type", "")
    if out["status"] >= 400:
        detail = out["body"].decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail)
        elif isinstance(detail, list):  # FastAPI's validation errors
            detail = "; ".join(f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}" for d in detail)
        raise ToolError(f"{out['status']}: {detail}")
    if raw:
        return out
    if "json" not in kind:
        return {"content_type": kind, "bytes": len(out["body"])}
    return json.loads(out["body"] or b"null")


def me():
    u = users.current()
    return {"id": u.id, "name": u.name, "admin": u.can_manage}


# ---------------------------------------------------------------- library: filters and search

# the library's filters (as FILTER_GROUPS in app.js; a test checks they match)
FILTERS = {
    "read": ("Reading status", {"unread": "Unread", "reading": "Reading", "completed": "Completed",
                                "on_hold": "On hold", "dropped": "Dropped", "plan_to_read": "Plan to read"}),
    "pub": ("Publication", {"RELEASING": "Releasing", "FINISHED": "Finished", "HIATUS": "Hiatus",
                            "CANCELLED": "Cancelled", "NOT_YET_RELEASED": "Not yet released",
                            "UNKNOWN": "Unknown status"}),
    "match": ("Metadata", {"matched": "Has metadata", "attention": "Needs attention", "unmatched": "No metadata"}),
    "format": ("Format", {"manga": "Manga", "manhwa": "Manhwa", "manhua": "Manhua", "one_shot": "One-shot",
                          "novel": "Novel"}),
    "have": ("Collection", {"complete": "Complete collection", "incomplete": "Incomplete collection",
                            "unknown": "Can't tell if complete"}),
}
SORTS = {"title": "Title", "added": "Recently added", "updated": "Recently updated", "read": "Recently read",
         "score": "Score", "year": "Year", "unread": "Unread count"}
NUMBERS = ("year_min", "year_max", "score_min", "chapters_min", "chapters_max", "volumes_min", "volumes_max")


def _list(desc, values=None):
    items = {"type": "string", **({"enum": list(values)} if values else {})}
    return {"type": "array", "items": items, "description": desc}


FILTER_PARAMS = {
    "q": {"type": "string", "description": "Words in a title, an alternative title or an author"},
    "genre": _list("Genres or tags a series must have, all of them (names from library_filters)"),
    "exclude": _list("Genres or tags a series must not have"),
    **{k: _list(f"{label}: any of these", values) for k, (label, values) in FILTERS.items()},
    "favorites": {"type": "boolean", "description": "Only favorites"},
    "letter": {"type": "string", "description": "Titles starting with this letter (A-Z), or # for the rest"},
    "year_min": {"type": "integer"}, "year_max": {"type": "integer"},
    "score_min": {"type": "integer", "description": "At least this score (0-100)"},
    "chapters_min": {"type": "integer", "description": "Published chapters at least (from the metadata)"},
    "chapters_max": {"type": "integer"}, "volumes_min": {"type": "integer"}, "volumes_max": {"type": "integer"},
    "length_unknown": {"type": "boolean", "description": "With a chapters/volumes range: also series whose length isn't known"},
    "sort": {"type": "string", "enum": list(SORTS)},
    "order": {"type": "string", "enum": ["asc", "desc"], "description": "Default: A-Z for title, newest/most first for the rest"},
    "more": {"type": "object", "description": "More query parameters, as plugins describe them (e.g. the libraries plugin's shelf)"},
}


def filter_query(args) -> list[tuple[str, str]]:
    """The tool's filter arguments as the library page's query (the same one its address has)."""
    out = []
    if args.get("q"):
        out.append(("q", str(args["q"])))
    for k in ("genre", "exclude"):
        out += [(k, str(v)) for v in _as_list(args.get(k))]
    for k, (_, values) in FILTERS.items():
        picked = [str(v) for v in _as_list(args.get(k))]
        bad = [v for v in picked if v not in values]
        if bad:
            raise ToolError(f"Unknown {k} value {bad[0]!r}: one of {', '.join(values)}")
        if picked:
            out.append((k, ",".join(picked)))
    if args.get("favorites"):
        out.append(("fav", "1"))
    if args.get("letter"):
        out.append(("letter", str(args["letter"])[:1].upper()))
    for k in NUMBERS:
        if args.get(k) is not None:
            out.append((k, str(int(args[k]))))
    if args.get("length_unknown"):
        out.append(("length_unknown", "1"))
    if args.get("sort"):
        if args["sort"] not in SORTS:
            raise ToolError(f"Unknown sort {args['sort']!r}: one of {', '.join(SORTS)}")
        out.append(("sort", args["sort"]))
    if args.get("order") in ("asc", "desc"):
        out.append(("order", args["order"]))
    for k, v in (args.get("more") or {}).items():
        out += [(str(k), str(x)) for x in _as_list(v)]
    return out


def _as_list(v):
    if v is None or v == "":
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def card(s):
    return {"series_id": s["id"], "title": s["title"], "reading_status": s.get("reading_status"),
            "books": s.get("book_count"), "unread": s.get("unread_count"), "favorite": s.get("favorite"),
            "publication": s.get("publication_status")}


@tool("library_filters", "The library's filters and what each can be set to: reading status, publication, "
      "metadata, format, collection, the genres and tags in use (with how many series have them), sorts, "
      "letters and length. Use the values with library_search or screen_library.", read_only=True)
async def library_filters(args):
    tags = await call_api("GET", "/api/tags")
    return {"filters": {k: {"label": label, "values": values} for k, (label, values) in FILTERS.items()},
            "genres_and_tags": [{"name": t["name"], "kind": t["kind"], "series": t["count"]} for t in tags],
            "sorts": SORTS, "letters": "A-Z or #",
            "numbers": {"year_min/year_max": "start year", "score_min": "score 0-100",
                        "chapters_min/chapters_max, volumes_min/volumes_max": "published length (metadata)"},
            "favorites": "true: only favorites", "q": "words in titles and authors",
            "plugins": "Plugins may add their own tools for more (e.g. libraries, saved searches)."}


@tool("library_search", "Search and filter the library; gives the matching series (id, title, reading status, "
      "unread books). Without filters, everything. To show the result on a screen, use screen_library.",
      {**FILTER_PARAMS, "offset": {"type": "integer", "description": "Skip this many (paging)"},
       "limit": {"type": "integer", "description": "How many, at most 100 (default 30)"}}, read_only=True)
async def library_search(args):
    q = filter_query(args) + [("cursor", int(args.get("offset") or 0)),
                              ("limit", max(1, min(int(args.get("limit") or 30), 100)))]
    r = await call_api("GET", "/api/series", q)
    return {"total": r["total"], "next_offset": r["next_cursor"], "series": [card(s) for s in r["items"]]}


@tool("continue_reading", "What the person is in the middle of, the last read first: the series and the book "
      "and page to go on with.", read_only=True)
async def continue_reading(args):
    r = await call_api("GET", "/api/continue-reading")
    return [{**card(s), "continue": {"book_id": s["continue"]["book_id"], "label": s["continue"]["label"],
                                     "page": s["continue"]["page"] + 1, "pages": s["continue"]["page_count"]}}
            for s in r]


# ---------------------------------------------------------------- one series

@tool("series_get", "A series: its details (titles, description, genres, authors, status) and its books in "
      "reading order, each with how far it is read; `continue` is the book to go on with.",
      {"series_id": {"type": "integer"},
       "description": {"type": "boolean", "description": "Include the description (default true)"}},
      ["series_id"], read_only=True)
async def series_get(args):
    sid = int(args["series_id"])
    s = await call_api("GET", f"/api/series/{sid}")
    books = await call_api("GET", f"/api/series/{sid}/books")
    out = {**card(s), "titles": s["titles"], "authors": s["authors"], "year": s["start_year"],
           "score": s["average_score"], "format": s["format"], "chapters_total": s["chapters_total"],
           "volumes_total": s["volumes_total"], "genres_and_tags": [t["name"] for t in s["tags"] if not t["spoiler"]],
           "reading_mode": s["direction"], "manual_reading_status": s["manual_reading_status"],
           "continue": s["continue"] and {**s["continue"], "page": s["continue"]["page"] + 1},
           "plugin_fields": {f["label"]: f["value"] for f in s["plugin_fields"]},
           "books": [{"book_id": b["id"], "label": b["label"], "pages": b["page_count"],
                      "read": b["completed"], "page": b["page"] + 1 if b["page"] and not b["completed"] else None,
                      **({"error": b["error"]} if b["error"] else {}),
                      **({"badges": [x["text"] for x in b["badges"]]} if b.get("badges") else {})} for b in books]}
    if args.get("description", True):
        out["description"] = s["description"]
    return out


@tool("series_update", "Change a series for the person: reading status (or back to worked out from progress), "
      "favorite, and the reading mode (paged or vertical; admins).",
      {"series_id": {"type": "integer"},
       "reading_status": {"type": "string", "enum": [*READING_STATUSES, "auto"],
                          "description": "auto: worked out from what is read"},
       "favorite": {"type": "boolean"},
       "reading_mode": {"type": "string", "enum": ["paged", "vertical", "default"]}},
      ["series_id"])
async def series_update(args):
    body = {}
    if args.get("reading_status") == "auto":
        body["clear_reading_status"] = True
    elif args.get("reading_status"):
        body["reading_status"] = args["reading_status"]
    if args.get("favorite") is not None:
        body["favorite"] = bool(args["favorite"])
    if args.get("reading_mode"):
        body["reader_mode"] = args["reading_mode"]
    if not body:
        raise ToolError("Nothing to change")
    await call_api("PATCH", f"/api/series/{int(args['series_id'])}", body=body)
    hub.tell(users.current_id(), {"type": "refresh"})
    return "Done"


@tool("mark_read", "Mark books read or unread: one book, every book of a series, or a series' books before "
      "one (\"read up to here\").",
      {"book_id": {"type": "integer", "description": "One book"},
       "series_id": {"type": "integer", "description": "Every book of this series"},
       "before_book_id": {"type": "integer", "description": "With series_id: only the books before this one"},
       "read": {"type": "boolean", "description": "true: read (default), false: unread"}})
async def mark_read(args):
    read = args.get("read", True) is not False
    if args.get("book_id"):
        book = await call_api("GET", f"/api/books/{int(args['book_id'])}")
        body = {"page": max(book["page_count"] - 1, 0), "completed": True} if read else {"page": 0, "completed": False}
        await call_api("PUT", f"/api/books/{book['id']}/progress", body=body)
    elif args.get("series_id"):
        sid = int(args["series_id"])
        if read:
            params = {"before_book": int(args["before_book_id"])} if args.get("before_book_id") else None
            await call_api("POST", f"/api/series/{sid}/mark-read", params)
        else:
            await call_api("POST", f"/api/series/{sid}/mark-unread")
    else:
        raise ToolError("Give book_id or series_id")
    hub.tell(users.current_id(), {"type": "refresh"})
    return "Done"


# ---------------------------------------------------------------- settings

# what each setting can be (as Settings → Display and Reader in app.js); a test checks every one is here
SETTINGS = {
    "title_language": ("Title language", ["english", "romaji", "native"]),
    "theme": ("Theme", ["dark", "light", "auto"]),
    "card_size": ("Card size", ["small", "medium", "large"]),
    "grid_columns": ("Grid columns", ["auto", "4", "5", "6", "8", "10", "12"]),
    "page_width": ("Page width", ["contained", "full"]),
    "page_width_series": ("Series page full width as well", [True, False]),
    "card_width": ("Card width (px, with auto columns)", ["auto", "120", "140", "160", "180", "200", "240", "280", "320"]),
    "nav_position": ("Navigation bar", ["bottom", "top"]),
    "library_listing": ("Library listing", ["lazy", "paged"]),
    "library_page_size": ("Series per page", [40, 80, 120]),
    "library_view": ("Library view", ["grid", "list"]),
    "reader_mode": ("Default reading mode", ["paged", "vertical"]),
    "reader_fit": ("Fit", ["screen", "width", "height", "original"]),
    "auto_fullscreen": ("Fullscreen on first tap", [True, False]),
    "preload": ("Pages to preload", [2, 3, 4, 5, 8]),
    "reader_autohide": ("Hide the reader controls after (seconds, 0 never)", [3, 5, 8, 15, 0]),
    "reader_drawer_side": ("Reading hand", ["right", "left"]),
    "reader_tap_layout": ("Tap zones", ["default", "one-handed", "off"]),
    "reader_volume_keys": ("Android app: volume buttons turn pages", [True, False]),
    "overlay_opacity": ("Overlay box background (%)", [100, 75, 50, 25, 0]),
    "overlay_outline": ("Overlay text outline", ["off", "thin", "thick"]),
    "reader_brightness": ("Page brightness (%)", [100, 85, 70, 55, 40]),
    "reader_contrast": ("Page contrast (%)", [75, 100, 125, 150]),
    "reader_colors": ("Page colours", ["normal", "dark", "warm", "dark-warm"]),
    "auto_match": ("Match new series automatically", [True, False]),
    "match_threshold": ("Auto-accept match score (0-100)", None),
    "include_adult": ("Include adult results from AniList", [True, False]),
    "auto_update": ("Check for a new version once a day", [True, False]),
}


@tool("settings_get", "The app's settings: each one's value, what it can be, and whether the person may change "
      "it (Settings → Reader, the theme and the layout are everyone's own; the rest only admins change). "
      "Plugins' own settings are under api_request /api/plugins (admins).", read_only=True)
async def settings_get(args):
    s = await call_api("GET", "/api/settings")
    admin = users.current().can_manage
    return {"you": me(), "settings": {
        k: {"label": label, "value": s.get(k), **({"values": values} if values else {}),
            "yours": k in PERSONAL_SETTINGS, "may_change": admin or k in PERSONAL_SETTINGS}
        for k, (label, values) in SETTINGS.items()}}


@tool("settings_change", "Change settings, e.g. {\"reader_mode\": \"vertical\", \"theme\": \"light\"} (names and "
      "values from settings_get). Open apps show the change at once.",
      {"values": {"type": "object", "description": "setting name -> new value"}}, ["values"])
async def settings_change(args):
    values = args.get("values") or {}
    unknown = [k for k in values if k not in DEFAULT_SETTINGS]
    if unknown:
        raise ToolError(f"Unknown setting {unknown[0]!r} (see settings_get)")
    for k, v in values.items():
        allowed = SETTINGS[k][1]
        if allowed and str(v).lower() not in {str(a).lower() for a in allowed}:
            raise ToolError(f"{k} can be one of: {', '.join(map(str, allowed))}")
        if allowed and isinstance(allowed[0], bool):
            values[k] = str(v).lower() == "true"
        elif allowed and isinstance(allowed[0], int):
            values[k] = int(v)
    await call_api("PUT", "/api/settings", body=values)
    hub.tell(users.current_id(), {"type": "settings"})
    return "Changed: " + ", ".join(values)


# ---------------------------------------------------------------- everything else

API_PATH = re.compile(r"^/api/[A-Za-z0-9_\-./%~]*$")


@tool("api_request", "Call MangaVibe's own HTTP API for anything the other tools don't cover (e.g. plugins' "
      "APIs at /api/plugins/<id>/api/..., Needs attention at /api/attention, scanning at /api/scan). Runs "
      "as you, with your rights. See docs/developers.md and the routes in app/api/.",
      {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
       "path": {"type": "string", "description": "Starts with /api/"},
       "query": {"type": "object", "description": "Query parameters"},
       "body": {"description": "JSON body"}}, ["method", "path"])
async def api_request(args):
    path = str(args.get("path") or "")
    if not API_PATH.match(path) or ".." in path:
        raise ToolError("path must be an /api/... path")
    return await call_api(args.get("method") or "GET", path, args.get("query"), args.get("body"))


# ---------------------------------------------------------------- the screens

async def on_screen(args, command_type, **command_args):
    """Send one command to a screen; its answer, with which screen did it."""
    try:
        screen, answer = await hub.send(users.current_id(), {"type": command_type, "args": command_args},
                                        args.get("screen"))
    except (NoScreen, ScreenTimeout, RuntimeError) as e:  # RuntimeError: the page said it couldn't
        raise ToolError(str(e)) from None
    if isinstance(answer, dict):
        return {**answer, "screen": screen.name}
    return answer


@tool("screens_list", "Who you are, and the apps you have open (browser tabs, the Android app): which one screen "
      "tools use by default (the one used last), and what each shows.", read_only=True)
async def screens_list(args):
    live = hub.live(users.current_id())
    return {"you": me(), "screens": [s.public(chosen=(i == 0)) for i, s in enumerate(live)],
            **({} if live else {"note": "Nothing open. Open MangaVibe in a browser or the Android app, "
                                        "logged in as you, to use the screen tools."})}


@tool("screen_describe", "What is on the screen now, for someone who can't see it: the page, its heading, what is "
      "open over it (a sheet, a dialog, the reader's options), the reader's state, and the buttons, links, "
      "fields and switches shown, each with a ref for screen_press / screen_fill. Call it again after "
      "the screen changed: refs only last until the next describe.",
      {"all": {"type": "boolean", "description": "Every control, not only the first 150"}}, screen=True,
      read_only=True)
async def screen_describe(args):
    return await on_screen(args, "describe", all=bool(args.get("all")))


@tool("screen_press", "Press a button, link, tab, switch or checkbox on the screen, by its ref from "
      "screen_describe. Gives what the screen shows afterwards.",
      {"ref": {"type": "integer"}}, ["ref"], screen=True)
async def screen_press(args):
    return await on_screen(args, "press", ref=int(args["ref"]))


@tool("screen_fill", "Type into a field, pick an option of a dropdown (by its value or text), or set a switch "
      "(true/false) or slider, by its ref from screen_describe.",
      {"ref": {"type": "integer"}, "value": {"type": ["string", "number", "boolean"]},
       "submit": {"type": "boolean", "description": "Press Enter afterwards (e.g. to search)"}},
      ["ref", "value"], screen=True)
async def screen_fill(args):
    return await on_screen(args, "fill", ref=int(args["ref"]), value=args["value"], submit=bool(args.get("submit")))


@tool("screen_key", "Press a key on the screen, e.g. Escape (closes what is open), ArrowRight / ArrowLeft (turn "
      "the page), Home, End, PageDown, f (fullscreen).",
      {"key": {"type": "string"}}, ["key"], screen=True)
async def screen_key(args):
    return await on_screen(args, "key", key=str(args["key"]))


@tool("screen_scroll", "Scroll the page (e.g. to load more of the library, or see more of a series page).",
      {"direction": {"type": "string", "enum": ["down", "up", "top", "bottom"]}}, ["direction"], screen=True)
async def screen_scroll(args):
    return await on_screen(args, "scroll", direction=args["direction"])


VIEWS = ["library", "continue", "series", "read", "settings", "back"]


@tool("screen_navigate", "Go to a page of the app: library, continue (Continue reading), series (series_id), read "
      "(book_id, or series_id to continue that series; page from 1), settings (section: e.g. reader, display, "
      "library, plugins, updates, or a plugin's own page), or back.",
      {"to": {"type": "string", "enum": VIEWS}, "series_id": {"type": "integer"}, "book_id": {"type": "integer"},
       "page": {"type": "integer", "description": "read: the page, from 1"},
       "section": {"type": "string", "description": "settings: which page"}}, ["to"], screen=True)
async def screen_navigate(args):
    to = args.get("to")
    if to == "back":
        return await on_screen(args, "back")
    if to == "library":
        address = "#/"
    elif to == "continue":
        address = "#/continue"
    elif to == "settings":
        section = re.sub(r"[^A-Za-z0-9_\-]", "", str(args.get("section") or ""))
        address = "#/settings" + (f"/{section}" if section else "")
    elif to == "series":
        if not args.get("series_id"):
            raise ToolError("Give series_id")
        address = f"#/series/{int(args['series_id'])}"
    elif to == "read":
        book, page = args.get("book_id"), args.get("page")
        if not book:
            if not args.get("series_id"):
                raise ToolError("Give book_id, or series_id to continue that series")
            s = await call_api("GET", f"/api/series/{int(args['series_id'])}")
            if not s.get("continue"):
                raise ToolError("Nothing to read in that series")
            book = s["continue"]["book_id"]
            page = page or s["continue"]["page"] + 1
        address = f"#/read/{int(book)}" + (f"?page={max(int(page) - 1, 0)}" if page else "")
    else:
        raise ToolError(f"to is one of {', '.join(VIEWS)}")
    return await on_screen(args, "go", address=address)


@tool("screen_library", "Show the library on the screen with these filters and sort (the same as library_search). "
      "Without any, it shows everything (clears the filters).", FILTER_PARAMS, screen=True)
async def screen_library(args):
    query = urlencode(filter_query(args))
    return await on_screen(args, "go", address="#/" + (f"?{query}" if query else ""))


@tool("reader_state", "The open book on the screen: series, book, page (from 1) of how many, reading mode, the "
      "previous and next book, whether the reading options are open, and what plugins put there "
      "(overlays to switch on or off, buttons, for reader_plugin).", screen=True, read_only=True)
async def reader_state(args):
    return await on_screen(args, "reader", action="state")


@tool("reader_turn", "Turn the page in the open book: next or previous, one or more pages. At the last page, "
      "next shows the end; next again opens the next book.",
      {"direction": {"type": "string", "enum": ["next", "previous"]},
       "pages": {"type": "integer", "description": "How many (default 1)"}}, ["direction"], screen=True)
async def reader_turn(args):
    return await on_screen(args, "reader", action="turn", forward=args.get("direction") != "previous",
                           pages=max(1, min(int(args.get("pages") or 1), 500)))


@tool("reader_go_to", "In the reader: go to a page of this book (from 1), or open the next / previous book or "
      "another book by id (at page, if given).",
      {"page": {"type": "integer"}, "book": {"type": "string", "description": "next, previous or a book id"}},
      screen=True)
async def reader_go_to(args):
    page = args.get("page")
    if args.get("book"):
        return await on_screen(args, "reader", action="book", which=str(args["book"]),
                               page=None if page is None else max(int(page) - 1, 0))
    if page is None:
        raise ToolError("Give page or book")
    return await on_screen(args, "reader", action="page", page=max(int(page) - 1, 0))


@tool("reader_mode", "Read the open series as pages (one at a time) or vertical (a long strip, for webtoons). "
      "It is saved for the series (admins).",
      {"mode": {"type": "string", "enum": ["paged", "vertical"]}}, ["mode"], screen=True)
async def reader_mode(args):
    return await on_screen(args, "reader", action="mode", mode=args["mode"])


@tool("reader_options", "Open or close the reader's options (the drawer with page, chapter, mode, fit, page look, "
      "fullscreen and the plugins' buttons); then screen_describe lists them.",
      {"open": {"type": "boolean"}}, ["open"], screen=True)
async def reader_options(args):
    return await on_screen(args, "reader", action="options", open=bool(args["open"]))


@tool("reader_plugin", "Use what a plugin put in the reader: switch its overlay (e.g. a translation) on or off, or "
      "press one of its buttons (names from reader_state).",
      {"plugin": {"type": "string"}, "button": {"type": "string", "description": "A button's key"},
       "overlay": {"type": "boolean", "description": "Switch its overlay on (true) or off (false)"}},
      ["plugin"], screen=True)
async def reader_plugin(args):
    if args.get("button"):
        return await on_screen(args, "reader", action="press", plugin=args["plugin"], key=args["button"])
    if args.get("overlay") is None:
        raise ToolError("Give button or overlay")
    return await on_screen(args, "reader", action="overlay", plugin=args["plugin"], on=bool(args["overlay"]))


@tool("reader_close", "Leave the reader, back to where it was opened from.", screen=True)
async def reader_close(args):
    return await on_screen(args, "reader", action="leave")


# ---------------------------------------------------------------- the plugins' tools

def tool_name(plugin_id, name) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", f"{plugin_id}__{name}")[:64]


def _plugin_tools(user) -> dict[str, Tool]:
    out = {}
    for p in plugins.enabled():
        for t in p.mcp_tools or []:
            if t.admin and not user.can_manage:
                continue
            name = tool_name(p.id, t.name)
            out[name] = Tool(name, f"{p.name}: {t.description}", dict(t.params or {}), list(t.required or []),
                             _server_runner(p.id, t.name), admin=t.admin, plugin=p.id)
    # what the open apps' plugins offer (MangaVibe.screenCommand), from the screens that say so
    for s in hub.live(user.id):
        for c in s.commands:
            name = tool_name(c["plugin"], c["name"])
            if name in out or plugins.get(c["plugin"]) is None:
                continue
            spec = c.get("spec") if isinstance(c.get("spec"), dict) else {}
            out[name] = Tool(name, f"{c['plugin']} (on the screen): {spec.get('description') or c['name']}",
                             dict(spec.get("params") or {}), list(spec.get("required") or []),
                             _screen_runner(c["plugin"], c["name"]), screen=True, plugin=c["plugin"])
    return out


def _server_runner(plugin_id, name):
    async def run(args):
        p = plugins.get(plugin_id)
        if p is None:
            raise ToolError("That plugin is switched off")

        def work():
            from app.db import SessionLocal
            with SessionLocal() as db:
                result = p.run_mcp_tool(Context(p, db), name, args)
                db.commit()
                return result
        try:
            return await run_in_threadpool(work)
        except HTTPException as e:
            raise ToolError(f"{e.status_code}: {e.detail}") from None
        except (ValueError, KeyError, LookupError, PermissionError) as e:
            raise ToolError(str(e)) from None
    return run


def _screen_runner(plugin_id, name):
    async def run(args):
        rest = {k: v for k, v in args.items() if k != "screen"}
        return await on_screen(args, "plugin", plugin=plugin_id, name=name, params=rest)
    return run


def available() -> dict[str, Tool]:
    """Every tool the current user has: the built-in ones, then the plugins'."""
    user = users.current()
    return {**BUILTIN, **{k: v for k, v in _plugin_tools(user).items() if k not in BUILTIN}}
