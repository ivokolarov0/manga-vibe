"""The view:home contract and Home's rows from plugins (Plugin API 30, issue #291): the built-in page, a page
plugin of one's own in its place, a plugin's own rows (series and tiles) in GET /api/home and in the settings'
list, and a row that fails left out without breaking Home."""
import shutil

import pytest

from tests.conftest import TMP

PLUGINS_DIR = TMP / "data" / "plugins"


def section(page):
    return page.split("<!-- ============ home:", 1)[1].split("<!-- ============ library:", 1)[0]


def plugin_info(client, pid):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == pid)


def rows(client):
    return {s["id"]: s for s in client.get("/api/home").json()["sections"]}


@pytest.fixture
def install(client, tmp_path):
    """install(name, code, templates={path: markup}): a plugin of the test's own, loaded."""
    names = []

    def put(name, code, templates=None):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "__init__.py").write_text(code, encoding="utf-8")
        for path, markup in (templates or {}).items():
            (folder / "templates" / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / "templates" / path).write_text(markup, encoding="utf-8")
        shutil.copytree(folder, PLUGINS_DIR / name)
        names.append(name)
        assert client.post("/api/plugins/reload").status_code == 200
    yield put
    client.put("/api/plugins/home-page", json={"enabled": True})
    for name in names:
        shutil.rmtree(PLUGINS_DIR / name)
    client.post("/api/plugins/reload")


def test_the_built_in_page(client):
    s = section(client.get("/").text)
    assert 'class="home-page-view"' in s and 'x-data="homeRow()"' in s
    assert 'data-slot="home-end"' in s and 'data-slot="card-badges"' in s
    assert 'class="card"' in s and 'class="poster"' in s  # the card view, both layouts
    assert plugin_info(client, "home-page")["warnings"] == []


PAGE = '''
from app.plugins import Plugin

class MyHome(Plugin):
    id = "my-home"
    name = "My home"
    category = "appearance"
    provides = {"view:home": 1}
'''


def test_a_home_page_of_its_own(client, install):
    install("my_home", PAGE, {"views/home.html": '{% from "ui/macros.html" import home_card with context %}'
                              '<div x-data class="my-home"><template x-for="sec in $store.home.sections || []">'
                              '<div><template x-for="s in sec.items">{{ home_card() }}</template></div></template>'
                              '{{ slot("home-end") }}</div>'})
    r = client.put("/api/plugins/my-home", json={"enabled": True})
    assert r.status_code == 200 and "home-page" in r.json()["stopped"], r.text
    s = section(client.get("/").text)
    assert "my-home" in s and "homeRow()" not in s and 'data-slot="card-badges"' in s
    assert plugin_info(client, "my-home")["warnings"] == []


def test_a_missing_slot_is_reported(client, install):
    install("my_home", PAGE, {"views/home.html": "<div x-data>Hello</div>"})
    client.put("/api/plugins/my-home", json={"enabled": True})
    found = plugin_info(client, "my-home")["warnings"]
    assert any("home-end" in w for w in found) and any("card-badges" in w for w in found)


ROWS = '''
from app.plugins import HomeSection, Plugin
from app.plugins.sdk import N_, Series

class Rows(Plugin):
    id = "my-rows"
    name = "My rows"
    home_sections = [HomeSection("az", N_("A to Z"), see_all="#/?sort=title"),
                     HomeSection("links", N_("Links"), kind="tiles"),
                     HomeSection("broken", N_("Broken")),
                     HomeSection("nothing", N_("Nothing"))]

    def home_section(self, ctx, request, key, limit):
        if key == "az":
            return ctx.db.query(Series).order_by(Series.folder_name).limit(limit + 5).all()
        if key == "links":
            return [{"id": 1, "title": "Docs", "subtitle": None, "href": "#/settings", "other": "x"}, {"no": "title"}]
        if key == "broken":
            raise RuntimeError("oops")
        return []
'''


def test_rows_from_a_plugin(client, install, caplog):
    install("my_rows", ROWS)
    found = rows(client)
    az = found["my-rows:az"]
    assert az["plugin"] == "my-rows" and az["kind"] == "series" and az["see_all"] == "#/?sort=title"
    assert 0 < len(az["items"]) <= 10 and {"title", "poster"} <= set(az["items"][0])
    assert found["my-rows:links"]["items"] == [{"id": "1", "title": "Docs", "subtitle": "", "image": "",
                                                 "href": "#/settings", "icon": ""}]
    # a row that fails, or has nothing, is left out; Home still answers
    assert "my-rows:broken" not in found and "my-rows:nothing" not in found
    assert "my-rows failed in home_section('broken')" in caplog.text
    # in the settings' list after the app's own, and can be moved and hidden like them
    listed = [s["id"] for s in client.get("/api/home/sections").json()]
    assert listed.index("my-rows:az") > listed.index("updated")
    client.put("/api/settings", json={"home_layout": {"order": ["my-rows:links"], "hidden": ["my-rows:az"]}})
    try:
        assert list(rows(client))[0] == "my-rows:links" and "my-rows:az" not in rows(client)
    finally:
        client.put("/api/settings", json={"home_layout": {}})
    # switched off: its rows are gone
    client.put("/api/plugins/my-rows", json={"enabled": False})
    assert not any(k.startswith("my-rows:") for k in rows(client))
