"""The activity log (issue #279, step 1 #280): activity.log() in the core, the on_activity event, and the
built-in Activity log plugin that keeps the entries and shows them to admins only."""
import shutil

import pytest

from app import activity, users
from app.plugins import manager
from tests.conftest import TMP, make_zip
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
    # nothing from while it was off; switching it on again is the first entry
    assert [(e["kind"], e["summary"]) for e in entries(client)] == [("plugin.enabled", "Activity log")]


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


# ---------------------------------------------------------------- step 2 (#281): what the app logs

def kinds(client, **params):
    return [e["kind"] for e in entries(client, **params)]


def one(client, kind):
    found = [e for e in entries(client, kind=kind)]
    assert len(found) == 1, found
    return found[0]


def test_accounts_are_logged_with_the_right_person(client, fresh, accounts_on):
    other = accounts_on
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    added = client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1pass", "admin": False}).json()
    # a failed login: the name tried, the address, nobody logged in; never the password
    assert other.post(f"{A}/login", json={"name": "Kid", "password": "wrongpass1"}).status_code == 403
    assert other.post(f"{A}/login", json={"name": "ghost", "password": "wrongpass2"}).status_code == 403
    assert other.post(f"{A}/login", json={"name": "Kid", "password": "kid1pass"}).status_code == 200
    other.post(f"{A}/logout")
    client.patch(f"{A}/accounts/{added['id']}", json={"name": "Kiddo", "admin": True, "password": "newpass1"})
    client.patch(f"{A}/accounts/{added['id']}", json={"admin": False})
    client.put("/api/plugins/libraries/api/access/2", json={"shelves": []})
    client.put("/api/plugins/libraries/api/access/2", json={"shelves": None})
    assert client.delete(f"{A}/accounts/{added['id']}").status_code == 200

    failed = entries(client, kind="account.login_failed")
    assert [(e["summary"], e["details"]["reason"]) for e in failed] == [("ghost", "no_such_account"),
                                                                       ("Kid", "wrong_password")]
    assert all(e["user_id"] == 0 and e["ip"] == "testclient" for e in failed)
    login = one(client, "account.login")
    assert (login["user_id"], login["user_name"]) == (added["id"], "Kid")  # the one logging in
    assert one(client, "account.logout")["user_name"] == "Kid"
    renamed = one(client, "account.renamed")
    assert renamed["details"] == {"old": "Kid", "new": "Kiddo"} and renamed["user_name"] == "Ivo"
    assert one(client, "account.password_reset")["summary"] == "Kiddo"
    assert len(entries(client, kind="account.access_changed")) == 2
    removed = one(client, "account.removed")
    assert (removed["summary"], removed["target"], removed["user_name"]) == ("Kiddo", ["account", str(added["id"])], "Ivo")
    assert {"account.added", "account.admin_given", "account.admin_taken"} <= set(kinds(client))


def test_folders_scans_and_deleting_a_series(client, fresh):
    from tests.test_delete import make_series
    folder = TMP / "extra-folder"
    make_zip(folder / "Extra One" / "Extra One ch01.zip")
    lib = client.post("/api/libraries", json={"root_path": str(folder)}).json()["id"]
    from app.scanner import walker
    while walker.status["running"]:
        pass
    added = one(client, "folder.added")
    assert added["summary"] == str(folder) and added["user_id"] == 1
    assert one(client, "scan.started")["user_id"] == 1  # someone added it: a scan of theirs
    finished = one(client, "scan.finished")
    assert finished["user_id"] is None and finished["details"]["new_series"] == 1  # the scan itself: System
    client.patch(f"/api/libraries/{lib}", json={"host_path": "/volume1/extra"})
    assert one(client, "folder.changed")["details"]["host_path"]["new"] == "/volume1/extra"
    client.delete(f"/api/libraries/{lib}")
    assert one(client, "folder.removed")["details"] == {"series": 1}

    _, sid = make_series(client, "Logged Deletion")
    assert client.delete(f"/api/series/{sid}").status_code == 200
    deleted = one(client, "series.deleted")
    assert deleted["summary"] == "Logged Deletion" and deleted["target"] == ["series", str(sid)]
    assert deleted["details"]["books"] == 3 and deleted["user_id"] == 1
    shutil.rmtree(folder, ignore_errors=True)


