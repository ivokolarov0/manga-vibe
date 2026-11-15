"""Updating plugins from their GitHub releases.

A plugin that sets `repo = "owner/name"` is updated from that repo's newest release (drafts and
pre-releases are left out), at the release's tag. The tag is the version (v1.2.0 or 1.2.0) and is
compared with the plugin's `version`.
- A single-file plugin (plugins/tracker.py): the file with the same name in the repo.
- A folder plugin (plugins/sources/): the folder with the same name in the release's source zip,
  or the whole repo when the plugin is the repo itself (an __init__.py at its top).

A private repo needs a GitHub token (Settings -> Plugins, or the GITHUB_TOKEN variable). It is sent
only to GitHub.

A new version is never installed by itself: the daily check (or Check for updates) only looks, and
Settings shows it with Update and Skip. A skipped version isn't offered again; a newer one is.
Only plugins in the data folder's plugins/ are updated; built-in ones come with the app, and a
folder that is a link (to a checkout) is the owner's to update.

Before the new version replaces the old one, it must load on its own and define a plugin with the
same id. The old one is kept in plugins/.backup/ (the loader skips folders that start with a dot).

When the new version lists permissions the installed one doesn't, nothing is installed until the
owner approves exactly those (Update asks, then sends them back as `accepted`).
"""
import importlib.util
import io
import logging
import os
import re
import shutil
import sys
import threading
import time
import types
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from app import config
from app.plugins import Plugin, manager

log = logging.getLogger("mangavibe.plugin_updates")
CHECK_EVERY = 24 * 3600
MAX_SIZE = 1_000_000  # a plugin is one .py file; anything bigger isn't one
MAX_ZIP = 50_000_000  # a release's source zip (the whole repo at the tag)
MAX_FOLDER = 20_000_000  # a folder plugin, unpacked
TOKEN_KEY = "plugin_github_token"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
transport = None  # tests put an httpx.MockTransport here
_lock = threading.Lock()  # one install at a time


class UpdateError(Exception):
    pass


class NeedsApproval(UpdateError):
    """The new version asks for permissions the installed one didn't have."""

    def __init__(self, name, version, permissions):
        self.permissions = permissions
        super().__init__(f"{name} {version} asks for new permissions")


def permissions_of(p_or_cls):
    """The plugin's permissions, cleaned up: strings, trimmed, no blanks or repeats, in order."""
    raw = getattr(p_or_cls, "permissions", None) or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for x in raw:
        x = str(x).strip()
        if x and x not in out:
            out.append(x)
    return out


def new_permissions(old, new):
    return [x for x in new if x not in old]


# ---------------- what's saved ----------------

def _states(db):
    from app.db import get_setting
    return get_setting(db, "plugin_updates", {}) or {}


def _save(db, states):
    from app.db import set_setting
    set_setting(db, "plugin_updates", states)
    db.commit()


def _update_state(db, plugin_id, **values):
    states = _states(db)
    states[plugin_id] = {**states.get(plugin_id, {}), **values}
    _save(db, states)


def skip(db, plugin_id):
    """Stop offering the version that's out now (a newer one is offered again)."""
    _update_state(db, plugin_id, skipped=_states(db).get(plugin_id, {}).get("latest"))


def unskip(db, plugin_id):
    _update_state(db, plugin_id, skipped=None)


# ---------------- which plugins, which versions ----------------

def path_of(p):
    """What an update replaces: the plugin's .py file, or its folder (a folder plugin). None when
    this plugin can't be updated."""
    path = manager.paths.get(p.id)
    if not p.repo or not REPO_RE.match(p.repo) or path is None or path.suffix != ".py":
        return None
    if path.name == "__init__.py":
        path = path.parent  # a folder plugin: the whole folder is replaced
    try:
        if path.resolve().parent != config.PLUGINS_DIR.resolve():
            return None  # built in, or a link to a folder elsewhere (a checkout)
    except OSError:
        return None
    return path


def version_tuple(v):
    """'v1.2.0' -> (1, 2, 0); '1.2' compares equal to '1.2.0'."""
    nums = [int(n) for n in re.findall(r"\d+", str(v or ""))[:4]]
    while nums and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def info(db, p):
    """The update part of a plugin in Settings."""
    st = _states(db).get(p.id, {})
    supported = path_of(p) is not None
    latest = st.get("latest")
    newer = bool(supported and latest and version_tuple(latest) > version_tuple(p.version))
    return {"supported": supported, "repo": p.repo or None,
            "latest": latest, "url": st.get("url"), "checked_at": st.get("checked_at"),
            "error": st.get("error"),
            "available": newer, "skipped": newer and st.get("skipped") == latest}


# ---------------- GitHub ----------------

