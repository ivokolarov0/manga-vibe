"""Run MangaVibe on your PC for testing: `python dev.py`, then open http://localhost:8090

Uses ./dev-data for the database and ./dev-library as the manga folder. If
dev-library does not exist, a small fake library with numbered pages is created.
Set LIBRARY_PATHS to point at real manga instead, e.g. LIBRARY_PATHS=D:\\Manga
"""
import io
import os
import random
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
os.environ.setdefault("DATA_DIR", str(HERE / "dev-data"))
os.environ.setdefault("LIBRARY_PATHS", str(HERE / "dev-library"))


def page_image(text, sub, hue):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (900, 1350), (hue, 40, 255 - hue))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 160)
        small = ImageFont.truetype("arial.ttf", 50)
    except OSError:
        font = small = ImageFont.load_default()
    d.text((450, 600), text, fill="white", anchor="mm", font=font)
    d.text((450, 800), sub, fill="white", anchor="mm", font=small)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=70)
    return buf.getvalue()


def make_sample_library(root: Path):
    print("Creating sample library in", root)
    series = {
        "Hana to Akuma": ["Hana_to_Akuma_v01_ch01_[OT].zip", "Hana_to_Akuma_v01_ch02_[OT].zip",
                          "Hana_to_Akuma_v01_ch03_[OT].zip", "Hana_to_Akuma_v01_ch03v2_[OT].zip",
                          "Hana_to_Akuma10.zip", "[KnO]Hana_to_Akuma52.zip",
                          "Hana_to_Akuma_Extra2.zip"],
        "Berserk": ["Berserk Vol.01 (Digital).cbz", "Berserk Vol.02 (Digital).cbz"],
        "Some Webtoon": ["Some Webtoon - Chapter 1.cbz", "Some Webtoon - Chapter 2.cbz"],
    }
    for name, files in series.items():
        hue = random.randint(0, 255)
        for f in files:
            path = root / name / f
            path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(path, "w") as z:
                for i in range(1, 13):
                    z.writestr(f"{i:03d}.jpg", page_image(str(i), f"{name} · {Path(f).stem}", hue))
    for i in range(1, 30):  # many small series to test the grid
        hue = (i * 37) % 255
        path = root / f"Filler Series {i:02d}" / "Vol 01.cbz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as z:
            for p in range(1, 4):
                z.writestr(f"{p}.jpg", page_image(str(p), f"Filler {i}", hue))


if __name__ == "__main__":
    lib = Path(os.environ["LIBRARY_PATHS"].split(",")[0])
    if not lib.exists() and lib == HERE / "dev-library":
        make_sample_library(lib)
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8090)))
