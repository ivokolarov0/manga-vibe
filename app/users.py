"""Accounts: who is using the app.

The first account is the admin: it adds, renames and removes the others, resets their passwords and
runs the server (library folders, scans, plugins, updates). Every account has its own reading
progress, reading status, favorites and settings, and sees its own libraries and what others share
with it (app/access.py).

Signing in: a password, a PIN (digits only) or nothing, per account. The right one gets the browser a
signed session in the `mv_session` cookie (HttpOnly, SameSite=Lax): for SESSION_DAYS with "Remember
me", else until the browser closes. Changing the password, or "Sign out everywhere", raises the
account's session_version, which ends its older sessions. A single account without a password means
nobody has to sign in (an install for one person works like before accounts).

Forgot a password: CLEAR_PASSWORD=<account name> on the next start, or
`python -m app.users clear-password <name>`.
"""
import hmac
import logging
import secrets
import sys
import threading
import time
from dataclasses import dataclass

from app import config, shelves
from app.models import Shelf, User

log = logging.getLogger("mangavibe")

COOKIE = "mv_session"
SESSION_DAYS = 180       # "Remember me"
SHORT_HOURS = 24         # without it the cookie goes when the browser closes, and the session after a day
KINDS = ("pin", "password")


@dataclass(frozen=True)
class Account:
    """What a request needs to know about an account (cached, so no database is needed for it)."""
    id: int
    name: str
    is_admin: bool
    session_version: int
    has_password: bool
    password_kind: str | None


# ---------------------------------------------------------------- the accounts, cached

CACHE_SECONDS = 10
_cache = {"accounts": None, "at": 0.0}
_cache_lock = threading.Lock()


def invalidate():
    with _cache_lock:
        _cache.update(accounts=None, at=0.0)


