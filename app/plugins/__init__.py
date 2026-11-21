"""Plugin system.

A plugin is a Python file (or package folder) in /data/plugins. It defines one or
more subclasses of `Plugin`. MangaVibe loads them on startup.

What a plugin can do:
- metadata provider: implement search() and fetch() (used by matching, phase 2)
- extra fields:       declare `fields` and call ctx.set_field(series, key, value)
- scan hooks:         on_book_scanned() can change parsed values; on_series_scanned()
- custom actions:     declare `actions` and implement run_action()
- dropdowns:          declare `choices`, implement get_choice() and set_choice()
- styling:            set `css` to change how the app looks (see examples/plugins/sakura_theme.py)
- reading events:     on_book_started(), on_book_completed(), on_series_status_changed()
- settings:           declare `settings_fields`, `global_actions` (buttons in Settings) and status()
- web callback:       handle_web() answers GET /api/plugins/<id>/web/<path> (e.g. an OAuth login)
- permissions:        list what it uses in `permissions`; an update that adds one asks first
- page overlays:      set `overlay_label` and implement page_overlay() to draw boxes over reader pages
- page data:          ctx.set_page_data()/get_page_data() keep a value per page (e.g. a translation)
- series data:        ctx.set_series_data()/get_series_data() keep values per series, never shown
- books:              book_badges() labels books in the list; `book_actions` are buttons in the reader
- long work:          ctx.run_in_thread() runs a worker of the plugin's own; it checks ctx.should_stop()
- its own API:        set `router` (a FastAPI APIRouter): served at /api/plugins/<id>/api/...
- its own UI:         `js` runs in the web app; `html` puts markup into the page's slots (see app/plugins/web.py)
- the app's lists:    series_filter() narrows what a request sees; `middleware` guards every request;
                      reading_mode(), series_info(), on_start(), can_switch_off(), folder hooks
- assistants (MCP):   `mcp_tools` and run_mcp_tool() add tools to the MCP server (the Remote control
                      plugin, github.com/ivokolarov0/manga-vibe-mcp);
                      find_user() says who a user id is, for a request that comes with a key (Plugin API 13)
- other books:        `reads` names book formats it has a reader for (e.g. "epub-text", light novels);
                      ctx.text_sections() lists a text book's sections (Plugin API 14)
- jobs:               `provides` names the parts of the app it does (and replaces), `requires` the ones
                      it needs another plugin to do (app/contracts, Plugin API 15)

Some of the app's own features are built-in plugins (app/plugins/builtin/), e.g. saved searches and
libraries, so the owner can switch them off and anyone can write a replacement.

Plugins get read-only access to books through `ctx`. They must never write to the
library; their data goes into the database. The one exception is the built-in Optimize plugin,
which replaces books with smaller ones when the owner asks (builtin/optimize/core.py).
See docs/PLUGINS.md and examples/plugins/.
"""
import importlib.util
import logging
import queue
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from app import config

log = logging.getLogger("mangavibe.plugins")
API_VERSION = 18
BUILTIN = Path(__file__).parent / "builtin"  # the plugins that come with the app
# built-in plugins that moved to a repo of their own: id -> its name and where to get it now
MOVED = {"mcp": {"name": "Remote control (MCP)", "url": "https://github.com/ivokolarov0/manga-vibe-mcp"}}

# Where a plugin is listed in Settings -> Plugins (Plugin.category), in the order shown there
CATEGORIES = {
    "metadata": "Metadata",      # metadata providers (is_provider)
    "reader": "Reader",          # draws on pages, buttons in the reader
    "library": "Library",        # lists, series pages, fields and actions, libraries, searches
    "system": "System",          # accounts, access, middleware, maintenance
    "appearance": "Appearance",  # themes: CSS only
    "other": "Other",
}


@dataclass
class FieldDef:
    key: str
    label: str


@dataclass
class Action:
    key: str
    label: str
    confirm: str | None = None  # optional "Are you sure?" text


@dataclass
class Choice:
    """A dropdown on the series page, e.g. a status on a tracker site. `options` is a list of
    (value, label); a value may be "". A plugin field with the same key is hidden while the
    dropdown shows, so the plugin can keep its value there."""
    key: str
    label: str
    options: list = field(default_factory=list)


@dataclass
class SettingDef:
    """A text box on the plugin's page (Settings -> Plugins -> the plugin), or a dropdown when `options` is given
    ([(value, label)]). The value lands in ctx.settings[key]; ctx.setting(key) falls back to
    `default` while nothing is saved."""
    key: str
    label: str
    help: str = ""
    secret: bool = False  # never sent back to the browser (shown as "saved")
    options: list = field(default_factory=list)
    default: str = ""


@dataclass
class OpenUrl:
    """Return this from an action or handle_web() to send the browser to `url`
    (for example a login page). `message` is shown as a toast first."""
    url: str
    message: str = ""


@dataclass
class Box:
    """One box drawn over a reader page. x, y, w, h are fractions of the page (0 to 1), so they fit
    any page size and zoom. `title` is shown when the box is held or hovered (e.g. the original text);
    `cls` adds CSS classes, which the plugin's `css` can style."""
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    title: str = ""
    cls: str = ""


@dataclass
class Overlay:
    """What page_overlay() returns. With `pending`, the reader asks again a few seconds later and
    shows `message` (e.g. "Translating…") meanwhile; `message` alone is a short status line
    (e.g. "PC offline"), shown over the page. `note` is a quiet line about the page (e.g.
    "Full context · Haruka, Taro"), shown in the reader's top bar only while the controls are open.
    `speech` is the page as plain text in reading order, for listening: the reader's "Read aloud" reads
    it, and screen readers get it. Without it, read aloud reads the boxes' text (plugin API 5)."""
    boxes: list = field(default_factory=list)  # [Box]
    pending: bool = False
    message: str = ""
    note: str = ""
    speech: str = ""


