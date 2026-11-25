"""The activity log: who did what, and when (issue #279). One place to write an entry, `activity.log()`.

    from app import activity                  # a plugin: from app.plugins import activity
    activity.log("series.deleted", "One Piece", target=("series", 42), details={"books": 110})

It fills in the time (UTC) and who: the person asking during a request (users.current(), with the address
the request came from; `via` is "key" for a request that came with a key, e.g. the MCP server), and
*System* for background work (scans, the folder watcher, metadata, plugin events): user_id None. Then it
sends the `on_activity(ctx, entry)` event, which runs in the background like the reading events. The
built-in Activity log plugin (app/plugins/builtin/activity_log/) stores the entries and shows them to
admins; with no plugin listening, log() does nothing.

An entry keeps the kind and its values, not a finished sentence, so the page can say it in the reader's
language (#263): `summary` is the name of what it's about at the time (a series' title, an account's
name), `details` a small JSON object (old and new value, counts, a folder). Never a password, key or
token: details whose name says it's one are left out (SECRET).

log() never breaks the action it records: a mistake in it is logged and swallowed.
"""
import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone

from app import users

log_ = logging.getLogger("mangavibe.activity")

# The kinds of activity, by group (the page's filter). A plugin may log kinds of its own, named after
# it ("<plugin id>.<what>"): they're in the group "other".
GROUPS = ("accounts", "library", "plugins", "settings", "app", "other")
KINDS = {
    # accounts (the accounts plugin; the keys: the Remote control plugin, manga-vibe-mcp)
    "account.login": "accounts", "account.logout": "accounts", "account.login_failed": "accounts",
    "account.added": "accounts", "account.removed": "accounts", "account.renamed": "accounts",
    "account.admin_given": "accounts", "account.admin_taken": "accounts",
    "account.password_changed": "accounts", "account.password_reset": "accounts",
    "account.access_changed": "accounts",  # the libraries an account sees (the libraries plugin)
    "account.key_created": "accounts", "account.key_removed": "accounts",
    # library: folders, scans, series, metadata, and the libraries plugin's libraries
    "folder.added": "library", "folder.removed": "library", "folder.changed": "library",
    "scan.started": "library", "scan.finished": "library",
    "series.deleted": "library", "series.moved": "library",
    "metadata.changed": "library", "metadata.matched": "library", "metadata.unmatched": "library",
    "metadata.refetched": "library",
    "library.added": "library", "library.changed": "library", "library.removed": "library",
    "library.password_set": "library", "library.password_removed": "library",
    "library.unlock_failed": "library",
    # plugins
    "plugin.installed": "plugins", "plugin.enabled": "plugins", "plugin.disabled": "plugins",
    "plugin.updated": "plugins", "plugin.restored": "plugins", "plugin.failed": "plugins",
    "plugin_source.added": "plugins", "plugin_source.removed": "plugins",
    "safe_mode.on": "plugins", "safe_mode.off": "plugins",
    # settings: the app's (not each person's own, like the theme) and the plugins'
    "setting.changed": "settings",
    # the app
    "backup.exported": "app", "backup.imported": "app", "activity.cleared": "app",
    "update.started": "app", "update.finished": "app", "update.failed": "app",
}
KIND = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)+$")
# a detail with one of these in its name is never kept, whatever its value
SECRET = re.compile(r"pass(word|wd)?|secret|token|api[_-]?key|cookie|hash|credential", re.I)
MAX_DETAILS = 8000  # characters of JSON; bigger details are cut down to a note saying so
MAX_TEXT = 300      # characters of the summary

# the ASGI scope of the request being served (the Requests middleware); unset outside requests: System
_request: ContextVar[dict | None] = ContextVar("mangavibe_activity_request", default=None)


def group_of(kind: str) -> str:
    return KINDS.get(kind, "other")


def in_request() -> bool:
    """True while a request is served (someone did it), False in background work (System)."""
    return _request.get() is not None


def clean(value, depth=0):
    """`value` without anything whose name says it's secret, as JSON can hold it."""
    if depth > 5:
        return "…"
    if isinstance(value, dict):
        return {str(k): clean(v, depth + 1) for k, v in value.items() if not SECRET.search(str(k))}
    if isinstance(value, (list, tuple, set)):
        return [clean(v, depth + 1) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def entry_of(kind, summary="", target=None, details=None) -> dict:
    """The entry log() sends: the time, who, and what."""
    if not isinstance(kind, str) or not KIND.match(kind):
        raise ValueError(f"Not a kind of activity: {kind!r} (like 'series.deleted')")
    scope = _request.get()
    if scope is None:
        who, ip, via = None, None, None
    else:  # read now: a plugin's middleware may have put the user in the scope after Requests saw it
        client = scope.get("client")
        keyed = scope.get(users.SCOPE_KEY) is not None or scope.get("mangavibe.internal")
        who, ip, via = users.current(), client[0] if client else None, "key" if keyed else None
    if target is not None:
        kind_of, target_id = target
        target = [str(kind_of), None if target_id is None else str(target_id)]
    details = clean(details or {})
    if not isinstance(details, dict):
        details = {"value": details}
    if len(json.dumps(details, ensure_ascii=False)) > MAX_DETAILS:
        details = {"note": "too long to keep"}
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "user_id": None if who is None else who.id,
        "user_name": "" if who is None else who.name,
        "ip": ip, "via": via,
        "kind": kind, "group": group_of(kind),
        "summary": str(summary or "")[:MAX_TEXT],
        "target": target,
        "details": details,
    }


def log(kind: str, summary: str = "", target: tuple | None = None, details: dict | None = None):
    """Write an entry: `kind` like "series.deleted" (KINDS), `summary` the name of what it's about,
    `target` (type, id) like ("series", 42) so the page can link to it, `details` a small JSON object
    (never a password, key or token). Sends on_activity in the background; never raises."""
    try:
        from app.plugins import manager
        manager.emit("on_activity", entry_of(kind, summary, target, details))
    except Exception:  # noqa: BLE001 - the log must never break what it records
        log_.exception("Could not log the activity %r", kind)


class Requests:
    """Notes where each request comes from, for log(): the address, and whether it came with a key (the
    MCP server sets the user in the scope, app/users.py SCOPE_KEY) or is the server's own call on someone's
    behalf. A plain ASGI middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = _request.set(scope)
        try:
            return await self.app(scope, receive, send)
        finally:
            _request.reset(token)
