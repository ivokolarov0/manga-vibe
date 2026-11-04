"""Libraries (Manga, Comics...; shelves in the code, see app/shelves.py): list, add, rename, delete,
reading mode, password, unlock and lock, who owns them and who they're shared with (app/access.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func

from app import shelves
from app.access import SHARED, Viewer, viewer
from app.db import get_db
from app.direction import MODES
from app.models import Library, Series, SeriesShare, Shelf, ShelfShare, User

router = APIRouter(prefix="/api/shelves")


def _set_cookie(response, value):
    # no max-age: the browser forgets it when it closes; each token also runs out (UNLOCK_HOURS)
    if value:
        response.set_cookie(shelves.COOKIE, value, httponly=True, samesite="lax", path="/")
    else:
        response.delete_cookie(shelves.COOKIE, path="/")


def counts(db, locked):
    """{shelf id: series} of the series on each shelf, none for locked shelves."""
    col = shelves.shelf_column().label("shelf")
    rows = (db.query(col, func.count(Series.id)).filter(Series.missing_since.is_(None))
            .group_by(col).all())
    return {sid: n for sid, n in rows if sid not in locked}


def _shared_counts(db, user_id):
    """{shelf id or SHARED: series} of the series shared with the account on their own, by where it put them."""
    rows = (db.query(SeriesShare.shelf_id, func.count(Series.id)).join(Series, Series.id == SeriesShare.series_id)
            .filter(SeriesShare.user_id == user_id, Series.missing_since.is_(None))
            .group_by(SeriesShare.shelf_id).all())
    return {SHARED if sid is None else sid: n for sid, n in rows}


@router.get("")
def list_shelves(request: Request, all: bool = False, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """The account's libraries: its own, then the ones shared with it, then "Shared with me" (id -1)
    when series were shared with it on their own. all=true (the admin): every library, with its owner
    (Settings → Library folders). locked: it has a password and this browser hasn't unlocked it (its
    series count is then left out). shared_with: the accounts it's shared with (for its owner)."""
    if all and not v.is_admin:
        raise HTTPException(403, "Only the admin can do this")
    locked = shelves.locked_for(db, request)
    n = counts(db, locked)
    alone = _shared_counts(db, v.id)
    names = dict(db.query(User.id, User.name))
    folders = {}
    if v.is_admin:
        for f in db.query(Library).order_by(Library.id):
            folders.setdefault(f.shelf_id, []).append({"id": f.id, "name": f.name, "root_path": f.root_path})
    shares = {}
    for sid, uid in db.query(ShelfShare.shelf_id, ShelfShare.user_id):
        shares.setdefault(sid, []).append(uid)
    q = db.query(Shelf)
    if not all:
        q = q.filter(Shelf.id.in_(sorted(v.shelves)))
    rows = sorted(q, key=lambda s: (s.owner_id != v.id, s.id))  # its own first
    out = []
    for s in rows:
        mine = s.owner_id == v.id
        out.append({
            "id": s.id, "name": s.name, "reader_mode": s.reader_mode, "has_password": bool(s.password_hash),
            "locked": s.id in locked,
            "series": None if s.id in locked else n.get(s.id, 0) + alone.get(s.id, 0),
            "folders": folders.get(s.id, []), "mine": mine,
            "owner": {"id": s.owner_id, "name": names.get(s.owner_id, "")} if s.owner_id else None,
            "shared_with": sorted(shares.get(s.id, [])) if mine or v.is_admin else [],
        })
    if alone.get(SHARED):
        out.append({"id": SHARED, "name": "Shared with me", "virtual": True, "reader_mode": None,
                    "has_password": False, "locked": False, "series": alone[SHARED], "folders": [],
                    "mine": False, "owner": None, "shared_with": []})
    return out


Name = Field(min_length=1, max_length=60)


def _check_name(db, name, owner_id, shelf_id=None):
    """A library's name: unique among one account's libraries."""
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the library a name")
    same = (db.query(Shelf).filter(func.lower(Shelf.name) == name.lower(), Shelf.owner_id == owner_id,
                                   Shelf.id != (shelf_id or -1)).first())
    if same:
        raise HTTPException(400, f"There is already a library called {same.name}")
    return name


class ShelfBody(BaseModel):
    name: str = Name


