"""Who may see and change what. One guard for every request, one set of rules for every list.

- Signed out: only /api/auth/..., /api/health and the page itself (index.html, /static) load. Every
  other /api/ call gets a 401 and the page shows the sign-in screen. (While there is one account
  without a password, everybody is that account: see app/users.py.)
- Server settings (library folders, scans, plugins, updates): the admin only (ADMIN below).
- A series is seen by the owner of the library (shelf) it's on, by the accounts that library is
  shared with, and by the accounts the series itself is shared with. A locked library (a password,
  app/shelves.py) hides its series until it's unlocked in that browser; a series shared on its own
  isn't locked for the account it's shared with.
- Changing a series (metadata, poster, file numbers, match, which library, delete) is for the owner of
  its library, or an admin. Shared means read only: reading, progress, reading status, favorites and
  reading mode are each account's own, so those stay open (READER_WRITES).

Guard checks every /api/series/<id>/..., /api/books/<id>/... and /api/links/<id>/... (details,
pages, thumbnails, posters, downloads, changes), so a new endpoint under those can't forget it; a
series that can't be seen answers 404, like one that doesn't exist. Lists use Viewer.series_cond().
"""
import re
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import false, or_, select
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app import shelves, users
from app.db import SessionLocal, get_db
from app.models import Book, Link, SeriesShare, Series, Shelf, ShelfShare

SHARED = -1  # the "Shared with me" library: series shared on their own, not put in a library of the account's

PUBLIC = re.compile(r"^/api/(auth|health)(/|$)")
# (a plugin's own web pages, /api/plugins/<id>/web/..., are for every account: e.g. where a tracker
# site sends someone back after logging in to their own account there; the plugin gets ctx.user_id)
ADMIN = re.compile(r"^/api/(libraries|folders|scan|plugins(?!/[^/]+/web/)|update|metadata/providers|users/admin)(/|$)")
ITEM = re.compile(r"^/api/(series|books|links)/(\d+)(/|$)")
# what an account may send about a series it only reads: its own state, and the plugins' buttons.
# PATCH /api/series/<id> checks itself which library may be picked (series.py _move()).
READER_WRITES = {
    "PATCH": re.compile(r"^/api/series/\d+$"),
    "POST": re.compile(r"^/api/(series/\d+/(mark-read|mark-unread|actions/[^/]+/[^/]+)|books/\d+/actions/[^/]+/[^/]+)$"),
    "PUT": re.compile(r"^/api/(series/\d+/choices/[^/]+/[^/]+|books/\d+/progress)$"),
}
# reads that are only for those who may change the series
EDIT_READS = re.compile(r"^/api/series/\d+/(delete-info|shares)$")


# ---------------------------------------------------------------- what each account may see (cached)

@dataclass(frozen=True)
class Grants:
    owned: frozenset    # the account's own shelves
    shared: frozenset   # shelves shared with it
    series: dict        # series shared with it on their own -> the shelf of its own it put them in, or None


_cache = {"grants": {}, "items": {}, "at": 0.0}
_cache_lock = threading.Lock()


def clear():
    """Forget what's cached (shelves.invalidate() calls this after any change to who sees what)."""
    with _cache_lock:
        _cache.update(grants={}, items={}, at=time.monotonic())


def _fresh():
    """Called with the lock held: drops the cache after shelves.CACHE_SECONDS (another process may
    have changed something)."""
    if time.monotonic() - _cache["at"] > shelves.CACHE_SECONDS:
        _cache.update(grants={}, items={}, at=time.monotonic())


def grants(db, user_id) -> Grants:
    with _cache_lock:
        _fresh()
        if user_id in _cache["grants"]:
            return _cache["grants"][user_id]
    g = Grants(frozenset(sid for (sid,) in db.query(Shelf.id).filter(Shelf.owner_id == user_id)),
               frozenset(sid for (sid,) in db.query(ShelfShare.shelf_id).filter(ShelfShare.user_id == user_id)),
               dict(db.query(SeriesShare.series_id, SeriesShare.shelf_id).filter(SeriesShare.user_id == user_id)))
    with _cache_lock:
        _cache["grants"][user_id] = g
    return g


def item(db, kind, item_id):
    """(series id, shelf id) of a series, book or link; None when there's no such thing (cached)."""
    key = (kind, item_id)
    with _cache_lock:
        _fresh()
        if key in _cache["items"]:
            return _cache["items"][key]
    if kind == "series":
        sid = item_id if db.query(Series.id).filter(Series.id == item_id).scalar() is not None else None
    else:
        model = Book if kind == "books" else Link
        sid = db.query(model.series_id).filter(model.id == item_id).scalar()
    if sid is None:
        return None  # not kept: a scan may add it a moment later, and it must be checked then
    found = (sid, shelves.series_shelf(db, sid))
    with _cache_lock:
        if len(_cache["items"]) > 50000:
            _cache["items"].clear()
        _cache["items"][key] = found
    return found


# ---------------------------------------------------------------- one account, one request

