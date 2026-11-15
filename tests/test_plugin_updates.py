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
        self.releases = []  # what /releases lists

    def __call__(self, request: httpx.Request):
        self.requests.append(str(request.url))
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        if request.url.host == "api.github.com" and request.url.path == f"/repos/{REPO}/releases":
            return httpx.Response(200, json=self.releases)
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
    for f in (PLUGINS / ".backup").glob("hello-*.py"):  # copies left by other tests
        f.unlink()
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
    assert "Does someone/mv-hello have a published release" in u["error"] and not u["available"]
    assert "GitHub token" in u["error"]  # a private repo answers 404 too
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


# ---------------- folder plugins (plugins/<name>/__init__.py), from the release's source zip

PKG = "hellopkg"


def pkg_files(version, plugin_id="hellopkg"):
    """A folder plugin whose version lives in a module of its own, so an update shows whether that
    module was loaded fresh too."""
    return {
        "__init__.py": (f'from app.plugins import Plugin\nfrom . import core\n'
                        f'class HelloPkg(Plugin):\n    id = "{plugin_id}"\n    name = "Hello folder"\n'
                        f'    version = core.VERSION\n    repo = "{REPO}"\n'),
        "core.py": f'VERSION = "{version}"\n',
    }


def source_zip(files, root=f"{PKG}/", extra=None):
    """What GitHub's zipball answers: the repo at the tag under one top folder."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("someone-mv-hello-abc123/README.md", "readme")
        for name, text in files.items():
            z.writestr(f"someone-mv-hello-abc123/{root}{name}", text)
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return buf.getvalue()


class FakeGitHubZip(FakeGitHub):
    """Also answers zipball (redirected to codeload, like GitHub) and remembers the auth it got."""

    def __init__(self):
        super().__init__()
        self.zips, self.auth = {}, {}

    def __call__(self, request: httpx.Request):
        self.auth[request.url.host] = request.headers.get("authorization")
        if request.url.host == "api.github.com" and "/zipball/" in request.url.path:
            self.requests.append(str(request.url))
            tag = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(302, headers={"location": f"https://codeload.github.com/zip/{tag}?token=tmp"})
        if request.url.host == "codeload.github.com":
            self.requests.append(str(request.url))
            tag = request.url.path.rsplit("/", 1)[1]
            if tag not in self.zips:
                return httpx.Response(404)
            return httpx.Response(200, content=self.zips[tag])
        return super().__call__(request)


def write_pkg(files):
    import shutil
    shutil.rmtree(PLUGINS / PKG, ignore_errors=True)
    (PLUGINS / PKG).mkdir()
    for name, text in files.items():
        (PLUGINS / PKG / name).write_text(text)


@pytest.fixture()
def ghz(client):
    import shutil

    from app import plugin_updates
    from app.db import SessionLocal, set_setting
    write_pkg(pkg_files("1.0.0"))
    with SessionLocal() as db:
        set_setting(db, "plugin_updates", {})
        set_setting(db, plugin_updates.TOKEN_KEY, "")
        db.commit()
    client.post("/api/plugins/reload")
    fake = FakeGitHubZip()
    old, plugin_updates.transport = plugin_updates.transport, httpx.MockTransport(fake)
    yield fake
    plugin_updates.transport = old
    shutil.rmtree(PLUGINS / PKG, ignore_errors=True)
    for d in (PLUGINS / ".backup").glob(f"{PKG}-*"):
        shutil.rmtree(d, ignore_errors=True)
    with SessionLocal() as db:
        set_setting(db, plugin_updates.TOKEN_KEY, "")
        db.commit()
    client.post("/api/plugins/reload")


def pkg_info(client):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "hellopkg")


def test_folder_plugin_updates_from_the_source_zip(client, ghz):
    assert pkg_info(client)["update"]["supported"]
    client.post("/api/plugins/hellopkg/updates/check")
    ghz.zips["v1.1.0"] = source_zip(pkg_files("1.1.0"), extra={
        f"someone-mv-hello-abc123/{PKG}/.secret": "left out",
        f"someone-mv-hello-abc123/{PKG}/__pycache__/x.pyc": "left out",
        "someone-mv-hello-abc123/../escape.py": "left out"})
    r = client.post("/api/plugins/hellopkg/updates/install")
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Hello folder updated from 1.0.0 to 1.1.0"
    # core.py was loaded fresh: the version comes from it
    assert pkg_info(client)["version"] == "1.1.0" and not pkg_info(client)["update"]["available"]
    assert sorted(x.name for x in (PLUGINS / PKG).iterdir() if x.name != "__pycache__") == ["__init__.py", "core.py"]
    assert not (PLUGINS / "escape.py").exists()
    assert (PLUGINS / ".backup" / f"{PKG}-1.0.0" / "core.py").read_text() == pkg_files("1.0.0")["core.py"]
    assert not list((PLUGINS / ".staging").glob(f"{PKG}*"))
    assert ghz.auth["api.github.com"] is None  # no token saved: nothing sent


def test_folder_plugin_that_is_the_whole_repo(client, ghz):
    client.post("/api/plugins/hellopkg/updates/check")
    ghz.zips["v1.1.0"] = source_zip(pkg_files("1.1.0"), root="")
    assert client.post("/api/plugins/hellopkg/updates/install").status_code == 200
    assert pkg_info(client)["version"] == "1.1.0"
    assert (PLUGINS / PKG / "README.md").exists()


def test_bad_folder_downloads_change_nothing(client, ghz):
    client.post("/api/plugins/hellopkg/updates/check")
    for zip_, why in ((source_zip(pkg_files("1.1.0"), root="other/"), "no hellopkg/ folder"),
                      (source_zip(pkg_files("1.1.0", plugin_id="else")), "has no plugin 'hellopkg'"),
                      (source_zip({"__init__.py": "raise RuntimeError('boom')"}), "doesn't load"),
                      (b"not a zip", "isn't a zip"),
                      (None, "Could not download")):
        ghz.zips.pop("v1.1.0", None)
        if zip_ is not None:
            ghz.zips["v1.1.0"] = zip_
        r = client.post("/api/plugins/hellopkg/updates/install")
        assert r.status_code == 400 and why in r.json()["detail"], r.text
        assert pkg_info(client)["version"] == "1.0.0"
    assert not list((PLUGINS / ".staging").glob(f"{PKG}*"))


def test_private_repo_token(client, ghz, monkeypatch):
    assert client.get("/api/plugins").json()["github_token"] == ""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    assert client.get("/api/plugins").json()["github_token"] == "env"
    r = client.put("/api/plugins/github-token", json={"token": " secret-token "})
    assert r.json() == {"github_token": "settings"}  # where it comes from, never the token
    assert "secret-token" not in client.get("/api/plugins").text

    client.post("/api/plugins/hellopkg/updates/check")
    assert ghz.auth["api.github.com"] == "Bearer secret-token"  # the saved one wins over the variable
    ghz.zips["v1.1.0"] = source_zip(pkg_files("1.1.0"))
    assert client.post("/api/plugins/hellopkg/updates/install").status_code == 200
    assert ghz.auth["codeload.github.com"] is None  # not passed on to the redirect

    client.put("/api/plugins/github-token", json={"token": ""})
    assert client.get("/api/plugins").json()["github_token"] == "env"
    gh_status = ghz.status
    ghz.status = 401
    u = client.post("/api/plugins/hellopkg/updates/check").json()["update"]
    assert "refused the token" in u["error"]
    ghz.status = gh_status


def test_reload_loads_a_folder_plugins_modules_fresh(client, ghz):
    """Reload plugins after changing a folder plugin's inner module uses the new code."""
    (PLUGINS / PKG / "core.py").write_text('VERSION = "9.9.10"\n')  # another size: no stale .pyc
    client.post("/api/plugins/reload")
    assert pkg_info(client)["version"] == "9.9.10"


