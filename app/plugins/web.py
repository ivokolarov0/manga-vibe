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
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.plugins import Context, manager

# where plugin markup can go: slot name -> where it is in the page
SLOTS = {
    "library-tools": "the library page's buttons, after Filter / Sort / Letter",
    "library-filters": "the row of filters on, after Clear all (shown while filters are on)",
    "sheets": "the end of the page, for sheets and dialogs of the plugin's own",
}


def with_ui(plugin) -> bool:
    return bool(plugin.js or plugin.html)


def render_index(page: str) -> str:
    """index.html with the switched-on plugins' markup in its slots and their scripts."""
    plugins = manager.enabled()
    for slot in SLOTS:
        # each slot stays in the page (display: contents), so a plugin switched on later can be added
        # to it, and each plugin's markup is in a box of its own, so it can be taken out (app.js)
        parts = [ui_box(p.id, p.html[slot]) for p in plugins if p.html.get(slot)]
        page = page.replace(f"<!-- slot:{slot} -->",
                            f'<div class="plugin-slot" data-slot="{slot}">{"".join(parts)}</div>')
    scripts = "\n".join(f'<script src="{script_url(p.id)}"></script>' for p in plugins if p.js)
    return page.replace("<!-- slot:scripts -->", scripts)


def ui_box(plugin_id, markup) -> str:
    return f'\n<div class="plugin-ui" data-plugin="{html_lib.escape(plugin_id)}">\n{markup.strip()}\n</div>'


def script_url(plugin_id) -> str:
    return html_lib.escape(f"/plugins/{quote(plugin_id)}.js")


def ui_of(plugin) -> dict:
    """What a plugin adds to the page, for switching it on without reloading (app.js)."""
    return {"html": {slot: markup for slot, markup in plugin.html.items() if slot in SLOTS},
            "js": f"/plugins/{quote(plugin.id)}.js" if plugin.js else None,
            "css": f"/plugins/{quote(plugin.id)}.css" if plugin.css else None}


def plugin_ctx(request: Request):
    """FastAPI dependency for a plugin's own routes: its `ctx`, with a database session."""
    from app.db import SessionLocal
    with SessionLocal() as db:
        yield Context(request.scope["mangavibe.plugin"], db)


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