@router.post("")
def add_shelf(body: ShelfBody, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """A new library of the account's own."""
    shelf = Shelf(name=_check_name(db, body.name, v.id), owner_id=v.id)
    db.add(shelf)
    db.commit()
    shelves.invalidate()
    return {"id": shelf.id, "name": shelf.name}


class ShelfPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    reader_mode: str | None = None  # paged | vertical, or "default" (Settings → Reader)
    owner_id: int | None = None     # the admin gives the library to another account


@router.patch("/{shelf_id}")
def patch_shelf(shelf_id: int, body: ShelfPatch, request: Request, db=Depends(get_db),
                v: Viewer = Depends(viewer)):
    """Rename the library, set the reading mode of its series that don't have their own, or (the
    admin) give it to another account. A locked library must be unlocked first. Once changed it's
    kept: removing its folder doesn't remove it."""
    shelf = v.own_shelf_or_403(db, shelf_id)
    _unlocked_or_403(db, request, shelf)
    shelf.made_for_folder = None
    if body.owner_id is not None and body.owner_id != shelf.owner_id:
        if not v.is_admin:
            raise HTTPException(403, "Only the admin can give a library to another account")
        if db.get(User, body.owner_id) is None:
            raise HTTPException(404, "Account not found")
        if body.name is not None:  # the new name must be free among the new owner's libraries
            shelf.name = _check_name(db, body.name, body.owner_id, shelf.id)
        shelf.owner_id = body.owner_id
        # its shares with the new owner go, others' series in it go back to Shared with me, and a
        # name the new owner already uses gets " (2)"
        shelves.owner_changed(db, [shelf.id], body.owner_id)
    elif body.name is not None:
        shelf.name = _check_name(db, body.name, shelf.owner_id, shelf.id)
    if body.reader_mode is not None:
        if body.reader_mode != "default" and body.reader_mode not in MODES:
            raise HTTPException(400, "Unknown reading mode")
        shelf.reader_mode = None if body.reader_mode == "default" else body.reader_mode
    db.commit()
    shelves.invalidate()
    return {"ok": True}


def _unlocked_or_403(db, request, shelf):
    if shelf.id in shelves.locked_for(db, request):
        raise HTTPException(403, f"Unlock {shelf.name} first")


class SharesBody(BaseModel):
    """The accounts to share a library (or a series, app/api/users.py) with; the others stop."""
    user_ids: list[int] = Field(max_length=1000)


@router.put("/{shelf_id}/shares")
def set_shares(shelf_id: int, body: SharesBody, request: Request, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """Share the whole library with these accounts (read only), and stop sharing it with the others.
    Series added to it later are shared too. Their progress stays when it stops, for next time."""
    shelf = v.own_shelf_or_403(db, shelf_id)
    _unlocked_or_403(db, request, shelf)
    return {"ok": True, "shared_with": shelves.replace_shares(db, ShelfShare.shelf_id, shelf.id, body.user_ids,
                                                               shelf.owner_id)}


@router.delete("/{shelf_id}")
def delete_shelf(shelf_id: int, request: Request, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """Delete the library, not its series: its folders go to another library (of the same owner, one
    without a password if there is one), and its series that were moved here by hand follow their
    folder again. A locked library must be unlocked first, or this would show its series elsewhere."""
    shelf = v.own_shelf_or_403(db, shelf_id)
    _unlocked_or_403(db, request, shelf)
    other = shelves.fallback_for(db, shelf.id)
    if other is None:
        raise HTTPException(400, "This is the only library. Add another one first.")
    if db.query(Library).filter(Library.shelf_id == shelf.id).first() and not v.is_admin:
        raise HTTPException(403, "Its folders' series would move to another library: ask the admin")
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
def set_password(shelf_id: int, body: PasswordBody, request: Request, response: Response, db=Depends(get_db),
                 v: Viewer = Depends(viewer)):
    """Set, change or remove the password (the library's owner). Changing or removing it needs the
    current one. Browsers unlocked with the old password are locked again; this one stays unlocked."""
    shelf = v.own_shelf_or_403(db, shelf_id)
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
    db.commit()
    shelves.invalidate()
    _set_cookie(response, cookie)
    return {"ok": True, "has_password": bool(shelf.password_hash)}


class UnlockBody(BaseModel):
    password: str = Field(max_length=200)


@router.post("/{shelf_id}/unlock")
def unlock(shelf_id: int, body: UnlockBody, request: Request, response: Response, db=Depends(get_db),
           v: Viewer = Depends(viewer)):
    shelf = db.get(Shelf, shelf_id)
    if shelf is None or shelf.id not in v.shelves:
        raise HTTPException(404, "Library not found")
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
