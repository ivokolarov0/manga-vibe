"""Accounts: storage, login sessions and the guard in front of every request.

Kept in the `accounts` setting: {"next_id": 3, "secret": "<hex>", "list": [{"id": 1, "name": "Ivo",
"password_hash": "scrypt$...", "admin": true, "version": 0}]}. Account 1 is the first admin: it gets the
owner's reading (app/users.py), can't be deleted and stays an admin.

A login gives the browser a signed token in the `mv_session` cookie (SESSION_DAYS), made of the account
id, its version and an end time. Changing an account's password raises its version, which logs out its
other browsers.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import users
from app.plugins.sdk import SessionLocal, Throttle, get_setting, hash_password, set_setting

KEY = "accounts"
COOKIE = "mv_session"
SESSION_DAYS = 30
NOBODY = users.User(0, "", can_manage=False)  # not logged in: only the login page and its API
throttle = Throttle()  # wrong passwords per account name

CACHE_SECONDS = 10  # the guard runs for every page and image: the accounts are kept this long
_cache = {"data": None, "at": 0.0}
_lock = threading.Lock()
setup_lock = threading.Lock()  # making the first admin: one at a time
_dummy = []


def dummy_hash():
    """A hash to check unknown names against, so they take as long as a wrong password."""
    if not _dummy:
        _dummy.append(hash_password(secrets.token_hex(8)))
    return _dummy[0]


def invalidate():
    with _lock:
        _cache.update(data=None, at=0.0)


def load(db) -> dict:
    """The accounts setting (cached; don't change what it returns, use save())."""
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["data"]
    data = get_setting(db, KEY, None) or {}
    data = {"next_id": data.get("next_id", 1), "secret": data.get("secret") or secrets.token_hex(32),
            "list": list(data.get("list") or [])}
    with _lock:
        _cache.update(data=data, at=time.monotonic())
    return data


def save(db, data):
    """Store a changed copy of the accounts setting. Not committed."""
    set_setting(db, KEY, data)
    invalidate()


def copy(db) -> dict:
    """A copy of the accounts setting to change and save()."""
    data = load(db)
    return {**data, "list": [dict(a) for a in data["list"]]}


def find(data, account_id):
    return next((a for a in data["list"] if a["id"] == account_id), None)


def by_name(data, name):
    return next((a for a in data["list"] if a["name"].lower() == name.strip().lower()), None)


def user_of(account) -> users.User:
    return users.User(account["id"], account["name"], can_manage=bool(account["admin"]))


def public(account) -> dict:
    return {"id": account["id"], "name": account["name"], "admin": bool(account["admin"])}


# ---------------------------------------------------------------- the session cookie

def _sign(secret, body):
    return hmac.new(bytes.fromhex(secret), body.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(data, account) -> str:
    body = f"{account['id']}.{account.get('version', 0)}.{int(time.time()) + SESSION_DAYS * 86400}"
    return f"{body}.{_sign(data['secret'], body)}"


def account_of_token(data, token):
    """The account a (valid, current) token belongs to, else None."""
    try:
        aid, ver, exp, sig = (token or "").split(".")
        aid, ver, exp = int(aid), int(ver), int(exp)
    except ValueError:
        return None
    account = find(data, aid)
    if (account is None or exp < time.time() or account.get("version", 0) != ver
            or not hmac.compare_digest(sig, _sign(data["secret"], f"{aid}.{ver}.{exp}"))):
        return None
    return account


def set_cookie(response, data, account):
    response.set_cookie(COOKIE, make_token(data, account), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", path="/")


def session_user(cookie):
    """The logged-in user for this cookie, or None."""
    with SessionLocal() as db:
        account = account_of_token(load(db), cookie)
        return user_of(account) if account else None


# ---------------------------------------------------------------- the guard

# what works without logging in: the page itself, its files, the UI kit page (no data on it), and logging in
PUBLIC = re.compile(r"^/(|kit|manifest\.json|custom\.css|static/.*|plugins/[^/]+\.(js|css)|api/health"
                    r"|api/plugins/accounts/api/(me|setup|login|logout))$")


class AccountsGuard:
    """Every request but the login page needs a logged-in account; it then runs as that user
    (app/users.py), so it sees and changes that person's reading. A plain ASGI middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # a request whose user is already known: one that came with a key (the MCP server checked it,
        # the Remote control plugin from manga-vibe-mcp), or the server's own call on someone's behalf.
        # A browser can't send it, but any installed plugin's middleware that runs before this one
        # (middleware_order < 0) can set it, and so log a request in as anyone: that is a power only
        # plugins you trust get (docs/PLUGINS.md, "Who is asking").
        user = scope.get(users.SCOPE_KEY)
        if user is None:
            cookie = Request(scope).cookies.get(COOKIE)
            user = await run_in_threadpool(session_user, cookie) if cookie else None
        if user is None and not PUBLIC.match(scope["path"]):
            detail = {"message": "Log in first", "login": True}
            return await JSONResponse({"detail": detail}, status_code=401)(scope, receive, send)
        with users.acting_as(user or NOBODY):
            return await self.app(scope, receive, send)
