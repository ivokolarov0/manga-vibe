"""Real-world broken/odd archives (from the owner's library errors)."""
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.scanner import archives
from tests.conftest import TMP, png

needs_7zip = pytest.mark.skipif(not archives.SEVENZIP, reason="7-Zip not installed")


def zip_with(path: Path, files: dict, method=zipfile.ZIP_STORED):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", method) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def pages_of(path: Path):
    fmt = archives.sniff(path, archives.ARCHIVE_FORMATS.get(path.suffix.lower()))
    return fmt, archives.list_pages(str(path), fmt)


def test_zip_named_rar(tmp_path):
    p = zip_with(tmp_path / "[AAS]_Alive_v20_c75.rar", {"1.png": png("red"), "2.png": png("blue")})
    fmt, pages = pages_of(p)
    assert fmt == "zip" and pages == ["1.png", "2.png"]


def test_nested_chapter_zips(tmp_path):
    inner = {}
    for ch in (10, 2):  # stored out of order: natural sort must put c2 first
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in (2, 1):
                z.writestr(f"p{i}.jpg", png((ch, i, 0)))
        inner[f"Lovely_Complex_c{ch}.zip"] = buf.getvalue()
    inner["readme.txt"] = b"thanks for reading"
    p = zip_with(tmp_path / "Lovely_Complex_v06[ochibichan].zip", inner)
    fmt, pages = pages_of(p)
    assert pages == ["Lovely_Complex_c2.zip!/p1.jpg", "Lovely_Complex_c2.zip!/p2.jpg",
                     "Lovely_Complex_c10.zip!/p1.jpg", "Lovely_Complex_c10.zip!/p2.jpg"]
    data, mime = archives.read_page(str(p), fmt, 3)
    assert mime == "image/jpeg" and Image.open(io.BytesIO(data)).getpixel((0, 0))[:2] == (10, 2)


def test_no_images_message_lists_contents(tmp_path):
    p = zip_with(tmp_path / "odd.zip", {"a.txt": b"x", "b.txt": b"y", "c.db": b"z"})
    with pytest.raises(archives.ArchiveError, match=r"2 × \.txt"):
        pages_of(p)


def test_truncated_zip_message(tmp_path):
    good = zip_with(tmp_path / "good.cbz", {f"{i}.png": png("red") for i in range(5)})
    bad = tmp_path / "Waiting for Spring v14 (2021) (Digital).cbz"
    bad.write_bytes(good.read_bytes()[:-300])  # download that stopped early
    with pytest.raises(archives.ArchiveError, match="damaged or incomplete"):
        pages_of(bad)


def test_more_image_types_and_tiff_served_as_webp(client):
    lib = TMP / "manga" / "Tiff Series"
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "green").save(buf, "TIFF")
    zip_with(lib / "Tiff Series ch01.zip", {"001.tif": buf.getvalue(), "002.JPE": png("red")})
    from app.scanner import walker
    walker.scan_all()
    s = next(x for x in client.get("/api/series?q=Tiff").json()["items"])
    book = client.get(f"/api/series/{s['id']}/books").json()[0]
    assert book["page_count"] == 2 and not book["error"]
    r = client.get(f"/api/books/{book['id']}/pages/0")
    assert r.headers["content-type"] == "image/webp"


@needs_7zip
def test_deflate64_zip(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in (1, 2):
        (src / f"00{i} [x].png").write_bytes(png((i * 50, 0, 0)))
    p = tmp_path / "[Extras]Maoyuu Maou Yuusha c03.zip"
    subprocess.run([archives.SEVENZIP, "a", "-tzip", "-mm=Deflate64", str(p), str(src / "*")],
                   check=True, capture_output=True)
    with zipfile.ZipFile(p) as z, pytest.raises(NotImplementedError):
        z.read(z.namelist()[0])  # what Python alone does
    fmt, pages = pages_of(p)
    assert fmt == "zip" and pages == ["001 [x].png", "002 [x].png"]
    data, _ = archives.read_page(str(p), fmt, 1)
    assert Image.open(io.BytesIO(data)).getpixel((0, 0))[0] == 100


@needs_7zip
def test_7z_named_cbz(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "01.png").write_bytes(png("blue"))
    p = tmp_path / "Real.cbz"
    subprocess.run([archives.SEVENZIP, "a", "-t7z", str(p), str(src / "*")], check=True, capture_output=True)
    fmt, pages = pages_of(p)
    assert fmt == "7z" and pages == ["01.png"]
    assert archives.read_page(str(p), fmt, 0)[1] == "image/png"


def test_broken_first_page_and_recheck_after_fix(client):
    """A bad cover must not make the book unreadable; a fixed file clears its error on rescan."""
    from app.scanner import walker
    lib = TMP / "manga" / "Real Clothes"
    zip_with(lib / "Real Clothes v09 [WingtipCafe].zip", {"000.jpg": b"not really a jpeg", "001.png": png("red")})
    broken = lib / "Real Clothes v10.zip"
    broken.write_bytes(b"garbage")
    walker.scan_all()
    sid = client.get("/api/series?q=Real Clothes").json()["items"][0]["id"]
    books = {b["file_name"]: b for b in client.get(f"/api/series/{sid}/books").json()}
    v9 = books["Real Clothes v09 [WingtipCafe].zip"]
    assert not v9["error"] and v9["page_count"] == 2
    assert client.get(v9["thumbnail"]).status_code == 200      # made from page 2
    assert books["Real Clothes v10.zip"]["error"]

    zip_with(broken, {"1.png": png("blue")})                     # owner replaced the file
    walker.scan_all()
    books = {b["file_name"]: b for b in client.get(f"/api/series/{sid}/books").json()}
    assert not books["Real Clothes v10.zip"]["error"]
    shutil.rmtree(lib)
    shutil.rmtree(TMP / "manga" / "Tiff Series", ignore_errors=True)
    walker.scan_all()