class Viewer:
    """The account asking, with what it may see in this browser (its locked libraries left out)."""

    def __init__(self, db, account: users.Account, unlock_cookie=None):
        self.account = account
        self.id, self.name, self.is_admin = account.id, account.name, account.is_admin
        g = grants(db, account.id)
        self.owned, self.shared_shelves, self.targets = g.owned, g.shared, g.series
        self.locked = shelves.locked_ids(db, unlock_cookie) & self.shelves

    @property
    def shelves(self):
        """Every library the account sees in its tabs: its own and the ones shared with it."""
        return self.owned | self.shared_shelves

    @property
    def open_shelves(self):
        return self.shelves - self.locked

    def access(self, series_id, shelf_id):
        """"edit", "read", "locked" (on a locked library of its own or shared with it) or None (not
        for this account) for a series on this shelf."""
        if shelf_id in self.shelves and shelf_id not in self.locked:
            return "edit" if self.is_admin or shelf_id in self.owned else "read"
        if series_id in self.targets:
            return "edit" if self.is_admin else "read"
        return "locked" if shelf_id in self.locked else None

    def access_of(self, db, series_id):
        found = item(db, "series", series_id)
        return self.access(*found) if found else None

    def can_edit(self, db, series_id):
        return self.access_of(db, series_id) == "edit"

    def edit_or_403(self, db, series_id):
        if not self.can_edit(db, series_id):
            raise HTTPException(403, "Shared with you to read: only its owner can change it")

    # SQL conditions on Series
    def series_cond(self):
        """The series this account sees (on its open libraries, or shared with it on their own)."""
        conds = [Series.id.in_(select(SeriesShare.series_id).where(SeriesShare.user_id == self.id))]
        if self.open_shelves:
            conds.append(shelves.shelf_column().in_(sorted(self.open_shelves)))
        return or_(*conds)

    def edit_cond(self):
        """The series this account may change (Needs attention lists only those)."""
        if self.is_admin:
            return self.series_cond()
        own = self.owned - self.locked
        return shelves.shelf_column().in_(sorted(own)) if own else false()

    def tab_conds(self, shelf):
        """[conditions] for a home screen tab: every series it sees (None or 0), one library of its
        own or shared with it (403 while locked), or SHARED (shared on their own, not put in a library)."""
        if not shelf:
            return [self.series_cond()]
        shared_in = lambda cond: Series.id.in_(  # noqa: E731
            select(SeriesShare.series_id).where(SeriesShare.user_id == self.id, cond))
        if shelf == SHARED:
            return [shared_in(SeriesShare.shelf_id.is_(None))]
        if shelf in self.locked:
            raise HTTPException(403, "This library is locked")
        if shelf not in self.shelves:
            raise HTTPException(404, "Library not found")
        return [or_(shelves.shelf_column() == shelf, shared_in(SeriesShare.shelf_id == shelf))]

    def own_shelf_or_403(self, db, shelf_id):
        """The shelf, if this account may change it (its own, or any for an admin)."""
        shelf = db.get(Shelf, shelf_id)
        if shelf is None or not (self.is_admin or shelf.id in self.shelves):
            raise HTTPException(404, "Library not found")
        if not (self.is_admin or shelf.owner_id == self.id):
            raise HTTPException(403, "Shared with you to read: only its owner can change it")
        return shelf


def viewer(request: Request, db=Depends(get_db)) -> Viewer:
    """FastAPI dependency: who is asking (the guard already checked they're signed in, and for a
    series, book or link already worked out the Viewer: it's used again)."""
    state = request.scope.get("state", {})
    if state.get("viewer") is not None:
        return state["viewer"]
    account = state.get("account") or users.account_of(db, request.cookies.get(users.COOKIE))
    if account is None:
        raise HTTPException(401, {"message": "Sign in first", "sign_in": True})
    return Viewer(db, account, request.cookies.get(shelves.COOKIE))


def admin(v: Viewer = Depends(viewer)) -> Viewer:
    if not v.is_admin:
        raise HTTPException(403, "Only the admin can do this")
    return v


# ---------------------------------------------------------------- the guard

class _Miss(Exception):
    """Something isn't cached: the check runs again in a thread with a database session."""


class _NoDatabase:
    def __getattr__(self, name):
        raise _Miss


def _decide(db, method, path, session_cookie, unlock_cookie):
    """(account, viewer or None, None) to let the request through, or (None, None, (status, detail))
    to answer it here. The Viewer worked out for a series, book or link goes on to the endpoint."""
    account = users.account_of(db, session_cookie)
    if account is None:
        return None, None, (401, {"message": "Sign in first", "sign_in": True})
    if ADMIN.match(path) and not account.is_admin:
        return None, None, (403, "Only the admin can do this")
    m = ITEM.match(path)
    if not m:
        return account, None, None
    found = item(db, m[1], int(m[2]))
    if found is None:
        return account, None, None  # the endpoint answers its own 404
    v = Viewer(db, account, unlock_cookie)
    access = v.access(*found)
    if access is None:
        return None, None, (404, "Not found")
    if access == "locked":
        shelf = db.get(Shelf, found[1])
        # the page asks for this library's password (detail.shelf)
        return None, None, (403, {"message": f"The library {shelf.name} is locked", "shelf": shelf.id})
    allowed = READER_WRITES.get(method)
    writes = method not in ("GET", "HEAD") and not (allowed and allowed.match(path))
    if access == "read" and (writes or EDIT_READS.match(path)):
        return None, None, (403, "Shared with you to read: only its owner can change it")
    return account, v, None


def _decide_with_session(*args):
    with SessionLocal() as db:
        return _decide(db, *args)


class Guard:
    """Checks every /api/ request (see the module docstring). A plain ASGI middleware, like
    RememberAddress in app/main.py. Answers from its caches when it can, without a database session
    or a thread; the account (and the Viewer, when it made one) go in scope["state"] for viewer()."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or PUBLIC.match(path):
            return await self.app(scope, receive, send)
        cookies = Request(scope).cookies
        args = (scope["method"], path, cookies.get(users.COOKIE), cookies.get(shelves.COOKIE))
        try:
            account, v, refused = _decide(_NoDatabase(), *args)
        except _Miss:
            account, v, refused = await run_in_threadpool(_decide_with_session, *args)
        if refused:
            status, detail = refused
            return await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
        state = scope.setdefault("state", {})
        state["account"], state["viewer"] = account, v
        return await self.app(scope, receive, send)
