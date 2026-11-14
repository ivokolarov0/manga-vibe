"""What plugins add to the web app: an API of their own and UI in the page (Plugin API 7).

- `Plugin.router` (a FastAPI APIRouter) answers /api/plugins/<id>/api/<its paths>. Only while the
  plugin is on; a switched-off plugin's API answers 404. Handlers get the plugin's `ctx` with
  `ctx: Context = Depends(plugin_ctx)` (its database session is `ctx.db`; commit what you change).
- `Plugin.html` puts markup into the page's slots (SLOTS below), `<!-- slot:<name> -->` in index.html.
  It's Alpine.js markup like the rest of the page, so it can use `$store.app` and the helpers on
  `window.MangaVibe` (app.js).
- `Plugin.js` is served at /plugins/<id>.js and runs after app.js. It registers its stores and
  components in `MangaVibe.onStart(fn)`. One file per plugin: a mistake in one doesn't stop the
  others or the app.

The page is put together with the switched-on plugins when it loads. Switching one on or off later
adds or takes out its markup in place (app.js, from GET /api/plugins/<id>/ui); no reload.
"""
import html as html_lib
import re
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse

from app import config
from app.plugins import Context, manager

# markup the page uses in more than one place: <!-- part:<name> --> is app/web/parts/<name>.html
PARTS_DIR = config.WEB_DIR / "parts"
PART = re.compile(r"<!-- part:([a-z0-9-]+) -->")

# where plugin markup can go: slot name -> where it is in the page
SLOTS = {
    "topbar": "the top bar, before the Settings button (e.g. the account menu)",
    "home-top": "above the library grid and Continue reading (e.g. the library tabs)",
    "library-tools": "the library page's buttons, after Filter / Sort / Letter",
    "library-filters": "the row of filters on, after Clear all (shown while filters are on)",
    "select-menu": "the ⋯ menu of the library's multi-select (can use bulk() and selCount)",
    "library-end": "the end of the library page: its dialogs (can use bulk() and selCount)",
    "series-edit": "the series Edit dialog's fields, saved with Save (events edit-open/-changed/-save)",
    "series-actions": "the series page's Continue button and dropdowns, after them (can use s: the series)",
    "series-menu": "the series page's ⋯ menu (admins), before Delete (can use s and menuOpen)",
    "series-panels": "the series page's side column of panels, after Plugin tools (can use s)",
    "series-end": "the end of the series page, after the Books panel: wide panels and dialogs (s, books)",
    "book-row": "each book in the series page's Books list, before its buttons (can use b: the book, s)",
    "card-badges": "each series card's poster in the library and Continue (can use s: the card's series)",
    "continue-top": "the Continue reading page, above its grid",
    "settings": "Settings' pages: a plugin's own (MangaVibe.settingsPage), shown with section === '<key>'",
    "folder-row": "each folder in Settings → Library folders (can use l: the folder)",
    "attention-note": "under the explanation of a Needs attention list",
    "sheets": "the end of the page, for sheets and dialogs of the plugin's own",
    # slots in plugins' own markup (a plugin's markup can hold a slot that others fill)
    "account-row": "each account in Settings → Accounts (accounts plugin; can use a: the account)",
}


def with_ui(plugin) -> bool:
    return bool(plugin.js or plugin.html)


def render_index(page: str) -> str:
    """index.html with the switched-on plugins' markup in its slots and their scripts."""
    plugins = manager.enabled()
    page = fill_slots(fill_parts(page), plugins)
    scripts = "\n".join(f'<script src="{script_url(p.id)}"></script>' for p in plugins if p.js)
    return page.replace("<!-- slot:scripts -->", scripts)


def fill_parts(markup: str, depth: int = 0) -> str:
    """The page with each <!-- part:<name> --> replaced by app/web/parts/<name>.html (which can hold
    parts and slots of its own). Put together on each request like the slots: no build step. A part
    that isn't there is an error, not an empty place (a test loads the page)."""
    def part(m):
        path = PARTS_DIR / f"{m.group(1)}.html"
        if not path.is_file():
            raise ValueError(f"No part {m.group(1)!r} in {PARTS_DIR}")
        if depth >= 4:
            raise ValueError(f"Part {m.group(1)!r} is nested too deep (does it include itself?)")
        return fill_parts(path.read_text(encoding="utf-8").strip(), depth + 1)
    return PART.sub(part, markup)


