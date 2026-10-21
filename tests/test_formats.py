"""EPUB (image and text), PDF, plus the custom CSS, folder picker and library removal APIs."""
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.scanner import archives
from tests.conftest import TMP, png

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""


def make_epub(path: Path, pages: list[tuple[str, str]], images: dict[str, bytes]):
    """pages: (file name, xhtml body) in reading order."""
    items = "".join(f'<item id="p{i}" href="text/{n}" media-type="application/xhtml+xml"/>'
                    for i, (n, _) in enumerate(pages))
    items += "".join(f'<item id="i{i}" href="images/{n}" media-type="image/png"/>' for i, n in enumerate(images))
    spine = "".join(f'<itemref idref="p{i}"/>' for i in range(len(pages)))
    opf = f"""<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <manifest>{items}</manifest><spine>{spine}</spine></package>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        for name, body in pages:
            z.writestr(f"OEBPS/text/{name}", f"<html><head><title>x</title></head><body>{body}</body></html>")
        for name, data in images.items():
            z.writestr(f"OEBPS/images/{name}", data)


def test_image_epub_follows_spine_order(tmp_path):
    # file names sort a, b, c but the book's order is c, a, b
    imgs = {"a.png": png((10, 0, 0)), "b.png": png((20, 0, 0)), "c.png": png((30, 0, 0))}
    pages = [("p1.xhtml", '<img src="../images/c.png"/>'), ("p2.xhtml", '<svg><image xlink:href="../images/a.png"/></svg>'),
             ("p3.xhtml", '<div><img alt="" src="../images/b.png"/></div>')]
    p = tmp_path / "Vol 01.epub"
    make_epub(p, pages, imgs)
    fmt = archives.sniff(p, "epub")
    assert fmt == "epub"
    assert archives.list_pages(str(p), fmt) == ["OEBPS/images/c.png", "OEBPS/images/a.png", "OEBPS/images/b.png"]
    data, mime = archives.read_page(str(p), fmt, 0)
    assert mime == "image/png" and Image.open(io.BytesIO(data)).getpixel((0, 0))[0] == 30


def test_text_epub_is_not_shown_as_comic(tmp_path):
    long_text = "<p>" + "It was a quiet morning in the village. " * 30 + "</p>"
    pages = [("cover.xhtml", '<img src="../images/cover.png"/>')] + [(f"ch{i}.xhtml", long_text) for i in range(5)]
    p = tmp_path / "Some Light Novel v01.epub"
    make_epub(p, pages, {"cover.png": png("white")})
    with pytest.raises(archives.UnsupportedFormat, match=archives.TEXT_EPUB):
        archives.list_pages(str(p), "epub")


def test_pdf_pages_rendered(tmp_path):
    import pymupdf
    p = tmp_path / "Comic.pdf"
    doc = pymupdf.open()
    for color in ((255, 0, 0), (0, 0, 255)):
        page = doc.new_page(width=400, height=600)
        page.insert_image(page.rect, stream=png(color))
    doc.save(p)
    doc.close()
    fmt = archives.sniff(p, "pdf")
    pages = archives.list_pages(str(p), fmt)
    assert fmt == "pdf" and pages == ["0001.jpg", "0002.jpg"]
    data, mime = archives.read_page(str(p), fmt, 1)
    im = Image.open(io.BytesIO(data))
    assert mime == "image/jpeg" and im.width == archives.PDF_WIDTH
    r, g, b = im.getpixel((im.width // 2, im.height // 2))[:3]
    assert b > 200 and r < 60


def test_epub_and_pdf_in_library(client):
    from app.scanner import walker
    lib = TMP / "manga" / "Format Series"
    make_epub(lib / "Format Series v01.epub", [("p.xhtml", '<img src="../images/1.png"/>')], {"1.png": png("red")})
    text = "<p>" + "words " * 200 + "</p>"
    make_epub(lib / "Format Series Novel v02.epub", [(f"c{i}.xhtml", text) for i in range(3)], {})
    walker.scan_all()
    sid = client.get("/api/series?q=Format Series").json()["items"][0]["id"]
    books = {b["file_name"]: b for b in client.get(f"/api/series/{sid}/books").json()}
    assert not books["Format Series v01.epub"]["error"] and books["Format Series v01.epub"]["page_count"] == 1
    att = client.get("/api/attention").json()
    assert "Format Series Novel v02.epub" in [x["file"] for x in att["unsupported"]]
    assert "Format Series Novel v02.epub" not in [x["file"] for x in att["unreadable"]]
    import shutil
    shutil.rmtree(lib)
    walker.scan_all()


def test_custom_css_and_plugin_css(client):
    from app import config
    from app.plugins import Plugin, manager

    class Pink(Plugin):
        id = "pink-test"
        css = ":root { --accent: pink; }"

    config.CUSTOM_CSS.write_text(".card-title { font-weight: 700; }", encoding="utf-8")
    manager.plugins["pink-test"] = Pink()
    try:
        css = client.get("/custom.css")
        assert css.headers["content-type"].startswith("text/css")
        body = css.text
        assert "--accent: pink" in body and ".card-title { font-weight: 700; }" in body
        assert body.index("pink") < body.index(".card-title")  # owner's file comes last and wins
    finally:
        del manager.plugins["pink-test"]


def test_folder_picker_and_remove_library(client):
    from app.db import SessionLocal
    from app.scanner import walker
    extra = TMP / "library2"
    (extra / "Some Series").mkdir(parents=True, exist_ok=True)
    listing = client.get(f"/api/folders?path={TMP}").json()
    names = {d["name"]: d for d in listing["dirs"]}
    assert "library2" in names and names["manga"]["library"] is True
    assert "data" not in names  # MangaVibe's own data folder is hidden
    assert client.post("/api/libraries", json={"root_path": str(TMP / "data")}).status_code == 400

    lib_id = client.post("/api/libraries", json={"root_path": str(extra)}).json()["id"]
    assert client.delete(f"/api/libraries/{lib_id}").json() == {"ok": True}
    with SessionLocal() as db:  # removed folders are not added back from LIBRARY_PATHS on restart
        walker.ensure_libraries(db, [str(extra)])
    assert str(extra) not in [l["root_path"] for l in client.get("/api/libraries").json()]
