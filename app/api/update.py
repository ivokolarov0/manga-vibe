"""Auto-update: what version runs, what is new on GitHub, install or skip it, or go back."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import updater
from app.db import get_db, get_setting, set_setting
from app.users import manage

router = APIRouter(prefix="/api/update", dependencies=[manage])  # admins only


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


class InstallBody(ShaBody):
    db: Literal["restore", "keep"] | None = None  # see updater.NeedsDbChoice


@router.post("/install")
def install(body: InstallBody):
    if not updater.supported():
        raise HTTPException(400, "Updates only work in the MangaVibe Docker image")
    latest = updater.latest or {}
    if latest.get("sha") != body.sha:
        raise HTTPException(409, "A newer version came out meanwhile. Reload the page.")
    if latest.get("needs_image"):
        raise HTTPException(409, "This version needs a new Docker image. See github.com/ivokolarov0/manga-vibe#updating")
    try:
        updater.install_in_background(body.sha, body.db)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/versions")
def versions():
    """Earlier versions to go back to (asks GitHub: only when the owner opens the list)."""
    if not updater.supported():
        raise HTTPException(400, "Updates only work in the MangaVibe Docker image")
    return updater.versions()


class RollbackBody(BaseModel):
    sha: str
    db: Literal["restore", "keep"] | None = None  # the answer when the version is older than the database


@router.post("/rollback")
def rollback(body: RollbackBody):
    if not updater.supported():
        raise HTTPException(400, "Updates only work in the MangaVibe Docker image")
    try:
        updater.check_rollback(body.sha, body.db)
        updater.rollback_in_background(body.sha, body.db)
    except updater.NeedsDbChoice as e:
        # 409 with the details: the browser asks what to do with the database and sends it back
        raise HTTPException(409, {"message": str(e), "needs_db": e.detail})
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