@dataclass
class McpTool:
    """A tool an assistant can use through the MCP server (the Remote control plugin), run by
    run_mcp_tool(). `params` is a JSON Schema "properties" object, e.g. {"name": {"type": "string",
    "description": "..."}}; `required` lists the ones that must be given. `admin`: only for people who
    may manage the app (it's left out of the list for the others). The tool is called
    `<plugin id>__<name>` (hyphens become _). Plugin API 13."""
    name: str
    description: str
    params: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    admin: bool = False


@dataclass
class Candidate:
    """A search result from a metadata provider."""
    provider_id: str
    title: str
    url: str | None = None
    cover_url: str | None = None
    year: int | None = None
    format: str | None = None
    status: str | None = None
    other_titles: list = field(default_factory=list)
    data: dict = field(default_factory=dict)  # full metadata if the search already returned it


class Plugin:
    id = ""            # unique, e.g. "translation-groups"
    name = ""          # shown in Settings
    version = "1.0"
    description = ""
    # Where Settings -> Plugins lists it: one of CATEGORIES, or a list whose first one is the main
    # category and the rest are shown as tags, e.g. ["reader", "library"]. Left empty, it's worked out
    # from what the plugin uses (categories_of). Plugin API 10
    category: str | list[str] = ""
    fields: list[FieldDef] = []
    actions: list[Action] = []
    choices: list[Choice] = []  # dropdowns on the series page
    is_provider = False  # True for metadata providers
    url = ""             # optional: the site a provider gets its data from (shown in Settings)
    css = ""             # extra CSS for the web app (loaded after the built-in styles, so it wins)
    settings_fields: list[SettingDef] = []  # text boxes on the plugin's page in Settings -> Plugins
    global_actions: list[Action] = []       # buttons on the plugin's page (not tied to a series)
    repo = ""  # "owner/name" on GitHub: its releases update this plugin (see app/plugin_updates.py)
    # What the plugin uses, in short sentences, e.g. "Connects to api.myanimelist.net". Shown in
    # Settings. Not enforced (a plugin is Python); an update that adds one asks the owner first.
    permissions: list[str] = []
    overlay_label = ""   # e.g. "Translation": a reader toggle that draws page_overlay() over pages
    # buttons for the open book, in the reader's options. The plugin's `js` can do one's work in the
    # page instead of run_book_action(), with a "book-action" handler (e.g. open an editor; Plugin API 12)
    book_actions: list[Action] = []
    # --- its own API and UI (Plugin API 7, see app/plugins/web.py) ---
    router = None        # a FastAPI APIRouter, served at /api/plugins/<id>/api/<its paths>
    js = ""              # JavaScript run in the web app before it starts (window.MangaVibe is there)
    # slot name -> markup put into that slot of the page (web.SLOTS), or (markup, order): lower goes first
    # among the plugins in that slot, 50 when not given (Plugin API 11)
    html: dict[str, str | tuple[str, int]] = {}
    # False: starts switched off the first time it's seen (the owner switches it on), e.g. accounts
    enabled_by_default = True
    # an ASGI middleware class, middleware(app), put in front of every request while the plugin is on,
    # e.g. to refuse what a request may not see (Plugin API 8; app/plugins/web.py PluginMiddleware)
    middleware = None
    # lower runs first (outermost). Who is asking is known from 0 on (accounts), so guards that look at
    # the user (e.g. libraries' LockGuard) need a higher number; equal ones keep the plugins' order
    middleware_order = 100
    # tools for assistants, through the MCP server (the Remote control plugin), run by run_mcp_tool(). Plugin API 13
    mcp_tools: list[McpTool] = []
    # book formats this plugin has a reader for, e.g. ["epub-text"] (light novels): such a book opens in
    # the reader its `js` gives MangaVibe.registerReader() instead of the page reader (Plugin API 14)
    reads: list[str] = []
    # --- jobs (Plugin API 15, app/contracts): the parts of the app this plugin does, and replaces ---
    # job -> contract version, e.g. {"library-query": 1}. One plugin at a time does an exclusive job:
    # switching this one on switches the other off. A job or version this MangaVibe doesn't have stops
    # the plugin from running (Settings says why)
    provides: dict[str, int] = {}
    # jobs another switched-on plugin must do for this one to run, e.g. {"library-query": 1}
    requires: dict[str, int] = {}

    # --- assistants (MCP, Plugin API 13)---------------------------------------------
    def run_mcp_tool(self, ctx, name, args: dict):
        """Run one of `mcp_tools` for an assistant, as the person whose key it uses (users.current()).
        Return text, or anything JSON can hold; raise (e.g. HTTPException or ValueError) to report an
        error to the assistant."""
        return "Not implemented"

    def find_user(self, ctx, user_id):
        """Who user `user_id` is (a users.User), or None if there is no such user any more. Asked for a
        request that comes with a key instead of a login (the MCP server): the accounts plugin answers.
        Without any plugin answering, only the owner (id 1) exists."""
        return None

    # --- shaping the app's lists and pages (Plugin API 8): how features like libraries plug in ---
    def series_filter(self, ctx, request) -> list:
        """SQL conditions on models.Series for what this request may see in every list and count (the
        grid, tags, Continue reading, Needs attention, folder counts, multi-select). Read your own query
        parameters from `request.query_params` (e.g. shelf=3). Raise HTTPException to refuse. Not called
        inside try/except: if it fails, the request fails, so nothing is shown by mistake. Fast: it runs
        for every list."""
        return []

    def series_info(self, ctx, series) -> dict | None:
        """Extra data for the series page, as `plugin_data[<plugin id>]` in GET /api/series/<id>."""
        return None

    def reading_mode(self, ctx, series) -> tuple[str, str] | None:
        """A default reading mode for a series that has none of its own: (mode, why), e.g.
        ("vertical", "the mode of Webtoons"). mode is paged or vertical; None: no opinion."""
        return None

    def on_start(self, ctx):
        """The plugin starts working: at app start, after Reload plugins and when switched on."""

    def can_switch_off(self, ctx) -> str | None:
        """Why the plugin can't be switched off right now (shown to the owner), or None."""
        return None

    def on_folders_changed(self, ctx):
        """A library folder was added (at start too, for the LIBRARY_PATHS folders)."""

    def on_folder_removed(self, ctx, folder):
        """A library folder was just removed (flushed, not committed): clean up what was made for it."""

    # --- scan hooks -------------------------------------------------------
    def on_book_scanned(self, ctx, book, parsed):
        """Called for each new/changed book. May change `parsed` (volume, chapter,
        chapter_end, extra, extra_index, version) to fix ordering."""

    def on_series_scanned(self, ctx, series, books):
        """Called after a series was scanned and ordered."""

    def on_series_deleted(self, ctx, series):
        """The owner is deleting this series (its folder and everything about it). Called just
        before, so the series, its books and the plugin's own data for it can still be read.
        Plugin API 6."""

    # --- reading events ---------------------------------------------------
    # Called in the background after the change is saved, one event at a time, so a slow
    # website never slows the reader. Only changes made by the owner fire them (reading,
    # the Read/Unread buttons, picking a status), not scans and not other plugins.
    def on_book_started(self, ctx, book, series):
        """The owner opened a book for the first time (or again after marking it unread)."""

    def on_book_completed(self, ctx, book, series):
        """A book became read: its last page was reached or it was marked read."""

    def on_series_status_changed(self, ctx, series, old, new):
        """The series' reading status changed. Values: unread, reading, completed,
        plan_to_read, on_hold, dropped. `series.publication_status` says whether it has ended."""

    # --- custom actions ---------------------------------------------------
    def run_action(self, ctx, action_key, series):
        """Run a button action on the series page. Return a short message, or OpenUrl."""
        return "Not implemented"

    # --- dropdowns on the series page --------------------------------------
    def get_choice(self, ctx, key, series):
        """The selected value of dropdown `key` for this series: one of its option values,
        None when it isn't known yet, or False to hide the dropdown for this series.
        Called each time the series page opens, so keep it fast (no website calls).
        By default it's the plugin field with the same key."""
        return ctx.get_field(series, key)

    def set_choice(self, ctx, key, series, value):
        """The owner picked `value` (always one of the option values). Return a short message,
        or OpenUrl. Raise or return an error message if it couldn't be done."""
        return "Not implemented"

    def run_global_action(self, ctx, action_key):
        """Run a button from the plugin's page in Settings. Return a short message, or OpenUrl.
        For long work, start it with ctx.run_in_background() and return right away."""
        return "Not implemented"

    # --- settings page ------------------------------------------------------
    def status(self, ctx) -> str:
        """A short line shown under the plugin in Settings, e.g. "Connected as Ivo"."""
        return ""

    # --- web callback -------------------------------------------------------
    def handle_web(self, ctx, path, params):
        """Answer GET /api/plugins/<id>/web/<path> (ctx.web_url(path) builds the full address).
        `params` is a dict of the query string. Return a message (shown on a small page with a
        link back to the app) or OpenUrl to redirect."""
        return None

    # --- reader pages and books ---------------------------------------------
    def page_overlay(self, ctx, book, page):
        """Boxes to draw over page `page` (0-based) of `book`, asked for while the owner reads it
        with the plugin's overlay switched on. Return an Overlay, or None for nothing. Called as
        pages are shown, so keep it fast: read what was saved (ctx.get_page_data), and do slow work
        in a thread (ctx.run_in_thread) while returning Overlay(pending=True)."""
        return None

    def book_badges(self, ctx, books) -> dict:
        """Short labels for the books in a series list, e.g. {12: "Translated", 13: "4/20"}.
        Called once for the whole list; keep it fast (no website calls)."""
        return {}

    def run_book_action(self, ctx, action_key, book):
        """Run a button from `book_actions` for the book open in the reader. Return a short
        message, or OpenUrl."""
        return "Not implemented"

    # --- metadata provider (phase 2) --------------------------------------
    def search(self, ctx, query, year=None) -> list[Candidate]:
        return []

    def fetch(self, ctx, provider_id) -> dict:
        """Return a dict of series fields (title_english, description, ...)."""
        return {}


