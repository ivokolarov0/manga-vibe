"""Plugin updates from the plugin's GitHub releases (app/plugin_updates.py)."""
import httpx
import pytest

from tests.conftest import TMP

PLUGINS = TMP / "data" / "plugins"
REPO = "someone/mv-hello"


def plugin_file(version, plugin_id="hello", broken=False, permissions=None):
    return (f'from app.plugins import Plugin\n'
            f'{"raise RuntimeError(\"boom\")" if broken else ""}\n'
            f'class Hello(Plugin):\n'
            f'    id = "{plugin_id}"\n'
            f'    name = "Hello"\n'
            f'    version = "{version}"\n'
            f'    repo = "{REPO}"\n'
            + (f'    permissions = {permissions!r}\n' if permissions is not None else ''))


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
    assert u["supported"] and u["repo"] == REPO and not u["skipped"]
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


def test_new_permissions_need_approval(client, gh):
    (PLUGINS / "hello.py").write_text(plugin_file("1.0.0", permissions=["Connects to a.example"]))
    client.post("/api/plugins/reload")
    assert info(client)["permissions"] == ["Connects to a.example"]
    client.post("/api/plugins/hello/updates/check")
    gh.files["v1.1.0"] = plugin_file("1.1.0", permissions=["Connects to a.example",
                                                            "Connects to b.example", "Reads book files"])
    # not approved: nothing changes, and the answer names only what's new
    r = client.post("/api/plugins/hello/updates/install")
    assert r.status_code == 409
    assert r.json()["detail"]["permissions"] == ["Connects to b.example", "Reads book files"]
    assert info(client)["version"] == "1.0.0"
    assert not list((PLUGINS / ".staging").glob("*.py"))
    # approving only part of it isn't enough
    r = client.post("/api/plugins/hello/updates/install",
                    json={"accept_permissions": ["Connects to b.example"]})
    assert r.status_code == 409 and info(client)["version"] == "1.0.0"
    # approved
    r = client.post("/api/plugins/hello/updates/install",
                    json={"accept_permissions": ["Connects to b.example", "Reads book files"]})
    assert r.status_code == 200 and info(client)["version"] == "1.1.0"
    assert info(client)["permissions"][-1] == "Reads book files"


def test_same_or_fewer_permissions_install_directly(client, gh):
    (PLUGINS / "hello.py").write_text(plugin_file("1.0.0", permissions=["A", "B"]))
    client.post("/api/plugins/reload")
    client.post("/api/plugins/hello/updates/check")
    gh.files["v1.1.0"] = plugin_file("1.1.0", permissions=["B"])
    assert client.post("/api/plugins/hello/updates/install").status_code == 200
    assert info(client)["permissions"] == ["B"]


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


def test_daily_check_only_looks(client, gh):
    from app import plugin_updates
    from app.db import SessionLocal
    gh.files["v1.1.0"] = plugin_file("1.1.0")
    with SessionLocal() as db:
        plugin_updates.run_daily(db)
    assert info(client)["version"] == "1.0.0" and info(client)["update"]["available"]
    assert not [u for u in gh.requests if "raw.githubusercontent.com" in u]  # nothing downloaded
    with SessionLocal() as db:  # checked less than a day ago: GitHub isn't asked again
        before = len(gh.requests)
        plugin_updates.run_daily(db)
    assert gh.requests[before:] == []


def test_skip_a_version(client, gh):
    client.post("/api/plugins/hello/updates/check")
    u = client.post("/api/plugins/hello/updates/skip").json()["update"]
    assert u["available"] and u["skipped"] and u["latest"] == "v1.1.0"
    # checking again doesn't bring the skipped version back
    assert client.post("/api/plugins/hello/updates/check").json()["update"]["skipped"]
    # "Show it again"
    assert not client.delete("/api/plugins/hello/updates/skip").json()["update"]["skipped"]
    client.post("/api/plugins/hello/updates/skip")
    # a newer release is offered again
    gh.tag = "v1.2.0"
    u = client.post("/api/plugins/hello/updates/check").json()["update"]
    assert u["latest"] == "v1.2.0" and u["available"] and not u["skipped"]
    # a skipped version can still be installed: nothing stops the owner from changing their mind
    gh.files["v1.2.0"] = plugin_file("1.2.0")
    client.post("/api/plugins/hello/updates/skip")
    assert client.post("/api/plugins/hello/updates/install").status_code == 200
    assert info(client)["version"] == "1.2.0" and not info(client)["update"]["skipped"]


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


def test_built_in_plugins_list_their_permissions(client):
    """Built-in plugins aren't updated from GitHub, but they still say what they use."""
    from app.plugin_updates import permissions_of
    from app.plugins import manager
    builtin = [p for p in manager.plugins.values()
               if manager.paths.get(p.id) and "builtin" in manager.paths[p.id].parts]
    assert {p.id for p in builtin} >= {"anilist", "mangaupdates"}
    for p in builtin:
        assert permissions_of(p), f"built-in plugin {p.id} has no permissions"
    shown = {p["id"]: p["permissions"] for p in client.get("/api/plugins").json()["plugins"]}
    assert "Connects to graphql.anilist.co" in shown["anilist"]