def accounts(db) -> dict[int, Account]:
    """{id: Account} of every account (cached for CACHE_SECONDS; invalidate() after a change)."""
    with _cache_lock:
        if _cache["accounts"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["accounts"]
    found = {u.id: Account(u.id, u.name, bool(u.is_admin), u.session_version or 0, bool(u.password_hash),
                           u.password_kind if u.password_hash else None)
             for u in db.query(User).order_by(User.id)}
    with _cache_lock:
        _cache.update(accounts=found, at=time.monotonic())
    return found


def open_account(db) -> Account | None:
    """The account everybody is while it's the only one and has no password; else None (sign in)."""
    found = accounts(db)
    if len(found) == 1:
        (only,) = found.values()
        if not only.has_password:
            return only
    return None


def main_admin_id(db) -> int | None:
    """The first admin: what has no other owner belongs to it (e.g. a library made for a new folder)."""
    return next((a.id for a in accounts(db).values() if a.is_admin), None)


# ---------------------------------------------------------------- sessions (the cookie)

def _signature(key, body):
    # "session." in what's signed: the key also signs the libraries' unlock tokens, which look the same
    # ("id.version.expiry.signature"), so neither can pass for the other
    return shelves.sign(key, f"session.{body}")


def make_session(db, user, remember=True) -> str:
    """A signed session for this account (the caller commits: the signing key may be new)."""
    life = SESSION_DAYS * 86400 if remember else SHORT_HOURS * 3600
    body = f"{user.id}.{user.session_version or 0}.{int(time.time()) + life}"
    return f"{body}.{_signature(shelves.secret(db, create=True), body)}"


def remembered(cookie) -> bool:
    """Whether this session was made with "Stay signed in" (it runs longer than a short one can)."""
    try:
        return int((cookie or "").split(".")[2]) - time.time() > SHORT_HOURS * 3600
    except (IndexError, ValueError):
        return False


def account_of(db, cookie) -> Account | None:
    """The account this session cookie is for, if it's valid; else the open account (see above)."""
    found = accounts(db)
    try:
        uid, ver, exp, sig = (cookie or "").split(".")
        uid, ver, exp = int(uid), int(ver), int(exp)
    except ValueError:
        return open_account(db)
    key = shelves.secret(db)
    a = found.get(uid)
    if (key and a and exp > time.time() and a.session_version == ver
            and hmac.compare_digest(sig, _signature(key, f"{uid}.{ver}.{exp}"))):
        return a
    return open_account(db)


# ---------------------------------------------------------------- passwords

def verify(user, password: str) -> bool:
    """Whether the password (or PIN) is the account's; one without a password takes anything. Slowed
    down after wrong tries (shelves.Throttle), so a PIN can't be guessed quickly."""
    if not user.password_hash:
        return True
    return shelves.throttle.attempt(("account", user.id),
                                    lambda: shelves.check_password(password or "", user.password_hash))


_dummy = None


def check_nobody(password: str):
    """As slow as checking a real password, for a name without an account (so the time taken doesn't
    tell which names have one). The hash is made once."""
    global _dummy
    if _dummy is None:
        _dummy = shelves.hash_password(secrets.token_hex(8))
    shelves.check_password(password or "", _dummy)


def forget(user_id):
    """An account's wrong tries (removed, or a new password)."""
    shelves.throttle.forget(("account", user_id))


def check_new_password(password: str, kind: str):
    """A ValueError saying what's wrong with a new password or PIN, if anything."""
    if kind not in KINDS:
        raise ValueError("Pick a password or a PIN")
    if kind == "pin" and not (password.isdigit() and 4 <= len(password) <= 12):
        raise ValueError("A PIN is 4 to 12 digits")
    if kind == "password" and len(password) < 4:
        raise ValueError("Use at least 4 characters")


def set_password(user, password: str | None, kind: str | None = None, check=True):
    """Set (or with None remove) the account's password or PIN, and end its other sessions. Not committed.
    check=False takes it as it is (ADMIN_PASSWORD: the owner's choice, and the app must start)."""
    if password:
        kind = kind or ("pin" if password.isdigit() else "password")
        if check:
            check_new_password(password, kind)
        user.password_hash, user.password_kind = shelves.hash_password(password), kind
    else:
        user.password_hash = user.password_kind = None
    user.session_version = (user.session_version or 0) + 1
    forget(user.id)
    invalidate()


# ---------------------------------------------------------------- first start, forgot the password

def ensure_admin(db):
    """A new install gets its admin account (ADMIN_USER / ADMIN_PASSWORD). Libraries without an owner
    (made before accounts by another process, or whose owner is gone) go to the first admin."""
    if db.query(User).count() == 0:
        user = User(name=config.ADMIN_USER or "Admin", is_admin=True)
        if config.ADMIN_PASSWORD:
            try:
                check_new_password(config.ADMIN_PASSWORD, "pin" if config.ADMIN_PASSWORD.isdigit() else "password")
            except ValueError as e:
                log.warning("ADMIN_PASSWORD: %s. It's used anyway; set a better one in Settings → Account.", e)
            set_password(user, config.ADMIN_PASSWORD, check=False)
        db.add(user)
        db.commit()
        log.info("Made the admin account %s", user.name)
    invalidate()
    admin = main_admin_id(db)
    if admin is not None:
        db.query(Shelf).filter(Shelf.owner_id.is_(None)).update({Shelf.owner_id: admin})
    shelves.secret(db, create=True)  # sessions can be checked without the database from now on
    db.commit()


def clear_password(db, name: str) -> list[str]:
    """Remove the password of the account with this name (or id). Returns the names it was removed from."""
    name = name.strip()
    out = []
    for user in db.query(User).all():
        if user.name.lower() == name.lower() or str(user.id) == name:
            set_password(user, None)
            out.append(user.name)
    db.commit()
    invalidate()
    return out


def clear_from_env(db, name):
    """CLEAR_PASSWORD at start."""
    if not name:
        return
    names = clear_password(db, name)
    if names:
        log.warning("CLEAR_PASSWORD: removed the password of %s. Remove the variable again, and set a "
                    "new password in Settings → Account.", ", ".join(names))
    else:
        log.warning("CLEAR_PASSWORD=%s: there is no account with that name", name)


def main(argv):
    """python -m app.users clear-password <name | id>"""
    if len(argv) != 2 or argv[0] != "clear-password":
        print("Usage: python -m app.users clear-password <account name>")
        return 2
    from app import db as database
    database.init_db()
    with database.SessionLocal() as db:
        names = clear_password(db, argv[1])
    print(f"Removed the password of {', '.join(names)}" if names else f"No account called {argv[1]!r}")
    return 0 if names else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
