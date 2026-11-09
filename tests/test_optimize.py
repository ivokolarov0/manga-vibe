"""Optimize, a built-in plugin (app/plugins/builtin/optimize/): pages converted to JPEG XL in place,
with the book's row, progress and plugins' page data kept."""
import io
import random
import subprocess
import time
import zipfile

import pytest
from PIL import Image

from tests.conftest import TMP, make_zip, png

ROOT = TMP / "manga"


@pytest.fixture
def on(client):
    assert client.put("/api/plugins/optimize", json={"enabled": True}).status_code == 200
    yield client
    client.post("/api/plugins/optimize/actions/stop")
    wait()
    client.put("/api/plugins/optimize", json={"enabled": False})


def wait():
    from app.db import SessionLocal
    from app.plugins.builtin.optimize import core
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            if not core.running() and not core.queue(db):
                return
        time.sleep(0.1)
    raise AssertionError("optimizing didn't finish")


def series_named(name):
    from app.db import SessionLocal
    from app.models import Series
    from app.scanner import walker
    walker.scan_all()
    with SessionLocal() as db:
        return db.query(Series).filter_by(folder_name=name).one().id


def books(sid):
    from app.db import SessionLocal
    from app.models import Book
    with SessionLocal() as db:
        return [(b.id, b.path, b.format, b.file_size, b.mtime, b.page_count)
                for b in db.query(Book).filter_by(series_id=sid).order_by(Book.sort_index)]


def noisy_jpeg(quality=30):
    """A colourful noisy JPEG: so compressed already that JPEG XL can't make it smaller."""
    rnd = random.Random(1)
    im = Image.frombytes("RGB", (300, 400), bytes(rnd.randrange(256) for _ in range(300 * 400 * 3)))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def test_convert_page():
    from app.plugins.builtin.optimize import core
    data = png((30, 30, 30), size=(800, 1200))
    out = core.convert_page(data, "jxl", 85)
    assert out is not None and len(out) < len(data)
    with Image.open(io.BytesIO(out)) as im:
        assert im.format == "JXL" and im.size == (800, 1200)
        assert im.mode == "L"  # a grey page in RGB is kept grey
    assert core.convert_page(noisy_jpeg(), "jxl", 85) is None  # wouldn't get smaller: kept
    assert core.convert_page(b"not an image", "jxl", 85) is None
    for fmt in ("avif", "webp"):
        assert Image.open(io.BytesIO(core.convert_page(data, fmt, 70))).format == fmt.upper()


def test_switched_off_by_default(client):
    info = next(p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "optimize")
    assert info["enabled"] is False
    assert any("Replaces book files" in x for x in info["permissions"])


def test_optimize_zip_series(on):
    from app.db import SessionLocal
    from app.models import PluginPage, Progress
    from app.scanner import walker
    folder = ROOT / "Optimize Zip"
    make_zip(folder / "Optimize Zip ch01.cbz", pages=4, extra_files=[("ComicInfo.xml", b"<ComicInfo/>")])
    sid = series_named("Optimize Zip")
    (bid, path, _, size, _, pages), = books(sid)
    with SessionLocal() as db:  # what must survive: reading progress and another plugin's page data
        db.add_all([Progress(book_id=bid, page=2), PluginPage(plugin="other", book_id=bid, page=1, value="hi")])
        db.commit()

    r = on.post(f"/api/series/{sid}/actions/optimize/optimize")
    assert "started" in r.json()["message"]
    wait()

    (bid2, path2, fmt, size2, mtime2, pages2), = books(sid)
    assert (bid2, path2, fmt, pages2) == (bid, path, "zip", pages)
    assert size2 < size
    assert walker.signature(folder / "Optimize Zip ch01.cbz", "zip") == (size2, mtime2)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert sorted(n for n in names if n.startswith("p")) == ["p1.jxl", "p2.jxl", "p3.jxl", "p4.jxl"]
    assert "ComicInfo.xml" in names and "Thumbs.db" in names  # everything else kept as it was
    assert not list(folder.glob(".mvopt-*"))

    walker.scan_all()  # the scan finds the book unchanged: nothing is lost
    with SessionLocal() as db:
        assert db.query(Progress).filter_by(book_id=bid).one().page == 2
        assert db.query(PluginPage).filter_by(plugin="other", book_id=bid).count() == 1
    assert books(sid)[0][3:5] == (size2, mtime2)
    assert on.get(f"/api/books/{bid}/pages/0").status_code == 200  # still readable

    badges = on.get(f"/api/series/{sid}/books").json()
    assert any(x["text"].startswith("−") for x in badges[0]["badges"])
    assert "optimized already" in on.post(f"/api/series/{sid}/actions/optimize/optimize").json()["message"]
    status = next(p for p in on.get("/api/plugins").json()["plugins"] if p["id"] == "optimize")["status"]
    assert status.startswith("Saved ")


def test_optimize_image_folder(on):
    folder = ROOT / "Optimize Folder" / "Chapter 1"
    folder.mkdir(parents=True)
    for i in (1, 2, 10):
        (folder / f"{i:03d}.png").write_bytes(png((10, 10, i * 20), size=(600, 900)))
    sid = series_named("Optimize Folder")
    on.post(f"/api/series/{sid}/actions/optimize/optimize")
    wait()
    assert sorted(p.name for p in folder.iterdir()) == ["001.jxl", "002.jxl", "010.jxl"]
    (_, _, fmt, _, _, pages), = books(sid)
    assert (fmt, pages) == ("folder", 3)