def test_folder_plugin_restores_its_previous_version(client, ghz):
    client.post("/api/plugins/hellopkg/updates/check")
    ghz.zips["v1.1.0"] = source_zip(pkg_files("1.1.0"))
    client.post("/api/plugins/hellopkg/updates/install")
    assert pkg_info(client)["update"]["backups"] == ["1.0.0"]
    r = client.post("/api/plugins/hellopkg/updates/restore", json={"version": "1.0.0"})
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Hello folder went back from 1.1.0 to 1.0.0"
    assert pkg_info(client)["version"] == "1.0.0"  # core.py was loaded fresh again
    assert (PLUGINS / PKG / "core.py").read_text() == pkg_files("1.0.0")["core.py"]
    assert (PLUGINS / ".backup" / f"{PKG}-1.1.0" / "core.py").read_text() == pkg_files("1.1.0")["core.py"]


# ---------------- going back to an earlier version

def test_restore_the_previous_version(client, gh):
    client.post("/api/plugins/hello/updates/check")
    assert info(client)["update"]["backups"] == []
    gh.files["v1.1.0"] = plugin_file("1.1.0")
    client.post("/api/plugins/hello/updates/install")
    assert info(client)["update"]["backups"] == ["1.0.0"]

    before = len(gh.requests)
    r = client.post("/api/plugins/hello/updates/restore", json={"version": "1.0.0"})
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Hello went back from 1.1.0 to 1.0.0"
    assert gh.requests[before:] == []  # from plugins/.backup, no download
    assert (PLUGINS / "hello.py").read_text() == plugin_file("1.0.0")
    u = info(client)["update"]
    assert info(client)["version"] == "1.0.0" and u["backups"] == ["1.1.0"]  # forward again, the same way
    # the version left isn't offered again, even after a check
    assert u["available"] and u["skipped"]
    assert client.post("/api/plugins/hello/updates/check").json()["update"]["skipped"]
    # ...but a newer one is
    gh.tag = "v1.2.0"
    u = client.post("/api/plugins/hello/updates/check").json()["update"]
    assert u["available"] and not u["skipped"]

    assert client.post("/api/plugins/hello/updates/restore", json={"version": "0.9.0"}).status_code == 400
    assert client.post("/api/plugins/hello/updates/restore", json={}).status_code == 400


