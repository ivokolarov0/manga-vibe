"""The page is put together from Jinja templates (app/web/templates, issue #213): includes, macros,
slot() and safe mode."""
import re

import pytest
from jinja2 import TemplateNotFound, UndefinedError

from app.plugins import web

SLOT_CALL = re.compile(r"""slot\(["']([a-z0-9-]+)["']\)""")


def templates():
    return sorted(web.TEMPLATES_DIR.rglob("*.html"))


def test_page_is_put_together(client):
    page = client.get("/").text
    assert page.startswith("<!doctype html>")  # nothing before it, not even the templates' notes
    assert "{{" not in page and "{%" not in page and "<!-- part:" not in page
    assert page.count('class="meta-progress"') == 2  # Settings → Metadata and Needs attention
    assert page.count('<label class="setting-row" :class="{ sub: r.sub }" x-show="!r.show || r.show(st)">') == 3  # Display, Reader, app
    for view in ("libraryPage()", "seriesPage()", "settingsView()", "attentionView()",
                 "fixMatch()", "editSeries()", "folderPicker()", "filterSheet()"):
        assert f'x-data="{view}"' in page, view
    assert page.rstrip().endswith("</html>")


def test_every_template_is_used():
    """Each template is included, imported or is the page: a file nobody uses is markup that's lost."""
    used = {"page.html"}
    for path in templates():
        used |= set(re.findall(r"""\{%-?\s*(?:include|import|from|extends)\s+["']([^"']+)["']""",
                               path.read_text(encoding="utf-8")))
    names = {p.relative_to(web.TEMPLATES_DIR).as_posix() for p in templates()}
    assert names == used


def test_unknown_slot_name_fails(monkeypatch, tmp_path):
    (tmp_path / "t.html").write_text('<div>{{ slot("no-such-slot") }}</div>', encoding="utf-8")
    monkeypatch.setattr(web.templates, "loader", web.FileSystemLoader(tmp_path))
    with pytest.raises(ValueError, match="No slot 'no-such-slot'"):
        web.render_index(template="t.html")


def test_misspelt_name_or_missing_template_fails(monkeypatch, tmp_path):
    (tmp_path / "a.html").write_text("{{ nope }}", encoding="utf-8")
    (tmp_path / "b.html").write_text('{% include "missing.html" %}', encoding="utf-8")
    monkeypatch.setattr(web.templates, "loader", web.FileSystemLoader(tmp_path))
    with pytest.raises(UndefinedError):
        web.render_index(template="a.html")
    with pytest.raises(TemplateNotFound):
        web.render_index(template="b.html")


def test_template_text_is_escaped_and_plugin_markup_is_not(monkeypatch, tmp_path):
    from types import SimpleNamespace
    (tmp_path / "t.html").write_text('{{ "<b>" }}|{{ slot("sheets") }}', encoding="utf-8")
    monkeypatch.setattr(web.templates, "loader", web.FileSystemLoader(tmp_path))
    plugin = SimpleNamespace(id="x", html={"sheets": "<i>hi</i>"}, js="")
    monkeypatch.setattr(web, "page_plugins", lambda safe=False: [plugin])
    out = web.render_index(template="t.html")
    assert out.startswith("&lt;b&gt;|") and "<i>hi</i>" in out
