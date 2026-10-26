"""Series posters: uploaded, taken from a book page, or downloaded from a provider.

Every poster, whatever its source, goes through save_poster_bytes():
- the bytes must decode as a real image (Pillow), of a normal picture format;
- size limits: MAX_UPLOAD bytes, MAX_PIXELS pixels (stops "decompression bombs");
- the picture is re-encoded to a fresh JPEG (big) and WebP (small). The original bytes, their
  metadata (EXIF, comments) and anything hidden in them are never stored or served;
- the file name is always covers/<series id>.jpg, never something the browser sent.
"""
import io
import os
from pathlib import Path

from PIL import Image, ImageOps

from app import config, images  # noqa: F401  (images registers JPEG XL)

MAX_UPLOAD = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000           # e.g. 5000 x 8000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "AVIF", "MPO", "JXL"}
BIG_WIDTH, SMALL_BOX = 600, (300, 450)


class PosterError(Exception):
    """Plain-English reason an image can't be used as a poster."""


def _decode(data: bytes) -> Image.Image:
    if not data:
        raise PosterError("The file is empty")
    if len(data) > MAX_UPLOAD:
        raise PosterError(f"The image is larger than {MAX_UPLOAD // 1024 // 1024} MB")
    try:
        im = Image.open(io.BytesIO(data))  # reads only the header
    except Exception:
        raise PosterError("That file is not an image (JPEG, PNG, WebP, GIF, AVIF or JPEG XL)")
    if im.format not in ALLOWED_FORMATS:
        raise PosterError(f"{im.format} images can't be used; use JPEG, PNG, WebP, GIF, AVIF or JPEG XL")
    if im.width * im.height > MAX_PIXELS or im.width < 16 or im.height < 16:
        raise PosterError("The image size is out of range")
    try:
        im.load()  # now decode the pixels; broken files fail here
    except Exception:
        raise PosterError("The image is damaged and can't be read")
    return im


def save_poster_bytes(series, data: bytes, source: str):
    """Validate, re-encode and store `data` as the poster of `series`."""
    im = _decode(data)
    try:
        im.seek(0)  # animated GIF/WebP: first frame
    except EOFError:
        pass
    im = ImageOps.exif_transpose(im).convert("RGB")
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    big = im.copy()
    if big.width > BIG_WIDTH:
        big = big.resize((BIG_WIDTH, round(big.height * BIG_WIDTH / big.width)), Image.LANCZOS)
    path = config.COVERS_DIR / f"{int(series.id)}.jpg"
    tmp = path.with_suffix(".tmp")
    big.save(tmp, "JPEG", quality=88)  # write then rename: a failed save never leaves half a file
    os.replace(tmp, path)
    small = im.copy()
    small.thumbnail(SMALL_BOX, Image.LANCZOS)
    small.save(config.COVERS_DIR / f"{int(series.id)}_sm.webp", "WEBP", quality=80)
    series.poster_path = str(path)
    series.poster_source = source


def remove_poster(series):
    for p in (config.COVERS_DIR / f"{int(series.id)}.jpg", config.COVERS_DIR / f"{int(series.id)}_sm.webp"):
        Path(p).unlink(missing_ok=True)
    series.poster_path = None
    series.poster_source = "first_page"
