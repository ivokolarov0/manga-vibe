"""The public SDK (Plugin API 22, issue #209): built-in plugins use only what a plugin of anyone's can, so a
replacement can always do what the built-in one does. And data a replacement needs is the job's: saved
searches live in jobs:saved-searches, moved there by a migration.
"""
import ast
import re
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db import MIGRATIONS

ROOT = Path(__file__).resolve().parents[2]
BUILTIN = ROOT / "app" / "plugins" / "builtin"

# What a plugin may import from MangaVibe (docs/PLUGINS.md, The SDK)
SDK = ("app.plugins", "app.plugins.web", "app.plugins.query", "app.plugins.sdk", "app.users")

# Imports outside the SDK that a built-in plugin still has, with the reason. This list only shrinks.
EXCEPTIONS = {
    ("optimize/core.py", "app.config"): "it rewrites library files: the cached pages it clears (PAGES_DIR)",
    ("optimize/core.py", "app.scanner.archives"): "it rewrites library files: reads and repacks archives",
    ("optimize/core.py", "app.scanner.walker"): "it rewrites library files: waits for the scanner's lock",
    ("optimize/core.py", "app.scanner.deleting"): "it rewrites library files: the read-only mount check",
    ("libraries/__init__.py", "app.config"): "CLEAR_LIBRARY_PASSWORD, the env setting to unlock a library",
    ("libraries/core.py", "app.db"): "its command line (clear-password) opens the database itself",
}


def _is_module(name):
    p = ROOT.joinpath(*name.split("."))
    return p.with_suffix(".py").is_file() or (p / "__init__.py").is_file()


def _imports(path):
    """Every app.* module the file imports, as (module, line)."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            # from app.scanner import archives: the module archives, not app.scanner
            mods = [f"{node.module}.{a.name}" if _is_module(f"{node.module}.{a.name}") else node.module
                    for a in node.names]
        elif isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        else:
            continue
        for m in mods:
            if m == "app" or m.startswith("app."):
                yield m, node.lineno


def test_built_in_plugins_import_only_the_sdk():
    found, wrong = set(), []
    for path in sorted(BUILTIN.rglob("*.py")):
        rel = path.relative_to(BUILTIN).as_posix()
        own = "app.plugins.builtin." + rel.split("/")[0].removesuffix(".py")
        for mod, line in _imports(path):
            if mod in SDK or mod == own or mod.startswith(own + "."):
                continue
            if (rel, mod) in EXCEPTIONS:
                found.add((rel, mod))
                continue
            wrong.append(f"{rel}:{line} imports {mod}")
    assert not wrong, "Built-in plugins import from outside the SDK:\n" + "\n".join(wrong)
    # an exception no longer needed comes off the list
    assert found == set(EXCEPTIONS), f"Not needed any more: {set(EXCEPTIONS) - found}"


def test_sdk_names_exist():
    import app.plugins.sdk as sdk
    assert all(hasattr(sdk, n) for n in sdk.__all__)


def _page_globals():
    """The top-level functions and variables of the app's own scripts (common.js, app.js, reader.js)."""
    names = set()
    for f in (ROOT / "app" / "web" / "static" / "js").glob("*.js"):
        t = f.read_text(encoding="utf-8")
        names |= set(re.findall(r"^(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)", t, re.M))
        names |= set(re.findall(r"^(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)", t, re.M))
    return names - {"MangaVibe"}


def _code_only(js):
    """The script without comments and strings, so a word in a URL or a message doesn't count."""
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    js = re.sub(r"`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", '""', js)
    return re.sub(r"(?m)//.*$", " ", js)


def test_built_in_scripts_use_only_window_mangavibe():
    names, wrong = _page_globals(), []
    assert {"api", "toast"} <= names  # the check sees the app's globals
    for path in sorted(BUILTIN.rglob("*.js")):
        code = _code_only(path.read_text(encoding="utf-8"))
        for n in names:
            # a use, not a property (x.n), an object key (n: ...) or a method of its own (n() {)
            for m in re.finditer(r"(?<![\w$.])" + re.escape(n) + r"(?![\w$])(?!\s*:)(?!\s*\([^()]*\)\s*\{)", code):
                wrong.append(f"{path.relative_to(BUILTIN).as_posix()}: {n}")
                break
    assert not wrong, "Built-in scripts use app.js globals instead of window.MangaVibe:\n" + "\n".join(wrong)


def test_saved_searches_move_to_the_job():
    """Migration 18 keeps every person's list: the owner's and each account's."""
    engine = create_engine("sqlite://")
    lists = {"saved_searches": [{"id": 1, "name": "Isekai", "query": "genre=Isekai"}],
             "user:2:saved_searches": [{"id": 1, "name": "Mine", "query": "sort=year"}],
             "user:3:saved_searches": []}
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE setting (key VARCHAR PRIMARY KEY, value JSON)"))
        for k, v in {**lists, "user:2:reader": "x", "other": 1}.items():
            conn.execute(text("INSERT INTO setting VALUES (:k, json(:v))"), {"k": k, "v": repr(v).replace("'", '"')})
        MIGRATIONS[17](conn)
        rows = dict(conn.execute(text("SELECT key, value FROM setting")).fetchall())
    assert set(rows) == {"jobs:saved-searches", "user:2:jobs:saved-searches", "user:3:jobs:saved-searches",
                         "user:2:reader", "other"}
    assert '"Isekai"' in rows["jobs:saved-searches"] and '"Mine"' in rows["user:2:jobs:saved-searches"]


def test_saved_searches_is_the_jobs_data(client):
    from app.contracts import JOBS
    from app.db import SessionLocal, get_setting
    assert JOBS["saved-searches"].exclusive is False
    saved = client.post("/api/plugins/saved-searches/api/searches", json={"name": "Job", "query": "genre=Job"})
    assert saved.status_code == 200, saved.text
    with SessionLocal() as db:
        assert any(x["name"] == "Job" for x in get_setting(db, "jobs:saved-searches", []))
    client.delete(f"/api/plugins/saved-searches/api/searches/{saved.json()['id']}")
