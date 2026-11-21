"""The view:series contract (Plugin API 18, issue #206): the built-in "Series page", and what the app does
with a series page plugin: a missing slot is reported, a page that fails falls back to the built-in one,
a page that extends the built-in one gets its component (seriesPage(), from its script) even though the
built-in plugin is switched off.

To check your own: install it as `page()` does below, then run the same checks.
"""
import shutil

import pytest

from tests.conftest import TMP

BUILTIN = "series-page"
PLUGINS_DIR = TMP / "data" / "plugins"
SLOTS = ("series-actions", "series-menu", "series-panels", "series-end", "book-row")

PAGE = '''
from app.plugins import Plugin

class MyPage(Plugin):
    id = "my-page"
    name = "My series page"
    category = "appearance"
    provides = {"view:series": 1}
'''


def section(page):
    """The series page's section in the page."""
    return page.split('<section class="view series-view"', 1)[1].split('<section class="view settings-page"', 1)[0]


def warnings(client, pid):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == pid)["warnings"]


def test_the_built_in_page(client):
    page = client.get("/").text
    s = section(page)
    assert 'x-data="seriesPage()"' in s
    for slot in SLOTS:
        assert f'data-slot="{slot}"' in s, slot
    assert '<script src="/plugins/series-page.js">' in page
    assert warnings(client, BUILTIN) == []


@pytest.fixture
def page(client, tmp_path):
    """A series page plugin of the test's own: page(template) installs it with that template."""
    folder = tmp_path / "my_page"
    (folder / "templates" / "views").mkdir(parents=True)
    (folder / "__init__.py").write_text(PAGE, encoding="utf-8")

    def install(template):
        (folder / "templates" / "views" / "series.html").write_text(template, encoding="utf-8")
        shutil.copytree(folder, PLUGINS_DIR / folder.name)
        client.post("/api/plugins/reload")
        r = client.put("/api/plugins/my-page", json={"enabled": True})
        assert r.status_code == 200 and BUILTIN in r.json()["stopped"], r.text
    yield install
    if (PLUGINS_DIR / folder.name).exists():
        assert client.put(f"/api/plugins/{BUILTIN}", json={"enabled": True}).status_code == 200
        shutil.rmtree(PLUGINS_DIR / folder.name)
        client.post("/api/plugins/reload")


def test_a_page_of_its_own(client, page):
    slots = "".join(f'{{{{ slot("{s}") }}}}' for s in SLOTS)
    page(f'<div x-data class="my-page"><h1 x-text="$store.series.s?.title"></h1>{slots}</div>')
    html = client.get("/").text
    assert "my-page" in section(html) and "seriesPage()" not in section(html)
    # the built-in one's script is still served and loaded: it's the fallback
    assert '<script src="/plugins/series-page.js">' in html
    assert client.get("/plugins/series-page.js").status_code == 200
    assert warnings(client, "my-page") == []


def test_a_missing_slot_is_reported(client, page):
    page('<div x-data class="my-page">{{ slot("series-actions") }}{{ slot("series-menu") }}'
         '{{ slot("series-panels") }}{{ slot("series-end") }}</div>')
    found = warnings(client, "my-page")
    assert len(found) == 1 and "book-row" in found[0]


def test_a_page_that_fails_falls_back_to_the_built_in_one(client, page, caplog):
    page('<div>{{ nothing.here }}</div>')
    html = client.get("/").text
    assert 'x-data="seriesPage()"' in section(html)  # the built-in page, with its script
    assert '<script src="/plugins/series-page.js">' in html
    assert "my-page: its series view failed" in caplog.text


def test_a_page_can_change_one_block_of_the_built_in_one(client, page):
    page('{% extends "@series-page/views/series.html" %}{% block tags %}<i class="no-tags"></i>{% endblock %}')
    html = client.get("/").text
    s = section(html)
    assert 'x-data="seriesPage()"' in s and "no-tags" in s and "Genres &amp; tags" not in s
    assert '<script src="/plugins/series-page.js">' in html  # its component, the built-in plugin being off
    assert warnings(client, "my-page") == []
    # a block with a slot in it, left out: reported
    shutil.rmtree(PLUGINS_DIR / "my_page")
    client.put(f"/api/plugins/{BUILTIN}", json={"enabled": True})
    page('{% extends "@series-page/views/series.html" %}{% block books %}{% endblock %}')
    assert any("book-row" in w for w in warnings(client, "my-page"))