def implements(plugin, hook):
    """True when the plugin overrides `hook` (the base class versions do nothing)."""
    fn = getattr(plugin, hook, None)
    return fn is not None and getattr(fn, "__func__", None) is not getattr(Plugin, hook, None)


# what a plugin can set besides css; a plugin with css and none of these is a theme
_FEATURES = ("fields", "actions", "choices", "settings_fields", "global_actions", "book_actions",
             "overlay_label", "router", "js", "html", "middleware", "is_provider", "mcp_tools", "reads",
             "provides")
# the hooks that do something, so a theme overriding one isn't only a theme. status() only shows a line,
# and search()/fetch() count only for providers (is_provider is in _FEATURES)
_HOOKS = tuple(k for k, v in vars(Plugin).items()
               if callable(v) and not k.startswith("_") and k not in ("status", "search", "fetch"))


def categories_of(plugin) -> list[str]:
    """The plugin's categories, main one first: what it says (unknown names are dropped), else worked
    out from what it uses: a provider is metadata, an overlay or reader buttons are reader, only css
    is appearance, anything else is other."""
    said = plugin.category
    said = [said] if isinstance(said, str) else list(said or [])
    found = list(dict.fromkeys(c.strip().lower() for c in said if isinstance(c, str) and c.strip()))
    known = [c for c in found if c in CATEGORIES]
    if known:
        return known
    if found:
        log.warning("Plugin %s: unknown category %s (one of %s)", plugin.id, found, ", ".join(CATEGORIES))
    if plugin.is_provider:
        return ["metadata"]
    if plugin.overlay_label or plugin.book_actions or plugin.reads:
        return ["reader"]
    if plugin.css and not any(getattr(plugin, k) for k in _FEATURES) \
            and not any(implements(plugin, h) for h in _HOOKS):
        return ["appearance"]
    return ["other"]


