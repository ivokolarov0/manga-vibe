"""Libraries (Manga, Comics...), the built-in libraries plugin: which series show together, their reader
default and their password. The plugin's hooks are in __init__.py, its API in api.py.

In the code a library is a *shelf* (models.Shelf), because `Library` has always been a scanned folder.
The shelf table and the shelf_id columns stay in app/models.py: with the plugin off they're unused.
A series shows on its own shelf (`Series.shelf_id`, set by the owner, never by a scan), else on its
folder's (`Library.shelf_id`). Every folder has one.

Password (optional): only a scrypt hash is stored. The right password gets the browser a signed
token for that shelf in the `mv_unlocked` cookie, good for UNLOCK_HOURS (the page also locks
everything again when a new tab starts, see app.js). "Lock now" takes the token out. Changing the
password raises the shelf's lock_version, which makes the old tokens useless.

While a shelf is locked for a request, its series are left out of every list and count, and the
LockGuard middleware refuses everything under /api/series/<id> and /api/books/<id> that belongs to
it (details, pages, thumbnails, posters, downloads, changes). The app has no accounts, so this only
keeps a library private from others using the same app: whoever can read the files or the database
sees everything.

Forgot the password: CLEAR_LIBRARY_PASSWORD=<name> (or "all") on the next start, or
`python -m app.plugins.builtin.libraries.core clear-password <name>`.
"""
import hashlib
import hmac
import logging
import re
import secrets
import sys
import threading
import time

from sqlalchemy import func, or_, select, true
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db import SessionLocal, get_setting, set_setting
from app.models import Book, Library, Link, Series, Shelf
from app.passwords import MAX_TRIES, Throttle, TooManyTries, check_password, hash_password

log = logging.getLogger("mangavibe")

COOKIE = "mv_unlocked"
UNLOCK_HOURS = 12


# ---------------------------------------------------------------- passwords (app/passwords.py)

_throttle = Throttle()
_fails = _throttle.fails  # shelf id -> (wrong tries in a row, no tries before this time)
# used as shelves.<name> by api.py and the tests
REEXPORTED = (MAX_TRIES, TooManyTries, check_password, hash_password)


def verify(shelf, password: str) -> bool:
    """Whether the password is the shelf's. After MAX_TRIES wrong ones in a row, each further try
    must wait (TooManyTries), so it can't be guessed quickly. The right one resets the count."""
    return _throttle.verify(shelf.id, password, shelf.password_hash)


# ---------------------------------------------------------------- unlock tokens (the cookie)

_key = None


def _secret(db, create=False):
    """The key that signs the tokens, kept in the database (made the first time it's needed)."""
    global _key
    if _key is None:
        value = get_setting(db, "unlock_secret")
        if not value and create:
            value = secrets.token_hex(32)
            set_setting(db, "unlock_secret", value)
        _key = bytes.fromhex(value) if value else None
    return _key


def _sign(key, body):
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(db, shelf) -> str:
    """A token that unlocks this shelf for UNLOCK_HOURS (the caller commits: the key may be new)."""
    body = f"{shelf.id}.{shelf.lock_version or 0}.{int(time.time()) + UNLOCK_HOURS * 3600}"
    return f"{body}.{_sign(_secret(db, create=True), body)}"


def _tokens(cookie):
    return [t for t in (cookie or "").split("-") if t]


def _token_shelf(token):
    try:
        return int(token.split(".")[0])
    except ValueError:
        return None


def cookie_with(cookie, token):
    """The cookie with this token added (and any older one for the same shelf dropped)."""
    sid = _token_shelf(token)
    return "-".join([t for t in _tokens(cookie) if _token_shelf(t) != sid] + [token])


def cookie_without(cookie, shelf_id):
    return "-".join(t for t in _tokens(cookie) if _token_shelf(t) != shelf_id)


# None: not known yet; False: no shelf has a password, so LockGuard has nothing to check
protected_hint = None

# LockGuard runs for every page and thumbnail, so what it reads is kept for CACHE_SECONDS: the
# protected shelves ({id: lock_version}) and which shelf a series, book or link is on. Anything that
# changes those calls invalidate(); the time limit covers changes made by another process.
CACHE_SECONDS = 10
_cache = {"versions": None, "at": 0.0, "shelf_of": {}, "access": None, "access_at": 0.0}
_cache_lock = threading.Lock()


def invalidate():
    with _cache_lock:
        _cache.update(versions=None, at=0.0, shelf_of={}, access=None, ids=None)


# ---------------------------------------------------------------- who sees which (with accounts)
# An admin picks the libraries an account sees (app/users.py, the accounts plugin): the others are hidden
# from it everywhere, like a locked library that no password opens. Admins see every library.
# Kept in the libraries_access setting: {"<user id>": [shelf ids]}; an account not in it sees all.

ACCESS_KEY = "libraries_access"
# False: no account has a limit, so LockGuard has nothing to check for that (None: not known yet)
access_hint = None


