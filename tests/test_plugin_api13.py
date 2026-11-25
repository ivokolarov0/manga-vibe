"""Plugin API 13, the app's side of Remote control (the MCP plugin lives in manga-vibe-mcp): a request
whose user server code already knows (users.SCOPE_KEY), find_user(), plugins' own mcp_tools, the
built-in plugin that moved out, and the page's lists that the MCP plugin copies."""
import asyncio
import json

from app import users
from app.db import SessionLocal, get_setting, set_setting
from app.models import Setting
from app.plugins import MOVED, Context, Plugin, manager
from app.plugins.builtin.accounts import core as accounts


def call_guard(path, scope_user=None):
    """One request through AccountsGuard: (status, the user the app ran as)."""
    seen, sent = {}, {}

    async def app(scope, receive, send):
        seen["user"] = users.current()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        if msg["type"] == "http.response.start":
            sent["status"] = msg["status"]

    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
    if scope_user is not None:
        scope[users.SCOPE_KEY] = scope_user
    asyncio.run(accounts.AccountsGuard(app)(scope, receive, send))
    return sent["status"], seen.get("user")


def test_scope_user_is_trusted():
    """What the MCP plugin's middleware relies on: a user in the scope runs the request as them."""
    assert call_guard("/api/series")[0] == 401  # no login, no scope user
    kid = users.User(2, "Kid", can_manage=False)
    status, ran_as = call_guard("/api/series", kid)
    assert status == 200 and ran_as is kid
    assert users.current() is users.OWNER  # only for that request


def test_find_user(client):
    """Who a key's user id is: the owner without accounts; with them, the account, until it's deleted."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import Progress, SeriesState
    A = "/api/plugins/accounts/api"
    with SessionLocal() as db:
        assert manager.find_user(db, 1) is users.OWNER and manager.find_user(db, 2) is None
    assert client.put("/api/plugins/accounts", json={"enabled": True}).status_code == 200
    try:
        client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
        assert client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1", "admin": False}).status_code == 200
        with SessionLocal() as db:
            kid = manager.find_user(db, 2)
        assert (kid.id, kid.name, kid.can_manage) == (2, "Kid", False)
        # the kid's own request, by the scope: runs as the kid, and may not manage the app
        assert call_guard("/api/series", kid) == (200, kid)
        assert client.delete(f"{A}/accounts/2").status_code == 200
        accounts.invalidate()
        with SessionLocal() as db:
            assert manager.find_user(db, 2) is None
        assert TestClient(app).get("/api/series").status_code == 401  # and still nobody without a login
    finally:
        client.cookies.clear()
        manager.set_enabled("accounts", False)
        with SessionLocal() as db:
            set_setting(db, "plugins_disabled", sorted(manager.disabled))
            db.query(Setting).filter(Setting.key == accounts.KEY).delete()
            db.query(Setting).filter(Setting.key.like("user:%")).delete(synchronize_session=False)
            db.query(Progress).filter(Progress.user_id != 1).delete()
            db.query(SeriesState).filter(SeriesState.user_id != 1).delete()
            db.commit()
        accounts.invalidate()


class FakeMcp(Plugin):
    id = "mcp"
    name = "Remote control (MCP)"


def _switches(known, disabled, moved=()):
    with SessionLocal() as db:
        set_setting(db, "plugins_known", sorted(known))
        set_setting(db, "plugins_disabled", sorted(disabled))
        set_setting(db, "plugins_moved", sorted(moved))
        db.commit()
        manager.load_switches(db)
        return (set(get_setting(db, "plugins_known", [])), set(get_setting(db, "plugins_disabled", [])),
                get_setting(db, "plugins_moved", []))


def test_moved_builtin_plugin(client):
    """The built-in Remote control (2.17.0) moved to manga-vibe-mcp: whoever used it is told where it
    went, and installing it again starts it off, so old keys don't work without switching it on."""
    assert "mcp" in MOVED and "mcp" not in manager.plugins
    with SessionLocal() as db:
        before = (get_setting(db, "plugins_known", []), get_setting(db, "plugins_disabled", []))
    others = set(manager.plugins)
    try:
        # it was on: forgotten, switched off, and the owner told
        known, disabled, moved = _switches(others | {"mcp"}, manager.disabled - {"mcp"})
        assert "mcp" not in known and "mcp" in disabled and moved == ["mcp"]
        listed = client.get("/api/plugins").json()["moved"]
        assert listed == [{"id": "mcp", **MOVED["mcp"]}]
        # the next start: still told, nothing else changes
        assert _switches(known, disabled, moved) == (known, disabled, moved)
        # installed again: starts off, and the notice goes
        manager.plugins["mcp"] = FakeMcp()
        try:
            known, disabled, moved = _switches(known, disabled, moved)
            assert "mcp" in known and "mcp" in disabled and moved == []
            assert client.get("/api/plugins").json()["moved"] == []
        finally:
            del manager.plugins["mcp"]
        # it was never switched on: forgotten quietly
        known, disabled, moved = _switches(others | {"mcp"}, manager.disabled | {"mcp"})
        assert "mcp" not in known and moved == []
    finally:
        with SessionLocal() as db:
            set_setting(db, "plugins_known", before[0])
            set_setting(db, "plugins_disabled", before[1])
            set_setting(db, "plugins_moved", [])
            db.commit()
            manager.load_switches(db)


