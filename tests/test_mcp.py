"""Remote control, a built-in plugin (app/plugins/builtin/mcp/): MangaVibe's MCP server. Keys act as their
person, data tools go through the app's own API, screen tools reach an open page that polls for commands,
and plugins add tools of their own (Plugin API 13)."""
import json
import re
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Setting
from app.plugins import McpTool, Plugin, manager
from app.plugins.builtin.mcp import keys, screens, tools

M = "/api/plugins/mcp/api"
APP_JS = Path(__file__).parent.parent / "app" / "web" / "static" / "js" / "app.js"


@pytest.fixture
def mcp_on(client):
    assert client.put("/api/plugins/mcp", json={"enabled": True}).status_code == 200
    try:
        yield
    finally:
        client.put("/api/plugins/mcp", json={"enabled": False})
        screens.hub.clear()
        with SessionLocal() as db:
            db.query(Setting).filter(Setting.key.like("%mcp_keys")).delete(synchronize_session=False)
            db.commit()


@pytest.fixture
def key(client, mcp_on):
    r = client.post(f"{M}/keys", json={"name": "Test"})
    assert r.status_code == 200, r.text
    return r.json()["key"]


class Mcp:
    """An MCP client: JSON-RPC over POST /mcp with a key."""

    def __init__(self, client, key):
        self.c, self.key, self.n = client, key, 0

    def rpc(self, method, params=None, **headers):
        self.n += 1
        r = self.c.post("/mcp", json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}},
                        headers={"Authorization": f"Bearer {self.key}", **headers})
        assert r.status_code == 200, r.text
        return r.json()

    def call(self, name, **args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        assert "result" in r, r
        return r["result"]

    def ok(self, name, **args):
        r = self.call(name, **args)
        assert not r.get("isError"), r["content"][0]["text"]
        if "structuredContent" in r:
            return r["structuredContent"]
        text = r["content"][0]["text"]
        return json.loads(text) if text[:1] in "[{" else text


def test_off_by_default(client):
    plugin = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "mcp")
    assert plugin["enabled"] is False and plugin["builtin"] is True
    assert client.post("/mcp", json={}).status_code in (404, 405)


def test_needs_a_key(client, mcp_on):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    r = client.post("/mcp", json=msg)
    assert r.status_code == 401 and "Bearer" in r.headers["www-authenticate"]
    assert client.post("/mcp", json=msg, headers={"Authorization": "Bearer mvk_1_" + "x" * 40}).status_code == 401
    # the plugin's own route answers only through /mcp and its key check
    assert client.post(f"{M}/mcp", json=msg).status_code == 404


