"""Test setup: a temporary /data and a small generated library."""
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="mangavibe-test-"))
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["LIBRARY_PATHS"] = str(TMP / "manga")
os.environ["RESCAN_HOURS"] = "0"
os.environ["START_WORKERS"] = "0"  # no background metadata calls in tests

from PIL import Image  # noqa: E402


def png(color, size=(400, 600)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def make_zip(path: Path, pages=3, extra_files=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for i in range(pages, 0, -1):  # written out of order on purpose
            z.writestr(f"p{i}.png", png((i * 40 % 255, 80, 120)))
        z.writestr("__MACOSX/._p1.png", b"junk")
        z.writestr("Thumbs.db", b"junk")
        for name, data in extra_files:
            z.writestr(name, data)


def build_library(root: Path):
    hana = root / "Hana to Akuma"
    make_zip(hana / "Hana_to_Akuma_v01_ch01_[OT].zip", pages=10)
    make_zip(hana / "Hana_to_Akuma_v01_ch03_[OT].zip")
    make_zip(hana / "Hana_to_Akuma_v01_ch03v2_[OT].zip")
    make_zip(hana / "[KnO]Hana_to_Akuma52.zip")
    make_zip(hana / "Hana_to_Akuma_Omake.zip")
    folder = root / "Series B" / "Chapter 5"
    folder.mkdir(parents=True)
    for i in (2, 10, 1):
        (folder / f"{i:03d}.png").write_bytes(png((10, 10, i * 20)))
    eadir = root / "Series B" / "@eaDir" / "thumbs"  # Synology junk: must be ignored
    eadir.mkdir(parents=True)
    (eadir / "SYNOPHOTO_THUMB_M.jpg").write_bytes(png((0, 0, 0)))
    (root / "Broken").mkdir()
    (root / "Broken" / "bad.cbz").write_bytes(b"not a zip")
    (root / "Empty").mkdir()


@pytest.fixture(scope="session")
def client():
    build_library(TMP / "manga")
    from fastapi.testclient import TestClient

    from app.main import app
    from app.scanner import walker
    with TestClient(app) as c:
        # startup kicked off a background scan; wait for it, then scan again synchronously
        while walker.status["running"]:
            pass
        walker.scan_all()
        yield c
    shutil.rmtree(TMP, ignore_errors=True)