def token(db) -> str:
    """The GitHub token for private plugin repos: the one saved in Settings, else GITHUB_TOKEN."""
    from app.db import get_setting
    return str(get_setting(db, TOKEN_KEY, "") or os.environ.get("GITHUB_TOKEN") or "").strip()


def token_source(db) -> str:
    """Where the token comes from, for Settings: "settings", "env" or ""."""
    from app.db import get_setting
    if str(get_setting(db, TOKEN_KEY, "") or "").strip():
        return "settings"
    return "env" if os.environ.get("GITHUB_TOKEN", "").strip() else ""


def set_token(db, value: str):
    from app.db import set_setting
    set_setting(db, TOKEN_KEY, (value or "").strip())
    db.commit()


def _client(db):
    headers = {"User-Agent": "MangaVibe-plugin-updater", "Accept": "application/vnd.github+json"}
    tok = token(db)
    if tok:
        # only GitHub gets it: httpx drops Authorization when a redirect leaves the host (the source
        # zip is redirected to codeload.github.com with a short-lived link of its own)
        headers["Authorization"] = f"Bearer {tok}"
    return httpx.Client(timeout=60, follow_redirects=True, transport=transport, headers=headers)


def _why(e, repo):
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return (f"GitHub answered 404. Does {repo} have a published release? If the repo is "
                    "private, add a GitHub token that can read it (Settings → Plugins).")
        if code == 401:
            return "GitHub refused the token. Check it in Settings → Plugins."
        if code in (403, 429):
            return "GitHub's request limit was reached. Try again in an hour."
        return f"GitHub answered {code}"
    if isinstance(e, httpx.RequestError):
        return "GitHub is not reachable. Is the NAS online?"
    return str(e)


def check(db, p):
    """Ask GitHub for the plugin's newest release and save the answer. Never raises."""
    try:
        with _client(db) as c:
            r = c.get(f"https://api.github.com/repos/{p.repo}/releases/latest")
            r.raise_for_status()
            rel = r.json()
        values = {"latest": str(rel["tag_name"])[:50], "url": rel.get("html_url"), "error": None}
    except Exception as e:
        log.warning("Could not check %s for updates: %s", p.id, e)
        values = {"error": f"Could not check for updates. {_why(e, p.repo)}"}
    _update_state(db, p.id, checked_at=time.time(), **values)


# ---------------- install ----------------

def _found(module, name):
    return [(cls.id, cls.version, permissions_of(cls)) for cls in vars(module).values()
            if isinstance(cls, type) and issubclass(cls, Plugin) and cls is not Plugin
            and cls.__module__ == name]


def _plugins_in(file):
    """Load a downloaded file on its own: [(id, version, permissions)] of the plugins it defines."""
    name = f"mangavibe_plugin_update_check_{file.stem}"
    try:
        # compiled from the text itself: a cached .pyc of an earlier download with the same size
        # and time could otherwise be used instead
        module = types.ModuleType(name)
        module.__file__ = str(file)
        sys.modules[name] = module
        exec(compile(file.read_bytes(), str(file), "exec"), module.__dict__)
        return _found(module, name)
    finally:
        sys.modules.pop(name, None)


