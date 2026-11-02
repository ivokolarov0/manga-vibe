"""Libraries (Manga, Comics...): which series show together, their reader default and their password.

In the code a library is a *shelf* (models.Shelf), because `Library` has always been a scanned folder.
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
`python -m app.shelves clear-password <name>`.
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

log = logging.getLogger("mangavibe")

COOKIE = "mv_unlocked"
UNLOCK_HOURS = 12
SCRYPT = (2 ** 14, 8, 1)  # n, r, p: about 50 ms and 16 MB per try
MAX_TRIES = 5             # wrong passwords before each try has to wait (30 s, then doubling, up to 15 min)


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = SCRYPT
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        kind, n, r, p, salt, digest = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(digest) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


class TooManyTries(Exception):
    def __init__(self, seconds):
        super().__init__(f"Too many wrong passwords. Try again in {seconds} seconds.")
        self.seconds = seconds


_fails = {}  # shelf id -> (wrong tries in a row, no tries before this time)
_fails_lock = threading.Lock()


def verify(shelf, password: str) -> bool:
    """Whether the password is the shelf's. After MAX_TRIES wrong ones in a row, each further try
    must wait (TooManyTries), so it can't be guessed quickly. The right one resets the count."""
    with _fails_lock:
        count, until = _fails.get(shelf.id, (0, 0.0))
        if until > time.time():
            raise TooManyTries(int(until - time.time()) + 1)
    ok = check_password(password or "", shelf.password_hash)
    with _fails_lock:
        if ok:
            _fails.pop(shelf.id, None)
        else:
            count = _fails.get(shelf.id, (0, 0.0))[0] + 1
            wait = min(30 * 2 ** (count - MAX_TRIES), 900) if count >= MAX_TRIES else 0
            _fails[shelf.id] = (count, time.time() + wait)
    return ok


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
_cache = {"versions": None, "at": 0.0, "shelf_of": {}}
_cache_lock = threading.Lock()


def invalidate():
    with _cache_lock:
        _cache.update(versions=None, at=0.0, shelf_of={})


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
    return locked_ids(db, request.cookies.get(COOKIE))


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
    """(id, name) of the locked shelf this series, book or link is on, else None. The session only
    connects when something isn't cached."""
    with SessionLocal() as db:
        locked = locked_ids(db, cookie)
        if not locked:
            return None
        shelf = _shelf_of(db, kind, item_id)
        return (shelf, db.get(Shelf, shelf).name) if shelf in locked else None


class LockGuard:
    """Refuses /api/series/<id>/..., /api/books/<id>/... and /api/links/<id>/... of a series on a
    locked shelf: details, pages, thumbnails, posters, progress, link icons, changes. Costs nothing
    while no shelf has a password. A plain ASGI middleware, like RememberAddress in app/main.py."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and protected_hint is not False:
            m = _PATH.match(scope["path"])
            if m:
                found = await run_in_threadpool(_locked_shelf, m[1], int(m[2]), Request(scope).cookies.get(COOKIE))
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
    """python -m app.shelves clear-password <name | id | all>"""
    if len(argv) != 2 or argv[0] != "clear-password":
        print("Usage: python -m app.shelves clear-password <library name | all>")
        return 2
    from app import db as database
    database.init_db()
    with database.SessionLocal() as db:
        names = clear_passwords(db, argv[1])
    print(f"Removed the password of {', '.join(names)}" if names else f"No library {argv[1]!r} with a password")
    return 0 if names else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
