"""The view:library and view:continue contracts (Plugin API 19, issue #207): the built-in pages, and what
the app does with a page plugin of one's own: its markup is drawn with the card view and the page's
card links (multi-select), a missing slot is reported, a page that fails falls back to the built-in one.
"""
import shutil

import pytest

from tests.conftest import TMP

PLUGINS_DIR = TMP / "data" / "plugins"
JOBS = {  # view -> the built-in plugin, its slots
    "library": ("library-page", ("library-tools", "library-filters", "select-menu", "library-end", "card-badges")),
    "continue": ("continue-page", ("continue-top", "card-badges")),
}


def section(page, view):
    start = '<section id="library-view"' if view == "library" else "<!-- ============ continue reading"
    return page.split(start, 1)[1].split("</section>\n    <!-- ============", 1)[0]


def plugin_info(client, pid):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == pid)


@pytest.mark.parametrize("view", list(JOBS))
def test_the_built_in_pages(client, view):
    builtin, slots = JOBS[view]
    s = section(client.get("/").text, view)
    for slot in slots:
        assert f'data-slot="{slot}"' in s, slot
    assert 'class="card"' in s and 'class="poster"' in s  # the card view, in the page's link
    assert plugin_info(client, builtin)["warnings"] == []


CODE = '''
from app.plugins import Plugin

class Mine(Plugin):
    id = "my-{view}"
    name = "My {view} page"
    category = "appearance"
    provides = {{"view:{view}": 1}}
'''


@pytest.fixture
def mine(client, tmp_path):
    """mine(view, template): a page plugin of the test's own, switched on in the built-in one's place."""
    installed = []

    def install(view, template):
        folder = tmp_path / f"my_{view}"
        (folder / "templates" / "views").mkdir(parents=True)
        (folder / "__init__.py").write_text(CODE.format(view=view), encoding="utf-8")
        (folder / "templates" / "views" / f"{view}.html").write_text(template, encoding="utf-8")
        shutil.copytree(folder, PLUGINS_DIR / folder.name)
        client.post("/api/plugins/reload")
        r = client.put(f"/api/plugins/my-{view}", json={"enabled": True})
        assert r.status_code == 200 and JOBS[view][0] in r.json()["stopped"], r.text
        installed.append((view, folder.name))
    yield install
    for view, name in installed:
        client.put(f"/api/plugins/{JOBS[view][0]}", json={"enabled": True})
        shutil.rmtree(PLUGINS_DIR / name)
    client.post("/api/plugins/reload")


def test_a_library_page_of_its_own(client, mine):
    """The cards with the page's links, the slots: e.g. a page with just a grid and the plugins' places."""
    mine("library", '{% from "ui/macros.html" import library_card with context %}'
         '<div x-data class="my-library"><template x-for="s in $store.library.items" :key="s.id">'
         '{{ library_card() }}</template>{{ slot("library-tools") }}{{ slot("library-filters") }}'
         '{{ slot("select-menu") }}{{ slot("library-end") }}<div x-init="$store.library.watchEnd($el)"></div></div>')
    s = section(client.get("/").text, "library")
    assert "my-library" in s and "libraryPage()" not in s
    assert '@pointerdown="$store.library.press($event, s)"' in s and 'data-slot="card-badges"' in s
    assert plugin_info(client, "my-library")["warnings"] == []


def test_a_missing_slot_is_reported(client, mine):
    mine("continue", '<div x-data><h2>In progress</h2></div>')  # no continue-top, no cards
    found = plugin_info(client, "my-continue")["warnings"]
    assert any("continue-top" in w for w in found) and any("card-badges" in w for w in found)


def test_a_page_that_fails_falls_back(client, mine, caplog):
    mine("library", '{{ library_card() }}')  # not imported: an error
    s = section(client.get("/").text, "library")
    assert 'x-data="libraryPage()"' in s
    assert "my-library: its library view failed" in caplog.text
    assert '<script src="/plugins/library-page.js">' in client.get("/").text  # the fallback's component
