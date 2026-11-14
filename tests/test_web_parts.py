"""Parts of the page used in more than one place (<!-- part:<name> -->, app/plugins/web.py)."""
import pytest

from app import config
from app.plugins import web


def test_page_has_its_parts(client):
    page = client.get("/").text
    assert "<!-- part:" not in page
    assert page.count('class="meta-progress"') == 2  # Settings → Metadata and Needs attention
    assert page.count('<label class="setting-row" x-show="!r.show || r.show(st)">') == 3  # Display, Reader, app


def test_every_part_is_used():
    used = set(web.PART.findall((config.WEB_DIR / "index.html").read_text(encoding="utf-8")))
    for path in web.PARTS_DIR.glob("*.html"):
        used |= set(web.PART.findall(path.read_text(encoding="utf-8")))
    assert used == {p.stem for p in web.PARTS_DIR.glob("*.html")}


def test_unknown_or_looping_part(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="No part 'nope'"):
        web.fill_parts("<div><!-- part:nope --></div>")
    monkeypatch.setattr(web, "PARTS_DIR", tmp_path)
    (tmp_path / "loop.html").write_text("<i><!-- part:loop --></i>", encoding="utf-8")
    with pytest.raises(ValueError, match="too deep"):
        web.fill_parts("<!-- part:loop -->")
    (tmp_path / "a.html").write_text("\n<b>a <!-- part:b --></b>\n", encoding="utf-8")
    (tmp_path / "b.html").write_text("<i>b</i>", encoding="utf-8")
    assert web.fill_parts("<p><!-- part:a --></p>") == "<p><b>a <i>b</i></b></p>"
