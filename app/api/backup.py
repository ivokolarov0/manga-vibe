"""Settings → Backup: export everything into one file, import it again (app/backup.py, issue #262)."""
import os
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app import backup, config
from app.users import manage

router = APIRouter(prefix="/api/backup", dependencies=[manage])  # admins only


def _temp_file(suffix) -> Path:
    """A file on the data volume (an export can be big; /tmp may be small)."""
    d = config.DATA_DIR / ".backup-work"
    d.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=suffix, dir=d)
    os.close(fd)
    return Path(name)


@router.get("/export")
def export(tokens: bool = False):
    """The export as a download. `tokens`: the GitHub token and the plugins' secret settings go along."""
    path = _temp_file(".zip")
    try:
        backup.export_to(path, include_tokens=tokens)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(path, media_type="application/zip",
                        filename=f"mangavibe-backup-{date.today().isoformat()}.zip",
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
        raise HTTPException(400, str(e))
    except backup.Busy as e:
        raise HTTPException(409, str(e))
    finally:
        path.unlink(missing_ok=True)
