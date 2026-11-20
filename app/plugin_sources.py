"""Plugin sources: lists of plugins to install with one button (Settings -> Plugins -> Get plugins).

A source is a JSON file anyone can publish (e.g. `plugins.json` in a GitHub repo):

    {"name": "My plugins",
     "plugins": [
       {"id": "tracker", "name": "Tracker", "description": "...", "category": "library",
        "repo": "owner/mangavibe-tracker", "file": "tracker.py"},
       {"id": "mcp", "name": "Remote control", "repo": "owner/manga-vibe-mcp", "folder": "mcp"}]}

- `repo` is the GitHub repo the plugin is installed (and later updated) from: its newest published
  release, like an update (app/plugin_updates.py).
- `file`: a single-file plugin, that file at the release's tag. `folder`: a folder plugin, that folder
  in the release's source zip (or the whole repo, when its __init__.py is at the top).
- `id` is the plugin's id: the download has to define a plugin with it.

MangaVibe's own source (OFFICIAL) is always listed; the owner adds others by URL. A source is given
as the JSON file's URL, or as a GitHub repo ("owner/name" or its github.com address), which means
the plugins.json at the top of its default branch.

Installing goes into the data folder's plugins/ (where updates and Reload plugins look), after the
same checks as an update: nothing of that name is there already, its permissions are shown and
approved first (all of them: it's new), and the download loads on its own and defines the plugin.
The permissions are read from the plugin's text, so none of its code runs before the owner agreed;
when they're worked out by code, the owner is asked whether it may run to say them.
"""
import ast
import json
import re
import shutil
import threading
import time
from urllib.parse import quote, urlparse

import httpx

from app import config, plugin_updates as pu
from app.plugins import CATEGORIES, manager

OFFICIAL = "ivokolarov0/manga-vibe"
SOURCES_KEY = "plugin_sources"
INDEX_FILE = "plugins.json"
MAX_INDEX = 1_000_000
MAX_PLUGINS = 300
CACHE_FOR = 600  # a source is asked again after 10 minutes, or on Refresh
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*\.py$")  # the loader skips names starting _ or .
FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
transport = None  # tests put an httpx.MockTransport here (sources that aren't on GitHub)
_cache: dict[str, dict] = {}  # url -> {"at", "name", "plugins", "error"}
_cache_lock = threading.Lock()


class SourceError(Exception):
    pass


# ---------------- which sources ----------------

def repo_ok(repo: str) -> bool:
    """A GitHub "owner/name": owners have no dots, and no part is "." or ".." or starts with a dot
    (it goes into api.github.com/repos/<repo>/..., with the token)."""
    if not pu.REPO_RE.match(repo or ""):
        return False
    owner, name = repo.split("/")
    return "." not in owner and not name.startswith(".")


def _raw(repo, rest):
    # GitHub names are case-insensitive: one spelling, so a source isn't listed twice
    return f"https://raw.githubusercontent.com/{repo.lower()}/{rest}"


def normalize(text: str) -> str:
    """The URL a source is read from. "owner/name" and https://github.com/owner/name mean that repo's
    plugins.json; any other http(s) address is the JSON file itself."""
    text = (text or "").strip()
    if repo_ok(text) and not text.lower().endswith(".json"):
        return _raw(text, f"HEAD/{INDEX_FILE}")
    u = urlparse(text)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise SourceError("A source is a GitHub repo (owner/name) or an address starting with https://")
    parts = [x for x in u.path.split("/") if x]
    if u.hostname in ("github.com", "www.github.com") and len(parts) == 2:
        repo = f"{parts[0]}/{parts[1].removesuffix('.git')}"
        if repo_ok(repo):
            return _raw(repo, f"HEAD/{INDEX_FILE}")
    if (u.hostname in ("github.com", "www.github.com") and len(parts) > 4 and parts[2] == "blob"
            and repo_ok(f"{parts[0]}/{parts[1]}")):
        # the page that shows the file: its raw copy is what can be read
        return _raw(f"{parts[0]}/{parts[1]}", "/".join(parts[3:]))
    if u.hostname == "raw.githubusercontent.com" and len(parts) > 3 and repo_ok(f"{parts[0]}/{parts[1]}"):
        return _raw(f"{parts[0]}/{parts[1]}", "/".join(parts[2:]))
    return text


