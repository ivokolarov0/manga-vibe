"""Site icons for the Links panel (MangaUpdates, AniList, ...).

Fetched by the server, never by the browser, and cached on disk. Safety rules:
- only public internet addresses (no LAN / localhost / NAS admin pages), checked again on every redirect
- small size limits, short timeouts
- the image is decoded with Pillow and re-encoded as a 32x32 PNG; the original bytes are never served
"""
import io
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from app import config
from app.metadata import http

log = logging.getLogger("mangavibe.favicon")

ICON_DIR = config.CACHE_DIR / "favicons"
MAX_BYTES = 256 * 1024
MAX_PAGE = 512 * 1024
RETRY_AFTER = 7 * 24 * 3600  # a site without a usable icon is asked again after a week
HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
ICON_LINK = re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?[^\"'>]*\bicon\b[^>]*>", re.I)
HREF = re.compile(r"\bhref\s*=\s*[\"']?([^\"' >]+)", re.I)


def _public_ip(host: str) -> str | None:
    """The address to connect to, if every address of `host` is a normal public one; else None."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return None
    return infos[0][4][0] if infos else None


def _get(url: str, limit: int, seen: dict) -> bytes | None:
    """GET with manual redirects (max 3), each hop re-checked; None on any problem.

    The connection goes to the IP that was checked (Host header and TLS name stay the real
    host), so a DNS answer that changes between the check and the connect can't reach the LAN.
    seen["answered"] becomes True when the site gave a real (non-5xx) answer."""
    for _ in range(4):
        parts = urlparse(url)
        ip = parts.hostname and _public_ip(parts.hostname)
        if parts.scheme != "https" or not ip:
            return None
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        pinned = parts._replace(netloc=(f"[{ip}]" if ":" in ip else ip) + (f":{parts.port}" if parts.port else ""))
        try:
            with httpx.Client(timeout=6, transport=http.transport, follow_redirects=False,
                              headers={"User-Agent": http.USER_AGENT}) as c:
                with c.stream("GET", pinned.geturl(), headers={"Host": host},
                              extensions={"sni_hostname": parts.hostname}) as r:
                    if r.status_code < 500:
                        seen["answered"] = True
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    if r.status_code != 200:
                        return None
                    data = b""
                    for chunk in r.iter_bytes():
                        data += chunk
                        if len(data) > limit:
                            return None
                    return data
        except httpx.HTTPError:
            return None
    return None


def _svg_to_raster(data: bytes) -> bytes | None:
    """Draw an SVG icon to PNG with PyMuPDF. The SVG itself is never served (it could hold scripts)."""
    try:
        import pymupdf
        with pymupdf.open(stream=data, filetype="svg") as doc:
            page = doc[0]
            zoom = 64 / max(page.rect.width, page.rect.height, 1)
            return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).tobytes("png")
    except Exception:
        return None


def _to_png(data: bytes) -> bytes | None:
    head = data[:300].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2000].lower()):
        data = _svg_to_raster(data)
        if not data:
            return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 4096 * 4096:
                return None
            im = im.convert("RGBA")  # for .ico Pillow already picks the largest size
            im.thumbnail((32, 32), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    except Exception:
        return None


def icon_png(url: str) -> bytes | None:
    """32x32 PNG icon for the site of `url`, or None. Cached per host."""
    host = (urlparse(url).hostname or "").lower()
    if not HOST_RE.match(host):
        return None
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    png, none = ICON_DIR / f"{host}.png", ICON_DIR / f"{host}.none"
    if png.exists():
        return png.read_bytes()
    if none.exists() and time.time() - none.stat().st_mtime < RETRY_AFTER:
        return None

    result = None
    seen = {"answered": False}
    base = f"https://{host}/"
    candidates = []
    page = _get(base, MAX_PAGE, seen)
    if page:
        for tag in ICON_LINK.findall(page.decode("utf-8", "replace")):
            m = HREF.search(tag)
            if m:
                candidates.append(urljoin(base, m.group(1)))
    candidates.append(urljoin(base, "/favicon.ico"))
    candidates.sort(key=lambda u: u.lower().split("?")[0].endswith(".svg"))  # bitmaps first, SVG last
    for c in candidates[:4]:
        data = _get(c, MAX_BYTES, seen)
        result = data and _to_png(data)
        if result:
            break
    if result:
        png.write_bytes(result)
    elif seen["answered"]:  # the site answered but has no usable icon: don't ask again for a week
        none.write_text("no usable icon")
        log.info("No icon for %s", host)
    # otherwise (offline, timeout, 5xx): nothing cached, the next page view tries again
    return result
