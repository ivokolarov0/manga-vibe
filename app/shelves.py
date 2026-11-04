"""Libraries (Manga, Comics...): which series show together, their reader default and their password.

In the code a library is a *shelf* (models.Shelf), because `Library` has always been a scanned folder.
A series shows on its own shelf (`Series.shelf_id`, set by the owner, never by a scan), else on its
folder's (`Library.shelf_id`). Every folder has one.

Password (optional): only a scrypt hash is stored. The right password gets the browser a signed
token for that shelf in the `mv_unlocked` cookie, good for UNLOCK_HOURS (the page also locks
everything again when a new tab starts, see app.js). "Lock now" takes the token out. Changing the
password raises the shelf's lock_version, which makes the old tokens useless.

While a shelf is locked for a request, its series are left out of every list and count, and the
access guard (app/access.py) refuses everything under /api/series/<id> and /api/books/<id> that
belongs to it (details, pages, thumbnails, posters, downloads, changes). With accounts, each shelf
belongs to one (owner_id) and is seen by it and those it's shared with (app/access.py); the password
keeps it private on a device several people use with the same account. Whoever can read the files or
the database sees everything.

Forgot the password: CLEAR_LIBRARY_PASSWORD=<name> (or "all") on the next start, or
`python -m app.shelves clear-password <name>`.
"""
import hashlib
import hmac
import logging
import secrets
import sys
import threading
import time

from sqlalchemy import func, or_, select

from app.db import get_setting, set_setting
from app.models import Library, Series, Shelf

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


class Throttle:
    """Wrong passwords per key (("shelf", id), ("account", id)): after MAX_TRIES in a row, each further
    try must wait (TooManyTries: 30 s, doubling, up to 15 min), so a password or PIN can't be guessed
    quickly. The right one resets the count. In memory."""

    def __init__(self):
        self._fails = {}  # key -> (wrong tries in a row, no tries before this time)
        self._lock = threading.Lock()

    def attempt(self, key, check) -> bool:
        """check() (the slow password check) unless the key has to wait; counts a wrong answer."""
        with self._lock:
            until = self._fails.get(key, (0, 0.0))[1]
            if until > time.time():
                raise TooManyTries(int(until - time.time()) + 1)
        ok = check()
        with self._lock:
            if ok:
                self._fails.pop(key, None)
            else:
                count = self._fails.get(key, (0, 0.0))[0] + 1
                wait = min(30 * 2 ** (count - MAX_TRIES), 900) if count >= MAX_TRIES else 0
                self._fails[key] = (count, time.time() + wait)
        return ok

    def forget(self, key=None, kind=None):
        """One key's wrong tries (e.g. its password changed), every key's of one kind ("shelf"), or
        with neither, all of them."""
        with self._lock:
            if key is not None:
                self._fails.pop(key, None)
            elif kind is not None:
                for k in [k for k in self._fails if k[0] == kind]:
                    del self._fails[k]
            else:
                self._fails.clear()


throttle = Throttle()


def verify(shelf, password: str) -> bool:
    """Whether the password is the shelf's (slowed down after wrong tries, see Throttle)."""
    return throttle.attempt(("shelf", shelf.id), lambda: check_password(password or "", shelf.password_hash))


# ---------------------------------------------------------------- unlock tokens (the cookie)

_key = None


def secret(db, create=False):
    """The key that signs the unlock tokens and the sessions (app/users.py), kept in the database
    (made the first time it's needed)."""
    global _key
    if _key is None:
        value = get_setting(db, "unlock_secret")
        if not value and create:
            value = secrets.token_hex(32)
            set_setting(db, "unlock_secret", value)
        _key = bytes.fromhex(value) if value else None
    return _key


def sign(key, body):
    return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(db, shelf) -> str:
    """A token that unlocks this shelf for UNLOCK_HOURS (the caller commits: the key may be new)."""
    body = f"{shelf.id}.{shelf.lock_version or 0}.{int(time.time()) + UNLOCK_HOURS * 3600}"
    return f"{body}.{sign(secret(db, create=True), body)}"


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


# The access guard (app/access.py) runs for every page and thumbnail, so what it reads is kept for
# CACHE_SECONDS: the protected shelves ({id: lock_version}) here, and the accounts, what each may see
# and where each series is there. Anything that changes those calls invalidate(), which clears them
# all; the time limit covers changes made by another process.
CACHE_SECONDS = 10
_cache = {"versions": None, "at": 0.0}
_cache_lock = threading.Lock()


def invalidate():
    from app import access, users
    with _cache_lock:
        _cache.update(versions=None, at=0.0)
    access.clear()
    users.invalidate()


