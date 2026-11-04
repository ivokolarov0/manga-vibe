"""Accounts (app/users.py): signing in and out, the profile picker, the account's own name and
password, the admin's list of accounts, and sharing one series with other accounts."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func

from app import shelves, users
from app.access import Viewer, admin, viewer
from app.api.common import get_or_404
from app.api.shelves import SharesBody
from app.db import get_db, get_setting
from app.models import Series, SeriesShare, Shelf, ShelfShare, User

router = APIRouter(prefix="/api")

Name = Field(min_length=1, max_length=40)
OptionalName = Field(default=None, min_length=1, max_length=40)
Secret = Field(default="", max_length=200)


def _me(a):
    return {"id": a.id, "name": a.name, "is_admin": a.is_admin, "has_password": a.has_password,
            "password_kind": a.password_kind}


def _set_session(response, token, remember):
    response.set_cookie(users.COOKIE, token, httponly=True, samesite="lax", path="/",
                        max_age=users.SESSION_DAYS * 86400 if remember else None)


# ---------------------------------------------------------------- signing in

@router.get("/auth")
def auth(request: Request, db=Depends(get_db)):
    """Who this browser is. user: the signed-in account, or None (the page shows the sign-in screen).
    open: nobody has to sign in (one account without a password). profiles: the accounts to pick
    from on the sign-in screen (unless the admin turned the picker off)."""
    found = users.accounts(db)
    me = users.account_of(db, request.cookies.get(users.COOKIE))
    picker = get_setting(db, "profile_picker", True)
    return {"user": _me(me) if me else None, "open": users.open_account(db) is not None,
            "accounts": len(found), "picker": picker,
            "profiles": [{"id": a.id, "name": a.name, "password_kind": a.password_kind} for a in found.values()]
            if picker and not me else None}


class SignIn(BaseModel):
    user_id: int | None = None   # from the profile picker
    name: str | None = Field(default=None, max_length=40)  # typed in, when the picker is off
    password: str = Secret
    remember: bool = True


@router.post("/auth/sign-in")
def sign_in(body: SignIn, response: Response, db=Depends(get_db)):
    """From the profile picker (user_id) or by name. With the picker hidden, only by name, and a wrong
    name and a wrong password look alike (same answer, same time), so the names stay private."""
    picker = get_setting(db, "profile_picker", True)
    by_name = body.user_id is None
    if not by_name and not picker:
        raise HTTPException(403, "Sign in with your name")
    if by_name:
        name = (body.name or "").strip().lower()
        user = db.query(User).filter(func.lower(User.name) == name).first() if name else None
    else:
        user = db.get(User, body.user_id)
    if user is None:
        users.check_nobody(body.password)  # as slow as a real try
        raise HTTPException(403, "Wrong name or password")
    try:
        ok = users.verify(user, body.password)
    except shelves.TooManyTries as e:
        raise HTTPException(429, str(e)) from None
    if not ok:
        # the picker already shows which profiles use a PIN; by name, nothing is given away
        raise HTTPException(403, "Wrong name or password" if by_name
                            else "Wrong PIN" if user.password_kind == "pin" else "Wrong password")
    token = users.make_session(db, user, body.remember)
    db.commit()  # the signing key, the first time
    _set_session(response, token, body.remember)
    # the libraries the last person unlocked in this browser lock again: the password is theirs
    response.delete_cookie(shelves.COOKIE, path="/")
    return {"ok": True, "user": _me(users.accounts(db)[user.id])}


@router.post("/auth/sign-out")
def sign_out(response: Response):
    """Sign this browser out (its unlocked libraries lock again too)."""
    response.delete_cookie(users.COOKIE, path="/")
    response.delete_cookie(shelves.COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- the account's own

class AccountPatch(BaseModel):
    name: str | None = OptionalName


def _check_name(db, name, user_id=None):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the account a name")
    same = db.query(User).filter(func.lower(User.name) == name.lower(), User.id != (user_id or -1)).first()
    if same:
        raise HTTPException(400, f"There is already an account called {same.name}")
    return name


@router.patch("/account")
def rename_me(body: AccountPatch, db=Depends(get_db), v: Viewer = Depends(viewer)):
    user = db.get(User, v.id)
    if body.name is not None:
        user.name = _check_name(db, body.name, user.id)
    db.commit()
    users.invalidate()
    return {"ok": True}


class PasswordBody(BaseModel):
    current: str = Secret
    password: str = Secret   # "": no password (or PIN) any more
    kind: str = "password"   # password | pin


@router.put("/account/password")
def set_my_password(body: PasswordBody, request: Request, response: Response, db=Depends(get_db),
                    v: Viewer = Depends(viewer)):
    """Set, change or remove the account's password or PIN; the current one is needed. Other
    browsers signed in to it are signed out; this one stays signed in, as long as it was before
    ("Stay signed in" or until the browser closes)."""
    user = db.get(User, v.id)
    try:
        if user.password_hash and not users.verify(user, body.current):
            raise HTTPException(403, "The current password is wrong")
    except shelves.TooManyTries as e:
        raise HTTPException(429, str(e)) from None
    if not body.password and not user.password_hash:
        raise HTTPException(400, "Type a password")
    if not body.password and user.is_admin and len(users.accounts(db)) > 1:
        raise HTTPException(400, "An admin needs a password or PIN while there are other accounts")
    try:
        users.set_password(user, body.password or None, body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    cookie = request.cookies.get(users.COOKIE)
    # no session yet (the one account without a password): the long kind, like a first sign-in
    remember = users.remembered(cookie) if cookie else True
    token = users.make_session(db, user, remember)
    db.commit()
    users.invalidate()
    _set_session(response, token, remember)
    return {"ok": True}


@router.post("/account/sign-out-everywhere")
def sign_out_everywhere(response: Response, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """End every session of the account, this one included."""
    user = db.get(User, v.id)
    user.session_version = (user.session_version or 0) + 1
    db.commit()
    users.invalidate()
    response.delete_cookie(users.COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- every account

@router.get("/users")
def list_users(db=Depends(get_db), v: Viewer = Depends(viewer)):
    """The accounts, for sharing (name and id); the admin also gets what Settings → Accounts shows."""
    out = []
    owned = dict(db.query(Shelf.owner_id, func.count(Shelf.id)).group_by(Shelf.owner_id).all()) if v.is_admin else {}
    for u in db.query(User).order_by(User.id):
        row = {"id": u.id, "name": u.name, "me": u.id == v.id}
        if v.is_admin:
            row.update(is_admin=bool(u.is_admin), has_password=bool(u.password_hash),
                       password_kind=u.password_kind if u.password_hash else None,
                       libraries=owned.get(u.id, 0), created_at=u.created_at.isoformat() if u.created_at else None)
        out.append(row)
    return out


class NewUser(BaseModel):
    name: str = Name
    password: str = Secret  # "": none (a click on the profile is enough)
    kind: str = "password"
    is_admin: bool = False


def _admins(db):
    return db.query(User).filter(User.is_admin.is_(True)).count()


@router.post("/users/admin")
def add_user(body: NewUser, db=Depends(get_db), v: Viewer = Depends(admin)):
    """A new account. It starts with no libraries: share some with it, or give it one (Settings →
    Libraries). Before the second account exists the admin needs a password, or anyone could be it."""
    me = db.get(User, v.id)
    if not me.password_hash:
        raise HTTPException(400, "Set a password or PIN for your own account first (Settings → Account): "
                                 "with more than one account, people sign in")
    if body.is_admin and not body.password:
        raise HTTPException(400, "An admin needs a password or PIN")
    user = User(name=_check_name(db, body.name), is_admin=body.is_admin)
    if body.password:
        try:
            users.set_password(user, body.password, body.kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    db.add(user)
    db.commit()
    users.invalidate()
    return {"id": user.id, "name": user.name}


class UserPatch(BaseModel):
    name: str | None = OptionalName
    is_admin: bool | None = None
    password: str | None = Field(default=None, max_length=200)  # a new one; "": remove it
    kind: str = "password"


@router.patch("/users/admin/{user_id}")
def patch_user(user_id: int, body: UserPatch, db=Depends(get_db), v: Viewer = Depends(admin)):
    """Rename an account, make it an admin (or not), or reset its password (which signs it out). Your
    own password is changed in Settings → Account (with the current one), so you stay signed in."""
    user = get_or_404(db, User, user_id)
    if body.password is not None and user.id == v.id:
        raise HTTPException(400, "Change your own password in Settings → Account")
    if body.name is not None:
        user.name = _check_name(db, body.name, user.id)
    if body.password is not None:  # first: making it an admin in the same request needs it
        try:
            users.set_password(user, body.password or None, body.kind)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    admin_now = bool(user.is_admin) if body.is_admin is None else body.is_admin
    if admin_now != bool(user.is_admin):
        if not admin_now and _admins(db) <= 1:
            raise HTTPException(400, "There must be at least one admin")
        user.is_admin = admin_now
        if user.id != v.id:  # what it may do changed: it signs in again (you stay signed in)
            user.session_version = (user.session_version or 0) + 1
    if admin_now and not user.password_hash:
        raise HTTPException(400, "An admin needs a password or PIN")
    db.commit()
    users.invalidate()
    return {"ok": True}


@router.delete("/users/admin/{user_id}")
def delete_user(user_id: int, db=Depends(get_db), v: Viewer = Depends(admin)):
    """Remove an account with its progress, statuses, favorites and settings. Its libraries become
    the admin's (yours); nothing is deleted from the drive."""
    user = get_or_404(db, User, user_id)
    if user.id == v.id:
        raise HTTPException(400, "You can't remove your own account")
    moved = [sid for (sid,) in db.query(Shelf.id).filter(Shelf.owner_id == user.id)]
    db.query(Shelf).filter(Shelf.id.in_(moved)).update({Shelf.owner_id: v.id}, synchronize_session=False)
    shelves.owner_changed(db, moved, v.id)  # e.g. its series shared with you don't show twice now
    db.delete(user)
    db.commit()
    users.forget(user_id)
    shelves.invalidate()
    return {"ok": True, "libraries_moved": len(moved)}