def access_rules(db) -> dict:
    """{user id (str): [shelf ids]} (cached)."""
    global access_hint
    with _cache_lock:
        if _cache.get("access") is not None and time.monotonic() - _cache.get("access_at", 0) < CACHE_SECONDS:
            return _cache["access"]
    rules = get_setting(db, ACCESS_KEY, {}) or {}
    access_hint = bool(rules)
    with _cache_lock:
        _cache.update(access=rules, access_at=time.monotonic())
    return rules


def hidden_ids(db) -> set[int]:
    """The libraries the current user may not see at all (with accounts); none for admins."""
    from app import users
    rules = access_rules(db)  # also tells LockGuard whether there is anything to check (access_hint)
    user = users.current()
    if user.can_manage:
        return set()
    allowed = rules.get(str(user.id))
    if allowed is None:
        return set()
    return _all_shelf_ids(db) - set(allowed)


def _all_shelf_ids(db) -> set[int]:
    """Every library's id (cached: LockGuard asks for every page and image of a limited account)."""
    with _cache_lock:
        if _cache.get("ids") is not None and time.monotonic() - _cache.get("ids_at", 0) < CACHE_SECONDS:
            return _cache["ids"]
    ids = {sid for (sid,) in db.query(Shelf.id)}
    with _cache_lock:
        _cache.update(ids=ids, ids_at=time.monotonic())
    return ids


def set_access(db, user_id, shelf_ids):
    """Which libraries an account sees: a list of ids, or None for all. Not committed."""
    global access_hint
    rules = dict(get_setting(db, ACCESS_KEY, {}) or {})
    if shelf_ids is None:
        rules.pop(str(user_id), None)
    else:
        rules[str(user_id)] = sorted(set(shelf_ids))
        access_hint = True  # LockGuard checks from now on, not only once the cache is read again
    set_setting(db, ACCESS_KEY, rules)
    invalidate()


