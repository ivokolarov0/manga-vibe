"""The page's slots for plugin markup ({{ slot("<name>") }} in the templates, <!-- slot:<name> --> in a
plugin's own markup; SLOTS in app/plugins/web.py)."""
import re
from types import SimpleNamespace

from app.plugins import BUILTIN, web

SLOT_CALL = re.compile(r"""slot\(["']([a-z0-9-]+)["']\)""")
SLOT_COMMENT = re.compile(r"<!-- slot:([a-z0-9-]+) -->")


def test_slot_names_match():
    """Every slot in SLOTS is somewhere in the page's templates or a built-in plugin's markup, and every
    slot there is in SLOTS: a typo on either side would leave a slot that's never filled. (A slot() name
    that isn't in SLOTS also fails the page itself.)"""
    found = set()
    for path in web.TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        found |= set(SLOT_CALL.findall(text))
        assert not SLOT_COMMENT.search(text), f"{path.name}: use slot() in templates"
    for path in BUILTIN.rglob("*.html"):
        found |= set(SLOT_COMMENT.findall(path.read_text(encoding="utf-8")))
    assert found == set(web.SLOTS)


def test_repeated_slots_are_filled_everywhere(monkeypatch):
    """card-badges is in both card lists (library and Continue), book-row in each book's row."""
    plugin = SimpleNamespace(id="tags", js="", html={"card-badges": '<span class="badge">EN</span>',
                                                     "book-row": '<span class="book-badge" x-text="b.id"></span>',
                                                     "series-menu": "<button>Sync</button>"})
    monkeypatch.setattr(web, "page_plugins", lambda safe=False: [plugin])
    out = web.render_index()
    assert out.count('<span class="badge">EN</span>') == 2
    assert out.count('x-text="b.id"') == 1 and out.count("<button>Sync</button>") == 1
    assert '<div class="plugin-slot" data-slot="continue-top"></div>' in out  # empty, kept for later
    assert "<!-- slot:" not in out


def test_a_slot_in_plugin_markup_is_filled():
    """The accounts plugin's markup holds account-row for other plugins."""
    host = SimpleNamespace(id="host", html={"settings": "<ul><!-- slot:account-row --></ul>"})
    guest = SimpleNamespace(id="guest", html={"account-row": "<li>key</li>"})
    out = web.slot_markup("settings", [host, guest])
    assert '<div class="plugin-slot" data-slot="account-row">' in out and "<li>key</li>" in out
    assert "<!-- slot:" not in out