class Context:
    """What plugins may use. Read-only for library files."""

    def __init__(self, plugin, db):
        from app.metadata import http
        self.plugin = plugin
        self.db = db
        self.log = logging.getLogger(f"mangavibe.plugin.{plugin.id}")
        self.http = http  # http.request/get_json/...: throttled, retries; raises http.ProviderError

    def web_url(self, path=""):
        """Full address of this plugin's handle_web() page as the browser reaches the app
        (taken from the last request), e.g. for an OAuth redirect address."""
        return f"{manager.base_url}/api/plugins/{self.plugin.id}/web/{path.lstrip('/')}"

    def run_in_background(self, fn, *args):
        """Run fn(ctx, *args) later on the plugin worker, with its own database session that
        is committed afterwards. Pass ids, not database rows."""
        manager.submit(self.plugin, fn, args)

    # reading status: what the app shows (manual choice, else worked out from progress)
    def reading_direction(self, series):
        """paged or vertical (webtoons): the way the reader shows the series (its own setting, its
        library's default, then Settings → Reader). Paged always turns forward to the right. Plugin API 5."""
        from app.direction import reading_direction
        return reading_direction(self.db, series)[0]

    def reading_status(self, series):
        from app.api.common import derived_status, series_counts
        return derived_status(series, series_counts(self.db, [series.id])[series.id])

    def set_reading_status(self, series, status):
        """Set the series' status by hand (None = work it out from progress again).
        Does not fire on_series_status_changed, so a sync can't loop back to itself."""
        from app.api.common import READING_STATUSES
        if status is not None and status not in READING_STATUSES:
            raise ValueError(f"unknown reading status {status!r}")
        series.reading_status = status

    # settings stored in the DB under "plugin:<id>"
    @property
    def settings(self) -> dict:
        from app.db import get_setting
        return get_setting(self.db, f"plugin:{self.plugin.id}", {}) or {}

    def setting(self, key):
        """One saved setting, or its SettingDef default while nothing is saved."""
        value = self.settings.get(key)
        if value in (None, ""):
            value = next((f.default for f in self.plugin.settings_fields if f.key == key), None)
        return value

    def save_settings(self, values: dict):
        from app.db import set_setting
        set_setting(self.db, f"plugin:{self.plugin.id}", values)

    def set_field(self, series, key, value):
        from app.models import PluginField
        labels = {f.key: f.label for f in self.plugin.fields}
        row = self.db.query(PluginField).filter_by(series_id=series.id, plugin=self.plugin.id,
                                                   key=key).one_or_none()
        if value is None:
            if row:
                self.db.delete(row)
            return
        if row is None:
            row = PluginField(series_id=series.id, plugin=self.plugin.id, key=key)
            self.db.add(row)
        row.label = labels.get(key, key)
        row.value = value

    def get_field(self, series, key, default=None):
        from app.models import PluginField
        row = self.db.query(PluginField).filter_by(series_id=series.id, plugin=self.plugin.id,
                                                   key=key).one_or_none()
        return default if row is None else row.value

    def list_pages(self, book):
        from app.scanner import archives
        return archives.list_pages(book.path, book.format)

    def read_entry(self, book, name) -> bytes:
        """Read any entry inside a book, e.g. 'ComicInfo.xml'."""
        from app.scanner import archives
        return archives.read_entry(book.path, book.format, name)

    def list_entries(self, book):
        """All entry names inside a book (not only images)."""
        from app.scanner import archives
        return archives.list_entries(book.path, book.format)

    def text_sections(self, book):
        """The sections of a text book (format "epub-text"): its spine documents in reading order,
        entry names for read_entry(). Section n is page n of the book's progress. Plugin API 14."""
        from app.scanner import archives
        return archives.text_sections(book.path)

    def read_series_file(self, series, name) -> bytes | None:
        """Read a sidecar file in the series folder (e.g. 'series.json'), or None."""
        root = Path(series.folder_path).resolve()
        p = (root / name).resolve()
        if root not in p.parents or not p.is_file():
            return None
        return p.read_bytes()

    # --- page images and per-page data ---------------------------------------
    def read_page(self, book, index, max_width=None) -> bytes:
        """Page `index` (0-based) as image bytes, for every book format (PDF and EPUB pages come
        rendered). With `max_width`, larger pages are shrunk to that width and returned as WebP,
        e.g. to send less over the network; pages that already fit come back untouched (each
        re-encoding blurs a little, which text detection notices). Raises IndexError for a page that
        doesn't exist."""
        import io

        from PIL import Image

        from app import images
        from app.scanner import archives
        data, mime = archives.read_page(book.path, book.format, index)
        if max_width and mime in archives.WEB_MIME:
            with Image.open(io.BytesIO(data)) as im:  # reads only the header
                if im.width <= max_width:
                    return data
        if max_width or mime not in archives.WEB_MIME:
            data = images.resize(data, int(max_width or 100_000), quality=92)
        return data

    def _page_rows(self, book):
        from app.models import PluginPage
        return self.db.query(PluginPage).filter_by(plugin=self.plugin.id, book_id=book.id)

    def set_page_data(self, book, page, value):
        """Save a value (anything JSON can hold) for one page; None removes it."""
        from app.models import PluginPage, now
        row = self._page_rows(book).filter_by(page=page).one_or_none()
        if value is None:
            if row:
                self.db.delete(row)
            return
        if row is None:
            row = PluginPage(plugin=self.plugin.id, book_id=book.id, page=page)
            self.db.add(row)
        row.value, row.updated_at = value, now()

    def get_page_data(self, book, page, default=None):
        row = self._page_rows(book).filter_by(page=page).one_or_none()
        return default if row is None else row.value

    def page_data(self, book) -> dict:
        """All saved pages of a book: {page: value}."""
        return {r.page: r.value for r in self._page_rows(book)}

    def page_data_counts(self, books) -> dict:
        """How many pages have saved data, per book: {book_id: count} (books with none are left out).
        One query for a whole list, e.g. for book_badges()."""
        from sqlalchemy import func
        from app.models import PluginPage
        ids = [b.id for b in books]
        if not ids:
            return {}
        rows = (self.db.query(PluginPage.book_id, func.count(PluginPage.id))
                .filter(PluginPage.plugin == self.plugin.id, PluginPage.book_id.in_(ids))
                .group_by(PluginPage.book_id))
        return dict(rows.all())

    def clear_page_data(self, book=None):
        """Remove the saved pages of one book, or of every book with book=None."""
        from app.models import PluginPage
        q = self.db.query(PluginPage).filter_by(plugin=self.plugin.id)
        if book is not None:
            q = q.filter_by(book_id=book.id)
        q.delete(synchronize_session=False)

    # --- per-series data (never shown in the app) --------------------------------
    def _series_rows(self, series):
        from app.models import PluginSeriesData
        series_id = getattr(series, "id", series)  # a Series or its id (e.g. book.series_id)
        return self.db.query(PluginSeriesData).filter_by(plugin=self.plugin.id, series_id=series_id)

    def set_series_data(self, series, key, value):
        """Save a value (anything JSON can hold) under `key` for a series (a Series or its id);
        None removes it. For the plugin's own use: unlike set_field(), nothing is shown."""
        from app.models import PluginSeriesData, now
        row = self._series_rows(series).filter_by(key=key).one_or_none()
        if value is None:
            if row:
                self.db.delete(row)
            return
        if row is None:
            row = PluginSeriesData(plugin=self.plugin.id, series_id=getattr(series, "id", series), key=key)
            self.db.add(row)
        row.value, row.updated_at = value, now()

    def get_series_data(self, series, key, default=None):
        row = self._series_rows(series).filter_by(key=key).one_or_none()
        return default if row is None else row.value

    def series_data(self, series) -> dict:
        """All saved values of a series: {key: value}."""
        return {r.key: r.value for r in self._series_rows(series)}

    def clear_series_data(self, series, key=None):
        """Remove one key of a series, or all of them with key=None."""
        q = self._series_rows(series)
        if key is not None:
            q = q.filter_by(key=key)
        q.delete(synchronize_session=False)

    # --- a worker thread of the plugin's own -----------------------------------
    def run_in_thread(self, fn, *args):
        """Run fn(ctx, *args) in a new thread of its own, with its own database session (committed
        when fn returns; call ctx.db.commit() yourself to save along the way). Unlike
        run_in_background(), it doesn't wait behind other plugins' work, so it suits a queue that
        runs for hours. The loop must check ctx.should_stop() and return when it's True: the plugin
        was reloaded, updated or switched off. Pass ids, not database rows."""
        stop = manager.stop_event(self.plugin)

        def run():
            from app.db import SessionLocal
            with SessionLocal() as db:
                ctx = Context(self.plugin, db)
                ctx._stop = stop
                try:
                    fn(ctx, *args)
                    db.commit()
                except Exception:
                    db.rollback()
                    log.error("Plugin %s failed in its thread:\n%s", self.plugin.id, traceback.format_exc())

        t = threading.Thread(target=run, daemon=True, name=f"plugin-{self.plugin.id}")
        t.start()
        return t

    def should_stop(self) -> bool:
        """True once the plugin was reloaded, updated or switched off: a thread should return."""
        return (self._stop or manager.stop_event(self.plugin)).is_set()

    _stop = None


