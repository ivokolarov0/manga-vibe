"""MCP keys: how an assistant says whose it is, and the guard in front of /mcp.

Each person makes their own keys (Settings → Remote control). A key acts as that person: it sees their
libraries and reading, and can do only what they can (an admin's key can change the app's settings,
another's only their own). A key is `mvk_<user id>_<secret>`; only its SHA-256 is kept, in the person's
own `mcp_keys` setting (`user:<id>:mcp_keys`, the owner's is `mcp_keys`), so deleting an account
deletes its keys too (users.forget). The secret is long and random, so a fast hash is enough.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app import users
from app.db import SessionLocal, get_setting, set_setting

KEY = "mcp_keys"
MAX_KEYS = 20
PATH = "/mcp"                        # where assistants connect
INNER = "/api/plugins/mcp/api/mcp"   # the plugin's own route that answers it (api.py)
TOKEN = re.compile(r"^mvk_(\d+)_([A-Za-z0-9_-]{20,})$")
USED_EVERY = 600  # "last used" is saved at most this often (seconds), not on every call

_used_lock = threading.Lock()
_used_saved: dict[str, float] = {}  # key id -> when "last used" was saved


def setting_key(user_id: int) -> str:
    return KEY if user_id == users.OWNER.id else f"user:{user_id}:{KEY}"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_keys(db, user_id) -> list[dict]:
    return list(get_setting(db, setting_key(user_id), []) or [])


def public(k) -> dict:
    return {"id": k["id"], "name": k["name"], "created": k.get("created"), "used": k.get("used")}


def create(db, user_id, name) -> tuple[dict, str]:
    """A new key for this user: (its public row, the key itself, shown only once). Not committed."""
    keys = list_keys(db, user_id)
    if len(keys) >= MAX_KEYS:
        raise ValueError(f"At most {MAX_KEYS} keys; delete one you don't use")
    secret = secrets.token_urlsafe(32)
    row = {"id": secrets.token_hex(4), "name": (name or "").strip()[:60] or "Assistant",
           "hash": _hash(secret), "created": _now(), "used": None}
    set_setting(db, setting_key(user_id), keys + [row])
    return row, f"mvk_{user_id}_{secret}"


def delete(db, user_id, key_id) -> bool:
    keys = list_keys(db, user_id)
    left = [k for k in keys if k["id"] != key_id]
    if len(left) == len(keys):
        return False
    set_setting(db, setting_key(user_id), left)
    return True


def user_of_token(token: str):
    """The user a key belongs to (users.User), or None: unknown key, deleted account."""
    m = TOKEN.match((token or "").strip())
    if not m:
        return None
    user_id, digest = int(m[1]), _hash(m[2])
    from app.plugins import manager
    with SessionLocal() as db:
        keys = list_keys(db, user_id)
        found = next((k for k in keys if hmac.compare_digest(k.get("hash", ""), digest)), None)
        if found is None:
            return None
        user = manager.find_user(db, user_id)
        if user is None:
            return None
        _mark_used(db, user_id, found["id"])
        return user


def _mark_used(db, user_id, key_id):
    with _used_lock:
        if time.monotonic() - _used_saved.get(key_id, -USED_EVERY) < USED_EVERY:
            return
        _used_saved[key_id] = time.monotonic()
    keys = list_keys(db, user_id)
    for k in keys:
        if k["id"] == key_id:
            k["used"] = _now()
    set_setting(db, setting_key(user_id), keys)
    db.commit()


def token_of(scope) -> str:
    """The key from `Authorization: Bearer <key>`, else from ?key= (for clients that can't set headers)."""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("key") or [""])[0]


def _cross_site(scope) -> bool:
    """A web page from another site calling /mcp from someone's browser (MCP clients send no Origin).
    The spec asks servers to refuse it: it would let any site drive the app (DNS rebinding)."""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    origin = headers.get("origin")
    if not origin or origin == "null":
        return bool(origin)
    return urlparse(origin).netloc.lower() != headers.get("host", "").lower()


def _refuse(status, message, code=-32001):
    body = {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}
    headers = {"WWW-Authenticate": 'Bearer realm="MangaVibe"'} if status == 401 else {}
    return JSONResponse(body, status_code=status, headers=headers)


class KeyGuard:
    """In front of every request while the plugin is on (before the accounts plugin's login): /mcp needs
    a key, and then runs as that key's person. It is handed on as the plugin's own route (INNER), with the
    user in the scope, so the accounts plugin doesn't ask for a login. Everything else passes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].rstrip("/") != PATH:
            return await self.app(scope, receive, send)
        if _cross_site(scope):
            return await _refuse(403, "MangaVibe's MCP server doesn't take calls from web pages")(scope, receive, send)
        user = await run_in_threadpool(user_of_token, token_of(scope))
        if user is None:
            return await _refuse(401, "A MangaVibe key is needed (Settings → Remote control): "
                                      "send it as Authorization: Bearer <key>")(scope, receive, send)
        scope = {**scope, "path": INNER, "raw_path": INNER.encode(), users.SCOPE_KEY: user}
        with users.acting_as(user):
            return await self.app(scope, receive, send)
