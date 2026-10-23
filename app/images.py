"""Thumbnails, resized pages and the size-limited page cache."""
import hashlib
import io
import logging
import threading

from PIL import Image, ImageOps

from app import config

log = logging.getLogger("mangavibe")
THUMB_WIDTH = 300
_cache_lock = threading.Lock()
_writes_since_trim = 0
# A decoded page can take tens of MB (long webtoon strips far more). The reader asks for the page
# plus several preloads at once, so only this many are decoded at the same time; the rest wait.
_resize_slots = threading.BoundedSemaphore(2)


def resize(data: bytes, width: int, quality=82) -> bytes:
    """Shrink to `width` (never enlarge) and return WebP bytes."""
    with _resize_slots, Image.open(io.BytesIO(data)) as im:
        if im.format == "JPEG" and im.width > width:
            # decode JPEGs at 1/2, 1/4 or 1/8 size directly: far less memory and CPU
            im.draft("RGB", (width, round(im.height * width / im.width)))
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGBA" if "transparency" in im.info else "RGB")
        if im.width > width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        out = io.BytesIO()
        # method 2 encodes about twice as fast as Pillow's default 4, for files <1% bigger
        im.save(out, "WEBP", quality=quality, method=2)
        return out.getvalue()


def make_thumbnail(data: bytes, book_id: int):
    path = config.THUMBS_DIR / f"{book_id}.webp"
    path.write_bytes(resize(data, THUMB_WIDTH, quality=75))
    return str(path)


def cached_resized_page(book_id: int, index: int, width: int, mtime: float, load):
    """Return resized page bytes, from the cache when possible. `load()` gives original bytes."""
    global _writes_since_trim
    key = hashlib.sha1(f"{book_id}:{mtime}".encode()).hexdigest()[:10]
    path = config.PAGES_DIR / str(book_id) / f"{key}_{index}_{width}.webp"
    if path.exists():
        path.touch()  # mark as recently used for LRU trimming
        return path.read_bytes()
    data = resize(load(), width)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with _cache_lock:
        _writes_since_trim += 1
        trim = _writes_since_trim >= 200
        if trim:
            _writes_since_trim = 0
    if trim:
        threading.Thread(target=trim_page_cache, daemon=True).start()
    return data


def trim_page_cache():
    """Delete least recently used cached pages until under CACHE_MAX_GB."""
    limit = config.CACHE_MAX_GB * 1024 ** 3
    files = [(p.stat().st_mtime, p.stat().st_size, p) for p in config.PAGES_DIR.rglob("*.webp")]
    total = sum(f[1] for f in files)
    if total <= limit:
        return
    for _, size, p in sorted(files):
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        if total <= limit * 0.9:
            break
    log.info("Page cache trimmed to %.1f GB", total / 1024 ** 3)