class PluginManager:
    def __init__(self):
        self.plugins: dict[str, Plugin] = {}
        self.errors: dict[str, str] = {}   # file name -> error text
        self.paths: dict[str, Path] = {}   # plugin id -> the file it was loaded from
        self.categories: dict[str, list[str]] = {}  # plugin id -> categories_of(), worked out at load
        self.disabled: set[str] = set()
        self.moved: dict[str, dict] = {}  # built-in plugins someone used that moved to a repo of their own
        # exclusive job -> the plugin the owner switched on for it last (jobs_chosen): it wins if two are on
        self.chosen: dict[str, str] = {}
        self._blocked_key, self._blocked = None, {}
        self.base_url = "http://localhost:8080"  # updated from each request (see main.py)
        self._queue = queue.Queue()
        self._worker = None
        self._lock = threading.Lock()
        self._stops: dict[int, threading.Event] = {}  # id(plugin object) -> its threads' stop signal
        self._load_lock = threading.RLock()  # load() empties and refills the dicts: see installed()

    def installed(self) -> dict[str, tuple[str, bool]]:
        """id -> (version, built in) of every loaded plugin, read while no load() is half done (a
        request in another thread may be reloading the plugins)."""
        with self._load_lock:
            return {pid: (p.version, self.is_builtin(pid)) for pid, p in self.plugins.items()}

    def stop_event(self, plugin) -> threading.Event:
        """The signal the plugin's current threads (ctx.run_in_thread) watch."""
        with self._lock:
            return self._stops.setdefault(id(plugin), threading.Event())

    def stop_threads(self, plugin):
        """Tell the plugin's threads to finish. Threads it starts later get a new signal."""
        with self._lock:
            event = self._stops.pop(id(plugin), None)
        if event:
            event.set()

    def set_enabled(self, plugin_id, enabled: bool):
        if enabled:
            self.disabled.discard(plugin_id)
        else:
            self.disabled.add(plugin_id)
            if plugin_id in self.plugins:
                self.stop_threads(self.plugins[plugin_id])

    def load(self, folders=None):
        with self._load_lock:
            self._load(folders)

    def _load(self, folders=None):
        for p in self.plugins.values():  # the old plugin objects are about to be replaced
            self.stop_threads(p)
        self.plugins.clear()
        self.errors.clear()
        self.paths.clear()
        self.categories.clear()
        folders = folders if folders is not None else [BUILTIN, config.PLUGINS_DIR]
        for folder in folders:
            folder = Path(folder)
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.name.startswith(("_", ".")):
                    continue
                if path.is_dir() and (path / "__init__.py").exists():
                    path = path / "__init__.py"
                elif path.suffix != ".py":
                    continue
                self._load_file(path)
        log.info("Plugins loaded: %s", ", ".join(self.plugins) or "none")

    def _load_file(self, path: Path):
        stem = path.parent.name if path.name == "__init__.py" else path.stem
        package = path.parent.parent if path.name == "__init__.py" else path.parent
        try:
            if package == BUILTIN:
                # the app's own: imported under their real name (app.plugins.builtin.<name>), once, so a
                # plugin folder's modules and their state exist one time, also after Reload plugins
                mod_name = f"app.plugins.builtin.{stem}"
                module = importlib.import_module(mod_name)
            else:
                mod_name = f"mangavibe_plugin_{stem}"
                folder = [str(path.parent)] if path.name == "__init__.py" else None  # relative imports
                # a folder plugin's own modules (its core.py, api.py...) are loaded fresh too: left in
                # sys.modules, `from . import core` would keep the old code after a reload or update
                for name in [m for m in sys.modules if m.startswith(mod_name + ".")]:
                    del sys.modules[name]
                spec = importlib.util.spec_from_file_location(mod_name, path, submodule_search_locations=folder)
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            found = [v for v in vars(module).values()
                     if isinstance(v, type) and issubclass(v, Plugin) and v is not Plugin
                     and v.__module__ == mod_name]
            for cls in found:
                p = cls()
                if not p.id:
                    raise ValueError(f"{cls.__name__} has no id")
                if p.id in self.plugins:
                    raise ValueError(f"duplicate plugin id '{p.id}'")
                self.plugins[p.id] = p
                self.paths[p.id] = path
                self.categories[p.id] = categories_of(p)
        except Exception as e:  # a broken plugin must never stop the app
            self.errors[path.name] = f"{type(e).__name__}: {e}"
            log.error("Could not load plugin %s:\n%s", path, traceback.format_exc())

    def is_builtin(self, plugin_id) -> bool:
        """True for the plugins that come with the app (app/plugins/builtin/)."""
        path = self.paths.get(plugin_id)
        return path is not None and BUILTIN in path.parents

    def categories_of(self, plugin) -> list[str]:
        return self.categories.get(plugin.id) or categories_of(plugin)

    def enabled(self):
        """The plugins that run: switched on, and not blocked (see blocked)."""
        blocked = self.blocked
        return [p for pid, p in self.plugins.items() if pid not in self.disabled and pid not in blocked]

    def switched_on(self):
        """The plugins switched on, including blocked ones."""
        return [p for pid, p in self.plugins.items() if pid not in self.disabled]

    # --- jobs (Plugin API 15, app/contracts) ---------------------------------
    @property
    def blocked(self) -> dict[str, str]:
        """id -> why a switched-on plugin doesn't run: a job or contract version this MangaVibe doesn't
        have, a need no running plugin meets, or another plugin doing its exclusive job. Worked out
        again whenever the plugins, the switches or the jobs change (tests change `disabled` directly)."""
        from app.contracts import JOBS
        key = (tuple((pid, id(p)) for pid, p in self.plugins.items()), frozenset(self.disabled),
               tuple(self.chosen.items()), tuple((k, j.versions) for k, j in JOBS.items()))
        if key != self._blocked_key:
            self._blocked, self._blocked_key = self._work_out_blocked(), key
        return self._blocked

    def _work_out_blocked(self) -> dict[str, str]:
        from app.contracts import job_label
        on = self.switched_on()
        if not any(p.provides or p.requires for p in on):
            return {}  # no plugin uses jobs: nothing to do (the usual case before #204)
        blocked = {p.id: why for p in on if (why := job_problem(p))}
        # two plugins on for one exclusive job (Settings switches the other off, so only after a manual
        # change): the one the owner picked last, else one of the owner's own, else the first
        for job in {j for p in on for j in exclusive_jobs(p)}:
            doing = [p for p in on if p.id not in blocked and job in exclusive_jobs(p)]
            if len(doing) > 1:
                keep = self._winner(job, doing)
                for p in doing:
                    if p is not keep:
                        blocked[p.id] = f"{keep.name} does {job_label(job)} now"
        # needs: blocking one plugin can leave another without what it needs, so until nothing changes
        changed = True
        while changed:
            changed = False
            running = [p for p in on if p.id not in blocked]
            for p in running:
                for job, v in plugin_jobs(p, "requires").items():
                    if not any(q is not p and q.id not in blocked and plugin_jobs(q, "provides").get(job) == v
                               for q in running):
                        blocked[p.id] = f"Needs a plugin that does {job_label(job)} (v{v}): switch one on"
                        changed = True
                        break
        return blocked

    def _winner(self, job, doing):
        chosen = next((p for p in doing if p.id == self.chosen.get(job)), None)
        return chosen or next((p for p in doing if not self.is_builtin(p.id)), doing[0])

    def provider_of(self, job):
        """The running plugin doing this job, or None. For a shared job, the first of them."""
        return next((p for p in self.enabled() if job in plugin_jobs(p, "provides")), None)

    def builtin_for(self, job):
        """The built-in plugin for this job, on or off: what the app falls back to when the plugin doing
        it fails (the hub's rule, issue #202). None if no built-in plugin does it."""
        return next((p for pid, p in self.plugins.items()
                     if self.is_builtin(pid) and job in plugin_jobs(p, "provides")), None)

    def doing(self, job):
        """Who to ask for this job, in order: the running plugin doing it, then the built-in one as the
        fallback (if that's another plugin)."""
        out = [p for p in (self.provider_of(job), self.builtin_for(job)) if p is not None]
        return list(dict.fromkeys(out))

    def providers_of(self, job):
        """Every running plugin doing this job."""
        return [p for p in self.enabled() if job in plugin_jobs(p, "provides")]

    def jobs_info(self) -> list[dict]:
        """The jobs, for Settings -> Plugins: which plugins can do each one and which do it now."""
        from app.contracts import JOBS
        running = {p.id for p in self.enabled()}
        out = []
        for key, job in JOBS.items():
            can = [pid for pid, p in self.plugins.items() if key in plugin_jobs(p, "provides")]
            out.append({"key": key, "label": job.label, "description": job.description,
                        "exclusive": job.exclusive, "required": job.required,
                        "versions": list(job.versions), "plugins": can,
                        "doing": [pid for pid in can if pid in running]})
        return out

    def switch(self, db, plugin_id, on: bool) -> dict:
        """Switch a plugin on or off, as Settings does, and save it. On: the other plugins doing one of
        its exclusive jobs are switched off (after their can_switch_off agrees). Off: refused while
        the plugin says so (can_switch_off), or while it's the only one doing a job the app needs.
        Raises SwitchRefused. Returns {"started": [...], "stopped": [...]}: the plugins that run, or
        stopped running, because of it (a plugin whose need is met now starts too), each started once."""
        from app.contracts import JOBS, job_label
        from app.db import set_setting
        p = self.plugins.get(plugin_id)
        if p is None:
            raise KeyError(plugin_id)
        before = {q.id for q in self.enabled()}
        off = set()
        if on:
            for job in exclusive_jobs(p):
                for q in self.switched_on():
                    if q is not p and job in exclusive_jobs(q):
                        self._check_can_switch_off(db, q, f"{p.name} can't replace {q.name}")
                        off.add(q.id)
                self.chosen[job] = p.id
        elif plugin_id not in self.disabled:
            self._check_can_switch_off(db, p, None)
            for job in plugin_jobs(p, "provides"):
                if job in JOBS and JOBS[job].required and plugin_id in before \
                        and not any(q.id != plugin_id for q in self.providers_of(job)):
                    raise SwitchRefused(f"{p.name} does {job_label(job)}, which MangaVibe needs. "
                                        "Switch on another plugin that does it instead")
        for qid in off:
            self.set_enabled(qid, False)
        self.set_enabled(plugin_id, on)
        set_setting(db, "plugins_disabled", sorted(self.disabled))
        set_setting(db, "jobs_chosen", self.chosen)
        db.commit()
        after = {q.id for q in self.enabled()}
        for qid in before - after:
            self.stop_threads(self.plugins[qid])
        started = [q for q in self.enabled() if q.id in after - before]
        for q in started:
            self.start(db, q)
        return {"started": [q.id for q in started], "stopped": sorted(before - after)}

    def _check_can_switch_off(self, db, p, prefix):
        if p.id in self.disabled or not implements(p, "can_switch_off"):
            return
        reason = p.can_switch_off(Context(p, db))
        if reason:
            raise SwitchRefused(f"{prefix}: {reason}" if prefix else reason)

    def providers(self):
        return [p for p in self.enabled() if p.is_provider]

    def reader_for(self, book_format):
        """The enabled plugin that reads books of this format (its `reads`), or None. Plugin API 14."""
        return next((p for p in self.enabled() if book_format in (p.reads or ())), None)

    def get(self, plugin_id):
        """An enabled plugin by id, or None."""
        p = self.plugins.get(plugin_id)
        return p if p is not None and plugin_id not in self.disabled and plugin_id not in self.blocked else None

    def load_switches(self, db):
        """Which plugins are switched off (plugins_disabled). A plugin seen for the first time that
        isn't enabled_by_default starts off (plugins_known remembers which were seen)."""
        from app.db import get_setting, set_setting
        self.disabled = set(get_setting(db, "plugins_disabled", []) or [])
        chosen = get_setting(db, "jobs_chosen", {}) or {}
        self.chosen = {k: v for k, v in chosen.items() if isinstance(k, str) and isinstance(v, str)} \
            if isinstance(chosen, dict) else {}
        known = set(get_setting(db, "plugins_known", []) or [])
        self._forget_moved(db, known)
        new = [pid for pid in self.plugins if pid not in known]
        changed = False
        if new:
            self.disabled |= {pid for pid in new if not self.plugins[pid].enabled_by_default}
            # a new plugin for an exclusive job someone already does starts off: installing a replacement
            # never takes over by itself, the owner picks it. The known ones first, then the new built-in
            # ones (a fresh install gets the app's own), then the others
            taken = {j for p in self.switched_on() if p.id not in new for j in exclusive_jobs(p)}
            for pid in sorted(new, key=lambda i: not self.is_builtin(i)):
                if pid in self.disabled:
                    continue
                jobs = exclusive_jobs(self.plugins[pid])
                if any(j in taken for j in jobs):
                    self.disabled.add(pid)
                taken |= set(jobs)
            set_setting(db, "plugins_known", sorted(known | set(new)))
            changed = True
        changed |= self._settle_jobs()
        if changed:
            set_setting(db, "plugins_disabled", sorted(self.disabled))
            db.commit()

    def _settle_jobs(self) -> bool:
        """Two plugins switched on for one exclusive job (e.g. both on before they did that job): the
        other one is switched off, like Settings does. True if something changed."""
        changed = False
        for job in {j for p in self.switched_on() for j in exclusive_jobs(p)}:
            doing = [p for p in self.switched_on() if job in exclusive_jobs(p) and not job_problem(p)]
            if len(doing) > 1:
                keep = self._winner(job, doing)
                for p in doing:
                    if p is not keep:
                        log.info("Plugin %s switched off: %s does the job %s", p.id, keep.id, job)
                        self.disabled.add(p.id)
                        changed = True
        return changed

    def _forget_moved(self, db, known: set):
        """A built-in plugin that moved to its own repo (MOVED) is gone after the update. Forget it was
        seen and switch it off, so installing it later starts it off like any new plugin (old keys
        don't come back to life on their own); and remember to tell the owner where it went
        (plugins_moved, shown in Settings -> Plugins until it is installed again)."""
        from app.db import get_setting, set_setting
        moved = set(get_setting(db, "plugins_moved", []) or [])
        gone = {pid for pid in MOVED if pid in known and pid not in self.plugins}
        if gone:
            moved |= {pid for pid in gone if pid not in self.disabled}  # only those someone used
            known -= gone
            self.disabled |= gone
            set_setting(db, "plugins_known", sorted(known))
            set_setting(db, "plugins_disabled", sorted(self.disabled))
        back = {pid for pid in moved if pid in self.plugins}
        if gone or back:
            set_setting(db, "plugins_moved", sorted(moved - back))
            db.commit()
        self.moved = {pid: MOVED[pid] for pid in sorted(moved - back) if pid in MOVED}

    def series_conditions(self, db, request) -> list:
        """Every enabled plugin's series_filter() for this request. Errors are not caught: a request
        whose filter fails fails too, instead of showing what a plugin hides."""
        return [cond for p in self.enabled() if implements(p, "series_filter")
                for cond in p.series_filter(Context(p, db), request)]

    def find_user(self, db, user_id):
        """Who user `user_id` is, from the first enabled plugin that knows (find_user()), else the
        owner for id 1 (the app without accounts), else None."""
        from app import users
        for p in self.enabled():
            if implements(p, "find_user"):
                return p.find_user(Context(p, db), user_id)
        return users.OWNER if user_id == users.OWNER.id else None

    def reading_mode(self, db, series):
        """(mode, why) from the first enabled plugin with a default for this series, else None."""
        for p in self.enabled():
            if not implements(p, "reading_mode"):
                continue
            try:
                found = p.reading_mode(Context(p, db), series)
            except Exception:
                log.error("Plugin %s failed in reading_mode:\n%s", p.id, traceback.format_exc())
                continue
            if found:
                return found
        return None

    def start(self, db, plugin=None):
        """Run on_start for one plugin, or every enabled one, and commit what they did."""
        for p in [plugin] if plugin else self.enabled():
            if implements(p, "on_start"):
                try:
                    p.on_start(Context(p, db))
                    db.commit()
                except Exception:
                    db.rollback()
                    log.error("Plugin %s failed in on_start:\n%s", p.id, traceback.format_exc())

    def run_hook(self, db, hook, *args):
        """Call a hook on every enabled plugin; errors are logged, not raised."""
        for p in self.enabled():
            if not implements(p, hook):
                continue
            try:
                getattr(p, hook)(Context(p, db), *args)
            except Exception:
                log.error("Plugin %s failed in %s:\n%s", p.id, hook, traceback.format_exc())


    # --- background worker for reading events and run_in_background() ------
    def emit(self, event, *args):
        """Queue a reading event: (book_id) for book events, (series_id, old, new) for status.
        The worker loads fresh rows. Costs nothing when no plugin listens."""
        if any(implements(p, event) for p in self.enabled()):
            self._put(("event", event, args))

    def submit(self, plugin, fn, args):
        self._put(("job", plugin, fn, args))

    def _put(self, item):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, daemon=True, name="plugin-events")
                self._worker.start()
        self._queue.put(item)

    def wait_idle(self):
        """Block until every queued event and job has run (used by tests)."""
        self._queue.join()

    def _work(self):
        while True:
            item = self._queue.get()
            try:
                if item[0] == "event":
                    self._run_event(item[1], item[2])
                else:
                    self._run_job(*item[1:])
            except Exception:
                log.error("Plugin worker failed:\n%s", traceback.format_exc())
            finally:
                self._queue.task_done()

    def _run_event(self, event, args):
        from app.db import SessionLocal
        from app.models import Book, Series
        for p in self.enabled():
            if not implements(p, event):
                continue
            with SessionLocal() as db:
                if event == "on_series_status_changed":
                    series_id, old, new = args
                    series = db.get(Series, series_id)
                    if series is None:
                        return
                    hook_args = (series, old, new)
                else:
                    book = db.get(Book, args[0])
                    if book is None:
                        return
                    hook_args = (book, db.get(Series, book.series_id))
                try:
                    getattr(p, event)(Context(p, db), *hook_args)
                    db.commit()
                except Exception:
                    db.rollback()
                    log.error("Plugin %s failed in %s:\n%s", p.id, event, traceback.format_exc())

    def _run_job(self, plugin, fn, args):
        from app.db import SessionLocal
        with SessionLocal() as db:
            try:
                fn(Context(plugin, db), *args)
                db.commit()
            except Exception:
                db.rollback()
                log.error("Plugin %s failed in a background job:\n%s", plugin.id, traceback.format_exc())


