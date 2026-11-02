"""Libraries (Manga, Comics...; shelves in the code, see app/shelves.py): list, add, rename, delete,
reading mode, password, unlock and lock."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func

from app import shelves
from app.api.common import get_or_404
from app.db import get_db
from app.direction import MODES
from app.models import Library, Series, Shelf

router = APIRouter(prefix="/api/shelves")


def _set_cookie(response, value):
    # no max-age: the browser forgets it when it closes; each token also runs out (UNLOCK_HOURS)
    if value:
        response.set_cookie(shelves.COOKIE, value, httponly=True, samesite="lax", path="/")
    else:
        response.delete_cookie(shelves.COOKIE, path="/")


def counts(db, locked):
    """{shelf id: series} of the series shown, none for locked shelves."""
    col = shelves.shelf_column().label("shelf")
    rows = (db.query(col, func.count(Series.id)).filter(Series.missing_since.is_(None))
            .group_by(col).all())
    return {sid: n for sid, n in rows if sid not in locked}


@router.get("")
def list_shelves(request: Request, db=Depends(get_db)):
    """Every library, in the order they were made. locked: it has a password and this browser hasn't
    unlocked it (its series count is then left out)."""
    locked = shelves.locked_for(db, request)
    n = counts(db, locked)
    folders = {}
    for f in db.query(Library).order_by(Library.id):
        folders.setdefault(f.shelf_id, []).append({"id": f.id, "name": f.name, "root_path": f.root_path})
    return [{"id": s.id, "name": s.name, "reader_mode": s.reader_mode, "has_password": bool(s.password_hash),
             "locked": s.id in locked, "series": None if s.id in locked else n.get(s.id, 0),
             "folders": folders.get(s.id, [])}
            for s in db.query(Shelf).order_by(Shelf.id)]


Name = Field(min_length=1, max_length=60)


def _check_name(db, name, shelf_id=None):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the library a name")
    same = db.query(Shelf).filter(func.lower(Shelf.name) == name.lower(), Shelf.id != (shelf_id or -1)).first()
    if same:
        raise HTTPException(400, f"There is already a library called {same.name}")
    return name


class ShelfBody(BaseModel):
    name: str = Name


@router.post("")
def add_shelf(body: ShelfBody, db=Depends(get_db)):
    shelf = Shelf(name=_check_name(db, body.name))
    db.add(shelf)
    db.commit()
    return {"id": shelf.id, "name": shelf.name}


class ShelfPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    reader_mode: str | None = None  # paged | vertical, or "default" (Settings → Reader)


@router.patch("/{shelf_id}")
def patch_shelf(shelf_id: int, body: ShelfPatch, request: Request, db=Depends(get_db)):
    """Rename the library, or set the reading mode of its series that don't have their own. A locked
    library must be unlocked first. Once changed it's the owner's: removing its folder keeps it."""
    shelf = get_or_404(db, Shelf, shelf_id)
    _unlocked_or_403(db, request, shelf)
    shelf.made_for_folder = None
    if body.name is not None:
        shelf.name = _check_name(db, body.name, shelf.id)
    if body.reader_mode is not None:
        if body.reader_mode != "default" and body.reader_mode not in MODES:
            raise HTTPException(400, "Unknown reading mode")
        shelf.reader_mode = None if body.reader_mode == "default" else body.reader_mode
    db.commit()
    return {"ok": True}


def _unlocked_or_403(db, request, shelf):
    if shelf.id in shelves.locked_for(db, request):
        raise HTTPException(403, f"Unlock {shelf.name} first")


@router.delete("/{shelf_id}")
def delete_shelf(shelf_id: int, request: Request, db=Depends(get_db)):
    """Delete the library, not its series: its folders go to another library (one without a password
    if there is one), and its series that were moved here by hand follow their folder again. A locked
    library must be unlocked first, or this would show its series elsewhere."""
    shelf = get_or_404(db, Shelf, shelf_id)
    _unlocked_or_403(db, request, shelf)
    other = shelves.fallback_for(db, shelf.id)
    if other is None:
        raise HTTPException(400, "This is the only library. Add another one first.")
    db.query(Library).filter(Library.shelf_id == shelf.id).update({Library.shelf_id: other.id})
    db.query(Series).filter(Series.shelf_id == shelf.id).update({Series.shelf_id: None})
    db.delete(shelf)
    db.commit()
    shelves.invalidate()
    return {"ok": True, "moved_to": {"id": other.id, "name": other.name}}


class PasswordBody(BaseModel):
    current: str = Field(default="", max_length=200)
    password: str = Field(default="", max_length=200)  # "": no password any more


def _verify_or_403(shelf, password):
    try:
        if not shelves.verify(shelf, password):
            raise HTTPException(403, "Wrong password")
    except shelves.TooManyTries as e:
        raise HTTPException(429, str(e)) from None


@router.put("/{shelf_id}/password")
def set_password(shelf_id: int, body: PasswordBody, request: Request, response: Response, db=Depends(get_db)):
    """Set, change or remove the password. Changing or removing it needs the current one. Browsers
    unlocked with the old password are locked again; this one stays unlocked."""
    shelf = get_or_404(db, Shelf, shelf_id)
    if shelf.password_hash:
        _verify_or_403(shelf, body.current)
    elif not body.password:
        raise HTTPException(400, "Type a password")
    shelf.password_hash = shelves.hash_password(body.password) if body.password else None
    shelf.lock_version = (shelf.lock_version or 0) + 1
    shelf.made_for_folder = None  # set up by the owner: kept when its folder goes
    cookie = shelves.cookie_without(request.cookies.get(shelves.COOKIE), shelf.id)
    if shelf.password_hash:
        cookie = shelves.cookie_with(cookie, shelves.make_token(db, shelf))
        shelves.protected_hint = True
    db.commit()
    shelves.invalidate()
    _set_cookie(response, cookie)
    return {"ok": True, "has_password": bool(shelf.password_hash)}


class UnlockBody(BaseModel):
    password: str = Field(max_length=200)


@router.post("/{shelf_id}/unlock")
def unlock(shelf_id: int, body: UnlockBody, request: Request, response: Response, db=Depends(get_db)):
    shelf = get_or_404(db, Shelf, shelf_id)
    if not shelf.password_hash:
        return {"ok": True}
    _verify_or_403(shelf, body.password)
    token = shelves.make_token(db, shelf)
    db.commit()  # the signing key, the first time
    _set_cookie(response, shelves.cookie_with(request.cookies.get(shelves.COOKIE), token))
    return {"ok": True, "hours": shelves.UNLOCK_HOURS}


@router.post("/{shelf_id}/lock")
def lock(shelf_id: int, request: Request, response: Response):
    """Lock now: this browser needs the password again."""
    _set_cookie(response, shelves.cookie_without(request.cookies.get(shelves.COOKIE), shelf_id))
    return {"ok": True}


@router.post("/lock")
def lock_all(response: Response):
    """Lock every library again in this browser (the page does it when a new tab starts)."""
    _set_cookie(response, "")
    return {"ok": True}
