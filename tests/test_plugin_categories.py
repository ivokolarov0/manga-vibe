"""Plugin categories (Plugin API 10): what a plugin says, else worked out; built in or installed."""
import shutil
from pathlib import Path

from app.plugins import CATEGORIES, Action, FieldDef, Plugin, categories_of
from tests.conftest import TMP

EXAMPLES = Path(__file__).parent.parent / "examples" / "plugins"


def make(**attrs):
    return type("P", (Plugin,), {"id": "p", **attrs})()


def test_said_category_wins():
    assert categories_of(make(category="reader", is_provider=True)) == ["reader"]
    assert categories_of(make(category=" Library ")) == ["library"]
    # several: the main one first, no repeats, unknown names dropped
    assert categories_of(make(category=["reader", "nonsense", "library", "reader"])) == ["reader", "library"]


def test_category_worked_out_when_not_said():
    assert categories_of(make(is_provider=True)) == ["metadata"]
    assert categories_of(make(overlay_label="Translation")) == ["reader"]
    assert categories_of(make(book_actions=[Action("a", "A")])) == ["reader"]
    assert categories_of(make(css=":root { --accent: pink; }")) == ["appearance"]
    # css plus anything else isn't only a theme
    assert categories_of(make(css="a {}", fields=[FieldDef("k", "K")])) == ["other"]
    assert categories_of(make(css="a {}", on_book_scanned=lambda self, ctx, book, parsed: None)) == ["other"]
    # a theme with a status line is still a theme
    assert categories_of(make(css="a {}", status=lambda self, ctx: "Pink")) == ["appearance"]
    assert categories_of(make()) == ["other"]
    assert categories_of(make(category="nonsense")) == ["other"]  # nothing known: worked out


def test_no_category_is_not_warned_about(caplog):
    caplog.set_level("WARNING", logger="mangavibe.plugins")
    categories_of(make())
    categories_of(make(category=["", "  "]))
    assert not caplog.records
    categories_of(make(category="themes"))
    assert "unknown category" in caplog.text


def test_every_builtin_and_example_says_its_category(client):
    from app.plugins import manager
    assert manager.plugins, "the built-in plugins are loaded"
    for p in manager.plugins.values():
        assert p.category, f"{p.id} has no category"
        assert set(categories_of(p)) <= set(CATEGORIES)
    for path in EXAMPLES.glob("*.py"):
        assert "\n    category = " in path.read_text(encoding="utf-8"), f"{path.name} has no category"


def test_api_sends_category_and_builtin(client):
    dest = TMP / "data" / "plugins" / "sakura_theme.py"
    shutil.copy(EXAMPLES / "sakura_theme.py", dest)
    try:
        r = client.post("/api/plugins/reload").json()
        assert list(r["categories"]) == list(CATEGORIES)
        info = {p["id"]: p for p in r["plugins"]}
        assert (info["anilist"]["category"], info["anilist"]["builtin"]) == ("metadata", True)
        assert info["optimize"]["categories"] == ["library", "system"]
        assert info["optimize"]["category"] == "library"
        assert info["accounts"]["category"] == "system"
        assert info["sakura-theme"]["builtin"] is False
        assert info["sakura-theme"]["categories"] == ["appearance"]
        assert "actions" in info["anilist"]
    finally:
        dest.unlink()
        client.post("/api/plugins/reload")
