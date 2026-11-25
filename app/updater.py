"""Updates the app's code from the newest GitHub release, without a new Docker image.

How it works (see DECISIONS.md, "Auto-update"):
- The Docker image is the base: Python, 7zip and the pip packages from its requirements.txt.
  It keeps a copy of its Dockerfile, entrypoint and requirements in /opt/mangavibe/image.
- The newest published release (not a draft or pre-release) is the version on offer. It is
  stored by the commit its tag points to; the tag (e.g. v1.4.0) is what the user sees.
- A new version is downloaded into DATA_DIR/code/<sha>/. New or changed pip packages go into
  DATA_DIR/code/<sha>/deps. If `import app.main` works there, DATA_DIR/code/current is pointed at it
  and the app restarts. docker/entrypoint.sh starts the app from that folder.
- If the new version changes the Dockerfile or the entrypoint, files alone are not enough: the
  update is refused and the user is told to get a new image.
- A version that fails to start three times is put back by the entrypoint (DATA_DIR/code/failed).

Going back (Settings -> Updates, "Go back to an earlier version"): any earlier release, the
versions still on disk (no download) or the code built into the image. The version that was left
is skipped, so it isn't offered again until a newer one is out.
Database migrations only go forward. Before a version that has more migrations than the database
starts, the database is copied to DATA_DIR/code/.db-backups/schema-<n>.db. Going back to a version
with fewer migrations than the database needs the owner's choice: put that copy back (changes since
then are lost) or keep the newer database (the older code may not understand it).
"""
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from app import config

log = logging.getLogger("mangavibe.update")

CHECK_EVERY = 24 * 3600
IMAGE_FILES = {"Dockerfile": "Dockerfile", "docker/entrypoint.sh": "entrypoint.sh"}  # repo path -> copy in image
KEEP_FILES = ("app/", "examples/", "requirements.txt", "Dockerfile", "docker/entrypoint.sh")

DB_BACKUPS = ".db-backups"  # in code/: copies of the database from before migrations ran
KEEP_DB_BACKUPS = 3

lock = threading.Lock()
latest = None   # last check result: {sha, version, date, message, url, needs_image, checked_at} or None
# state: idle|checking|installing|restarting|error; needs_db: see NeedsDbChoice
status = {"state": "idle", "message": "", "error": None, "checked_at": None, "needs_db": None}
offered = {}    # sha -> version, from the last versions() list: what a rollback may switch to
pending_restore = None  # schema number of the database copy to put back right before the restart


class NeedsDbChoice(RuntimeError):
    """The version is older than the database: the owner must choose to restore a copy or keep it."""

    def __init__(self, have, target, backup):
        self.detail = {"have": have, "target": target, "backup": backup}
        super().__init__("This version is older than your database: it knows "
                         f"{target} of the database's {have} changes.")


def code_dir():
    return config.DATA_DIR / "code"


def supported():
    """Updates only work inside the Docker image (the entrypoint picks the code folder)."""
    return config.IMAGE_DIR.is_dir() and config.ENTRYPOINT.is_file() and config.AUTO_UPDATE


def image_sha():
    """The commit the Docker image was built from ("" for a local build without GIT_SHA)."""
    f = config.IMAGE_DIR / "sha"
    return f.read_text().strip() if f.exists() else ""


def running_dir():
    """The folder the running code came from: DATA_DIR/code/<sha>, or None for the built-in code."""
    root = Path(__file__).resolve().parent.parent
    code = code_dir().resolve()
    return root if root.parent == code else None


def current():
    """{sha, version, date, message, builtin} of the running code."""
    d = running_dir()
    if d is not None:
        info = _read_json(d / ".info.json") or {}
        return {"sha": d.name, "version": info.get("version"), "date": info.get("date"),
                "message": info.get("message"), "builtin": False}
    sha = image_sha()
    known = latest if sha and latest and latest.get("sha") == sha else {}  # the image is the newest release
    return {"sha": sha or None, "version": known.get("version"), "date": known.get("date"),
            "message": known.get("message"), "builtin": True}


