"""Updating plugins from their GitHub releases.

A plugin that sets `repo = "owner/name"` is updated from that repo's newest release (drafts and
pre-releases are left out): the file with the same name as the installed one, at the release's tag.
The tag is the version (v1.2.0 or 1.2.0) and is compared with the plugin's `version`.

A new version is never installed by itself: the daily check (or Check for updates) only looks, and
Settings shows it with Update and Skip. A skipped version isn't offered again; a newer one is.
Only single-file plugins in the data folder's plugins/ are updated; built-in ones come with the app.

Before a new file replaces the old one, it must load on its own and define a plugin with the same
id. The old file is kept in plugins/.backup/ (the loader skips folders that start with a dot).
"""
import importlib.util
import logging
import os
import re
import shutil
import sys
import threading
import time
import types
from pathlib import Path
from urllib.parse import quote

import httpx

from app import config
from app.plugins import Plugin, manager

log = logging.getLogger("mangavibe.plugin_updates")
CHECK_EVERY = 24 * 3600
MAX_SIZE = 1_000_000  # a plugin is one .py file; anything bigger isn't one
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
transport = None  # tests put an httpx.MockTransport here
_lock = threading.Lock()  # one install at a time


class UpdateError(Exception):
    pass


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
    """The file an update replaces, or None when this plugin can't be updated."""
    path = manager.paths.get(p.id)
    if not p.repo or not REPO_RE.match(p.repo) or path is None or path.suffix != ".py":
        return None
    try:
        if path.resolve().parent != config.PLUGINS_DIR.resolve():
            return None  # built in, or a package folder
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

def _client():
    return httpx.Client(timeout=30, follow_redirects=True, transport=transport,
                        headers={"User-Agent": "MangaVibe-plugin-updater",
                                 "Accept": "application/vnd.github+json"})


def _why(e, repo):
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return f"GitHub answered 404. Is {repo} public, and does it have a published release?"
        if code in (403, 429):
            return "GitHub's request limit was reached. Try again in an hour."
        return f"GitHub answered {code}"
    if isinstance(e, httpx.RequestError):
        return "GitHub is not reachable. Is the NAS online?"
    return str(e)


def check(db, p):
    """Ask GitHub for the plugin's newest release and save the answer. Never raises."""
    try:
        with _client() as c:
            r = c.get(f"https://api.github.com/repos/{p.repo}/releases/latest")
            r.raise_for_status()
            rel = r.json()
        values = {"latest": str(rel["tag_name"])[:50], "url": rel.get("html_url"), "error": None}
    except Exception as e:
        log.warning("Could not check %s for updates: %s", p.id, e)
        values = {"error": f"Could not check for updates. {_why(e, p.repo)}"}
    _update_state(db, p.id, checked_at=time.time(), **values)


# ---------------- install ----------------

def _plugins_in(file):
    """Load a downloaded file on its own: [(id, version)] of the plugins it defines."""
    name = f"mangavibe_plugin_update_check_{file.stem}"
    try:
        # compiled from the text itself: a cached .pyc of an earlier download with the same size
        # and time could otherwise be used instead
        module = types.ModuleType(name)
        module.__file__ = str(file)
        sys.modules[name] = module
        exec(compile(file.read_bytes(), str(file), "exec"), module.__dict__)
        return [(cls.id, cls.version) for cls in vars(module).values()
                if isinstance(cls, type) and issubclass(cls, Plugin) and cls is not Plugin
                and cls.__module__ == name]
    finally:
        sys.modules.pop(name, None)


def reload_plugins(db):
    from app.db import get_setting
    manager.load()
    manager.disabled = set(get_setting(db, "plugins_disabled", []) or [])


def install(db, plugin_id):
    """Download the newest release's file, check it, swap it in and reload the plugins.
    Returns a message; raises UpdateError when nothing was changed."""
    with _lock:
        p = manager.plugins.get(plugin_id)
        path = path_of(p) if p else None
        if path is None:
            raise UpdateError("MangaVibe can't update this plugin")
        latest = _states(db).get(p.id, {}).get("latest")
        if not latest or version_tuple(latest) <= version_tuple(p.version):
            raise UpdateError(f"{p.name} is up to date. Press Check for updates to look again.")
        url = (f"https://raw.githubusercontent.com/{p.repo}/{quote(latest, safe='')}/"
               f"{quote(path.name)}")
        try:
            with _client() as c:
                r = c.get(url)
                r.raise_for_status()
        except Exception as e:
            raise UpdateError(f"Could not download {path.name} {latest}. {_why(e, p.repo)}") from e
        if len(r.content) > MAX_SIZE:
            raise UpdateError(f"The downloaded {path.name} is too big to be a plugin")

        staging = config.PLUGINS_DIR / ".staging"
        staging.mkdir(exist_ok=True)
        new = staging / path.name
        new.write_bytes(r.content)
        try:
            found = _plugins_in(new)
        except Exception as e:
            new.unlink(missing_ok=True)
            raise UpdateError(f"{p.name} {latest} doesn't load, so it wasn't installed: "
                              f"{type(e).__name__}: {e}") from e
        new_version = next((v for pid, v in found if pid == p.id), None)
        if new_version is None:
            new.unlink(missing_ok=True)
            raise UpdateError(f"{path.name} {latest} has no plugin '{p.id}', so it wasn't installed")

        backup = config.PLUGINS_DIR / ".backup"
        backup.mkdir(exist_ok=True)
        old_version, name = p.version, p.name
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