# What manga-vibe-mcp's mcp/tools.py describes to assistants (FILTERS, SORTS, SETTINGS). A change to the
# page's filters, sorts or settings fails this test: update the lists here and in manga-vibe-mcp too,
# or assistants can't use what's new.
MCP_FILTERS = {
    "read": ["unread", "reading", "completed", "on_hold", "dropped", "plan_to_read"],
    "pub": ["RELEASING", "FINISHED", "HIATUS", "CANCELLED", "NOT_YET_RELEASED", "UNKNOWN"],
    "match": ["matched", "attention", "unmatched"],
    "format": ["manga", "manhwa", "manhua", "one_shot", "novel"],
    "have": ["complete", "incomplete", "unknown"],
}
MCP_SORTS = ["title", "added", "updated", "read", "score", "year", "unread"]
MCP_SETTINGS = [
    "language", "title_language", "theme", "card_size", "grid_columns", "page_width", "page_width_series", "card_width",
    "nav_position", "library_listing", "library_page_size", "library_view", "home_layout",
    "reader_mode", "reader_fit",
    "reader_spread", "auto_fullscreen", "preload", "reader_autohide", "reader_top_bar", "reader_drawer_side", "reader_tap_layout",
    "reader_volume_keys", "overlay_opacity", "overlay_outline", "reader_brightness", "reader_contrast",
    "reader_colors", "auto_match", "match_threshold", "include_adult", "auto_update",
]
SEE = " changed: update this test's lists, and FILTERS / SORTS / SETTINGS in manga-vibe-mcp's mcp/tools.py"


def test_lists_remote_control_copies():
    # the library's filters and sorts are the built-in library-query plugin's (#204); a plugin that
    # replaces it says its own in GET /api/library/schema, which the MCP plugin could read instead
    from app.plugins.builtin.library_query import SCHEMA
    page_filters = {f["key"]: [v for v, _ in f["options"]] for f in SCHEMA["filters"] if f["kind"] == "choice"}
    assert page_filters == MCP_FILTERS, "The library filters" + SEE
    assert [s["key"] for s in SCHEMA["sorts"]] == MCP_SORTS, "The library sorts" + SEE
    from app.api.system import DEFAULT_SETTINGS
    assert sorted(DEFAULT_SETTINGS) == sorted(MCP_SETTINGS), "The settings" + SEE


def test_saved_searches_tool(client):
    """A built-in plugin's own tool: the person's saved searches, as run_mcp_tool returns them."""
    S = "/api/plugins/saved-searches/api/searches"
    made = client.post(S, json={"name": "Isekai", "query": "genre=Isekai&sort=year"}).json()
    try:
        p = manager.plugins["saved-searches"]
        assert [t.name for t in p.mcp_tools] == ["list"]
        with SessionLocal() as db:
            found = p.run_mcp_tool(Context(p, db), "list", {})
            # and as user 2, who has none
            with users.acting_as(users.User(2, "Kid", can_manage=False)):
                theirs = p.run_mcp_tool(Context(p, db), "list", {})
        assert {"name": "Isekai", "query": "genre=Isekai&sort=year"} in found["saved_searches"]
        assert json.dumps(found)  # what an assistant gets must be JSON
        assert theirs == {"saved_searches": []}
    finally:
        client.delete(f"{S}/{made['id']}")