def fill_slots(markup: str, plugins) -> str:
    """Put the plugins' markup into the slots of this markup, and into slots in what was put in (a
    plugin's markup can hold a slot for others, e.g. account-row). Each slot stays in the page
    (display: contents), so a plugin switched on later can be added to it, and each plugin's markup is in
    a box of its own, so it can be taken out (app.js). In each slot the boxes go by their order."""
    for _ in range(2):  # the page's slots, then slots in plugin markup
        for slot in SLOTS:
            entries = [(p.id, *slot_entry(p.html[slot])) for p in plugins if slot in p.html]
            parts = [ui_box(pid, m, order) for pid, m, order in sorted(entries, key=lambda e: e[2]) if m]
            markup = markup.replace(f"<!-- slot:{slot} -->",
                                    f'<div class="plugin-slot" data-slot="{slot}">{"".join(parts)}</div>')
    return markup


def ui_box(plugin_id, markup, order) -> str:
    return (f'\n<div class="plugin-ui" data-plugin="{html_lib.escape(plugin_id)}" data-order="{order}">'
            f'\n{markup.strip()}\n</div>')


DEFAULT_ORDER = 50


def slot_entry(value) -> tuple[str, int]:
    """A plugin's html for a slot: "<markup>", or ("<markup>", order) to choose where it goes among the
    other plugins' (lower first; DEFAULT_ORDER when not given). The same order keeps the plugins' order."""
    if isinstance(value, (tuple, list)):
        markup, order = value
        return markup or "", int(order)
    return value or "", DEFAULT_ORDER


def script_url(plugin_id) -> str:
    return html_lib.escape(f"/plugins/{quote(plugin_id)}.js")


def ui_of(plugin) -> dict:
    """What a plugin adds to the page, for switching it on without reloading (app.js)."""
    others = [p for p in manager.enabled() if p is not plugin]
    slots = {slot: slot_entry(value) for slot, value in plugin.html.items() if slot in SLOTS}
    return {"html": {slot: fill_slots(markup, others) for slot, (markup, _) in slots.items() if markup},
            "order": {slot: order for slot, (markup, order) in slots.items() if markup},
            "js": f"/plugins/{quote(plugin.id)}.js" if plugin.js else None,
            "css": f"/plugins/{quote(plugin.id)}.css" if plugin.css else None}


def plugin_ctx(request: Request):
    """FastAPI dependency for a plugin's own routes: its `ctx`, with a database session."""
    from app.db import SessionLocal
    with SessionLocal() as db:
        yield Context(request.scope["mangavibe.plugin"], db)


class PluginMiddleware:
    """Puts the switched-on plugins' `middleware` in front of every request (e.g. the libraries
    plugin's password check). The chain is rebuilt only when that set of plugins changes, so switching
    one off takes effect at once and costs nothing per request otherwise."""

    def __init__(self, app):
        self.app = app
        self._key, self._chain = (), app

    async def __call__(self, scope, receive, send):
        found = tuple(sorted((p for p in manager.enabled() if p.middleware is not None),
                             key=lambda p: p.middleware_order))  # e.g. accounts (0) first: who is asking
        key = tuple(id(p) for p in found)
        if key != self._key:
            chain = self.app
            for p in reversed(found):
                chain = p.middleware(chain)
            self._key, self._chain = key, chain
        await self._chain(scope, receive, send)


class PluginApi:
    """Mounted at /api/plugins/{plugin_id}/api: hands the request to that plugin's router.
    Looked up on every request, so "Reload plugins" and switching one off take effect at once."""

    async def __call__(self, scope, receive, send):
        plugin = manager.get(scope.get("path_params", {}).get("plugin_id", ""))
        if plugin is None or plugin.router is None:
            response = JSONResponse({"detail": "No such plugin, or it is switched off"}, status_code=404)
            return await response(scope, receive, send)
        scope["mangavibe.plugin"] = plugin
        await plugin.router(scope, receive, send)
