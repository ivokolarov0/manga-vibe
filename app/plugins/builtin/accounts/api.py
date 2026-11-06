"""The accounts plugin's API, at /api/plugins/accounts/api/...: who am I (/me), the first admin
(/setup), log in and out, my password; and for admins, the accounts (/accounts)."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import users
from app.passwords import TooManyTries, check_password, hash_password
from app.plugins import Context
from app.plugins.web import plugin_ctx
from app.users import manage

from . import core

router = APIRouter()
admin = APIRouter(prefix="/accounts", dependencies=[manage])

Name = Field(min_length=1, max_length=40)
Password = Field(min_length=4, max_length=200)


def _name(data, name, account_id=None):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Give the account a name")
    same = core.by_name(data, name)
    if same and same["id"] != account_id:
        raise HTTPException(400, f"There is already an account called {same['name']}")
    return name


@router.get("/me")
def me(ctx: Context = Depends(plugin_ctx)):
    """The logged-in account (None when not logged in), and whether the first admin must be made."""
    user = users.current()
    data = core.load(ctx.db)
    account = core.find(data, user.id) if user.id else None
    return {"user": core.public(account) if account else None, "setup": not data["list"]}


class LoginBody(BaseModel):
    name: str = Name
    password: str = Field(max_length=200)


@router.post("/setup")
def setup(body: LoginBody, response: Response, ctx: Context = Depends(plugin_ctx)):
    """The first account, an admin, while there is none. It gets everything read so far (user 1)."""
    if len(body.password) < 4:
        raise HTTPException(400, "Use a password of at least 4 characters")
    password_hash = hash_password(body.password)
    with core.setup_lock:  # two at once: only the first makes it
        core.invalidate()  # from the database, not the cache
        data = core.copy(ctx.db)
        if data["list"]:
            raise HTTPException(409, "There are accounts already: log in")
        account = {"id": users.OWNER.id, "name": _name(data, body.name), "password_hash": password_hash,
                   "admin": True, "version": 0}
        data["list"].append(account)
        data["next_id"] = max(data["next_id"], account["id"] + 1)
        core.save(ctx.db, data)
        ctx.db.commit()
    core.set_cookie(response, data, account)
    return {"user": core.public(account)}


@router.post("/login")
def login(body: LoginBody, response: Response, ctx: Context = Depends(plugin_ctx)):
    data = core.load(ctx.db)
    account = core.by_name(data, body.name)
    try:  # a wrong name counts, and takes as long, as a wrong password: names can't be found by trying
        ok = core.throttle.verify(body.name.strip().lower(), body.password,
                                  account["password_hash"] if account else core.dummy_hash())
    except TooManyTries as e:
        raise HTTPException(429, str(e)) from None
    if not ok or account is None:
        raise HTTPException(403, "Wrong name or password")
    core.set_cookie(response, data, account)
    return {"user": core.public(account)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(core.COOKIE, path="/")
    return {"ok": True}


class PasswordBody(BaseModel):
    current: str = Field(max_length=200)
    password: str = Password


@router.put("/me/password")
def my_password(body: PasswordBody, response: Response, ctx: Context = Depends(plugin_ctx)):
    """Change my password. My other browsers are logged out; this one stays in."""
    data = core.copy(ctx.db)
    account = core.find(data, users.current_id())
    if account is None:
        raise HTTPException(401, "Log in first")
    if not check_password(body.current, account["password_hash"]):
        raise HTTPException(403, "The current password is wrong")
    account["password_hash"] = hash_password(body.password)
    account["version"] = account.get("version", 0) + 1
    core.save(ctx.db, data)
    ctx.db.commit()
    core.set_cookie(response, data, account)
    return {"ok": True}


# ---------------------------------------------------------------- admins: the accounts

@admin.get("")
def list_accounts(ctx: Context = Depends(plugin_ctx)):
    return [core.public(a) for a in core.load(ctx.db)["list"]]


class NewAccount(BaseModel):
    name: str = Name
    password: str = Password
    admin: bool = False


@admin.post("")
def add_account(body: NewAccount, ctx: Context = Depends(plugin_ctx)):
    data = core.copy(ctx.db)
    account = {"id": data["next_id"], "name": _name(data, body.name), "password_hash": hash_password(body.password),
               "admin": body.admin, "version": 0}
    data["list"].append(account)
    data["next_id"] += 1
    core.save(ctx.db, data)
    ctx.db.commit()
    return core.public(account)


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    password: str | None = Field(default=None, min_length=4, max_length=200)
    admin: bool | None = None


@admin.patch("/{account_id}")
def patch_account(account_id: int, body: AccountPatch, response: Response, ctx: Context = Depends(plugin_ctx)):
    """Rename, set a new password (logs it out everywhere) or make it an admin or not. The first
    account stays an admin, so there's always one."""
    data = core.copy(ctx.db)
    account = core.find(data, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    if body.name is not None:
        account["name"] = _name(data, body.name, account_id)
    if body.admin is not None:
        if account_id == users.OWNER.id and not body.admin:
            raise HTTPException(400, "The first account stays an admin")
        account["admin"] = body.admin
    if body.password is not None:
        account["password_hash"] = hash_password(body.password)
        account["version"] = account.get("version", 0) + 1
    core.save(ctx.db, data)
    ctx.db.commit()
    if body.password is not None and account_id == users.current_id():
        core.set_cookie(response, data, account)  # my own: this browser stays logged in
    return core.public(account)


@admin.delete("/{account_id}")
def delete_account(account_id: int, ctx: Context = Depends(plugin_ctx)):
    """Delete an account and its own reading (progress, favorites, saved searches...). Not the first
    account (it has the owner's reading), and not your own."""
    if account_id == users.OWNER.id:
        raise HTTPException(400, "The first account can't be deleted")
    if account_id == users.current_id():
        raise HTTPException(400, "You can't delete your own account")
    data = core.copy(ctx.db)
    if core.find(data, account_id) is None:
        raise HTTPException(404, "Account not found")
    data["list"] = [a for a in data["list"] if a["id"] != account_id]
    core.save(ctx.db, data)
    users.forget(ctx.db, account_id)
    ctx.db.commit()
    return {"ok": True}


router.include_router(admin)