def official_url() -> str:
    return normalize(OFFICIAL)


def _added(db) -> list[str]:
    from app.db import get_setting
    return [str(x) for x in get_setting(db, SOURCES_KEY, []) or [] if isinstance(x, str)]


def urls(db) -> list[str]:
    """Every source, MangaVibe's own first."""
    out = [official_url()]
    for u in _added(db):
        try:
            u = normalize(u)  # saved by an earlier version: the same spelling as a new one
        except SourceError:
            continue
        if u not in out:
            out.append(u)
    return out


def add(db, text: str) -> str:
    """Add a source; it has to be readable and valid. Returns its URL."""
    from app.db import set_setting
    url = normalize(text)
    if url in urls(db):
        raise SourceError("That source is already listed")
    _read(db, url, refresh=True, strict=True)
    set_setting(db, SOURCES_KEY, _added(db) + [url])
    db.commit()
    return url


def remove(db, url: str):
    from app.db import set_setting
    if url == official_url():
        raise SourceError("MangaVibe's own source can't be removed")
    left = [u for u in _added(db) if u != url]
    if len(left) == len(_added(db)):
        raise SourceError("That source isn't listed")
    set_setting(db, SOURCES_KEY, left)
    db.commit()
    with _cache_lock:
        _cache.pop(url, None)


# ---------------- reading a source ----------------

def _is_github(url) -> bool:
    return urlparse(url).hostname in ("raw.githubusercontent.com", "api.github.com")


class TooBig(Exception):
    pass


def download(client, url, limit) -> bytes:
    """GET url, stopping as soon as the answer passes `limit` bytes (TooBig): a host that sends
    gigabytes isn't kept in memory first. Raises httpx errors."""
    with client.stream("GET", url) as r:
        r.raise_for_status()
        if int(r.headers.get("content-length") or 0) > limit:
            raise TooBig()
        out = bytearray()
        for chunk in r.iter_bytes():
            out += chunk
            if len(out) > limit:
                raise TooBig()
        return bytes(out)


def _get(db, url) -> bytes:
    # the GitHub token (for private repos) only goes to GitHub; other hosts get a plain client
    client = (pu._client(db) if _is_github(url) else
              httpx.Client(timeout=30, follow_redirects=True, transport=transport,
                           headers={"User-Agent": "MangaVibe-plugin-sources"}))
    try:
        with client as c:
            return download(c, url, MAX_INDEX)
    except TooBig as e:
        raise SourceError("The source is too big to be a list of plugins") from e
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        raise SourceError(f"The source answered {code}" + (
            ". Is the address right? A private GitHub repo needs the GitHub token below." if code == 404 else "")) from e
    except httpx.RequestError as e:
        raise SourceError("The source can't be reached. Is the address right, and the server online?") from e


def _entry(raw) -> dict | None:
    """One plugin of a source, cleaned up, or None when it isn't a valid one."""
    if not isinstance(raw, dict):
        return None
    pid, repo = str(raw.get("id") or "").strip(), str(raw.get("repo") or "").strip()
    file, folder = str(raw.get("file") or "").strip(), str(raw.get("folder") or "").strip()
    if not ID_RE.match(pid) or not repo_ok(repo) or bool(file) == bool(folder):
        return None
    if (file and not FILE_RE.match(file)) or (folder and not FOLDER_RE.match(folder)):
        return None
    category = str(raw.get("category") or "").strip()
    homepage = str(raw.get("homepage") or "").strip()
    if not homepage.startswith(("https://", "http://")):
        homepage = f"https://github.com/{repo}"
    return {"id": pid, "name": str(raw.get("name") or pid).strip()[:80],
            "description": str(raw.get("description") or "").strip()[:600],
            "category": category if category in CATEGORIES else "other",
            "repo": repo, "file": file or None, "folder": folder or None, "homepage": homepage}