def _protected(db):
    """{shelf id: lock_version} of the shelves with a password (cached)."""
    with _cache_lock:
        if _cache["versions"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["versions"]
    versions = dict(db.query(Shelf.id, Shelf.lock_version).filter(Shelf.password_hash.is_not(None)))
    with _cache_lock:
        _cache.update(versions=versions, at=time.monotonic(), shelf_of={})
    return versions


def locked_ids(db, cookie) -> set[int]:
    """The shelves with a password that this cookie doesn't unlock."""
    global protected_hint
    versions = _protected(db)
    protected_hint = bool(versions)
    if not versions:
        return set()
    key, now, unlocked = _secret(db), time.time(), set()
    for token in _tokens(cookie) if key else []:
        try:
            sid, ver, exp, sig = token.split(".")
            sid, ver, exp = int(sid), int(ver), int(exp)
        except ValueError:
            continue
        if exp > now and versions.get(sid) == ver and hmac.compare_digest(sig, _sign(key, f"{sid}.{ver}.{exp}")):
            unlocked.add(sid)
    return set(versions) - unlocked


def locked_for(db, request) -> set[int]:
    """What this request can't see: libraries with a password it hasn't unlocked, and (with accounts)
    the ones its user may not see."""
    return locked_ids(db, request.cookies.get(COOKIE)) | hidden_ids(db)


# ---------------------------------------------------------------- which shelf a series is on

def shelf_column():
    """The shelf a series shows on, as SQL (for queries on Series): its own, else its folder's."""
    folder = select(Library.shelf_id).where(Library.id == Series.library_id).scalar_subquery()
    return func.coalesce(Series.shelf_id, folder)


def visible(locked):
    """SQL condition: the series isn't on one of these (locked) shelves."""
    if not locked:
        return true()
    col = shelf_column()
    return or_(col.is_(None), col.not_in(locked))


def shelf_id_of(db, series):
    if series.shelf_id is not None:
        return series.shelf_id
    return db.query(Library.shelf_id).filter(Library.id == series.library_id).scalar()


def shelf_for_folder(db, folder):
    """The shelf a newly found folder goes on: one the owner made with its name that has no folder
    yet (e.g. "Comics" made before adding /comics), else a new one named like the folder ("manga (2)"
    if that name is taken), with the folder's old reading mode (for the upgrade that added shelves).
    The folder's own reading mode is cleared: the shelf has it now. Not committed."""
    db.flush()  # the folder's id
    with_folders = select(Library.shelf_id).where(Library.shelf_id.is_not(None), Library.id != folder.id)
    shelf = (db.query(Shelf).filter(func.lower(Shelf.name) == folder.name.lower(), Shelf.id.not_in(with_folders))
             .order_by(Shelf.id).first())
    if shelf is None:
        taken = {n.lower() for (n,) in db.query(Shelf.name)}
        name, n = folder.name, 2
        while name.lower() in taken:
            name, n = f"{folder.name} ({n})", n + 1
        shelf = Shelf(name=name, reader_mode=folder.reader_mode, made_for_folder=folder.id)
        db.add(shelf)
        db.flush()
        log.info("Added library %s for the folder %s", shelf.name, folder.root_path)
    folder.reader_mode = None
    invalidate()
    return shelf


def ensure_folder_shelves(db):
    """Every folder gets a shelf: after the upgrade one per folder, so nothing looks different."""
    for folder in db.query(Library).filter(Library.shelf_id.is_(None)).order_by(Library.id).all():
        folder.shelf_id = shelf_for_folder(db, folder).id
    db.commit()


def fallback_for(db, shelf_id):
    """Where a deleted shelf's folders go: the first other shelf without a password, else the first."""
    others = db.query(Shelf).filter(Shelf.id != shelf_id).order_by(Shelf.id).all()
    return next((s for s in others if not s.password_hash), others[0] if others else None)


# ---------------------------------------------------------------- the server checks every request

_PATH = re.compile(r"^/api/(series|books|links)/(\d+)(?:/|$)")


def series_shelf(db, series_id):
    """The shelf a series is on (SQL, no cache)."""
    return db.query(shelf_column()).filter(Series.id == series_id).scalar()


def _shelf_of(db, kind, item_id):
    """The shelf a series, book or link is on, or None (cached, see CACHE_SECONDS)."""
    key = (kind, item_id)
    with _cache_lock:
        if key in _cache["shelf_of"]:
            return _cache["shelf_of"][key]
    if kind == "series":
        sid = item_id
    else:
        model = Book if kind == "books" else Link
        sid = db.query(model.series_id).filter(model.id == item_id).scalar()
    shelf = series_shelf(db, sid) if sid is not None else None
    with _cache_lock:
        if len(_cache["shelf_of"]) > 50000:
            _cache["shelf_of"].clear()
        _cache["shelf_of"][key] = shelf
    return shelf


def _locked_shelf(kind, item_id, cookie):
    """(id, name, hidden) of the locked shelf this series, book or link is on, else None; hidden: the
    user may not see it at all (no password opens it). The session only connects when something isn't
    cached."""
    with SessionLocal() as db:
        locked, hidden = locked_ids(db, cookie), hidden_ids(db)
        if not locked and not hidden:
            return None
        shelf = _shelf_of(db, kind, item_id)
        if shelf in hidden:
            return shelf, "", True
        return (shelf, db.get(Shelf, shelf).name, False) if shelf in locked else None


class LockGuard:
    """Refuses /api/series/<id>/..., /api/books/<id>/... and /api/links/<id>/... of a series on a
    locked shelf: details, pages, thumbnails, posters, progress, link icons, changes. Costs nothing
    while no shelf has a password. A plain ASGI middleware, like RememberAddress in app/main.py."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (protected_hint is not False or access_hint is not False):
            m = _PATH.match(scope["path"])
            if m:
                found = await run_in_threadpool(_locked_shelf, m[1], int(m[2]), Request(scope).cookies.get(COOKIE))
                if found and found[2]:  # not for this account: no password helps
                    detail = {"message": "This series isn't in your libraries"}
                    return await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
                if found:
                    # the page asks for this shelf's password (detail.shelf)
                    detail = {"message": f"The library {found[1]} is locked", "shelf": found[0]}
                    return await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)


# ---------------------------------------------------------------- forgot the password

def clear_passwords(db, which: str) -> list[str]:
    """Remove the password of the shelf with this name (or id), or of every shelf with "all".
    Returns the names of the shelves that had one."""
    which = which.strip()
    q = db.query(Shelf).filter(Shelf.password_hash.is_not(None))
    if which.lower() != "all":
        q = q.filter(or_(func.lower(Shelf.name) == which.lower(),
                         Shelf.id == (int(which) if which.isdigit() else -1)))
    names = []
    for shelf in q:
        shelf.password_hash = None
        shelf.lock_version = (shelf.lock_version or 0) + 1
        names.append(shelf.name)
    db.commit()
    _fails.clear()
    invalidate()
    return names


def clear_from_env(db, which):
    """CLEAR_LIBRARY_PASSWORD at start."""
    if not which:
        return
    names = clear_passwords(db, which)
    if names:
        log.warning("CLEAR_LIBRARY_PASSWORD: removed the password of %s. Remove the variable again.",
                    ", ".join(names))
    else:
        log.warning("CLEAR_LIBRARY_PASSWORD=%s: no library with that name has a password", which)


def main(argv):
    """python -m app.plugins.builtin.libraries.core clear-password <name | id | all>"""
    if len(argv) != 2 or argv[0] != "clear-password":
        print("Usage: python -m app.plugins.builtin.libraries.core clear-password <library name | all>")
        return 2
    from app import db as database
    database.init_db()
    with database.SessionLocal() as db:
        names = clear_passwords(db, argv[1])
    print(f"Removed the password of {', '.join(names)}" if names else f"No library {argv[1]!r} with a password")
    return 0 if names else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
