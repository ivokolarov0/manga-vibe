"""Plugin API v2: reading events, settings fields, global actions, web callback."""
import shutil
from pathlib import Path

import pytest

from tests.conftest import TMP

PLUGIN = Path(__file__).parent.parent / "examples" / "plugins" / "reading_log.py"


@pytest.fixture()
def log_plugin(client):
    from app.plugins import manager
    dest = TMP / "data" / "plugins" / "reading_log.py"
    shutil.copy(PLUGIN, dest)
    client.post("/api/plugins/reload")
    yield manager.get("reading-log")
    manager.wait_idle()
    dest.unlink()
    client.post("/api/plugins/reload")


def series_id(client, title):
    return next(s["id"] for s in client.get("/api/series").json()["items"] if s["title"] == title)


def plugin_info(client):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "reading-log")


def test_reading_events(client, log_plugin):
    from app.plugins import manager
    sid = series_id(client, "Series B")
    client.post(f"/api/series/{sid}/mark-unread")
    manager.wait_idle()
    client.post("/api/plugins/reading-log/actions/clear")
    book =client.get(f"/api/series/{sid}/books").json()[0]

    client.put(f"/api/books/{book['id']}/progress", json={"page": 1})
    client.put(f"/api/books/{book['id']}/progress", json={"page": 1})  # same page again: no new event
    client.put(f"/api/books/{book['id']}/progress", json={"page": book["page_count"] - 1})
    manager.wait_idle()
    log = client.get("/api/plugins/reading-log/web/log").text
    assert "started Chapter 5" in log and "finished Chapter 5" in log
    assert "unread -&gt; reading" in log and "reading -&gt; completed" in log
    assert log.count("started") == 1
    assert plugin_info(client)["status"] == "4 entries"

    # the series page shows the plugin field, and its own button
    s = client.get(f"/api/series/{sid}").json()
    assert "Series B: reading -> completed" in s["plugin_fields"][0]["value"]
    msg = client.post(f"/api/series/{sid}/actions/reading-log/show").json()["message"]
    assert msg.endswith("reading -> completed")

    # picking a status by hand, and mark all unread
    client.patch(f"/api/series/{sid}", json={"reading_status": "on_hold"})
    client.post(f"/api/series/{sid}/mark-unread")
    client.patch(f"/api/series/{sid}", json={"clear_reading_status": True})
    manager.wait_idle()
    log = client.get("/api/plugins/reading-log/web/log").text
    assert "completed -&gt; on_hold" in log and "on_hold -&gt; unread" in log


def test_plugin_changes_do_not_fire_events(client, log_plugin):
    from app.db import SessionLocal
    from app.models import Series
    from app.plugins import Context, manager
    sid = series_id(client, "Series B")
    client.post("/api/plugins/reading-log/actions/clear")
    with SessionLocal() as db:
        ctx = Context(log_plugin, db)
        s = db.get(Series, sid)
        ctx.set_reading_status(s, "completed")
        assert ctx.reading_status(s) == "completed"
        with pytest.raises(ValueError):
            ctx.set_reading_status(s, "nonsense")
        ctx.set_reading_status(s, None)
        db.commit()
    manager.wait_idle()
    assert plugin_info(client)["status"] == "0 entries"


def test_settings_and_global_actions(client, log_plugin):
    info = plugin_info(client)
    assert info["settings"][0]["key"] == "prefix" and info["settings"][0]["value"] == ""
    assert [a["key"] for a in info["global_actions"]] == ["clear", "page"]
    r = client.put("/api/plugins/reading-log/settings", json={"prefix": " >> ", "log": ["hack"]})
    assert r.json()["settings"][0]["value"] == ">>"  # trimmed; undeclared keys are ignored
    # buttons can send the browser somewhere (e.g. a login page)
    r = client.post("/api/plugins/reading-log/actions/page").json()
    assert r["open_url"] == "http://testserver/api/plugins/reading-log/web/log"
    assert client.post("/api/plugins/reading-log/actions/nope").status_code == 404
    client.put("/api/plugins/reading-log/settings", json={"prefix": ""})
    assert plugin_info(client)["settings"][0]["saved"] is False
    assert client.get("/api/plugins/reading-log/web/other").status_code == 404


def test_secret_settings_never_sent_back(client):
    from app.plugins import Plugin, SettingDef, manager

    class Secret(Plugin):
        id = "secret-test"
        settings_fields = [SettingDef("token", "Token", secret=True)]

    manager.plugins["secret-test"] = Secret()
    try:
        client.put("/api/plugins/secret-test/settings", json={"token": "abc"})
        info = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "secret-test")
        assert info["settings"][0] == {"key": "token", "label": "Token", "help": "", "secret": True,
                                       "value": "", "saved": True}
    finally:
        del manager.plugins["secret-test"]


def test_broken_event_hook_is_contained(client):
    from app.plugins import Plugin, manager

    class Broken(Plugin):
        id = "broken-events"

        def on_series_status_changed(self, ctx, series, old, new):
            raise RuntimeError("boom")

    manager.plugins["broken-events"] = Broken()
    try:
        sid = series_id(client, "Series B")
        assert client.patch(f"/api/series/{sid}", json={"reading_status": "dropped"}).status_code == 200
        manager.wait_idle()  # the error is logged; the worker keeps going
        client.patch(f"/api/series/{sid}", json={"clear_reading_status": True})
        manager.wait_idle()
    finally:
        del manager.plugins["broken-events"]
