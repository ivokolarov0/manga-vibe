"""Plugin system.

A plugin is a Python file (or package folder) in /data/plugins. It defines one or
more subclasses of `Plugin`. MangaVibe loads them on startup.

What a plugin can do:
- metadata provider: implement search() and fetch() (used by matching, phase 2)
- extra fields:       declare `fields` and call ctx.set_field(series, key, value)
- scan hooks:         on_book_scanned() can change parsed values; on_series_scanned()
- custom actions:     declare `actions` and implement run_action()
- styling:            set `css` to change how the app looks (see examples/plugins/sakura_theme.py)

Plugins get read-only access to books through `ctx`. They must never write to the
library (it is mounted read-only anyway); their data goes into the database.
See docs/PLUGINS.md and examples/plugins/.
"""
import importlib.util
import logging
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from app import config

log = logging.getLogger("mangavibe.plugins")
API_VERSION = 1


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
    is_provider = False  # True for metadata providers
    css = ""             # extra CSS for the web app (loaded after the built-in styles, so it wins)

    # --- scan hooks -------------------------------------------------------
    def on_book_scanned(self, ctx, book, parsed):
        """Called for each new/changed book. May change `parsed` (volume, chapter,
        chapter_end, extra, extra_index, version) to fix ordering."""

    def on_series_scanned(self, ctx, series, books):
        """Called after a series was scanned and ordered."""

    # --- custom actions ---------------------------------------------------
    def run_action(self, ctx, action_key, series):
        """Run a button action. Return a short message string shown to the owner."""
        return "Not implemented"

    # --- metadata provider (phase 2) --------------------------------------
    def search(self, ctx, query, year=None) -> list[Candidate]:
        return []

    def fetch(self, ctx, provider_id) -> dict:
        """Return a dict of series fields (title_english, description, ...)."""
        return {}


class Context:
    """What plugins may use. Read-only for library files."""

    def __init__(self, plugin, db):
        self.plugin = plugin
        self.db = db
        self.log = logging.getLogger(f"mangavibe.plugin.{plugin.id}")

    # settings stored in the DB under "plugin:<id>"
    @property
    def settings(self) -> dict:
        from app.db import get_setting
        return get_setting(self.db, f"plugin:{self.plugin.id}", {}) or {}

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
        self.disabled: set[str] = set()

    def load(self, folders=None):
        self.plugins.clear()
        self.errors.clear()
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
        except Exception as e:  # a broken plugin must never stop the app
            self.errors[path.name] = f"{type(e).__name__}: {e}"
            log.error("Could not load plugin %s:\n%s", path, traceback.format_exc())

    def enabled(self):
        return [p for pid, p in self.plugins.items() if pid not in self.disabled]

    def providers(self):
        return [p for p in self.enabled() if p.is_provider]

    def run_hook(self, db, hook, *args):
        """Call a hook on every enabled plugin; errors are logged, not raised."""
        for p in self.enabled():
            fn = getattr(p, hook)
            if getattr(fn, "__func__", None) is getattr(Plugin, hook):
                continue  # plugin doesn't implement it
            try:
                fn(Context(p, db), *args)
            except Exception:
                log.error("Plugin %s failed in %s:\n%s", p.id, hook, traceback.format_exc())


manager = PluginManager()