def test_metadata_by_hand(client, fresh):
    sid = next(s["id"] for s in client.get("/api/series").json()["items"] if s["title"] == "Series B")
    client.put(f"/api/series/{sid}/metadata", json={"description": "Logged", "unlock": []})
    changed = one(client, "metadata.changed")
    assert changed["details"] == {"fields": ["description"]} and changed["target"] == ["series", str(sid)]
    client.put(f"/api/series/{sid}/metadata", json={"unlock": ["description"]})
    client.patch(f"/api/series/{sid}", json={"reader_mode": "vertical"})
    client.patch(f"/api/series/{sid}", json={"reader_mode": "default"})
    client.patch(f"/api/series/{sid}", json={"favorite": True})  # each person's own: not logged
    client.patch(f"/api/series/{sid}", json={"favorite": False})
    assert len(entries(client, kind="metadata.changed")) == 4


def test_a_plugin_switched_off_and_on(client, fresh):
    assert client.put("/api/plugins/random-manga", json={"enabled": False}).status_code == 200
    assert client.put("/api/plugins/random-manga", json={"enabled": False}).status_code == 200  # no change
    assert client.put("/api/plugins/random-manga", json={"enabled": True}).status_code == 200
    off = one(client, "plugin.disabled")
    assert (off["summary"], off["target"], off["user_id"]) == ("Random manga", ["plugin", "random-manga"], 1)
    assert kinds(client) == ["plugin.enabled", "plugin.disabled"]


def test_settings_the_apps_not_each_persons(client, fresh):
    from app.db import SessionLocal, get_setting
    with SessionLocal() as db:
        before = get_setting(db, "match_threshold", 90)
    client.put("/api/settings", json={"match_threshold": 77, "theme": "light"})
    client.put("/api/settings", json={"match_threshold": 77})  # the same again: nothing new
    client.put("/api/settings", json={"match_threshold": before, "theme": "dark"})
    first, last = entries(client)[::-1]
    assert first["details"] == {"setting": "match_threshold", "old": before, "new": 77}
    assert last["details"] == {"setting": "match_threshold", "old": 77, "new": before}
    # the GitHub token: that it changed, never what it is
    client.put("/api/plugins/github-token", json={"token": "ghp_secretvalue123"})
    client.put("/api/plugins/github-token", json={"token": ""})
    assert [e["details"]["cleared"] for e in entries(client, kind="setting.changed")[:2]] == [True, False]


def test_safe_mode(client, fresh):
    client.get("/?safe=1")
    client.get("/?safe=1")  # still in it
    client.get("/")
    assert kinds(client) == ["safe_mode.off", "safe_mode.on"]


def test_no_entry_keeps_a_password_key_or_token(client, fresh, accounts_on):
    """Everything above that takes a password or token, and then not one of them anywhere in the log."""
    import json
    other = accounts_on
    secrets = ["Pw-admin-9137", "Pw-kid-5521", "Pw-wrong-4410", "Pw-new-7783", "ghp_Tok3n-8812", "Lib-pw-6650"]
    client.post(f"{A}/setup", json={"name": "Ivo", "password": secrets[0]})
    kid = client.post(f"{A}/accounts", json={"name": "Kid", "password": secrets[1]}).json()
    other.post(f"{A}/login", json={"name": "Kid", "password": secrets[2]})
    other.post(f"{A}/login", json={"name": "Kid", "password": secrets[1]})
    other.put(f"{A}/me/password", json={"current": secrets[1], "password": secrets[3]})
    client.patch(f"{A}/accounts/{kid['id']}", json={"password": secrets[3] + "x"})
    client.put("/api/plugins/github-token", json={"token": secrets[4]})
    client.put("/api/plugins/github-token", json={"token": ""})
    shelf = client.post("/api/plugins/libraries/api/shelves", json={"name": "Logged shelf"}).json()["id"]
    client.put(f"/api/plugins/libraries/api/shelves/{shelf}/password", json={"password": secrets[5]})
    client.post(f"/api/plugins/libraries/api/shelves/{shelf}/unlock", json={"password": "nope"})
    client.put(f"/api/plugins/libraries/api/shelves/{shelf}/password", json={"current": secrets[5], "password": ""})
    client.delete(f"/api/plugins/libraries/api/shelves/{shelf}")
    logged = entries(client, limit=200)
    assert {"account.password_changed", "account.password_reset", "library.password_set",
            "library.password_removed", "library.unlock_failed", "account.login_failed"} <= {e["kind"] for e in logged}
    text = json.dumps(logged)
    assert not [s for s in secrets if s in text]
    assert "password_hash" not in text and "scrypt" not in text