def test_keys(client, key):
    mcp = Mcp(client, key)
    assert mcp.rpc("ping")["result"] == {}
    listed = client.get(f"{M}/keys").json()
    assert [k["name"] for k in listed] == ["Test"] and "hash" not in listed[0] and "key" not in listed[0]
    assert listed[0]["used"]  # it was just used
    # the key also works as ?key= (clients that can't set a header), and stops working once deleted
    assert client.post(f"/mcp?key={key}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200
    assert client.delete(f"{M}/keys/{listed[0]['id']}").status_code == 200
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_web_pages_from_other_sites_are_refused(client, key):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {key}", "Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Authorization": f"Bearer {key}", "Origin": "http://testserver"})
    assert r.status_code == 200


def test_protocol(client, key):
    mcp = Mcp(client, key)
    init = mcp.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"]
    assert init["serverInfo"]["name"] == "MangaVibe" and init["instructions"]
    assert mcp.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    # a notification gets 202 and nothing else
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 202 and not r.content
    assert mcp.rpc("nope")["error"]["code"] == -32601
    assert mcp.rpc("tools/call", {"name": "nope"})["error"]["code"] == -32602
    # no event stream from the server
    assert client.get("/mcp", headers={"Authorization": f"Bearer {key}"}).status_code == 405
    names = {t["name"] for t in mcp.rpc("tools/list")["result"]["tools"]}
    assert {"library_filters", "library_search", "series_get", "continue_reading", "settings_get",
            "settings_change", "screens_list", "screen_describe", "screen_press", "screen_navigate",
            "screen_library", "reader_turn", "reader_go_to", "reader_mode", "reader_plugin", "api_request"} <= names
    for t in mcp.rpc("tools/list")["result"]["tools"]:
        assert re.fullmatch(r"[A-Za-z0-9_]{1,64}", t["name"]) and t["inputSchema"]["type"] == "object"


def test_library_and_series(client, key):
    mcp = Mcp(client, key)
    found = mcp.ok("library_search", q="hana")
    assert found["total"] == 1  # (its title may come from metadata other tests matched)
    sid = found["series"][0]["series_id"]
    assert mcp.ok("library_search", read=["completed"], q="hana")["total"] == 0
    bad = mcp.call("library_search", read=["nonsense"])
    assert bad["isError"] and "unread" in bad["content"][0]["text"]
    filters = mcp.ok("library_filters")
    assert filters["filters"]["read"]["values"]["plan_to_read"] == "Plan to read" and "title" in filters["sorts"]

    s = mcp.ok("series_get", series_id=sid)
    assert s["series_id"] == sid and len(s["books"]) >= 4 and s["continue"]["page"] == 1
    first = s["books"][0]["book_id"]
    assert mcp.ok("mark_read", book_id=first) == "Done"
    assert mcp.ok("series_get", series_id=sid)["books"][0]["read"] is True
    assert any(c["series_id"] == sid for c in mcp.ok("continue_reading"))
    mcp.ok("series_update", series_id=sid, favorite=True, reading_status="on_hold")
    assert mcp.ok("library_search", favorites=True)["series"][0]["series_id"] == sid
    mcp.ok("series_update", series_id=sid, favorite=False, reading_status="auto")
    mcp.ok("mark_read", series_id=sid, read=False)
    assert mcp.ok("series_get", series_id=sid)["books"][0]["read"] is False
    missing = mcp.call("series_get", series_id=999999)
    assert missing["isError"] and missing["content"][0]["text"].startswith("404")


def test_settings(client, key):
    mcp = Mcp(client, key)
    s = mcp.ok("settings_get")["settings"]
    assert s["reader_mode"]["values"] == ["paged", "vertical"] and s["reader_mode"]["may_change"] is True
    assert mcp.ok("settings_change", values={"reader_brightness": "70"}).startswith("Changed")
    assert client.get("/api/settings").json()["reader_brightness"] == 70
    assert mcp.call("settings_change", values={"reader_brightness": 33})["isError"]
    assert mcp.call("settings_change", values={"nope": 1})["isError"]
    mcp.ok("settings_change", values={"reader_brightness": 100})


def test_api_request(client, key):
    mcp = Mcp(client, key)
    assert mcp.ok("api_request", method="GET", path="/api/health") == {"ok": True}
    assert mcp.call("api_request", method="GET", path="/mcp")["isError"]
    assert mcp.call("api_request", method="GET", path="/api/../mcp")["isError"]
    # the server's own calls don't change the address plugins use for links back
    assert manager.base_url != "http://mangavibe"


def test_screens(client, key):
    """A page polls for commands; a screen tool's command reaches it and its answer comes back."""
    mcp = Mcp(client, key)
    nothing = mcp.call("screen_describe")
    assert nothing["isError"] and "isn't open" in nothing["content"][0]["text"]
    page = {"screen": "tab1", "name": "Firefox on Windows", "visible": True, "input_ago": 1,
            "where": {"view": "library", "address": "#/", "title": "MangaVibe"},
            "commands": [{"plugin": "mcp", "name": "wave", "spec": {"description": "Wave at the person",
                                                                     "params": {"hand": {"type": "string"}}}}]}
    got = []

    def browser():
        """The page: asks for commands, does them, answers (as mcp.js does)."""
        for _ in range(3):
            r = client.post(f"{M}/screens/poll", json=page)
            for cmd in r.json()["commands"]:
                got.append(cmd)
                client.post(f"{M}/screens/result", json={"screen": "tab1", "id": cmd["id"], "ok": True,
                                                         "result": {"done": cmd["type"]}})

    old = screens.POLL_SECONDS
    screens.POLL_SECONDS = 2
    try:
        client.post(f"{M}/screens/state", json=page)  # it's there before its first command
        listed = mcp.ok("screens_list")
        assert listed["screens"][0]["name"] == "Firefox on Windows" and listed["screens"][0]["default"] is True
        t = threading.Thread(target=browser)
        t.start()
        time.sleep(0.3)
        assert mcp.ok("screen_navigate", to="series", series_id=5) == {"done": "go", "screen": "Firefox on Windows"}
        assert got[-1]["args"] == {"address": "#/series/5"}
        mcp.ok("screen_library", genre=["Romance"], read=["reading"], sort="added")
        assert got[-1]["args"]["address"] == "#/?genre=Romance&read=reading&sort=added"
        # the page's plugin commands are tools too
        names = {x["name"] for x in mcp.rpc("tools/list")["result"]["tools"]}
        assert "mcp__wave" in names
        mcp.ok("mcp__wave", hand="left")
        assert got[-1]["args"] == {"plugin": "mcp", "name": "wave", "params": {"hand": "left"}}
        t.join(10)
        # a screen asked for by a name it doesn't have
        assert "No open screen" in mcp.call("reader_state", screen="tv")["content"][0]["text"]
    finally:
        screens.POLL_SECONDS = old


def test_screen_errors_and_timeouts(client, key):
    mcp = Mcp(client, key)
    page = {"screen": "tab2", "name": "Chrome"}

    def browser():
        r = client.post(f"{M}/screens/poll", json=page)
        for cmd in r.json()["commands"]:
            client.post(f"{M}/screens/result", json={"screen": "tab2", "id": cmd["id"], "ok": False,
                                                     "error": "No book is open on this screen"})

    old = screens.POLL_SECONDS, screens.ANSWER_SECONDS
    screens.POLL_SECONDS, screens.ANSWER_SECONDS = 2, 1
    try:
        client.post(f"{M}/screens/state", json=page)
        t = threading.Thread(target=browser)
        t.start()
        time.sleep(0.3)
        r = mcp.call("reader_turn", direction="next")
        assert r["isError"] and r["content"][0]["text"] == "No book is open on this screen"
        t.join(5)
        # nobody answers: the tool says so instead of waiting forever
        r = mcp.call("screen_key", key="Escape")
        assert r["isError"] and "didn't answer" in r["content"][0]["text"]
    finally:
        screens.POLL_SECONDS, screens.ANSWER_SECONDS = old


def test_plugin_tools(client, key):
    class Tools(Plugin):
        id = "tool-test"
        name = "Tool test"
        mcp_tools = [McpTool("echo", "Say it back", {"text": {"type": "string"}}, ["text"]),
                     McpTool("secret", "Admins only", admin=True)]

        def run_mcp_tool(self, ctx, name, args):
            if name == "echo":
                if args["text"] == "fail":
                    raise ValueError("It failed")
                return {"said": args["text"], "settings": ctx.settings}
            return "ok"

    manager.plugins["tool-test"] = Tools()
    try:
        mcp = Mcp(client, key)
        listed = {t["name"]: t for t in mcp.rpc("tools/list")["result"]["tools"]}
        assert listed["tool_test__echo"]["inputSchema"]["required"] == ["text"]
        assert listed["tool_test__echo"]["description"].startswith("Tool test: ")
        assert mcp.ok("tool_test__echo", text="hi") == {"said": "hi", "settings": {}}
        assert mcp.call("tool_test__echo")["content"][0]["text"] == "Missing: text"
        assert mcp.call("tool_test__echo", text="fail")["content"][0]["text"] == "It failed"
        assert mcp.ok("tool_test__secret") == "ok"  # the owner is an admin
    finally:
        del manager.plugins["tool-test"]


def test_saved_searches_tool(client, key):
    """A built-in plugin's own tool: the saved searches, the person's own."""
    S = "/api/plugins/saved-searches/api/searches"
    made = client.post(S, json={"name": "Isekai", "query": "genre=Isekai&sort=year"}).json()
    try:
        found = Mcp(client, key).ok("saved_searches__list")
        assert {"name": "Isekai", "query": "genre=Isekai&sort=year"} in found["saved_searches"]
    finally:
        client.delete(f"{S}/{made['id']}")


def test_acts_as_its_person(client, mcp_on):
    """With accounts: a key runs as the account that made it, and stops when the account is deleted."""
    from app.main import app
    from app.plugins.builtin.accounts import core as accounts
    A = "/api/plugins/accounts/api"
    assert client.put("/api/plugins/accounts", json={"enabled": True}).status_code == 200
    other = TestClient(app)
    try:
        client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
        assert client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1", "admin": False}).status_code == 200
        assert other.post(f"{A}/login", json={"name": "Kid", "password": "kid1"}).status_code == 200
        kid_key = other.post(f"{M}/keys", json={"name": "Kid's"}).json()["key"]
        assert kid_key.startswith("mvk_2_")
        assert client.get(f"{M}/keys").json() == []  # each person sees only their own
        # no login cookie: the key is enough, and the tools run as the kid
        kid = Mcp(TestClient(app), kid_key)
        assert kid.ok("screens_list")["you"] == {"id": 2, "name": "Kid", "admin": False}
        s = kid.ok("settings_get")["settings"]
        assert s["reader_mode"]["may_change"] and not s["auto_match"]["may_change"]
        ivo_mode = client.get("/api/settings").json()["reader_mode"]
        kid_mode = "paged" if ivo_mode == "vertical" else "vertical"
        assert kid.ok("settings_change", values={"reader_mode": kid_mode})
        assert kid.ok("settings_get")["settings"]["reader_mode"]["value"] == kid_mode
        refused = kid.call("settings_change", values={"auto_match": False})
        assert refused["isError"] and "403" in refused["content"][0]["text"]
        assert client.get("/api/settings").json()["reader_mode"] == ivo_mode  # the kid's own, not Ivo's
        assert kid.call("api_request", method="GET", path="/api/plugins")["isError"]  # admins only
        # the account goes: its key with it
        assert client.delete(f"{A}/accounts/2").status_code == 200
        accounts.invalidate()
        r = TestClient(app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 headers={"Authorization": f"Bearer {kid_key}"})
        assert r.status_code == 401
    finally:
        client.cookies.clear()
        manager.set_enabled("accounts", False)
        from app.db import set_setting
        from app.models import Progress, SeriesState
        with SessionLocal() as db:
            set_setting(db, "plugins_disabled", sorted(manager.disabled))
            db.query(Setting).filter(Setting.key == accounts.KEY).delete()
            db.query(Setting).filter(Setting.key.like("user:%")).delete(synchronize_session=False)
            db.query(Progress).filter(Progress.user_id != 1).delete()
            db.query(SeriesState).filter(SeriesState.user_id != 1).delete()
            db.commit()
        accounts.invalidate()


def test_tool_lists_match_the_page():
    """The filters and settings the tools describe are the page's (app.js): new ones need adding there."""
    js = APP_JS.read_text(encoding="utf-8")
    groups = js[js.index("const FILTER_GROUPS"):js.index("const LIST_KEYS")]
    for key, (_, values) in tools.FILTERS.items():
        assert f'["{key}",' in groups, key
        for v in values:
            assert re.search(rf"\b{re.escape(v)}:", groups), (key, v)
    assert len(re.findall(r'\["(\w+)", "', groups)) == len(tools.FILTERS)
    sorts = js[js.index("const SORTS"):js.index("const FILTER_GROUPS")]
    assert set(re.findall(r'\["(\w+)", "', sorts)) == set(tools.SORTS)
    from app.api.system import DEFAULT_SETTINGS
    assert set(tools.SETTINGS) == set(DEFAULT_SETTINGS)


def test_keys_hash_only():
    with SessionLocal() as db:
        row, token = keys.create(db, 1, "x")
        assert token.startswith("mvk_1_") and token.split("_", 2)[2] not in str(row)
        db.rollback()
