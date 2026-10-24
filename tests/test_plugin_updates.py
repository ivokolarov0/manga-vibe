"""Plugin updates from the plugin's GitHub releases (app/plugin_updates.py)."""
import httpx
import pytest

from tests.conftest import TMP

PLUGINS = TMP / "data" / "plugins"
REPO = "someone/mv-hello"


def plugin_file(version, plugin_id="hello", broken=False):
    return (f'from app.plugins import Plugin\n'
            f'{"raise RuntimeError(\"boom\")" if broken else ""}\n'
            f'class Hello(Plugin):\n'
            f'    id = "{plugin_id}"\n'
            f'    name = "Hello"\n'
            f'    version = "{version}"\n'
            f'    repo = "{REPO}"\n')


class FakeGitHub:
    """The newest release's tag, and the plugin file at each tag."""

    def __init__(self):
        self.tag, self.files, self.requests, self.status = "v1.1.0", {}, [], 200

    def __call__(self, request: httpx.Request):
        self.requests.append(str(request.url))
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        if request.url.host == "api.github.com":
            assert request.url.path == f"/repos/{REPO}/releases/latest"
            return httpx.Response(200, json={"tag_name": self.tag,
                                             "html_url": f"https://github.com/{REPO}/releases/tag/{self.tag}"})
        assert request.url.host == "raw.githubusercontent.com"
        _, owner, name, tag, filename = request.url.path.split("/")
        assert f"{owner}/{name}" == REPO and filename == "hello.py"
        if tag not in self.files:
            return httpx.Response(404, text="404: Not Found")
        return httpx.Response(200, text=self.files[tag])


def info(client):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "hello")


@pytest.fixture()
def gh(client):
    from app import plugin_updates
    from app.db import SessionLocal, set_setting
    (PLUGINS / "hello.py").write_text(plugin_file("1.0.0"))
    with SessionLocal() as db:
        set_setting(db, "plugin_updates", {})
        db.commit()
    client.post("/api/plugins/reload")
    fake = FakeGitHub()
    old, plugin_updates.transport = plugin_updates.transport, httpx.MockTransport(fake)
    yield fake
    plugin_updates.transport = old
    (PLUGINS / "hello.py").unlink(missing_ok=True)
    client.post("/api/plugins/reload")


def test_manual_check_and_update(client, gh):
    u = info(client)["update"]
    assert u["supported"] and u["mode"] == "manual" and u["repo"] == REPO
    assert u["latest"] is None and not u["available"]

    u = client.post("/api/plugins/hello/updates/check").json()["update"]
    assert u["latest"] == "v1.1.0" and u["available"] and u["checked_at"] and u["error"] is None

    gh.files["v1.1.0"] = plugin_file("1.1.0")
    r = client.post("/api/plugins/hello/updates/install").json()
    assert r["message"] == "Hello updated from 1.0.0 to 1.1.0"
    assert info(client)["version"] == "1.1.0" and not info(client)["update"]["available"]
    assert "1.1.0" in (PLUGINS / "hello.py").read_text()
    assert (PLUGINS / ".backup" / "hello-1.0.0.py").read_text() == plugin_file("1.0.0")
    # nothing newer: Install refuses
    assert client.post("/api/plugins/hello/updates/install").status_code == 400


def test_bad_downloads_change_nothing(client, gh):
    client.post("/api/plugins/hello/updates/check")
    for content, why in ((plugin_file("1.1.0", broken=True), "doesn't load"),
                         (plugin_file("1.1.0", plugin_id="other"), "has no plugin 'hello'"),
                         (None, "Could not download")):
        gh.files.pop("v1.1.0", None)
        if content:
            gh.files["v1.1.0"] = content
        r = client.post("/api/plugins/hello/updates/install")
        assert r.status_code == 400 and why in r.json()["detail"]
        assert info(client)["version"] == "1.0.0"
        assert (PLUGINS / "hello.py").read_text() == plugin_file("1.0.0")
    assert not list((PLUGINS / ".staging").glob("*.py"))


def test_github_errors_are_shown(client, gh):
    gh.status = 404
    u = client.post("/api/plugins/hello/updates/check").json()["update"]
    assert "Is someone/mv-hello public" in u["error"] and not u["available"]
    gh.status = 403
    assert "request limit" in client.post("/api/plugins/hello/updates/check").json()["update"]["error"]


def test_automatic_mode_installs_in_the_daily_check(client, gh):
    from app import plugin_updates
    from app.db import SessionLocal
    gh.files["v1.1.0"] = plugin_file("1.1.0")
    assert client.put("/api/plugins/hello/updates", json={"mode": "sometimes"}).status_code == 400

    with SessionLocal() as db:  # Manual: the daily check only looks
        plugin_updates.run_daily(db)
    assert info(client)["version"] == "1.0.0" and info(client)["update"]["available"]

    assert client.put("/api/plugins/hello/updates", json={"mode": "auto"}).json()["update"]["mode"] == "auto"
    with SessionLocal() as db:  # checked less than a day ago: no new request, but it installs
        before = len(gh.requests)
        plugin_updates.run_daily(db)
    assert info(client)["version"] == "1.1.0"
    assert [u for u in gh.requests[before:] if "api.github.com" in u] == []

    # a release that doesn't load is tried once, not every hour
    gh.tag, gh.files["v1.2.0"] = "v1.2.0", plugin_file("1.2.0", broken=True)
    with SessionLocal() as db:
        plugin_updates.check(db, plugin_updates.manager.plugins["hello"])
        plugin_updates.run_daily(db)
        downloads = [u for u in gh.requests if u.endswith("/v1.2.0/hello.py")]
        plugin_updates.run_daily(db)
    assert len(downloads) == 1
    assert [u for u in gh.requests if u.endswith("/v1.2.0/hello.py")] == downloads
    assert "doesn't load" in info(client)["update"]["error"]
    assert info(client)["version"] == "1.1.0"


def test_only_plugins_with_a_repo_in_the_plugins_folder(client, gh):
    from app import plugin_updates
    from app.plugins import Plugin, manager
    builtin = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "anilist")
    assert builtin["update"]["supported"] is False
    assert client.post("/api/plugins/anilist/updates/check").status_code == 400
    assert client.post("/api/plugins/missing/updates/check").status_code == 404

    class NoRepo(Plugin):
        id = "no-repo"
    manager.plugins["no-repo"] = NoRepo()
    manager.paths["no-repo"] = PLUGINS / "no_repo.py"
    try:
        assert plugin_updates.path_of(manager.plugins["no-repo"]) is None
    finally:
        del manager.plugins["no-repo"], manager.paths["no-repo"]


def test_version_compare():
    from app.plugin_updates import version_tuple
    assert version_tuple("v1.2.0") == version_tuple("1.2") == (1, 2)
    assert version_tuple("v1.10.0") > version_tuple("1.9.9")
    assert version_tuple("1.0.1") > version_tuple("1.0")