class SwitchRefused(Exception):
    """A plugin can't be switched on or off now; the message says why (shown to the owner)."""


def plugin_jobs(plugin, attr) -> dict[str, int]:
    """The plugin's provides or requires as {job: version}, {} when they're set up wrong (it's
    blocked for that, see PluginManager.blocked)."""
    from app.contracts import jobs_of
    try:
        return jobs_of(getattr(plugin, attr, None))
    except ValueError:
        return {}


def exclusive_jobs(plugin) -> list[str]:
    """The exclusive jobs this plugin does (app/contracts)."""
    from app.contracts import JOBS
    return [k for k in plugin_jobs(plugin, "provides") if k in JOBS and JOBS[k].exclusive]


def job_problem(plugin) -> str:
    """Why this plugin can't run in this MangaVibe, whatever else is on: a job or contract version it
    doesn't have. "" when there's none."""
    from app.contracts import JOBS, job_label, jobs_of
    try:
        provides, requires = jobs_of(plugin.provides), jobs_of(plugin.requires)
    except ValueError as e:
        return f"Its jobs are set up wrong: {e}"
    for what, jobs in (("does", provides), ("needs", requires)):
        for key, v in jobs.items():
            job = JOBS.get(key)
            if job is None:
                return f"Made for another MangaVibe: it {what} “{key}”, which this version doesn't have"
            if v not in job.versions:
                newer = "a newer" if v > max(job.versions) else "an older"
                have = ", ".join(f"v{x}" for x in job.versions)
                return f"Made for {newer} MangaVibe: it {what} {job_label(key)} v{v}; this version has {have}"
    return ""


def action_result(result):
    """What an action returned -> the JSON the web app expects."""
    if isinstance(result, OpenUrl):
        return {"message": result.message, "open_url": result.url}
    return {"message": str(result or "Done")}


manager = PluginManager()
