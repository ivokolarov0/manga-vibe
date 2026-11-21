"""The view:card contract (Plugin API 17, issue #205), run against every plugin doing the job: the
built-in "Series cards" and the example that changes it. And what the app does with a card plugin that
breaks the rules: a missing slot is reported, a template that fails falls back to the built-in card.

To check your own card plugin: add its folder to PLUGINS.
"""
import shutil
from pathlib import Path

import pytest

from tests.conftest import TMP

EXAMPLES = Path(__file__).parent.parent.parent / "examples" / "plugins"
BUILTIN = "cards"
PLUGINS = {BUILTIN: None, "compact-cards": EXAMPLES / "compact_cards"}  # id -> its folder (None: built in)
PLUGINS_DIR = TMP / "data" / "plugins"


def install(client, pid, folder):
    shutil.copytree(folder, PLUGINS_DIR / folder.name)
    client.post("/api/plugins/reload")
    r = client.put(f"/api/plugins/{pid}", json={"enabled": True})
    assert r.status_code == 200, r.text
    assert BUILTIN in r.json()["stopped"]


def uninstall(client, folder):
    assert client.put(f"/api/plugins/{BUILTIN}", json={"enabled": True}).status_code == 200
    shutil.rmtree(PLUGINS_DIR / folder.name)
    client.post("/api/plugins/reload")


@pytest.fixture(params=list(PLUGINS))
def doing(request, client):
    pid, folder = request.param, PLUGINS[request.param]
    if folder:
        install(client, pid, folder)
    yield pid
    if folder:
        uninstall(client, folder)


def cards(page):
    """The library's and Continue's card markup in the page."""
    lib = page.split('x-for="s in items" :key="s.id"', 1)[1].split("</template>", 1)[0]
    cont = page.split('x-for="s in items || []" :key="s.id"', 1)[1].split("</template>", 1)[0]
    return lib, cont


def test_the_card_is_in_the_page(doing, client):
    page = client.get("/").text
    lib, cont = cards(page)
    for markup in (lib, cont):
        assert 'class="poster"' in markup and 's.poster' in markup and 's.title' in markup
        assert 'data-slot="card-badges"' in markup  # other plugins' badges
    # the link around it is the page's: it opens the series, and does multi-select in the library
    assert "@pointerdown=\"press($event, s)\"" in page and ":href=\"'#/series/' + s.id\"" in page
    info = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == doing)
    assert info["warnings"] == [] and info["blocked"] == ""


def test_safe_mode_uses_the_built_in_card(client):
    folder = PLUGINS["compact-cards"]
    install(client, "compact-cards", folder)
    try:
        assert "compact-title" in client.get("/").text
        assert "compact-title" not in client.get("/?safe=1").text
    finally:
        client.get("/")  # out of safe mode
        uninstall(client, folder)


CARD = '''
from app.plugins import Plugin

class Odd(Plugin):
    id = "odd-cards"
    name = "Odd cards"
    category = "appearance"
    provides = {"view:card": 1}
'''


@pytest.fixture
def odd(client, tmp_path):
    """A card plugin of the test's own, its template written by the test."""
    folder = tmp_path / "odd_cards"
    (folder / "templates" / "views").mkdir(parents=True)
    (folder / "__init__.py").write_text(CARD, encoding="utf-8")

    def write(template):
        (folder / "templates" / "views" / "card.html").write_text(template, encoding="utf-8")
        if not (PLUGINS_DIR / folder.name).exists():
            install(client, "odd-cards", folder)
        else:  # the installed copy
            (PLUGINS_DIR / folder.name / "templates" / "views" / "card.html").write_text(template, encoding="utf-8")
    yield write
    if (PLUGINS_DIR / folder.name).exists():
        uninstall(client, folder)


def warnings(client):
    return next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "odd-cards")["warnings"]


def test_a_missing_slot_is_reported(client, odd):
    odd('<div class="poster odd-card"><img :src="s.poster"></div><div class="card-title" x-text="s.title"></div>')
    assert "odd-card" in client.get("/").text  # it's drawn
    found = warnings(client)
    assert len(found) == 1 and "card-badges" in found[0] and "won't show" in found[0]


def test_a_card_that_fails_falls_back_to_the_built_in_one(client, odd, caplog):
    odd('<div class="odd-card">{{ no_such_thing.title }}</div>')
    page = client.get("/").text
    assert "odd-card" not in page and 'x-text="MangaVibe.ui.cardSub(s)"' in page  # the built-in card
    assert "odd-cards: its card view failed" in caplog.text
    assert "fails" in warnings(client)[0]


def test_a_card_can_change_one_block_of_the_built_in_one(client, odd):
    odd('{% extends "@cards/views/card.html" %}{% block details %}<i class="odd-details"></i>{% endblock %}')
    page = client.get("/").text
    assert "odd-details" in page and 'class="card-meta"' not in page
    assert 'x-text="MangaVibe.ui.cardSub(s)"' in page  # the rest is the built-in card's
    assert warnings(client) == []