def _plugins_in_folder(folder):
    """Load a downloaded folder plugin on its own (with its own modules, under a name of its own):
    [(id, version, permissions)] of the plugins it defines."""
    name = f"mangavibe_plugin_update_check_{folder.name}"
    try:
        spec = importlib.util.spec_from_file_location(name, folder / "__init__.py",
                                                      submodule_search_locations=[str(folder)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return _found(module, name)
    finally:
        for m in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[m]
        for cache in folder.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def _unpack_folder(data: bytes, folder_name: str, dest: Path):
    """Unpack a folder plugin from a GitHub source zip (one top folder, the repo at the tag) into
    `dest`: the folder named like the installed one, or the whole repo when the plugin is the repo
    itself. Dot-files and __pycache__ are left out."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError("The release's download isn't a zip file") from e
    with zf:
        names = set(zf.namelist())
        tops = {n.split("/", 1)[0] for n in names if n.strip("/")}
        if len(tops) != 1:
            raise UpdateError("The release's download isn't a GitHub source zip")
        top = tops.pop()
        prefix = next((pre for pre in (f"{top}/{folder_name}/", f"{top}/")
                       if f"{pre}__init__.py" in names), None)
        if prefix is None:
            raise UpdateError(f"The release has no {folder_name}/ folder with an __init__.py")
        total = 0
        for info in zf.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            rel = PurePosixPath(info.filename[len(prefix):])
            parts = rel.parts
            if not parts or rel.is_absolute() or any(
                    x in ("", "..") or x.startswith(".") or x == "__pycache__" or "\\" in x or ":" in x
                    for x in parts):
                continue  # dot-files (.github, .gitignore), caches, and anything leaving the folder
            total += info.file_size
            if total > MAX_FOLDER:
                raise UpdateError(f"The release's {folder_name}/ folder is too big to be a plugin")
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _swap_folder(new: Path, path: Path, backup: Path, staging: Path):
    """Put the new folder in the old one's place. The old one is copied to `backup` first, and put
    back if the new one can't be moved in."""
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(path, backup, ignore=shutil.ignore_patterns("__pycache__"))
    old = staging / f"{path.name}.old"
    shutil.rmtree(old, ignore_errors=True)
    os.replace(path, old)
    try:
        os.replace(new, path)
    except OSError:
        os.replace(old, path)
        raise
    shutil.rmtree(old, ignore_errors=True)


def reload_plugins(db):
    from app.db import get_setting
    manager.load()
    manager.disabled = set(get_setting(db, "plugins_disabled", []) or [])


def install(db, plugin_id, accepted=None):
    """Download the newest release's file, check it, swap it in and reload the plugins.
    `accepted` is the list of new permissions the owner approved.
    Returns a message; raises UpdateError (NeedsApproval for unapproved new permissions) when
    nothing was changed."""
    with _lock:
        p = manager.plugins.get(plugin_id)
        path = path_of(p) if p else None
        if path is None:
            raise UpdateError("MangaVibe can't update this plugin")
        latest = _states(db).get(p.id, {}).get("latest")
        if not latest or version_tuple(latest) <= version_tuple(p.version):
            raise UpdateError(f"{p.name} is up to date. Press Check for updates to look again.")
        folder = path.is_dir()
        what = f"{path.name}/" if folder else path.name
        tag = quote(latest, safe="")
        url = (f"https://api.github.com/repos/{p.repo}/zipball/{tag}" if folder
               else f"https://raw.githubusercontent.com/{p.repo}/{tag}/{quote(path.name)}")
        try:
            with _client(db) as c:
                r = c.get(url)
                r.raise_for_status()
        except Exception as e:
            raise UpdateError(f"Could not download {what} {latest}. {_why(e, p.repo)}") from e
        if len(r.content) > (MAX_ZIP if folder else MAX_SIZE):
            raise UpdateError(f"The downloaded {what} is too big to be a plugin")

        staging = config.PLUGINS_DIR / ".staging"
        staging.mkdir(exist_ok=True)
        new = staging / path.name

        def discard():
            if new.is_dir():
                shutil.rmtree(new, ignore_errors=True)
            else:
                new.unlink(missing_ok=True)

        discard()  # left over from an update that was cut off
        try:
            if folder:
                new.mkdir()
                _unpack_folder(r.content, path.name, new)
                found = _plugins_in_folder(new)
            else:
                new.write_bytes(r.content)
                found = _plugins_in(new)
        except UpdateError:
            discard()
            raise
        except Exception as e:
            discard()
            raise UpdateError(f"{p.name} {latest} doesn't load, so it wasn't installed: "
                              f"{type(e).__name__}: {e}") from e
        match = next(((v, perms) for pid, v, perms in found if pid == p.id), None)
        if match is None:
            discard()
            raise UpdateError(f"{what} {latest} has no plugin '{p.id}', so it wasn't installed")
        new_version, perms = match
        added = new_permissions(permissions_of(p), perms)
        if any(x not in (accepted or []) for x in added):
            discard()
            raise NeedsApproval(p.name, latest, added)

        backup = config.PLUGINS_DIR / ".backup"
        backup.mkdir(exist_ok=True)
        old_version, name = p.version, p.name
        if folder:
            try:
                _swap_folder(new, path, backup / f"{path.name}-{old_version}", staging)
            except OSError as e:
                discard()
                raise UpdateError(f"Could not replace the {what} folder, so nothing changed: {e}") from e
        else:
            shutil.copy2(path, backup / f"{path.stem}-{old_version}.py")
            os.replace(new, path)
            Path(importlib.util.cache_from_source(str(path))).unlink(missing_ok=True)  # see _plugins_in
        reload_plugins(db)
        _update_state(db, plugin_id, error=None, skipped=None)
        log.info("Plugin %s updated from %s to %s", plugin_id, old_version, new_version)
        return f"{name} updated from {old_version} to {new_version}"


# ---------------- daily ----------------

def run_daily(db):
    """Check each updatable plugin, once a day each. Only looks: installing is always a button."""
    for p in list(manager.plugins.values()):
        if path_of(p) is None:
            continue
        if time.time() - (_states(db).get(p.id, {}).get("checked_at") or 0) >= CHECK_EVERY:
            check(db, p)


def daily_loop():
    """Background thread. Follows Settings -> Updates "Check for updates every day"."""
    from app import db as database
    time.sleep(90)  # after the app's own update check
    while True:
        try:
            with database.SessionLocal() as s:
                if database.get_setting(s, "auto_update", True):
                    run_daily(s)
        except Exception:
            log.exception("Plugin update check failed")
        time.sleep(3600)
