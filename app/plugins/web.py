"""What plugins add to the web app: an API of their own and UI in the page (Plugin API 7).

- `Plugin.router` (a FastAPI APIRouter) answers /api/plugins/<id>/api/<its paths>. Only while the
  plugin is on; a switched-off plugin's API answers 404. Handlers get the plugin's `ctx` with
  `ctx: Context = Depends(plugin_ctx)` (its database session is `ctx.db`; commit what you change).
- `Plugin.html` puts markup into the page's slots (SLOTS below): `{{ slot("<name>") }}` in the page's
  templates, `<!-- slot:<name> -->` in a plugin's own markup (a slot it holds for others).
  It's Alpine.js markup like the rest of the page, so it can use `$store.app` and the helpers on
  `window.MangaVibe` (app.js).
- `Plugin.js` is served at /plugins/<id>.js and runs after app.js. It registers its stores and
  components in `MangaVibe.onStart(fn)`. One file per plugin: a mistake in one doesn't stop the
  others or the app.

The page is put together from Jinja templates (app/web/templates/, page.html first) with the
switched-on plugins when it loads (issue #213). Switching one on or off later adds or takes out its
markup in place (app.js, from GET /api/plugins/<id>/ui); no reload.
"""
import html as html_lib
import logging
import traceback
from urllib.parse import quote

from jinja2 import (BaseLoader, Environment, FileSystemLoader, StrictUndefined, TemplateNotFound, pass_context,
                    select_autoescape)
from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import config
from app.plugins import Context, manager, plugin_jobs

log = logging.getLogger("mangavibe.plugins")

# the page's templates: page.html, and the shell, views, Settings sections, sheets and dialogs it includes
TEMPLATES_DIR = config.WEB_DIR / "templates"


def plugin_templates(plugin_id):
    """A folder plugin's templates/ folder (next to its __init__.py), or None."""
    path = manager.paths.get(plugin_id)
    folder = path.parent / "templates" if path is not None and path.name == "__init__.py" else None
    return folder if folder is not None and folder.is_dir() else None


class PageLoader(BaseLoader):
    """The page's templates (TEMPLATES_DIR), and plugins' own: "@<plugin id>/<path>" is <path> in that
    plugin's templates/ folder (Plugin API 17), e.g. "@cards/views/card.html". A plugin's template can
    extend another plugin's, e.g. the built-in card: {% extends "@cards/views/card.html" %}."""

    def __init__(self, folder):
        self.core = FileSystemLoader(folder)

    def get_source(self, environment, name):
        if not name.startswith("@"):
            return self.core.get_source(environment, name)
        plugin_id, _, rest = name[1:].partition("/")
        folder = plugin_templates(plugin_id)
        if folder is None or not rest:
            raise TemplateNotFound(name)
        return FileSystemLoader(folder).get_source(environment, rest)


# Compiled once and kept; auto_reload looks at the files' times, so an edited template shows on the next
# load (cheap, and what you want while working on the page). Autoescape: text a template puts in is
# escaped unless marked safe (plugin markup is, by slot()). StrictUndefined: a misspelt name is an
# error, not an empty place (a test renders the page)
templates = Environment(loader=PageLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]),
                        undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True,
                        keep_trailing_newline=True, auto_reload=True)

# The views a plugin can replace (Plugin API 17): name -> the job, its template in the plugin's
# templates/ folder, the slots it must keep (other plugins put things there) and what they're for.
VIEWS = {
    "card": {"job": "view:card", "template": "views/card.html", "layouts": ("library", "continue"),
             "slots": {"card-badges": "badges on the cards"}},
    "library": {"job": "view:library", "template": "views/library.html",
                "slots": {"library-tools": "buttons next to Filter and Sort", "library-filters": "buttons with the filters on",
                          "select-menu": "entries in multi-select's ⋯ menu", "library-end": "the library's dialogs",
                          "card-badges": "badges on the cards"}},
    "continue": {"job": "view:continue", "template": "views/continue.html",
                 "slots": {"continue-top": "what goes above Continue reading", "card-badges": "badges on the cards"}},
    "series": {"job": "view:series", "template": "views/series.html",
               "slots": {"series-actions": "buttons next to Continue", "series-menu": "entries in the ⋯ menu",
                         "series-panels": "panels on the series page", "series-end": "wide panels and dialogs",
                         "book-row": "labels and buttons on the books"}},
}

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


def page_plugins(safe: bool = False):
    """The plugins whose markup, scripts and styles the page gets. Safe mode (/?safe=1, Plugin API 15):
    only the built-in ones, so a broken plugin of the owner's own can be switched off. On the server
    every plugin keeps running."""
    return [p for p in manager.enabled() if not safe or manager.is_builtin(p.id)]


