"""Auto-update: what version runs, what is new on GitHub, install or skip it."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import updater
from app.db import get_db, get_setting, set_setting

router = APIRouter(prefix="/api/update")


def _info(db):
    return updater.info(skipped=get_setting(db, "update_skipped"))


@router.get("")
def update_info(db=Depends(get_db)):
    return _info(db)


@router.post("/check")
def check_now(db=Depends(get_db)):
    if not updater.supported():
        raise HTTPException(400, "Updates only work in the MangaVibe Docker image")
    updater.check(force=True)
    db.expire_all()
    return _info(db)


class ShaBody(BaseModel):
    sha: str


@router.post("/install")
def install(body: ShaBody):
    if not updater.supported():
        raise HTTPException(400, "Updates only work in the MangaVibe Docker image")
    latest = updater.latest or {}
    if latest.get("sha") != body.sha:
        raise HTTPException(409, "A newer version came out meanwhile. Reload the page.")
    if latest.get("needs_image"):
        raise HTTPException(409, "This version needs a new Docker image. See docs/SYNOLOGY.md → Updating.")
    try:
        updater.install_in_background(body.sha)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/skip")
def skip(body: ShaBody, db=Depends(get_db)):
    """Don't offer this version again (a newer one is offered as usual)."""
    set_setting(db, "update_skipped", body.sha)
    db.commit()
    return _info(db)


@router.delete("/skip")
def unskip(db=Depends(get_db)):
    set_setting(db, "update_skipped", None)
    db.commit()
    return _info(db)
