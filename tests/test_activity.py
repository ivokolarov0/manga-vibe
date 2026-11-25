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


# ---------------------------------------------------------------- step 3 (#282): Settings → Activity

def test_search_dates_people_and_targets(client, fresh):
    from datetime import datetime, timedelta, timezone
    sid = next(s["id"] for s in client.get("/api/series").json()["items"] if s["title"] == "Series B")
    activity.log("series.moved", "Series B", target=("series", sid), details={"to": "Comics 100%"})
    activity.log("series.deleted", "Long Gone", target=("series", 999999))
    activity.log("plugin.enabled", "Random manga", target=("plugin", "random-manga"))
    with users.acting_as(users.User(7, "Mira")):  # a person's entry: logged as from a request
        token = activity._request.set({"type": "http", "client": ("10.1.2.3", 1)})
        try:
            activity.log("account.login_failed", "mira_x", details={"name": "mira_x"})
        finally:
            activity._request.reset(token)
    # words: all of them, anywhere (summary, name, kind, address, details); % and _ are only themselves
    assert [e["summary"] for e in entries(client, q="series b")] == ["Series B"]
    assert [e["summary"] for e in entries(client, q="100%")] == ["Series B"]
    assert [e["summary"] for e in entries(client, q="mira 10.1.2")] == ["mira_x"]
    assert entries(client, q="_x_") == [] and entries(client, q="Comics Gone") == []
    assert [e["summary"] for e in entries(client, q="deleted gone")] == ["Long Gone"]  # the kind counts too
    # dates: from (inclusive) and before
    now = datetime.now(timezone.utc)
    assert len(entries(client, since=(now - timedelta(minutes=5)).isoformat())) == 4
    assert entries(client, until=(now - timedelta(minutes=5)).isoformat()) == []
    # the targets still there get a link on the page
    there = {e["summary"]: e["target_exists"] for e in entries(client)}
    assert there == {"Series B": True, "Long Gone": False, "Random manga": True, "mira_x": False}
    # who, for the filter: System and Mira (the latest name each had)
    people = client.get("/api/plugins/activity-log/api/people").json()
    assert {(p["id"], p["name"]) for p in people} == {(None, ""), (7, "Mira")}


def test_the_page_is_the_plugins_and_only_while_it_is_on(client, fresh):
    page = client.get("/").text
    assert 'x-data="activityLog"' in page and "/plugins/activity-log.js" in page
    script = client.get("/plugins/activity-log.js").text
    assert 'MV.settingsPage(ID, "activity"' in script and 'store("app").admin' in script  # admins only
    assert client.put("/api/plugins/activity-log", json={"enabled": False}).status_code == 200
    try:
        page = client.get("/").text
        assert 'x-data="activityLog"' not in page and "/plugins/activity-log.js" not in page
        assert client.get("/plugins/activity-log.js").status_code == 404
    finally:
        client.put("/api/plugins/activity-log", json={"enabled": True})


def test_the_page_knows_every_kind_the_app_logs():
    """Each of the app's kinds has its sentence on the page (activity_log.js SAID)."""
    from pathlib import Path
    script = (Path(activity.__file__).parent / "plugins" / "builtin" / "activity_log" / "activity_log.js").read_text("utf-8")
    missing = [k for k in activity.KINDS if f'"{k}":' not in script]
    assert not missing, f"activity_log.js has no words for {missing}"
    assert set(activity.GROUPS) == set(__import__("re").findall(r'\["(\w+)", MV\.t\(', script.split("const GROUPS")[1].split(";")[0]))


def test_the_people_filter_knows_people_who_are_gone(client, fresh):
    with users.acting_as(users.User(9, "Old name")):
        token = activity._request.set({"type": "http"})
        try:
            activity.log("account.logout", "Old name")
        finally:
            activity._request.reset(token)
    with users.acting_as(users.User(9, "New name")):
        token = activity._request.set({"type": "http"})
        try:
            activity.log("account.logout", "New name")
        finally:
            activity._request.reset(token)
    manager.wait_idle()
    assert client.get("/api/plugins/activity-log/api/people").json() == [{"id": 9, "name": "New name"}]
    assert len(entries(client, user="9")) == 2


# ---------------------------------------------------------------- step 4 (#283): keeping, clearing, CSV, backup

O = "/api/plugins/activity-log/api/options"


def _aged(summaries_days: dict):
    """Entries `days` old, one per summary (written, then moved into the past)."""
    from datetime import datetime, timedelta, timezone
    from app.db import SessionLocal
    from app.plugins.builtin.activity_log import store
    for summary in summaries_days:
        activity.log("folder.changed", summary)
    manager.wait_idle()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as db:
        for summary, days in summaries_days.items():
            db.execute(store.table.update().where(store.table.c.summary == summary)
                       .values(at=now - timedelta(days=days)))
        db.commit()


