"""Plugin sources: lists of plugins installed with one button (app/plugin_sources.py)."""
import io
import json
import shutil
import zipfile

import httpx
import pytest

from tests.conftest import TMP

PLUGINS = TMP / "data" / "plugins"
OFFICIAL = "https://raw.githubusercontent.com/ivokolarov0/manga-vibe/HEAD/plugins.json"
OTHER = "https://example.com/mv/list.json"


def plugin_code(pid, version="1.2.0", permissions=None):
    return (f'from app.plugins import Plugin\n'
            f'class P(Plugin):\n'
            f'    id = "{pid}"\n'
            f'    name = "{pid.title()}"\n'
            f'    version = "{version}"\n'
            f'    repo = "someone/{pid}"\n'
            + (f'    permissions = {permissions!r}\n' if permissions is not None else ''))


def zipball(top, files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(f"{top}/{name}", text)
    return buf.getvalue()


class Fake:
    def __init__(self):
        self.indexes = {OFFICIAL: {"name": "MangaVibe", "plugins": [
            {"id": "srchello", "name": "Source hello", "description": "Says hello", "category": "reader",
             "repo": "someone/srchello", "file": "srchello.py"},
            {"id": "srcfolder", "name": "Folder one", "repo": "someone/srcfolder", "folder": "srcfolder"},
            {"id": "bad", "repo": "not a repo", "file": "x.py"},  # left out
        ]}}
        self.files = {"someone/srchello": plugin_code("srchello")}
        self.zips = {"someone/srcfolder": zipball("someone-srcfolder-abc", {
            "srcfolder/__init__.py": "from app.plugins import Plugin\nfrom .core import VERSION\n"
                                     "class P(Plugin):\n"
                                     "    id = 'srcfolder'\n    name = 'Folder one'\n    version = VERSION\n",
            "srcfolder/core.py": "VERSION = '0.3.0'\n",
            ".github/x.yml": "junk"})}
        self.requests = []

    def __call__(self, req: httpx.Request):
        url = str(req.url)
        self.requests.append((url, req.headers.get("authorization")))
        if url in self.indexes:
            v = self.indexes[url]
            return httpx.Response(200, content=v if isinstance(v, bytes) else json.dumps(v).encode())
        path = req.url.path
        if req.url.host == "api.github.com" and path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": "v1.2.0"})
        if req.url.host == "api.github.com" and "/zipball/" in path:
            repo = "/".join(path.split("/")[2:4])
            return httpx.Response(200, content=self.zips[repo])
        if req.url.host == "raw.githubusercontent.com":
            _, owner, name, tag, file = path.split("/")
            text = self.files.get(f"{owner}/{name}")
            if text is not None and file == f"{name}.py":
                return httpx.Response(200, text=text)
        return httpx.Response(404, text="nope")


@pytest.fixture()
def src(client):
    from app import plugin_sources, plugin_updates
    from app.db import SessionLocal, set_setting
    fake = Fake()
    old = plugin_updates.transport, plugin_sources.transport
    plugin_updates.transport = plugin_sources.transport = httpx.MockTransport(fake)
    plugin_sources._cache.clear()
    with SessionLocal() as db:
        set_setting(db, "plugin_sources", [])
        set_setting(db, "plugin_github_token", "")
        db.commit()
    yield fake
    plugin_updates.transport, plugin_sources.transport = old
    plugin_sources._cache.clear()
    (PLUGINS / "srchello.py").unlink(missing_ok=True)
    shutil.rmtree(PLUGINS / "srcfolder", ignore_errors=True)
    (PLUGINS / "other.py").unlink(missing_ok=True)
    client.post("/api/plugins/reload")


def entry(r, pid):
    return next(p for p in r["plugins"] if p["id"] == pid)


def test_official_source_is_listed(client, src):
    r = client.get("/api/plugins/available").json()
    assert r["sources"] == [{"url": OFFICIAL, "name": "MangaVibe", "official": True, "error": None, "count": 2}]
    assert [p["id"] for p in r["plugins"]] == ["srchello", "srcfolder"]
    e = entry(r, "srchello")
    assert e["category"] == "reader" and not e["installed"] and e["homepage"] == "https://github.com/someone/srchello"
    assert entry(r, "srcfolder")["category"] == "other"


def test_install_a_file_plugin(client, src):
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srchello"}).json()
    assert r["message"] == "Source hello 1.2.0 installed"
    assert (PLUGINS / "srchello.py").read_text() == plugin_code("srchello")
    assert any(p["id"] == "srchello" and p["enabled"] for p in r["plugins"])
    e = entry(r["available"], "srchello")
    assert e["installed"] and e["installed_version"] == "1.2.0"
    # installed already
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srchello"})
    assert r.status_code == 400 and "installed already" in r.json()["detail"]


def test_install_a_folder_plugin(client, src):
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srcfolder"})
    assert r.status_code == 200, r.text
    assert (PLUGINS / "srcfolder" / "core.py").exists() and not (PLUGINS / "srcfolder" / ".github").exists()
    assert not any((PLUGINS / ".staging").iterdir())


def test_permissions_are_asked_for(client, src):
    src.files["someone/srchello"] = plugin_code("srchello", permissions=["Reads book files"])
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srchello"})
    assert r.status_code == 409 and r.json()["detail"]["permissions"] == ["Reads book files"]
    assert not (PLUGINS / "srchello.py").exists()
    r = client.post("/api/plugins/available/install",
                    json={"source": OFFICIAL, "id": "srchello", "accept_permissions": ["Reads book files"]})
    assert r.status_code == 200 and (PLUGINS / "srchello.py").exists()


def test_a_download_without_the_plugin_changes_nothing(client, src):
    src.files["someone/srchello"] = plugin_code("someone-else")
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srchello"})
    assert r.status_code == 400 and "has no plugin 'srchello'" in r.json()["detail"]
    src.files["someone/srchello"] = "raise RuntimeError('boom')"
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srchello"})
    assert r.status_code == 400 and "doesn't load" in r.json()["detail"]
    assert not (PLUGINS / "srchello.py").exists()


def test_an_existing_file_is_never_replaced(client, src):
    (PLUGINS / "srchello.py").write_text("# mine, not a plugin\nx = 1\n")
    r = client.post("/api/plugins/available/install", json={"source": OFFICIAL, "id": "srchello"})
    assert r.status_code == 400 and "there already" in r.json()["detail"]
    assert (PLUGINS / "srchello.py").read_text().startswith("# mine")


def test_add_and_remove_a_source(client, src):
    src.indexes[OTHER] = {"name": "Someone's plugins", "plugins": [
        {"id": "other", "name": "Other", "repo": "someone/other", "file": "other.py", "category": "library"}]}
    src.files["someone/other"] = plugin_code("other")
    r = client.post("/api/plugins/sources", json={"url": OTHER}).json()
    assert [s["url"] for s in r["sources"]] == [OFFICIAL, OTHER]
    assert entry(r, "other")["source"] == OTHER
    # the GitHub token never goes to a source that isn't on GitHub
    from app import plugin_sources
    from app.db import SessionLocal, set_setting
    with SessionLocal() as db:
        set_setting(db, "plugin_github_token", "secret")
        db.commit()
    plugin_sources._cache.clear()
    client.get("/api/plugins/available")
    assert [a for u, a in src.requests if u == OTHER][-1] is None
    assert [a for u, a in src.requests if u == OFFICIAL][-1] == "Bearer secret"
    # its plugin installs from its repo
    r = client.post("/api/plugins/available/install", json={"source": OTHER, "id": "other"})
    assert r.status_code == 200 and (PLUGINS / "other.py").exists()

    assert client.post("/api/plugins/sources", json={"url": OTHER}).status_code == 400  # listed already
    r = client.post("/api/plugins/sources/remove", json={"url": OTHER}).json()
    assert [s["url"] for s in r["sources"]] == [OFFICIAL]
    assert (PLUGINS / "other.py").exists()  # what was installed from it stays
    assert client.post("/api/plugins/sources/remove", json={"url": OFFICIAL}).status_code == 400


def test_bad_sources_are_refused(client, src):
    src.indexes[OTHER] = b"<html>not json</html>"
    r = client.post("/api/plugins/sources", json={"url": OTHER})
    assert r.status_code == 400 and "isn't a JSON file" in r.json()["detail"]
    src.indexes[OTHER] = {"plugins": [{"id": "x"}]}
    assert client.post("/api/plugins/sources", json={"url": OTHER}).status_code == 400
    r = client.post("/api/plugins/sources", json={"url": "https://example.com/missing.json"})
    assert r.status_code == 400 and "404" in r.json()["detail"]
    assert client.post("/api/plugins/sources", json={"url": "file:///etc/passwd"}).status_code == 400


def test_a_source_that_is_down_shows_its_error(client, src):
    del src.indexes[OFFICIAL]
    r = client.get("/api/plugins/available?refresh=1").json()
    assert "404" in r["sources"][0]["error"] and r["plugins"] == []


def test_a_plugin_from_another_source_cant_be_installed(client, src):
    r = client.post("/api/plugins/available/install", json={"source": OTHER, "id": "srchello"})
    assert r.status_code == 400 and "isn't listed" in r.json()["detail"]


def test_source_addresses():
    from app.plugin_sources import normalize
    assert normalize("someone/list") == "https://raw.githubusercontent.com/someone/list/HEAD/plugins.json"
    assert normalize("https://github.com/someone/list") == normalize("someone/list")
    assert normalize("https://github.com/someone/list.git") == normalize("someone/list")
    assert (normalize("https://github.com/someone/list/blob/main/x/plugins.json")
            == "https://raw.githubusercontent.com/someone/list/main/x/plugins.json")
    assert normalize(" https://example.com/a.json ") == "https://example.com/a.json"


def test_the_official_list_is_valid():
    """plugins.json at the top of this repo is MangaVibe's own source."""
    from pathlib import Path
    from app.plugin_sources import parse
    raw = json.loads((Path(__file__).parent.parent / "plugins.json").read_text(encoding="utf-8"))
    name, plugins = parse(json.dumps(raw).encode(), strict=True)
    assert name == "MangaVibe" and len(plugins) == len(raw["plugins"]) >= 5