def _protected(db):
    """{shelf id: lock_version} of the shelves with a password (cached)."""
    with _cache_lock:
        if _cache["versions"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["versions"]
    versions = dict(db.query(Shelf.id, Shelf.lock_version).filter(Shelf.password_hash.is_not(None)))
    with _cache_lock:
        _cache.update(versions=versions, at=time.monotonic())
    return versions


def locked_ids(db, cookie) -> set[int]:
    """The shelves with a password that this cookie doesn't unlock."""
    versions = _protected(db)
    if not versions:
        return set()
    key, now, unlocked = secret(db), time.time(), set()
    for token in _tokens(cookie) if key else []:
        try:
            sid, ver, exp, sig = token.split(".")
            sid, ver, exp = int(sid), int(ver), int(exp)
        except ValueError:
            continue
        if exp > now and versions.get(sid) == ver and hmac.compare_digest(sig, sign(key, f"{sid}.{ver}.{exp}")):
            unlocked.add(sid)
    return set(versions) - unlocked


def locked_for(db, request) -> set[int]:
    return locked_ids(db, request.cookies.get(COOKIE))


# ---------------------------------------------------------------- which shelf a series is on

def shelf_column():
    """The shelf a series shows on, as SQL (for queries on Series): its own, else its folder's."""
    folder = select(Library.shelf_id).where(Library.id == Series.library_id).scalar_subquery()
    return func.coalesce(Series.shelf_id, folder)


def shelf_id_of(db, series):
    if series.shelf_id is not None:
        return series.shelf_id
    return db.query(Library.shelf_id).filter(Library.id == series.library_id).scalar()


def shelf_for_folder(db, folder):
    """The shelf a newly found folder goes on: one the admin made with its name that has no folder
    yet (e.g. "Comics" made before adding /comics), else a new one of the admin's named like the
    folder ("manga (2)" if that name is taken), with the folder's old reading mode (for the upgrade
    that added shelves). The folder's own reading mode is cleared: the shelf has it now. Not committed."""
    from app.users import main_admin_id
    db.flush()  # the folder's id
    admin = main_admin_id(db)
    with_folders = select(Library.shelf_id).where(Library.shelf_id.is_not(None), Library.id != folder.id)
    shelf = (db.query(Shelf).filter(func.lower(Shelf.name) == folder.name.lower(), Shelf.id.not_in(with_folders),
                                    Shelf.owner_id.is_(None) if admin is None else Shelf.owner_id == admin)
             .order_by(Shelf.id).first())
    if shelf is None:
        taken = {n.lower() for (n,) in db.query(Shelf.name)}
        name, n = folder.name, 2
        while name.lower() in taken:
            name, n = f"{folder.name} ({n})", n + 1
        shelf = Shelf(name=name, reader_mode=folder.reader_mode, made_for_folder=folder.id, owner_id=admin)
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
    """Where a deleted shelf's folders go: another shelf of the same owner without a password, else
    any of theirs, else the first admin's, else the first there is."""
    from app.users import main_admin_id
    gone = db.get(Shelf, shelf_id)
    others = db.query(Shelf).filter(Shelf.id != shelf_id).order_by(Shelf.id).all()
    for owner in (gone.owner_id if gone else None, main_admin_id(db)):
        mine = [s for s in others if s.owner_id == owner]
        if mine:
            return next((s for s in mine if not s.password_hash), mine[0])
    return others[0] if others else None


def series_shelf(db, series_id):
    """The shelf a series is on (SQL, no cache)."""
    return db.query(shelf_column()).filter(Series.id == series_id).scalar()


# ---------------------------------------------------------------- sharing (app/access.py)

def replace_shares(db, column, value, wanted, owner_id):
    """Share one shelf or series (ShelfShare.shelf_id or SeriesShare.series_id == value) with exactly
    these accounts: the rows of the others go, missing ones are added. Never with its owner.
    Commits and clears the caches. Returns the account ids it's shared with."""
    from app.models import User
    model = column.class_
    wanted = {uid for (uid,) in db.query(User.id).filter(User.id.in_(wanted))} - {owner_id}
    db.query(model).filter(column == value, model.user_id.not_in(wanted)).delete(synchronize_session=False)
    have = {uid for (uid,) in db.query(model.user_id).filter(column == value)}
    for uid in wanted - have:
        db.add(model(**{column.key: value, "user_id": uid}))
    db.commit()
    invalidate()
    return sorted(wanted)


def unique_name(db, name, owner_id, shelf_id):
    """The name, or "Name (2)"... if the owner has another shelf called that."""
    taken = {n.lower() for (n,) in db.query(Shelf.name).filter(Shelf.owner_id == owner_id, Shelf.id != shelf_id)}
    out, n = name, 2
    while out.lower() in taken:
        out, n = f"{name} ({n})", n + 1
    return out


def owner_changed(db, shelf_ids, new_owner):
    """These shelves now belong to new_owner (given away, or their owner's account removed). Tidy what
    pointed at the old arrangement (not committed):
    - the new owner no longer needs them, or their series, shared with it: those rows go, or its
      series would show twice (in the shelf and in Shared with me);
    - accounts that had put a series shared with them into one of these shelves of theirs get it back
      in Shared with me, since the shelf isn't theirs any more;
    - a name the new owner already uses gets " (2)"."""
    from app.models import SeriesShare, ShelfShare
    shelf_ids = list(shelf_ids)
    if not shelf_ids:
        return
    db.flush()
    db.query(ShelfShare).filter(ShelfShare.shelf_id.in_(shelf_ids), ShelfShare.user_id == new_owner
                                ).delete(synchronize_session=False)
    on_them = select(Series.id).where(shelf_column().in_(shelf_ids))
    db.query(SeriesShare).filter(SeriesShare.user_id == new_owner, SeriesShare.series_id.in_(on_them)
                                 ).delete(synchronize_session=False)
    db.query(SeriesShare).filter(SeriesShare.shelf_id.in_(shelf_ids)).update(
        {SeriesShare.shelf_id: None}, synchronize_session=False)
    for shelf in db.query(Shelf).filter(Shelf.id.in_(shelf_ids)):
        shelf.name = unique_name(db, shelf.name, new_owner, shelf.id)


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
    throttle.forget(kind="shelf")  # the libraries' wrong tries only: the accounts' stay
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