@pytest.fixture
def keep_default(client):
    yield
    client.put(O, json={"keep_days": 90})
    manager.wait_idle()


def test_old_entries_are_removed_after_the_time_chosen(client, fresh, keep_default):
    from app.db import SessionLocal
    from app.plugins import Context
    from app.plugins.builtin import activity_log
    assert client.get(O).json() == {"keep_days": 90, "choices": [30, 90, 365, 0]}
    _aged({"new": 1, "month": 40, "year": 200, "ancient": 400})
    # the daily clean-up (the plugin's thread runs clean_up(); here it's called once)
    with SessionLocal() as db:
        ctx = Context(manager.get("activity-log"), db)
        assert activity_log.clean_up(ctx) == 2
        db.commit()
    assert {e["summary"] for e in entries(client)} == {"new", "month"}
    # a shorter time removes the older ones at once; forever keeps everything
    r = client.put(O, json={"keep_days": 30}).json()
    assert r == {"keep_days": 30, "removed": 1}
    assert {e["summary"] for e in entries(client, kind="folder.changed")} == {"new"}
    changed = one(client, "setting.changed")
    assert changed["details"] == {"plugin": "activity-log", "setting": "keep_days", "old": 90, "new": 30}
    _aged({"old again": 5000})
    assert client.put(O, json={"keep_days": 0}).json()["removed"] == 0
    assert "old again" in {e["summary"] for e in entries(client)}
    assert client.put(O, json={"keep_days": 7}).status_code == 400


def test_the_clean_up_runs_in_the_plugins_thread(client, fresh):
    import threading
    assert any(t.name == "plugin-activity-log" and t.is_alive() for t in threading.enumerate())


def test_clearing_leaves_one_entry_saying_who(client, fresh):
    for i in range(3):
        activity.log("folder.added", f"f{i}")
    manager.wait_idle()
    assert client.post("/api/plugins/activity-log/api/clear").json() == {"removed": 3}
    [e] = entries(client)
    assert (e["kind"], e["details"], e["user_id"]) == ("activity.cleared", {"entries": 3}, 1)


def test_export_as_csv_what_the_filters_show(client, fresh):
    import csv
    import io
    activity.log("plugin.enabled", "=HYPERLINK(\"http://evil\")", target=("plugin", "x"), details={"v": "Ä"})
    activity.log("account.login_failed", "+mira")
    activity.log("folder.added", "Not this one")
    manager.wait_idle()
    r = client.get("/api/plugins/activity-log/api/export.csv",
                   params={"group": "plugins,accounts", "tz": "No/Such_Zone", "offset": 120})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="mangavibe-activity-' in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # the byte order mark: spreadsheet programs read it as UTF-8
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:2] == ["Time (UTC)", "Time (local)"] and len(rows) == 3  # the header and the two filtered
    failed, enabled = rows[1], rows[2]
    assert failed[8] == "'+mira" and enabled[8].startswith("'=HYPERLINK")  # not run as formulas
    assert failed[3] == "System" and enabled[9:11] == ["plugin", "x"] and '"v": "Ä"' in enabled[11]
    from datetime import datetime
    utc, local = (datetime.strptime(enabled[i], "%Y-%m-%d %H:%M:%S") for i in (0, 1))
    assert (local - utc).total_seconds() == 7200  # the browser's offset, as the zone isn't known here


def test_keeping_clearing_and_exporting_are_for_admins(client, fresh, accounts_on):
    other = accounts_on
    client.post(f"{A}/setup", json={"name": "Ivo", "password": "secret1"})
    client.post(f"{A}/accounts", json={"name": "Kid", "password": "kid1", "admin": False})
    other.post(f"{A}/login", json={"name": "Kid", "password": "kid1"})
    assert other.get(O).status_code == 403
    assert other.put(O, json={"keep_days": 30}).status_code == 403
    assert other.post("/api/plugins/activity-log/api/clear").status_code == 403
    assert other.get("/api/plugins/activity-log/api/export.csv").status_code == 403


def test_the_backup_has_the_log_only_when_asked(client, fresh, tmp_path):
    import io
    import json
    import sqlite3
    import zipfile
    activity.log("folder.added", "kept or not")
    manager.wait_idle()

    def logged(activity_log):
        r = client.get(f"/api/backup/export?activity_log={'true' if activity_log else 'false'}")
        assert r.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(r.content))
        path = tmp_path / f"db-{activity_log}.db"
        path.write_bytes(z.read("mangavibe.db"))
        con = sqlite3.connect(path)
        try:
            n = con.execute("SELECT COUNT(*) FROM activity WHERE summary = 'kept or not'").fetchone()[0]
        finally:
            con.close()
        return n, json.loads(z.read("manifest.json"))["activity_log"]

    assert logged(False) == (0, False)
    assert logged(True) == (1, True)
