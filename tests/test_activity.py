"""The activity log (issue #279, step 1 #280): activity.log() in the core, the on_activity event, and the
built-in Activity log plugin that keeps the entries and shows them to admins only."""
import pytest

from app import activity, users
from app.plugins import manager
from tests.test_accounts import A, accounts_on  # noqa: F401 - the fixture

E = "/api/plugins/activity-log/api/entries"


@pytest.fixture
def fresh(client):
    """The log empty, and on."""
    from app.db import SessionLocal
    from app.plugins.builtin.activity_log import store
    manager.wait_idle()
    with SessionLocal() as db:
        store.ensure_table(db)
        db.execute(store.table.delete())
        db.commit()
    yield
    manager.wait_idle()


def entries(client, **params):
    manager.wait_idle()
    r = client.get(E, params=params)
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def test_an_entry_is_kept_and_read_back(client, fresh):
    activity.log("series.deleted", "One Piece", target=("series", 42), details={"books": 3})
    [e] = entries(client)
    assert e["kind"] == "series.deleted" and e["group"] == "library" and e["summary"] == "One Piece"
    assert e["target"] == ["series", "42"] and e["details"] == {"books": 3}
    assert e["at"].endswith("Z")
    assert e["user_id"] is None and e["ip"] is None  # not in a request: System


def test_a_request_is_the_persons(client):
    from app.main import app
    assert any(m.cls is activity.Requests for m in app.user_middleware)
    scope = {"type": "http", "client": ("10.0.0.7", 5000)}
    token = activity._request.set(scope)
    try:
        with users.acting_as(users.User(3, "Kid", can_manage=False)):
            e = activity.entry_of("probe.did", "it")
        assert (e["user_id"], e["user_name"], e["ip"], e["via"]) == (3, "Kid", "10.0.0.7", None)
        assert e["group"] == "other"  # a plugin's own kind
        scope[users.SCOPE_KEY] = users.OWNER  # a request that came with a key (the MCP server)
        assert activity.entry_of("probe.did")["via"] == "key"
    finally:
        activity._request.reset(token)
    assert activity.entry_of("probe.did")["user_id"] is None  # outside a request: System


def test_newest_first_paged_and_filtered(client, fresh):
    for i in range(5):
        activity.log("plugin.enabled", f"p{i}")
    activity.log("account.login_failed", "x")
    manager.wait_idle()
    first = client.get(E, params={"limit": 3}).json()
    assert [e["summary"] for e in first["entries"]] == ["x", "p4", "p3"] and first["more"] is True
    rest = client.get(E, params={"limit": 3, "before": first["entries"][-1]["id"]}).json()
    assert [e["summary"] for e in rest["entries"]] == ["p2", "p1", "p0"] and rest["more"] is False
    assert [e["summary"] for e in entries(client, kind="account.")] == ["x"]
    assert len(entries(client, kind="plugin.enabled")) == 5
    assert len(entries(client, group="accounts,plugins")) == 6
    assert len(entries(client, user="system")) == 6
    assert entries(client, user="1") == [] and entries(client, user="nobody") == []


def test_secrets_are_never_kept(client, fresh):
    activity.log("account.password_changed", "Ivo",
                 details={"password": "hunter2", "new_password": "x", "token": "t", "api_key": "k",
                          "nested": {"secret": "s", "ok": 1}, "name": "Ivo"})
    [e] = entries(client)
    assert e["details"] == {"nested": {"ok": 1}, "name": "Ivo"}


def test_log_never_breaks_what_it_records(client, fresh, caplog):
    activity.log("not a kind")  # logged and swallowed
    activity.log("setting.changed", details={"odd": object()})  # kept as text
    assert "Could not log" in caplog.text
    [e] = entries(client)
    assert e["details"]["odd"].startswith("<object")


def test_switched_off_nothing_is_kept_and_nothing_fails(client, fresh):
    assert client.put("/api/plugins/activity-log", json={"enabled": False}).status_code == 200
    try:
        activity.log("series.deleted", "Gone")
        manager.wait_idle()
        assert client.get(E).status_code == 404  # its API is gone with it
    finally:
        assert client.put("/api/plugins/activity-log", json={"enabled": True}).status_code == 200
    assert entries(client) == []


def test_only_admins_can_read_it(client, fresh, accounts_on):
    other = accounts_on
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1", "admin": False})
    other.post(f"{A}/login", json={"name": "Kid", "password": "kid1"})
    assert other.get(E).status_code == 403
    assert client.get(E).status_code == 200


def test_a_missing_table_is_made_again(client, fresh):
    from app.db import SessionLocal
    from app.plugins.builtin.activity_log import store
    with SessionLocal() as db:
        store.table.drop(db.get_bind())
        assert store.page(db) == {"entries": [], "more": False}  # nothing logged, no error
    activity.log("backup.imported", "file.zip")
    assert [e["summary"] for e in entries(client)] == ["file.zip"]
