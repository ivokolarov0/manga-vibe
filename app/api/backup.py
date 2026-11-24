"""Settings → Backup: export everything into one file, import it again (app/backup.py, issue #262)."""
import os
import re
import tempfile
import time
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app import backup, config
from app.i18n import t
from app.users import manage

router = APIRouter(prefix="/api/backup", dependencies=[manage])  # admins only

READY_PREFIX = "export-"
READY_NAME = re.compile(r"^export-[\w-]+\.zip$")
READY_KEEP_SECONDS = 3600  # a prepared export nobody downloaded is removed after an hour


def _work() -> Path:
    d = config.DATA_DIR / ".backup-work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _temp_file(suffix, prefix="tmp") -> Path:
    """A file on the data volume (an export can be big; /tmp may be small)."""
    fd, name = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=_work())
    os.close(fd)
    return Path(name)


def _file_name() -> str:
    return f"mangavibe-backup-{date.today().isoformat()}.zip"


def _build(tokens: bool, prefix="tmp") -> Path:
    path = _temp_file(".zip", prefix)
    try:
        backup.export_to(path, include_tokens=tokens)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


@router.get("/export")
def export(tokens: bool = False):
    """The export as a download. `tokens`: the GitHub token and the plugins' secret settings go along."""
    path = _build(tokens)
    return FileResponse(path, media_type="application/zip", filename=_file_name(),
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.post("/export")
def prepare_export(tokens: bool = False):
    """Make the export and say where to download it (GET /download/<id>). The page shows that it's
    working while this runs (it can take a while), then the browser downloads the ready file at once."""
    now = time.time()
    for old in _work().glob(READY_PREFIX + "*.zip"):  # ones nobody downloaded
        try:
            if now - old.stat().st_mtime > READY_KEEP_SECONDS:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    path = _build(tokens, READY_PREFIX)
    return {"id": path.name, "file": _file_name(), "size": path.stat().st_size}


@router.get("/download/{export_id}")
def download(export_id: str):
    """A prepared export (POST /export), once: it's removed after it was sent."""
    path = _work() / export_id
    if not READY_NAME.match(export_id) or not path.is_file():
        raise HTTPException(404, "This export isn't there anymore: export again.")
    return FileResponse(path, media_type="application/zip", filename=_file_name(),
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.post("/import")
async def import_(request: Request):
    """Replace the database, posters and custom.css with an export, sent as the request's body (the
    file itself, not a form: it goes straight to the data volume). The database before it is kept in
    /data/backups."""
    path = _temp_file(".zip")
    try:
        with open(path, "wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
        return await run_in_threadpool(backup.import_from, path)
    except backup.ImportRefused as e:
        raise HTTPException(400, t(e.text, **e.values))
    except backup.Busy as e:
        raise HTTPException(409, t(e.text, **e.values))
    finally:
        path.unlink(missing_ok=True)
