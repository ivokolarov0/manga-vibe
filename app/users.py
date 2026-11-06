"""Who is asking: the current user of a request.

Without an accounts plugin there is one user, the owner (id 1), who may do everything: the app works as
it always did. An accounts plugin (app/plugins/builtin/accounts/) logs people in and sets the user for
each request from its middleware (`acting_as`); the user's id picks their own reading progress, reading
status, favorites, Continue reading, saved searches and reader settings (app/models.py SeriesState,
Progress.user_id). Background work (scans, metadata, plugin events) runs as the owner.

`manage` guards what only an admin may do (settings, folders, scans, plugins, metadata, deleting,
updates): FastAPI routes add `dependencies=[manage]`.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Depends, HTTPException


@dataclass(frozen=True)
class User:
    id: int
    name: str = ""
    can_manage: bool = True  # an admin: settings, folders, plugins, metadata, deleting


OWNER = User(1)  # the only user without accounts; the first account (the admin) gets this id and its data
_current: ContextVar[User] = ContextVar("mangavibe_user", default=OWNER)


def current() -> User:
    return _current.get()


def current_id() -> int:
    return _current.get().id


@contextmanager
def acting_as(user: User):
    """Run the code inside as this user (a request in the accounts middleware, a test)."""
    token = _current.set(user)
    try:
        yield user
    finally:
        _current.reset(token)


def _require_manage():
    if not current().can_manage:
        raise HTTPException(403, "Only an admin can do this")


manage = Depends(_require_manage)  # dependencies=[manage] on a route: admins only


def forget(db, user_id: int):
    """Delete a user's own data (an account was removed). Not committed."""
    from app.models import Progress, Setting, SeriesState
    db.query(Progress).filter(Progress.user_id == user_id).delete()
    db.query(SeriesState).filter(SeriesState.user_id == user_id).delete()
    db.query(Setting).filter(Setting.key.like(f"user:{user_id}:%")).delete(synchronize_session=False)


def personal_key(key: str) -> str:
    """The settings key for the current user's own copy of a setting (the owner uses the shared one)."""
    uid = current_id()
    return key if uid == OWNER.id else f"user:{uid}:{key}"