def parse(data: bytes, strict=False) -> tuple[str, list[dict]]:
    """A source's name and plugins. Invalid entries are left out (strict: the whole source is refused
    when none is valid)."""
    try:
        doc = json.loads(data)
    except ValueError as e:
        raise SourceError("The source isn't a JSON file") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("plugins"), list):
        raise SourceError('The source has no "plugins" list')
    plugins, seen = [], set()
    for raw in doc["plugins"][:MAX_PLUGINS]:
        e = _entry(raw)
        if e and e["id"] not in seen:
            seen.add(e["id"])
            plugins.append(e)
    if strict and doc["plugins"] and not plugins:
        raise SourceError('None of the source\'s plugins has an "id", a "repo" and a "file" or "folder"')
    return str(doc.get("name") or "").strip()[:80], plugins


def _read(db, url, refresh=False, strict=False) -> dict:
    with _cache_lock:
        hit = _cache.get(url)
    if hit and not refresh and time.time() - hit["at"] < CACHE_FOR:
        return hit
    try:
        name, plugins = parse(_get(db, url), strict)
        out = {"at": time.time(), "name": name, "plugins": plugins, "error": None}
    except SourceError as e:
        if strict:
            raise
        # keep what was read before: a source that's down for a moment still lists its plugins
        out = {"at": time.time(), "name": hit["name"] if hit else "", "plugins": hit["plugins"] if hit else [],
               "error": str(e)}
    with _cache_lock:
        _cache[url] = out
    return out


def available(db, refresh=False) -> dict:
    """Every source and its plugins, with whether each is installed already."""
    installed = manager.installed()  # id -> (version, built in), never a half-done reload
    sources, plugins = [], []
    for url in urls(db):
        s = _read(db, url, refresh)
        official = url == official_url()
        sources.append({"url": url, "name": s["name"] or ("MangaVibe" if official else urlparse(url).hostname),
                        "official": official, "error": s["error"], "count": len(s["plugins"])})
        for e in s["plugins"]:
            version, builtin = installed.get(e["id"], (None, False))
            plugins.append({**e, "source": url, "installed": e["id"] in installed,
                            "installed_version": version, "builtin": builtin})
    return {"sources": sources, "plugins": plugins}


# ---------------- installing ----------------

# asked for when a plugin's permissions can't be read from its text (worked out by its code)
UNREADABLE = "Runs its own code to say what it uses: MangaVibe can't read that before it runs"


def declared_permissions(path, plugin_id) -> list[str] | None:
    """The permissions of the plugin `plugin_id` as written in its files (a .py, or a folder's .py
    files), read without running anything: the class whose `id = "<plugin_id>"` and its
    `permissions = [...]` ([] when it has none). None when they can't be read that way."""
    files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
    for f in files:
        try:
            tree = ast.parse(f.read_bytes(), str(f))
        except SyntaxError as ex:
            raise pu.UpdateError(f"{f.name} doesn't load, so it wasn't installed: SyntaxError: {ex.msg}") from ex
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            values = {}
            for st in node.body:
                target = (st.targets[0] if isinstance(st, ast.Assign) and len(st.targets) == 1
                          else st.target if isinstance(st, ast.AnnAssign) and st.value is not None else None)
                if isinstance(target, ast.Name):
                    values[target.id] = st.value
            pid = values.get("id")
            if not (isinstance(pid, ast.Constant) and pid.value == plugin_id):
                continue
            if "permissions" not in values:
                return []
            try:
                raw = ast.literal_eval(values["permissions"])
            except ValueError:
                return None
            return pu.permissions_of(type("P", (), {"permissions": raw}))
    return None

def _find(db, url, plugin_id) -> dict:
    if url not in urls(db):
        raise pu.UpdateError("That source isn't listed")
    e = next((x for x in _read(db, url)["plugins"] if x["id"] == plugin_id), None)
    if e is None:
        raise pu.UpdateError("That source doesn't list this plugin (any more). Press Refresh.")
    return e