def mark_started():
    """Called after a successful start: the entrypoint stops counting failed starts for this version."""
    d = running_dir()
    if d is not None and not (d / ".ok").exists():
        try:
            (d / ".ok").write_text(datetime.now(timezone.utc).isoformat())
        except OSError as e:
            log.warning("Could not mark version %s as started: %s", d.name, e)


def _why(e):
    """A short, readable reason for a failed GitHub request."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return f"GitHub answered 404. Is {config.UPDATE_REPO} public, and does it have a published release?"
        if code in (403, 429):
            return "GitHub's request limit was reached. Try again in an hour."
        return f"GitHub answered {code}"
    if isinstance(e, httpx.RequestError):
        return "GitHub is not reachable. Is the NAS online?"
    return str(e)


def failed_sha():
    f = code_dir() / "failed"
    return f.read_text().strip() if f.exists() else None


# ---------------- GitHub ----------------

def _client():
    return httpx.Client(timeout=30, follow_redirects=True,
                        headers={"User-Agent": "MangaVibe-updater", "Accept": "application/vnd.github+json"})


def fetch_latest():
    """Newest published release (its tag's commit), and whether it needs a new Docker image."""
    api = f"https://api.github.com/repos/{config.UPDATE_REPO}"
    with _client() as c:
        r = c.get(f"{api}/releases/latest")  # GitHub leaves out drafts and pre-releases
        r.raise_for_status()
        rel = r.json()
        r = c.get(f"{api}/commits/{quote(rel['tag_name'], safe='')}")
        r.raise_for_status()
        info = release_info(rel, r.json()["sha"])
        sha = info["sha"]
        info["image"] = image_sha()
        if latest and latest.get("sha") == sha and latest.get("image") == info["image"]:
            info["needs_image"] = latest["needs_image"]
        else:
            files = {}
            for path in IMAGE_FILES:
                f = c.get(f"https://raw.githubusercontent.com/{config.UPDATE_REPO}/{sha}/{path}")
                f.raise_for_status()
                files[path] = f.text
            info["needs_image"] = needs_new_image(files)
    return info


def release_info(rel, sha):
    """What the app keeps of a GitHub release. The tag is the version; a title other than the tag is the message."""
    tag, name = rel["tag_name"], (rel.get("name") or "").strip()
    return {"sha": sha, "version": tag[:50], "date": rel.get("published_at"),
            "message": name[:200] if name and name != tag else None, "url": rel.get("html_url"),
            **release_notes(rel.get("body") or "")}


def fetch_releases():
    """The newest published releases (no drafts or pre-releases), newest first, with their commits.
    Two requests: the releases, and the tags (which name each tag's commit)."""
    api = f"https://api.github.com/repos/{config.UPDATE_REPO}"
    with _client() as c:
        r = c.get(f"{api}/releases", params={"per_page": 30})
        r.raise_for_status()
        rels = [x for x in r.json() if not x.get("draft") and not x.get("prerelease")]
        r = c.get(f"{api}/tags", params={"per_page": 100})
        r.raise_for_status()
        shas = {t["name"]: t["commit"]["sha"] for t in r.json()}
    return [release_info(rel, shas[rel["tag_name"]]) for rel in rels if rel["tag_name"] in shas]


_PR_CREDIT = re.compile(r"\s+by @\S+ in https://github\.com/\S+/pull/(\d+)\s*$")


def release_notes(body):
    """A release body as {notes, changes}: the text above the first heading (e.g. "needs a new Docker
    image"), and the bullets GitHub generated, one per merged PR, as "Title (#12)". "New Contributors"
    and the "Full Changelog" link are left out; the release page still has them."""
    notes, changes, section = [], [], None
    for line in body.replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
        elif line[:2] in ("* ", "- "):
            if section != "new contributors" and len(changes) < 30:
                changes.append(_PR_CREDIT.sub(r" (#\1)", line[2:].strip())[:200])
        elif line and section is None and not line.startswith("**Full Changelog**"):
            notes.append(line)
    return {"notes": " ".join(notes)[:1000] or None, "changes": changes}


def needs_new_image(files):
    """True when the Dockerfile or entrypoint of a version differs from the ones this image was built with."""
    for path, name in IMAGE_FILES.items():
        mine = config.IMAGE_DIR / name
        if not mine.exists() or _norm(mine.read_bytes().decode("utf-8", "replace")) != _norm(files.get(path, "")):
            return True
    return False


def _norm(text):
    return "\n".join(line.rstrip() for line in text.replace("\r", "").strip().split("\n"))


def check(force=False):
    """Ask GitHub for the newest version (at most once a day unless forced). Never raises."""
    global latest
    from app import db as database
    with database.SessionLocal() as s:
        last = database.get_setting(s, "update_checked_at")
    if not force and last and time.time() - last < CHECK_EVERY and latest:
        return latest
    if status["state"] in ("installing", "restarting"):
        return latest
    status.update(state="checking", error=None, needs_db=None)
    try:
        latest = fetch_latest()
        status.update(state="idle", message="")
    except Exception as e:  # offline, GitHub down, rate limit: try again later
        log.warning("Could not check for updates: %s", e)
        status.update(state="error", error=f"Could not check for updates. {_why(e)}")
        return latest
    now = time.time()
    latest["checked_at"] = now
    status["checked_at"] = now
    with database.SessionLocal() as s:
        database.set_setting(s, "update_checked_at", now)
        database.set_setting(s, "update_latest", latest)
        s.commit()
    return latest


def load_cached():
    """The last check result survives restarts, so the toast shows without asking GitHub again."""
    global latest
    from app import db as database
    with database.SessionLocal() as s:
        latest = database.get_setting(s, "update_latest") or None
        status["checked_at"] = database.get_setting(s, "update_checked_at")
    if latest and latest.get("image") != image_sha():
        latest = None  # checked with another image: "needs a new image" may no longer be true


def _short(v):
    """How a version is shown: its tag, or the start of its commit for versions from before releases."""
    return v.get("version") or (v.get("sha") or "")[:7]


def info(skipped=None):
    cur = current()
    avail = None
    if latest and latest["sha"] != cur["sha"]:
        avail = {**latest, "short": _short(latest), "skipped": latest["sha"] == skipped,
                 "failed": latest["sha"] == failed_sha()}
    return {"supported": supported(), "current": {**cur, "short": _short(cur)},
            "available": avail, "status": status, "repo": config.UPDATE_REPO}


def daily_loop():
    """Background thread: check once a day while automatic checks are on."""
    from app import db as database
    time.sleep(60)  # let the first scan start first
    while True:
        try:
            with database.SessionLocal() as s:
                on = database.get_setting(s, "auto_update", True)
            if on:
                check()
        except Exception:
            log.exception("Update check failed")
        time.sleep(3600)


# ---------------- install ----------------

def _requirements(text):
    """{name: line} of a requirements.txt, comments and blank lines dropped."""
    out = {}
    for line in text.replace("\r", "").split("\n"):
        line = line.split("#", 1)[0].strip()
        if line:
            name = re.split(r"[=<>!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-")
            out[name] = line.replace(" ", "")
    return out


def _same(a, b):
    return a is not None and b is not None and a.lower() == b.lower()


def new_requirements(new_text):
    """Lines of the new requirements.txt that the image does not already have."""
    base_file = config.IMAGE_DIR / "requirements.txt"
    base = _requirements(base_file.read_text(encoding="utf-8")) if base_file.exists() else {}
    return [line for name, line in _requirements(new_text).items() if not _same(base.get(name), line)]


DEPS_LIST = ".requirements"  # in a version's deps/: the requirement lines installed there


def _reuse_deps(extra, deps):
    """Copy the running version's deps/ to deps when every package in it is still wanted (same
    version), so an update doesn't install them again. Returns the lines it brings, lowercased.
    Only for the same image: packages are built for its Python."""
    cur = _read(code_dir() / "current")
    src = code_dir() / cur / "deps" if cur else None
    if not extra or not src or not (src / DEPS_LIST).is_file():
        return set()
    if _read(code_dir() / cur / ".image") != image_sha():
        return set()
    have = {line.strip().lower() for line in (src / DEPS_LIST).read_text(encoding="utf-8").splitlines() if line.strip()}
    if not have <= {line.lower() for line in extra}:
        return set()  # a package changed or was dropped: start clean
    shutil.copytree(src, deps)
    log.info("Reusing the installed packages of version %s: %s", cur[:7], ", ".join(sorted(have)))
    return have


def _download(sha, dest):
    with _client() as c:
        r = c.get(f"https://codeload.github.com/{config.UPDATE_REPO}/tar.gz/{sha}")
        r.raise_for_status()
        data = r.content
    _extract(data, dest)


def _extract(data, dest):
    """Unpack only the files the app needs. The archive has one top folder, e.g. manga-vibe-<sha>/."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = []
        for m in tar.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1].startswith(KEEP_FILES):
                continue
            m.name = parts[1]
            members.append(m)
        tar.extractall(dest, members=members, filter="data")
    if not (dest / "app" / "main.py").is_file():
        raise RuntimeError("The download does not contain the app")


def _run(cmd, cwd, env=None, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or p.stdout).strip().splitlines()[-8:])
        raise RuntimeError(tail or f"exit code {p.returncode}")
    return p.stdout


# Run in a version's folder: does it start, and how many database migrations does it know?
_TEST = ("import app.main\n"
         "try:\n    from app.db import MIGRATIONS\nexcept Exception:\n    MIGRATIONS = None\n"
         "print('MIGRATIONS', -1 if MIGRATIONS is None else len(MIGRATIONS))\n")


def _test(folder, import_main=True):
    """Import the version in `folder` in a separate process. Returns its number of migrations
    (None when it can't tell). Raises when it doesn't import."""
    script = _TEST if import_main else _TEST.replace("import app.main\n", "", 1)
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "DATA_DIR": tmp, "START_WORKERS": "0",
               "PYTHONPATH": os.pathsep.join([str(folder / "deps"), str(folder)])}
        out = _run([sys.executable, "-c", script], cwd=folder, env=env, timeout=120) or ""
    m = re.search(r"^MIGRATIONS (-?\d+)$", out, re.M)
    return int(m.group(1)) if m and int(m.group(1)) >= 0 else None


def install(sha=None, downloader=_download, meta=None, db_choice=None):
    """Download, prepare and switch to a version. Returns the new folder. Raises with a readable message.
    `meta` is what the folder remembers of the version (default: the last check's); `db_choice`
    is the owner's answer to NeedsDbChoice ("restore" or "keep")."""
    sha = sha or (latest or {}).get("sha")
    if not sha or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("No version to install. Check for updates first.")
    if latest and latest.get("sha") == sha and latest.get("needs_image"):
        raise RuntimeError("This version needs a new Docker image. See github.com/ivokolarov0/manga-vibe#updating")
    final = _prepare(sha, downloader, meta)
    _go(sha, _read_json(final / ".info.json").get("migrations"), db_choice)
    log.info("Installed version %s", sha[:7])
    return final


def _prepare(sha, downloader, meta=None):
    """Download a version into code/<sha>, install its packages and test it. Doesn't switch."""
    code = code_dir()
    code.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=code))
    try:
        status["message"] = "Downloading…"
        downloader(sha, staging)
        files = {p: (staging / p).read_bytes().decode("utf-8", "replace") if (staging / p).exists() else ""
                 for p in IMAGE_FILES}
        if needs_new_image(files):
            raise RuntimeError("This version needs a new Docker image. See github.com/ivokolarov0/manga-vibe#updating")
        req = staging / "requirements.txt"
        extra = new_requirements(req.read_text(encoding="utf-8")) if req.exists() else []
        deps = staging / "deps"
        reused = _reuse_deps(extra, deps)
        todo = [line for line in extra if line.lower() not in reused]
        if todo:
            status["message"] = "Installing " + ", ".join(todo) + "…"
            log.info("Installing new packages for version %s: %s", sha[:7], ", ".join(todo))
            _run(["uv", "pip", "install", "--no-cache", "--python", sys.executable,
                  "--target", str(deps), *todo], cwd=staging)
        if extra:
            deps.mkdir(exist_ok=True)
            (deps / DEPS_LIST).write_text("\n".join(extra) + "\n", encoding="utf-8")
        status["message"] = "Testing the new version…"
        migrations = _test(staging)
        if meta is None:
            meta = latest if latest and latest.get("sha") == sha else {"sha": sha}
        keep = {k: meta.get(k) for k in ("version", "date", "message")}
        (staging / ".info.json").write_text(json.dumps({"sha": sha, **keep, "migrations": migrations}))
        (staging / ".image").write_text(image_sha())  # a newer image runs its own code (entrypoint.sh)
        final = code / sha
        if final.exists():
            shutil.rmtree(final)
        staging.chmod(0o755)  # mkdtemp makes it private; the entrypoint may read it as another user
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


# ---------------- the database across versions ----------------

def _db_file():
    from app import db as database
    return Path(database.engine.url.database) if database.engine is not None else None


def db_schema():
    """How many migrations the database has had (None before it's opened)."""
    f = _db_file()
    if f is None or not f.exists():
        return None
    try:
        conn = sqlite3.connect(f, timeout=30)
        try:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _backups():
    return code_dir() / DB_BACKUPS


def backup_info(schema):
    """{date, version} of the database copy made at `schema` migrations, or None."""
    d = _backups()
    if schema is None or not (d / f"schema-{schema}.db").is_file():
        return None
    return _read_json(d / f"schema-{schema}.json") or {"date": None, "version": None}


def _copy_db(src, dst):
    """SQLite's own backup: a consistent copy even while the app writes."""
    a = sqlite3.connect(src, timeout=30)
    try:
        b = sqlite3.connect(dst, timeout=30)
        try:
            a.backup(b)
        finally:
            b.close()
    finally:
        a.close()


def backup_db(schema, version):
    """Copy the database (at `schema` migrations) before a version with more migrations starts.
    Only the newest few copies are kept."""
    d = _backups()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"schema-{schema}.db.tmp"
    tmp.unlink(missing_ok=True)
    _copy_db(_db_file(), tmp)
    os.replace(tmp, d / f"schema-{schema}.db")
    (d / f"schema-{schema}.json").write_text(json.dumps(
        {"date": datetime.now(timezone.utc).isoformat(), "version": version}))
    copies = sorted(d.glob("schema-*.json"), key=lambda f: (_read_json(f) or {}).get("date") or "", reverse=True)
    for f in copies[KEEP_DB_BACKUPS:]:
        f.unlink(missing_ok=True)
        f.with_suffix(".db").unlink(missing_ok=True)
    log.info("Database copied to %s before its migrations", d / f"schema-{schema}.db")


def restore_db(schema):
    """Put back the copy made at `schema` migrations. Run right before the restart."""
    from app import db as database
    src = _backups() / f"schema-{schema}.db"
    if database.engine is not None:
        database.engine.dispose()
    _copy_db(src, _db_file())
    log.info("Database put back as it was at %s migrations", schema)


def _db_plan(target, choice):
    """What switching to a version with `target` migrations does to the database: "backup" (it has
    more, so the database is copied first), "restore", or None. Raises NeedsDbChoice when the
    version is older than the database and the owner hasn't chosen."""
    have = db_schema()
    if target is None or have is None or target == have:
        return None
    if target > have:
        return "backup"
    backup = backup_info(target)
    if choice == "keep":
        return None
    if choice == "restore" and backup:
        return "restore"
    raise NeedsDbChoice(have, target, backup)


def _go(sha, migrations, db_choice):
    """Switch to a prepared version (None: the built-in code), minding the database."""
    global pending_restore
    plan = _db_plan(migrations, db_choice)
    if plan == "backup":
        backup_db(db_schema(), _short(current()))
    pending_restore = migrations if plan == "restore" else None
    _switch(sha)
    _cleanup()


def _read(path):
    return path.read_text().strip() if path.exists() else None


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_atomic(path, text):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _switch(sha):
    """Point code/current at a version; None runs the code built into the image."""
    code = code_dir()
    old = _read(code / "current")
    if old and old != sha:
        _write_atomic(code / "previous", old)
    if sha is None:
        (code / "current").unlink(missing_ok=True)
        return
    _write_atomic(code / "current", sha)
    if failed_sha() == sha:
        (code / "failed").unlink()


def _cleanup():
    """Remove downloaded versions other than the current and the previous one."""
    keep = {_read(code_dir() / "current"), _read(code_dir() / "previous")}
    for d in code_dir().iterdir():
        if d.is_dir() and d.name not in keep and not d.name.startswith("."):
            shutil.rmtree(d, ignore_errors=True)


# ---------------- going back ----------------

def _on_disk():
    """{sha: {sha, version, date, message}} of the downloaded versions this image can run right
    away (not the one that failed to start)."""
    out, img, failed = {}, image_sha(), failed_sha()
    if not code_dir().is_dir():
        return out
    for d in code_dir().iterdir():
        if (re.fullmatch(r"[0-9a-f]{40}", d.name) and d.name != failed and (d / "app" / "main.py").is_file()
                and (_read(d / ".image") or "") == img):
            info = _read_json(d / ".info.json") or {}
            out[d.name] = {"sha": d.name, **{k: info.get(k) for k in ("version", "date", "message")}}
    return out


def versions():
    """What the owner can go back to, newest first: earlier releases, the versions still on disk
    (`on_disk`: no download) and the code built into the image (`builtin`). Never raises: `error`
    says why GitHub's part is missing. Remembered in `offered`, the only versions rollback() takes."""
    global offered
    cur = current()
    found, error = {}, None
    try:
        for v in fetch_releases():
            found[v["sha"]] = {**v, "on_disk": False, "builtin": False}
    except Exception as e:
        log.warning("Could not list the releases: %s", e)
        error = f"Could not list the earlier versions on GitHub. {_why(e)}"
    for sha, meta in _on_disk().items():
        found[sha] = {**meta, **found.get(sha, {}), "on_disk": True, "builtin": False}
    img = image_sha()
    if img and not cur["builtin"] and (config.BUILTIN_DIR / "app" / "main.py").is_file():
        found[img] = {"sha": img, "version": None, "date": None, "message": None,
                      **found.get(img, {}), "on_disk": True, "builtin": True}
    since = cur["date"] or found.get(cur["sha"], {}).get("date")
    found.pop(cur["sha"], None)
    items = [{**v, "short": _short(v)} for v in found.values()
             if not (since and v.get("date") and v["date"] > since)]  # newer ones are updates
    items.sort(key=lambda v: v.get("date") or "", reverse=True)
    offered = {v["sha"]: v for v in items}
    return {"current": {**cur, "short": _short(cur)}, "versions": items, "error": error}


def _migrations_of(folder):
    """How many migrations the version in `folder` knows (remembered in its .info.json)."""
    info = _read_json(folder / ".info.json")
    if info is not None and "migrations" in info:
        return info["migrations"]
    n = _test(folder, import_main=False)
    if info is not None:
        try:
            (folder / ".info.json").write_text(json.dumps({**info, "migrations": n}))
        except OSError:
            pass
    return n


def _target(sha):
    """(sha for code/current, or None for the built-in code; its folder or None before a download)."""
    v = offered.get(sha)
    if v is None:
        raise RuntimeError("That version isn't in the list. Open the list of versions again.")
    if v["builtin"]:
        return None, config.BUILTIN_DIR
    d = code_dir() / sha
    return sha, d if v["on_disk"] and (d / "app" / "main.py").is_file() else None


def check_rollback(sha, db_choice=None):
    """Before a rollback starts: raises NeedsDbChoice right away for a version that's on disk."""
    _, folder = _target(sha)
    if folder is not None:
        _db_plan(_migrations_of(folder), db_choice)


def rollback(sha, db_choice=None, downloader=None):
    """Switch to an earlier version from versions(). Returns the sha of the version left."""
    left = current()["sha"]
    target, folder = _target(sha)
    if folder is None:
        folder = _prepare(sha, downloader or _download, meta=offered[sha])
    _go(target, _migrations_of(folder), db_choice)
    log.info("Went back from version %s to %s", (left or "?")[:7], sha[:7])
    return left


def skip_left(left):
    """After going back: don't offer the version that was left until a newer one is out."""
    from app import db as database
    if not left or (latest and latest.get("sha") != left):
        return  # what's offered is newer than the version left, so it may be offered
    with database.SessionLocal() as s:
        database.set_setting(s, "update_skipped", left)
        s.commit()


def _finish(old_current):
    """Right before the restart: put the database copy back if the switch asked for it. When that
    fails, the switch is undone and the error raised."""
    global pending_restore
    if pending_restore is None:
        return
    try:
        restore_db(pending_restore)
    except Exception:
        if old_current:
            _write_atomic(code_dir() / "current", old_current)
        else:
            (code_dir() / "current").unlink(missing_ok=True)
        raise
    finally:
        pending_restore = None


def restart():
    """Start the entrypoint again in this same process (it picks DATA_DIR/code/current).
    Works without Docker's auto-restart, and the container keeps its PID."""
    log.info("Restarting MangaVibe with the new version")
    logging.shutdown()
    os.execv("/bin/sh", ["/bin/sh", str(config.ENTRYPOINT)])


def _in_background(job, what):
    """Run job() (install or rollback; returns the version left after a rollback, else None),
    then restart. Progress is in `status`."""
    if not lock.acquire(blocking=False):
        raise RuntimeError("An update is already running")
    status.update(state="installing", message="Starting…", error=None, needs_db=None)

    def work():
        old = _read(code_dir() / "current")
        try:
            left = job()
            status.update(state="restarting", message="Restarting…")
            time.sleep(1.5)  # let the browser get the answer first
            _finish(old)
            skip_left(left)  # after the database is put back, or the restored copy would lose it
            restart()
        except NeedsDbChoice as e:
            status.update(state="error", message="", error=str(e), needs_db=e.detail)
        except Exception as e:
            log.error("%s failed: %s", what, e)
            status.update(state="error", message="", error=f"{what} failed. {_why(e)}")
            from app import activity
            activity.log("update.failed", what, details={"error": _why(e)[:500]})
        finally:
            lock.release()

    threading.Thread(target=work, daemon=True, name="update").start()


def install_in_background(sha=None, db_choice=None):
    """Used by the API: install, then restart."""
    _in_background(lambda: install(sha, db_choice=db_choice) and None, "Update")


def rollback_in_background(sha, db_choice=None):
    """Used by the API: go back to an earlier version, then restart."""
    _in_background(lambda: rollback(sha, db_choice), "Going back")
