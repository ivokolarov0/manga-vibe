"""Updates the app's code from the `releases` branch on GitHub, without a new Docker image.

How it works (see DECISIONS.md, "Auto-update"):
- The Docker image is the base: Python, unrar/7zip and the pip packages from its requirements.txt.
  It keeps a copy of its Dockerfile, entrypoint and requirements in /opt/mangavibe/image.
- A new version is downloaded into DATA_DIR/code/<sha>/. New or changed pip packages go into
  DATA_DIR/code/<sha>/deps. If `import app.main` works there, DATA_DIR/code/current is pointed at it
  and the app restarts. docker/entrypoint.sh starts the app from that folder.
- If the new version changes the Dockerfile or the entrypoint, files alone are not enough: the
  update is refused and the user is told to get a new image.
- A version that fails to start three times is put back by the entrypoint (DATA_DIR/code/failed).
"""
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import config

log = logging.getLogger("mangavibe.update")

CHECK_EVERY = 24 * 3600
IMAGE_FILES = {"Dockerfile": "Dockerfile", "docker/entrypoint.sh": "entrypoint.sh"}  # repo path -> copy in image
KEEP_FILES = ("app/", "examples/", "requirements.txt", "Dockerfile", "docker/entrypoint.sh")

lock = threading.Lock()
latest = None   # last check result: {sha, date, message, needs_image, checked_at} or None
status = {"state": "idle", "message": "", "error": None, "checked_at": None}  # state: idle|checking|installing|restarting|error


def code_dir():
    return config.DATA_DIR / "code"


def supported():
    """Updates only work inside the Docker image (the entrypoint picks the code folder)."""
    return config.IMAGE_DIR.is_dir() and config.ENTRYPOINT.is_file() and config.AUTO_UPDATE


def running_dir():
    """The folder the running code came from: DATA_DIR/code/<sha>, or None for the built-in code."""
    root = Path(__file__).resolve().parent.parent
    code = code_dir().resolve()
    return root if root.parent == code else None


def current():
    """{sha, date, message, builtin} of the running code."""
    d = running_dir()
    if d is not None:
        info = _read_json(d / ".info.json") or {}
        return {"sha": d.name, "date": info.get("date"), "message": info.get("message"), "builtin": False}
    sha_file = config.IMAGE_DIR / "sha"
    sha = sha_file.read_text().strip() if sha_file.exists() else ""
    return {"sha": sha or None, "date": None, "message": None, "builtin": True}


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
            return (f"GitHub answered 404. Is {config.UPDATE_REPO} public, "
                    f"and does it have a '{config.UPDATE_BRANCH}' branch?")
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
    """Newest commit on the release branch, and whether it needs a new Docker image."""
    with _client() as c:
        r = c.get(f"https://api.github.com/repos/{config.UPDATE_REPO}/commits/{config.UPDATE_BRANCH}")
        r.raise_for_status()
        data = r.json()
        sha = data["sha"]
        info = {"sha": sha, "date": data["commit"]["committer"]["date"],
                "message": data["commit"]["message"].split("\n")[0][:200]}
        if latest and latest.get("sha") == sha:
            info["needs_image"] = latest["needs_image"]
        else:
            files = {}
            for path in IMAGE_FILES:
                f = c.get(f"https://raw.githubusercontent.com/{config.UPDATE_REPO}/{sha}/{path}")
                f.raise_for_status()
                files[path] = f.text
            info["needs_image"] = needs_new_image(files)
    return info


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
    status.update(state="checking", error=None)
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


def info(skipped=None):
    cur = current()
    avail = None
    if latest and latest["sha"] != cur["sha"]:
        avail = {**latest, "short": latest["sha"][:7], "skipped": latest["sha"] == skipped,
                 "failed": latest["sha"] == failed_sha()}
    return {"supported": supported(), "current": {**cur, "short": (cur["sha"] or "")[:7]},
            "available": avail, "status": status, "repo": config.UPDATE_REPO, "branch": config.UPDATE_BRANCH}


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


def install(sha=None, downloader=_download):
    """Download, prepare and switch to a version. Returns the new folder. Raises with a readable message."""
    sha = sha or (latest or {}).get("sha")
    if not sha or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("No version to install. Check for updates first.")
    if latest and latest.get("sha") == sha and latest.get("needs_image"):
        raise RuntimeError("This version needs a new Docker image. See github.com/ivokolarov0/manga-vibe#updating")
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
        if extra:
            status["message"] = "Installing " + ", ".join(extra) + "…"
            log.info("Installing new packages for version %s: %s", sha[:7], ", ".join(extra))
            _run(["uv", "pip", "install", "--no-cache", "--python", sys.executable,
                  "--target", str(staging / "deps"), *extra], cwd=staging)
        status["message"] = "Testing the new version…"
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "DATA_DIR": tmp, "START_WORKERS": "0",
                   "PYTHONPATH": os.pathsep.join([str(staging / "deps"), str(staging)])}
            _run([sys.executable, "-c", "import app.main"], cwd=staging, env=env, timeout=120)
        meta = latest if latest and latest.get("sha") == sha else {"sha": sha}
        (staging / ".info.json").write_text(json.dumps({k: meta.get(k) for k in ("sha", "date", "message")}))
        final = code / sha
        if final.exists():
            shutil.rmtree(final)
        staging.chmod(0o755)  # mkdtemp makes it private; the entrypoint may read it as another user
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _switch(sha)
    _cleanup(keep={sha, _read(code / "previous")})
    log.info("Installed version %s", sha[:7])
    return final


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
    code = code_dir()
    old = _read(code / "current")
    if old and old != sha:
        _write_atomic(code / "previous", old)
    _write_atomic(code / "current", sha)
    if failed_sha() == sha:
        (code / "failed").unlink()


def _cleanup(keep):
    for d in code_dir().iterdir():
        if d.is_dir() and d.name not in keep and not d.name.startswith("."):
            shutil.rmtree(d, ignore_errors=True)


def restart():
    """Start the entrypoint again in this same process (it picks DATA_DIR/code/current).
    Works without Docker's auto-restart, and the container keeps its PID."""
    log.info("Restarting MangaVibe with the new version")
    logging.shutdown()
    os.execv("/bin/sh", ["/bin/sh", str(config.ENTRYPOINT)])


def install_in_background(sha=None):
    """Used by the API: install, then restart. Progress is in `status`."""
    if not lock.acquire(blocking=False):
        raise RuntimeError("An update is already running")
    status.update(state="installing", message="Starting…", error=None)

    def work():
        try:
            install(sha)
            status.update(state="restarting", message="Restarting…")
            time.sleep(1.5)  # let the browser get the answer first
            restart()
        except Exception as e:
            log.error("Update failed: %s", e)
            status.update(state="error", message="", error=f"Update failed. {_why(e)}")
        finally:
            lock.release()

    threading.Thread(target=work, daemon=True, name="update").start()