def test_7z_becomes_cbz(on, tmp_path):
    from app.scanner import archives
    if not archives.SEVENZIP:
        pytest.skip("needs the 7-Zip program")
    folder = ROOT / "Optimize Seven"
    folder.mkdir()
    for i in (1, 2):
        (tmp_path / f"{i:02d}.png").write_bytes(png((200, 200, 200), size=(500, 700)))
    subprocess.run([archives.SEVENZIP, "a", str(folder / "Seven ch01.cb7"), str(tmp_path / "*.png")],
                   check=True, capture_output=True)
    sid = series_named("Optimize Seven")
    (bid, *_), = books(sid)
    on.post(f"/api/series/{sid}/actions/optimize/optimize")
    wait()
    (bid2, path, fmt, *_), = books(sid)
    assert (bid2, fmt) == (bid, "zip") and path.endswith("Seven ch01.cbz")
    assert sorted(p.name for p in folder.iterdir()) == ["Seven ch01.cbz"]


def test_pages_that_dont_shrink_stay(on):
    folder = ROOT / "Optimize Noisy"
    folder.mkdir()
    with zipfile.ZipFile(folder / "Noisy ch01.cbz", "w") as z:
        z.writestr("001.jpg", noisy_jpeg())
        z.writestr("002.jpg", noisy_jpeg())
    before = (folder / "Noisy ch01.cbz").read_bytes()
    sid = series_named("Optimize Noisy")
    on.post(f"/api/series/{sid}/actions/optimize/optimize")
    wait()
    assert (folder / "Noisy ch01.cbz").read_bytes() == before  # untouched
    assert not list(folder.glob(".mvopt-*"))
    assert "optimized already" in on.post(f"/api/series/{sid}/actions/optimize/optimize").json()["message"]


def test_same_name_and_order_are_kept(on):
    """001.png and 001.jpg can't both become 001.jxl: one is converted, the other stays."""
    folder = ROOT / "Optimize Twins"
    folder.mkdir()
    with zipfile.ZipFile(folder / "Twins ch01.cbz", "w") as z:
        z.writestr("001.png", png((50, 50, 50), size=(600, 900)))
        z.writestr("001.jpg", _jpeg((60, 60, 60)))
        z.writestr("002.png", png((70, 70, 70), size=(600, 900)))
    sid = series_named("Optimize Twins")
    on.post(f"/api/series/{sid}/actions/optimize/optimize")
    wait()
    with zipfile.ZipFile(folder / "Twins ch01.cbz") as z:
        names = sorted(z.namelist())
    assert len(names) == 3 and "002.jxl" in names
    assert sum(n.startswith("001.") and n.endswith(".jxl") for n in names) == 1


def _jpeg(color):
    out = io.BytesIO()
    Image.new("RGB", (600, 900), color).save(out, "JPEG", quality=95)
    return out.getvalue()


def test_a_file_changed_meanwhile_is_left_alone(on):
    from app.db import SessionLocal
    from app.models import Book
    from app.plugins.builtin.optimize import core
    folder = ROOT / "Optimize Changed"
    make_zip(folder / "Changed ch01.cbz", pages=3)
    sid = series_named("Optimize Changed")
    with SessionLocal() as db:
        book = db.query(Book).filter_by(series_id=sid).one()
        result, before = core.optimize_book(book, "jxl", 85)
        assert result["temp"].exists()
        make_zip(folder / "Changed ch01.cbz", pages=5)  # replaced while it was converted
        new = (folder / "Changed ch01.cbz").read_bytes()
        with pytest.raises(core.Skip, match="changed"):
            core.swap_in(db, book, result, before)
    assert (folder / "Changed ch01.cbz").read_bytes() == new
    assert not result["temp"].exists()


def test_optimize_all_and_stop(on, monkeypatch):
    from app.db import SessionLocal
    from app.plugins.builtin.optimize import core
    # "all" here means this test's two series: the other tests' library stays as it is
    real = core.candidate
    monkeypatch.setattr(core, "candidate", lambda b: real(b) and "Optimize All" in b.path)
    for name in ("Optimize All A", "Optimize All B"):
        make_zip(ROOT / name / f"{name} ch01.cbz", pages=3)
    series_named("Optimize All A")
    with SessionLocal() as db:
        assert len(core.series_to_optimize(core_ctx(db))) == 2
    assert on.post("/api/plugins/optimize/actions/all").json()["message"] == "Optimizing 2 series"
    wait()
    for name in ("Optimize All A", "Optimize All B"):
        with zipfile.ZipFile(ROOT / name / f"{name} ch01.cbz") as z:
            assert "p1.jxl" in z.namelist()
    with SessionLocal() as db:
        assert core.series_to_optimize(core_ctx(db)) == []
    assert on.post("/api/plugins/optimize/actions/all").json()["message"] == "Every series is optimized already"
    assert on.post("/api/plugins/optimize/actions/stop").json()["message"] == "Nothing is being optimized"


def core_ctx(db):
    from app.plugins import Context, manager
    return Context(manager.plugins["optimize"], db)