def install(db, url, plugin_id, accepted=None) -> str:
    """Install a source's plugin at its repo's newest release into plugins/, reload and start it.
    Raises UpdateError (NeedsApproval for permissions the owner hasn't approved yet)."""
    with pu._lock:
        e = _find(db, url, plugin_id)
        if plugin_id in manager.plugins:
            raise pu.UpdateError(f"{e['name']} is installed already")
        name = e["file"] or e["folder"]
        target = config.PLUGINS_DIR / name
        if target.exists() or (e["folder"] and (config.PLUGINS_DIR / f"{name}.py").exists()) or (
                e["file"] and (config.PLUGINS_DIR / name.removesuffix(".py")).exists()):
            raise pu.UpdateError(f"plugins/{name} is there already. Remove it first, or press Reload plugins.")
        repo = e["repo"]
        try:
            with pu._client(db) as c:
                r = c.get(f"https://api.github.com/repos/{repo}/releases/latest")
                r.raise_for_status()
                tag = str(r.json()["tag_name"])[:50]
        except Exception as ex:
            raise pu.UpdateError(f"Could not find {e['name']}'s newest release. {pu._why(ex, repo)}") from ex
        if not pu.TAG_RE.match(tag):
            raise pu.UpdateError("The newest release's tag isn't one MangaVibe can use")
        q = quote(tag, safe="")
        dl = (f"https://api.github.com/repos/{repo}/zipball/{q}" if e["folder"]
              else f"https://raw.githubusercontent.com/{repo}/{q}/{quote(name)}")
        try:
            with pu._client(db) as c:
                data = download(c, dl, pu.MAX_ZIP if e["folder"] else pu.MAX_SIZE)
        except TooBig as ex:
            raise pu.UpdateError(f"The downloaded {name} is too big to be a plugin") from ex
        except Exception as ex:
            raise pu.UpdateError(f"Could not download {name} {tag}. {pu._why(ex, repo)}") from ex

        config.PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        staging = config.PLUGINS_DIR / ".staging"
        staging.mkdir(exist_ok=True)
        new = staging / name

        def discard():
            if new.is_dir():
                shutil.rmtree(new, ignore_errors=True)
            else:
                new.unlink(missing_ok=True)

        discard()
        try:
            if e["folder"]:
                new.mkdir()
                pu._unpack_folder(data, name, new)
            else:
                new.write_bytes(data)
            # nothing of it runs before the owner approved what it uses: read from its text first
            asks = declared_permissions(new, plugin_id)
            if asks is None:
                asks = [UNREADABLE]
            if any(x not in (accepted or []) for x in asks):
                discard()
                raise pu.NeedsApproval(e["name"], tag, asks)
            found = pu._plugins_in_folder(new) if e["folder"] else pu._plugins_in(new)
        except pu.UpdateError:
            discard()
            raise
        except Exception as ex:
            discard()
            raise pu.UpdateError(f"{e['name']} {tag} doesn't load, so it wasn't installed: "
                                 f"{type(ex).__name__}: {ex}") from ex
        match = next(((v, perms) for pid, v, perms in found if pid == plugin_id), None)
        if match is None:
            discard()
            raise pu.UpdateError(f"{name} {tag} has no plugin '{plugin_id}', so it wasn't installed")
        version, perms = match
        if any(x not in (accepted or []) for x in perms):
            # it says more when it runs than its text showed (they're worked out by its code)
            discard()
            raise pu.NeedsApproval(e["name"], tag, perms)
        try:
            shutil.move(str(new), str(target))
        except OSError as ex:
            discard()
            raise pu.UpdateError(f"Could not put {name} in the plugins folder: {ex}") from ex
        # like Reload plugins: load() stops every plugin's threads and makes new objects, so every
        # switched-on plugin is started again, not only the new one
        manager.load()
        manager.load_switches(db)
        manager.start(db)
        p = manager.plugins.get(plugin_id)
        pu._update_state(db, plugin_id, latest=tag, checked_at=time.time(), error=None, skipped=None)
        on = p is not None and plugin_id not in manager.disabled
        return f"{e['name']} {version} installed" + ("" if on else ". Switch it on to use it")
