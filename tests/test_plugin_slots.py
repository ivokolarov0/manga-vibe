"""The page's slots for plugin markup (<!-- slot:<name> -->, SLOTS in app/plugins/web.py)."""
import re
from types import SimpleNamespace

from app import config
from app.plugins import BUILTIN, web

SLOT = re.compile(r"<!-- slot:([a-z0-9-]+) -->")


def markup_files():
    return [config.WEB_DIR / "index.html", *sorted(BUILTIN.rglob("*.html"))]


def test_slot_names_match():
    """Every slot in SLOTS is somewhere in the page or a built-in plugin's markup, and every slot there is
    in SLOTS: a typo on either side would leave a slot that's never filled, without an error."""
    found = set()
    for path in markup_files():
        found |= set(SLOT.findall(path.read_text(encoding="utf-8")))
    found.discard("scripts")  # the plugins' <script> tags, not markup
    assert found == set(web.SLOTS)


def test_repeated_slots_are_filled_everywhere():
    """card-badges is in both card lists (library and Continue), book-row in each book's row."""
    page = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
    plugin = SimpleNamespace(id="tags", html={"card-badges": '<span class="badge">EN</span>',
                                             "book-row": '<span class="book-badge" x-text="b.id"></span>',
                                             "series-menu": "<button>Sync</button>"})
    out = web.fill_slots(page, [plugin])
    assert out.count('<span class="badge">EN</span>') == 2
    assert out.count('x-text="b.id"') == 1 and out.count("<button>Sync</button>") == 1
    assert '<div class="plugin-slot" data-slot="continue-top"></div>' in out  # empty, kept for later
    assert "<!-- slot:" not in out.replace("<!-- slot:scripts -->", "")