def test_restore_checks_like_an_update(client, gh):
    from app.plugins import manager
    backup = PLUGINS / ".backup"
    backup.mkdir(exist_ok=True)
    # an old copy asks for a permission the installed version doesn't
    (backup / "hello-0.9.0.py").write_text(plugin_file("0.9.0", permissions=["Reads your files"]))
    (backup / "hello-0.8.0.py").write_text(plugin_file("0.8.0", plugin_id="other"))
    (backup / "hello-0.7.0.py").write_text(plugin_file("0.7.0", broken=True))
    (backup / "hello-world-2.0.py").write_text(plugin_file("2.0", plugin_id="world"))  # another plugin's copy
    assert info(client)["update"]["backups"] == ["0.9.0", "0.8.0", "0.7.0"]

    r = client.post("/api/plugins/hello/updates/restore", json={"version": "0.9.0"})
    assert r.status_code == 409 and r.json()["detail"]["permissions"] == ["Reads your files"]
    assert "no plugin 'hello'" in client.post("/api/plugins/hello/updates/restore", json={"version": "0.8.0"}).text
    assert "doesn't load" in client.post("/api/plugins/hello/updates/restore", json={"version": "0.7.0"}).text
    assert manager.plugins["hello"].version == "1.0.0" and (PLUGINS / "hello.py").read_text() == plugin_file("1.0.0")

    r = client.post("/api/plugins/hello/updates/restore",
                    json={"version": "0.9.0", "accept_permissions": ["Reads your files"]})
    assert r.status_code == 200 and info(client)["version"] == "0.9.0"


def test_install_an_earlier_release(client, gh):
    gh.releases = [{"tag_name": "v1.2.0-beta", "prerelease": True}, {"tag_name": "v1.1.0", "published_at": "2026-10-02"},
                   {"tag_name": "v1.0.0"}, {"tag_name": "v0.9.0"}, {"tag_name": "draft", "draft": True}]
    assert [x["version"] for x in client.get("/api/plugins/hello/updates/releases").json()["releases"]] == [
        "v1.1.0", "v1.0.0", "v0.9.0"]
    client.post("/api/plugins/hello/updates/check")
    gh.files["v0.9.0"] = plugin_file("0.9.0")
    r = client.post("/api/plugins/hello/updates/install", json={"version": "v0.9.0"})
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Hello went back from 1.0.0 to 0.9.0"
    # 1.0.0 wasn't the newest release, so the newest is still offered
    u = info(client)["update"]
    assert u["available"] and not u["skipped"] and u["backups"] == ["1.0.0"]
    assert client.post("/api/plugins/hello/updates/install", json={"version": "0.9"}).status_code == 400  # installed
    assert client.post("/api/plugins/hello/updates/install", json={"version": "../x"}).status_code == 400
    gh.status = 500
    assert client.get("/api/plugins/hello/updates/releases").status_code == 400


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
