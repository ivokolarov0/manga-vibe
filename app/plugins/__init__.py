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

Plugins get read-only access to books through `ctx`. They must never write to the
library (it is mounted read-only anyway); their data goes into the database.
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
API_VERSION = 2


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
    """A text box on the plugin's page (Settings -> Plugins -> Settings), or a dropdown when `options` is given
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

    # --- scan hooks -------------------------------------------------------
    def on_book_scanned(self, ctx, book, parsed):
        """Called for each new/changed book. May change `parsed` (volume, chapter,
        chapter_end, extra, extra_index, version) to fix ordering."""

    def on_series_scanned(self, ctx, series, books):
        """Called after a series was scanned and ordered."""

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

    def read_series_file(self, series, name) -> bytes | None:
        """Read a sidecar file in the series folder (e.g. 'series.json'), or None."""
        root = Path(series.folder_path).resolve()
        p = (root / name).resolve()
        if root not in p.parents or not p.is_file():
            return None
        return p.read_bytes()


class PluginManager:
    def __init__(self):
        self.plugins: dict[str, Plugin] = {}
        self.errors: dict[str, str] = {}   # file name -> error text
        self.paths: dict[str, Path] = {}   # plugin id -> the file it was loaded from
        self.disabled: set[str] = set()
        self.base_url = "http://localhost:8080"  # updated from each request (see main.py)
        self._queue = queue.Queue()
        self._worker = None
        self._lock = threading.Lock()

    def load(self, folders=None):
        self.plugins.clear()
        self.errors.clear()
        self.paths.clear()
        folders = folders if folders is not None else [Path(__file__).parent / "builtin",
                                                       config.PLUGINS_DIR]
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
        mod_name = f"mangavibe_plugin_{path.parent.name if path.name == '__init__.py' else path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
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
        except Exception as e:  # a broken plugin must never stop the app
            self.errors[path.name] = f"{type(e).__name__}: {e}"
            log.error("Could not load plugin %s:\n%s", path, traceback.format_exc())

    def enabled(self):
        return [p for pid, p in self.plugins.items() if pid not in self.disabled]

    def providers(self):
        return [p for p in self.enabled() if p.is_provider]

    def get(self, plugin_id):
        """An enabled plugin by id, or None."""
        p = self.plugins.get(plugin_id)
        return p if p is not None and plugin_id not in self.disabled else None

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


def action_result(result):
    """What an action returned -> the JSON the web app expects."""
    if isinstance(result, OpenUrl):
        return {"message": result.message, "open_url": result.url}
    return {"message": str(result or "Done")}


manager = PluginManager()