# ---------------------------------------------------------------- one series, shared on its own

@router.get("/series/{series_id}/shares")
def series_shares(series_id: int, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """For the Share dialog (the series' owner): every other account, whether the series is shared
    with it on its own, and whether it sees it anyway through a library shared with it."""
    s = get_or_404(db, Series, series_id)
    on = shelves.shelf_id_of(db, s)
    owner = db.get(Shelf, on).owner_id if on is not None else None
    alone = {uid for (uid,) in db.query(SeriesShare.user_id).filter_by(series_id=s.id)}
    via = {uid for (uid,) in db.query(ShelfShare.user_id).filter_by(shelf_id=on)} if on is not None else set()
    return [{"id": u.id, "name": u.name, "shared": u.id in alone, "via_library": u.id in via}
            for u in db.query(User).order_by(User.id) if u.id != owner]


@router.put("/series/{series_id}/shares")
def set_series_shares(series_id: int, body: SharesBody, db=Depends(get_db), v: Viewer = Depends(viewer)):
    """Share the series on its own with these accounts (read only), and stop sharing it with the
    others. It shows in their "Shared with me" library until they put it in one of theirs. Their
    progress stays when it stops, for next time."""
    s = get_or_404(db, Series, series_id)
    v.edit_or_403(db, s.id)
    on = shelves.shelf_id_of(db, s)
    owner = db.get(Shelf, on).owner_id if on is not None else None
    return {"ok": True, "shared_with": shelves.replace_shares(db, SeriesShare.series_id, s.id, body.user_ids, owner)}
