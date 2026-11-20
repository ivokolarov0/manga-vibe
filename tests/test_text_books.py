"""Text books (light novels, Plugin API 14): the scan keeps them as books whose sections count as
pages, and a plugin that `reads` the format shows them."""
import shutil
import zipfile

from app.metadata import matcher
from app.plugins import Plugin, categories_of, manager
from app.scanner import archives
from tests.conftest import TMP, png
from tests.test_formats import CONTAINER

TEXT = "<p>" + "The rain had not stopped for three days. " * 30 + "</p>"


def make_novel(path, chapters=3, cover=b""):
    """An EPUB 3 light novel: a cover named with properties="cover-image", then the chapters."""
    items = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(chapters))
    if cover:
        items += '<item id="cov" href="cover.png" media-type="image/png" properties="cover-image"/>'
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(chapters))
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", f"""<?xml version="1.0"?>
          <package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>{items}</manifest>
          <spine>{spine}</spine></package>""")
        for i in range(chapters):
            z.writestr(f"OEBPS/c{i}.xhtml", f"<html><body><h1>Chapter {i + 1}</h1>{TEXT}</body></html>")
        if cover:
            z.writestr("OEBPS/cover.png", cover)


class NovelReader(Plugin):
    id = "novel-reader-test"
    name = "Novel reader (test)"
    reads = [archives.TEXT_BOOK]


def _scan_series(client, name, **kw):
    from app.scanner import walker
    make_novel(TMP / "manga" / name / f"{name} v01.epub", **kw)
    walker.scan_all()
    sid = client.get(f"/api/series?q={name}").json()["items"][0]["id"]
    return sid, client.get(f"/api/series/{sid}/books").json()[0]


def _remove(name):
    from app.scanner import walker
    shutil.rmtree(TMP / "manga" / name)
    walker.scan_all()


def test_cover_names_itself(tmp_path):
    p = tmp_path / "n.epub"
    make_novel(p, cover=png("blue"))
    assert archives.epub_cover(str(p)) == png("blue")
    make_novel(tmp_path / "plain.epub")
    assert archives.epub_cover(str(tmp_path / "plain.epub")) is None


def test_reader_plugin_reads_text_books(client):
    sid, book = _scan_series(client, "Rainy Novel", chapters=4, cover=png("green"))
    assert book["format"] == archives.TEXT_BOOK and book["page_count"] == 4 and not book["error"]
    assert client.get(f"/api/books/{book['id']}/thumbnail").status_code == 200  # from the cover
    assert client.get(f"/api/books/{book['id']}/pages/0").status_code == 409  # no page pictures
    manager.plugins[NovelReader.id] = plugin = NovelReader()
    try:
        assert categories_of(plugin) == ["reader"]
        assert client.get(f"/api/books/{book['id']}").json()["reader"] == NovelReader.id
        att = client.get("/api/attention").json()
        assert "Rainy Novel v01.epub" not in [x["file"] for x in att["unsupported"]]
        # the plugin reads the sections the progress counts
        from app.db import SessionLocal
        from app.models import Book
        from app.plugins import Context
        with SessionLocal() as db:
            ctx, b = Context(plugin, db), db.get(Book, book["id"])
            sections = ctx.text_sections(b)
            assert len(sections) == 4
            assert b"Chapter 2" in ctx.read_entry(b, sections[1])
        # switched off, the book is back under Not supported
        manager.disabled.add(NovelReader.id)
        assert client.get(f"/api/books/{book['id']}").json()["reader"] is None
        assert "Rainy Novel v01.epub" in [x["file"] for x in client.get("/api/attention").json()["unsupported"]]
    finally:
        manager.disabled.discard(NovelReader.id)
        del manager.plugins[NovelReader.id]
        _remove("Rainy Novel")


def test_last_section_is_read_only_when_the_reader_says_so(client):
    sid, book = _scan_series(client, "Long Ending", chapters=3)
    try:
        url = f"/api/books/{book['id']}/progress"
        assert client.put(url, json={"page": 2}).json() == {"page": 2, "completed": False}
        assert client.put(url, json={"page": 2, "completed": True}).json()["completed"] is True
        assert client.put(url, json={"page": 2}).json()["completed"] is True  # stays read
    finally:
        _remove("Long Ending")


def test_light_novels_from_older_versions_are_read_again(client):
    """Migration 17: books an older version listed as "Text EPUB" are scanned again into text books."""
    from sqlalchemy import text

    from app.db import MIGRATIONS, SessionLocal
    from app.models import Book
    from app.scanner import walker
    sid, book = _scan_series(client, "Old Novel")
    try:
        with SessionLocal() as db:
            b = db.get(Book, book["id"])
            b.format, b.page_count, b.error = "epub", 0, f"{archives.TEXT_EPUB} (a novel, not a comic)"
            db.commit()
            MIGRATIONS[16](db.connection())
            db.commit()
            assert db.execute(text("SELECT mtime FROM book WHERE id = :i"), {"i": b.id}).scalar() == -1
        walker.scan_all()
        b = client.get(f"/api/series/{sid}/books").json()[0]
        assert b["format"] == archives.TEXT_BOOK and not b["error"] and b["page_count"] == 3
    finally:
        _remove("Old Novel")


def test_novel_series_match_the_novel():
    titles = ["Sword Story"]
    manga, novel = (matcher.score("Sword Story", titles, cand_format=f) for f in ("MANGA", "NOVEL"))
    assert manga > novel
    manga, novel = (matcher.score("Sword Story", titles, cand_format=f, novel=True) for f in ("MANGA", "NOVEL"))
    assert novel > manga