def render_index(safe: bool = False, template: str = "page.html") -> str:
    """The app's page (templates/page.html) with the switched-on plugins' markup in its slots and their
    scripts. Safe mode: only the built-in plugins'."""
    plugins = page_plugins(safe)
    # the built-in view plugins' scripts are always loaded, on or off: their view is the fallback, and a
    # plugin's view may extend theirs and use their components (e.g. seriesPage())
    found = list(plugins)
    for base in base_views():
        if not any(base is p for p in found):
            found.append(base)
    scripts = "\n".join(f'<script src="{script_url(p.id)}"></script>' for p in found if p.js)
    return templates.get_template(template).render(slot=slot_function(plugins), safe=safe, view=view,
                                                   plugin_scripts=Markup(scripts))


def slot_function(plugins, used=None):
    """slot("<name>") for the templates: the slot with these plugins' markup in it. A name that isn't
    in SLOTS is an error, so a typo in a template fails the page (and its test) instead of leaving a
    slot nobody fills. `used` collects the names asked for (a view's missing slots)."""
    def slot(name):
        if name not in SLOTS:
            raise ValueError(f"No slot {name!r}: add it to SLOTS in app/plugins/web.py")
        if used is not None:
            used.add(name)
        return Markup(slot_markup(name, plugins))
    return slot


# --- views a plugin can replace (Plugin API 17, issue #205) -------------------------------------

def base_views():
    """The built-in plugins that draw the views, on or off: the fallback, and what other views extend.
    Their scripts are always in the page (and served: /plugins/<id>.js)."""
    found = [manager.builtin_for(spec["job"]) for spec in VIEWS.values()]
    return [p for i, p in enumerate(found) if p is not None and not any(p is q for q in found[:i])]


def view_plugins(name, safe=False):
    """Who draws view `name`, in order: the running plugin doing its job (not in safe mode, unless it's
    built in), then the built-in one as the fallback."""
    return [p for p in manager.doing(VIEWS[name]["job"]) if not safe or manager.is_builtin(p.id)]


@pass_context
def view(ctx, name, **values):
    """{{ view("card", layout="library") }} in the page's templates: the view's markup from the plugin
    doing its job (its templates/views/<name>.html), rendered with the page's context and `values`. A
    template that's missing or fails is logged and the built-in plugin's is used instead, so a broken
    view never breaks the page."""
    spec = VIEWS[name]
    tried = []
    for p in view_plugins(name, ctx.get("safe", False)):
        try:
            return Markup(templates.get_template(f"@{p.id}/{spec['template']}").render(
                {**ctx.get_all(), **values}))
        except Exception:  # a plugin's template: whatever it does wrong, the page still draws
            tried.append(p.id)
            log.error("Plugin %s: its %s view failed:\n%s", p.id, name, traceback.format_exc())
    raise RuntimeError(f"No plugin could draw the {name} view (tried {', '.join(tried) or 'none'})")


def view_warnings(plugin) -> list[str]:
    """What's wrong with the views this plugin draws, for Settings -> Plugins: a template that's
    missing or doesn't render, or a slot it leaves out (other plugins' markup there won't show)."""
    out = []
    jobs = plugin_jobs(plugin, "provides")
    for name, spec in VIEWS.items():
        if spec["job"] not in jobs:
            continue
        used = set()
        try:
            for layout in spec.get("layouts", (None,)):
                templates.get_template(f"@{plugin.id}/{spec['template']}").render(
                    slot=slot_function([], used), safe=False, view=view, layout=layout)
        except TemplateNotFound:
            out.append(f"Its {name} view has no templates/{spec['template']}: the built-in one is used")
            continue
        except Exception as e:
            out.append(f"Its {name} view fails ({type(e).__name__}: {e}): the built-in one is used")
            continue
        for slot, what in spec["slots"].items():
            if slot not in used:
                who = [p.name for p in manager.enabled() if slot in p.html]
                out.append(f"Its {name} view leaves out the {slot} slot, so {what}"
                           + (f" ({', '.join(who)})" if who else "") + " won't show")
    return out


def slot_markup(slot: str, plugins, nested: bool = True) -> str:
    """One slot with the plugins' markup in it, each in a box of its own so it can be taken out
    (app.js), in their order. The slot stays in the page (display: contents), so a plugin switched on
    later can be added to it. A plugin's markup can hold slots for others (e.g. account-row): with
    `nested`, those are filled too (one level)."""
    entries = [(p.id, *slot_entry(p.html[slot])) for p in plugins if slot in p.html]
    parts = [ui_box(pid, fill_slots(m, plugins) if nested else m, order)
             for pid, m, order in sorted(entries, key=lambda e: e[2]) if m]
    return f'<div class="plugin-slot" data-slot="{slot}">{"".join(parts)}</div>'


def fill_slots(markup: str, plugins) -> str:
    """Put the plugins' markup into the slots of a plugin's own markup: `<!-- slot:<name> -->` there
    (the page's templates use slot() instead). The markup put in isn't searched again."""
    for slot in SLOTS:
        if f"<!-- slot:{slot} -->" in markup:
            markup = markup.replace(f"<!-- slot:{slot} -->", slot_markup(slot, plugins, nested=False))
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
