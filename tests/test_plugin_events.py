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


def test_choices_on_the_series_page(client):
    from app.plugins import Choice, FieldDef, Plugin, manager

    class Tracker(Plugin):
        id = "choice-test"
        fields = [FieldDef("site", "Site")]
        choices = [Choice("site", "Site", options=[("reading", "Reading"), ("", "Not on my list")])]
        picked = []

        def set_choice(self, ctx, key, series, value):
            self.picked.append((key, series.id, value))
            ctx.set_field(series, key, value)
            ctx.set_reading_status(series, value or None)
            return f"Set to {value or 'nothing'}"

    class Hidden(Plugin):
        id = "choice-hidden"
        choices = [Choice("x", "X", options=[("a", "A")])]

        def get_choice(self, ctx, key, series):
            return False

    class Broken(Plugin):
        id = "choice-broken"
        choices = [Choice("x", "X", options=[("a", "A")])]

        def get_choice(self, ctx, key, series):
            raise RuntimeError("boom")

    for p in (Tracker(), Hidden(), Broken()):
        manager.plugins[p.id] = p
    sid = series_id(client, "Series B")
    try:
        s = client.get(f"/api/series/{sid}").json()  # hidden and broken ones are left out
        assert s["choices"] == [{"plugin": "choice-test", "key": "site", "label": "Site",
                                 "options": [["reading", "Reading"], ["", "Not on my list"]], "value": None}]

        url = f"/api/series/{sid}/choices/choice-test/site"
        assert client.put(url, json={"value": "reading"}).json()["message"] == "Set to reading"
        s = client.get(f"/api/series/{sid}").json()
        assert s["choices"][0]["value"] == "reading" and s["manual_reading_status"] == "reading"
        assert not [f for f in s["plugin_fields"] if f["plugin"] == "choice-test"]  # not shown twice

        assert client.put(url, json={"value": ""}).json()["message"] == "Set to nothing"
        assert client.get(f"/api/series/{sid}").json()["choices"][0]["value"] == ""
        assert client.put(url, json={"value": "dropped"}).status_code == 400
        assert client.put(f"/api/series/{sid}/choices/choice-test/nope", json={"value": ""}).status_code == 404
        assert client.put(f"/api/series/{sid}/choices/missing/site", json={"value": ""}).status_code == 404
        assert Tracker.picked == [("site", sid, "reading"), ("site", sid, "")]
    finally:
        for pid in ("choice-test", "choice-hidden", "choice-broken"):
            del manager.plugins[pid]
        client.patch(f"/api/series/{sid}", json={"clear_reading_status": True})


def test_provider_error_has_the_status_code():
    import httpx
    from app.metadata import http
    old = http.transport
    http.transport = httpx.MockTransport(lambda req: httpx.Response(404, json={"error": "not_found"}))
    try:
        with pytest.raises(http.ProviderError) as e:
            http.request("DELETE", "https://api.example/x", retries=0)
        assert e.value.status == 404
    finally:
        http.transport = old


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
