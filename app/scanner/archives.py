"""Read pages from archives and image folders without extracting to disk.

Everything here only reads. The library is mounted read-only and we never open
a file for writing.

- The real archive type is detected from the file's first bytes, so a ZIP named
  .rar or a RAR named .cbz still opens.
- ZIPs that Python can't decompress (Deflate64, PPMd) and .7z/.cb7 are read with
  the 7-Zip program; RARs fall back to 7-Zip too if `unrar` fails.
- Archives inside archives (a volume zip holding chapter zips) are read one level
  deep. Their pages are named "inner.zip!/001.jpg".
"""
import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from collections import OrderedDict, Counter
from functools import lru_cache
from urllib.parse import unquote
from xml.etree import ElementTree
from pathlib import Path, PurePosixPath

from app.scanner.parser import natural_key

IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"}
ARCHIVE_FORMATS = {".cbz": "zip", ".zip": "zip", ".cbr": "rar", ".rar": "rar",
                   ".cb7": "7z", ".7z": "7z", ".epub": "epub", ".pdf": "pdf"}
SUPPORTED = {"zip", "rar", "7z", "folder", "epub", "pdf"}
TEXT_EPUB = "Text EPUB"  # start of the message for light novels (shown under "Not supported")
PDF_WIDTH = 1600         # PDF pages are rendered this wide (px)
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg", ".jfif": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
        ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff"}
WEB_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/bmp"}
NESTED = "!/"  # separator between an inner archive and a page inside it
MAGIC = [(b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"Rar!\x1a\x07", "rar"),
         (b"7z\xbc\xaf\x27\x1c", "7z"), (b"%PDF", "pdf")]
SKIP_PARTS = {"__MACOSX", "@eaDir"}


class ArchiveError(Exception):
    """A book that can't be read. The message is meant for the owner (plain English)."""


class UnsupportedFormat(ArchiveError):
    pass


# ---------------------------------------------------------------- helpers

def _find_7zip():
    for name in ("7zz", "7z"):
        if shutil.which(name):
            return shutil.which(name)
    win = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    return str(win) if win.exists() else None


SEVENZIP = _find_7zip()


def is_image(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    if any(part in SKIP_PARTS or part.startswith(".") for part in p.parts):
        return False
    return p.suffix.lower() in IMAGE_EXT


def is_archive_name(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    if any(part in SKIP_PARTS or part.startswith(".") for part in p.parts):
        return False
    return ARCHIVE_FORMATS.get(p.suffix.lower()) in ("zip", "rar", "7z")


def mime_for(name: str) -> str:
    return MIME.get(PurePosixPath(name.replace("\\", "/")).suffix.lower(), "application/octet-stream")


def sniff_bytes(head: bytes):
    for magic, fmt in MAGIC:
        if head.startswith(magic):
            return fmt
    return None


def sniff(path: Path, ext_format=None):
    """Real format of a book file from its first bytes; falls back to the extension."""
    if path.is_dir():
        return "folder"
    if ext_format == "epub":
        return "epub"  # an epub is a zip, but needs its own reader
    try:
        with open(path, "rb") as f:
            found = sniff_bytes(f.read(8))
    except OSError:
        found = None
    return found or ext_format


def book_format(path: Path):
    """'zip', 'rar', ... for archive files, 'folder' for image folders, else None."""
    if path.is_dir():
        return "folder" if any(is_image(e.name) for e in os.scandir(path) if e.is_file()) else None
    return ARCHIVE_FORMATS.get(path.suffix.lower())


def _sorted_pages(names):
    return sorted((n for n in names if is_image(n)), key=natural_key)


# ---------------------------------------------------------------- 7-Zip program

def _7z_run(args, timeout=120):
    if not SEVENZIP:
        raise ArchiveError("This file needs the 7-Zip program, which is not installed")
    r = subprocess.run([SEVENZIP, *args], capture_output=True, timeout=timeout)
    if r.returncode not in (0, 1):  # 1 = warnings
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ArchiveError("7-Zip could not read it: " + (err[-1] if err else f"exit code {r.returncode}"))
    return r.stdout


def _7z_list(path):
    out = _7z_run(["l", "-slt", "-ba", "-sccUTF-8", path]).decode("utf-8", "replace")
    names = []
    for block in out.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" in fields and fields.get("Folder") != "+" and not fields.get("Attributes", "").startswith("D"):
            names.append(fields["Path"].replace("\\", "/"))
    return names


def _7z_read(path, name):
    return _7z_run(["x", "-so", "-spd", "-sccUTF-8", path, "--", name])


# ---------------------------------------------------------------- one archive level

def _entries(path, fmt):
    """All file names inside a book (not only images)."""
    if fmt == "epub":
        fmt = "zip"
    if fmt == "pdf":
        return [f"{i + 1:04d}.jpg" for i in range(_pdf_count(path))]
    if fmt == "folder":
        return [e.name for e in os.scandir(path) if e.is_file()]
    if fmt == "zip":
        try:
            with zipfile.ZipFile(path) as z:
                return [i.filename for i in z.infolist() if not i.is_dir()]
        except zipfile.BadZipFile:
            if SEVENZIP:
                try:
                    return _7z_list(path)
                except ArchiveError:
                    pass
            raise ArchiveError("The ZIP file is damaged or incomplete (maybe it didn't finish "
                               "downloading). Try getting a new copy of it.")
    if fmt == "rar":
        try:
            import rarfile
            with rarfile.RarFile(path) as r:
                return [i.filename for i in r.infolist() if not i.is_dir()]
        except Exception as e:
            if SEVENZIP:
                try:
                    return _7z_list(path)
                except ArchiveError:
                    pass
            raise ArchiveError(f"The RAR file is damaged or can't be opened ({type(e).__name__}: {e})")
    if fmt == "7z":
        return _7z_list(path)
    raise UnsupportedFormat(f"{(fmt or 'unknown').upper()} files are not supported yet")


def _read(path, fmt, name) -> bytes:
    if fmt == "epub":
        fmt = "zip"
    if fmt == "pdf":
        return _pdf_page(path, int(PurePosixPath(name).stem) - 1)
    if fmt == "folder":
        target = (Path(path) / name).resolve()
        if target.parent != Path(path).resolve():
            raise ValueError("bad page name")
        return target.read_bytes()
    if fmt == "zip":
        try:
            with zipfile.ZipFile(path) as z:
                return z.read(name)
        except (NotImplementedError, zipfile.BadZipFile):
            # Deflate64 / PPMd compression (made by Windows/WinZip for big files): 7-Zip reads them
            return _7z_read(path, name)
    if fmt == "rar":
        try:
            import rarfile
            with rarfile.RarFile(path) as r:
                return r.read(name)
        except Exception:
            if SEVENZIP:
                return _7z_read(path, name)
            raise
    if fmt == "7z":
        return _7z_read(path, name)
    raise UnsupportedFormat(fmt)


# ---------------------------------------------------------------- nested archives

class _InnerCache:
    """The last few inner archives (bytes), so turning pages doesn't re-extract them.
    Kept small: it stays in RAM, and a NAS may only have 1-2 GB."""

    def __init__(self, max_bytes=48 * 1024 * 1024):
        self.items, self.max, self.lock = OrderedDict(), max_bytes, threading.Lock()

    def get(self, key, load):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        data = load()
        with self.lock:
            self.items[key] = data
            while sum(len(v) for v in self.items.values()) > self.max and len(self.items) > 1:
                self.items.popitem(last=False)
        return data


_inner = _InnerCache()


def _inner_bytes(path, fmt, inner_name):
    key = (path, os.path.getmtime(path), inner_name)
    return _inner.get(key, lambda: _read(path, fmt, inner_name))


def _with_inner(data, fn):
    """Run fn(inner_path_or_file, inner_fmt) on an inner archive given as bytes."""
    ifmt = sniff_bytes(data[:8]) or "zip"
    if ifmt == "zip":
        try:
            return fn(io.BytesIO(data), "zip-bytes")
        except (NotImplementedError, zipfile.BadZipFile):
            pass  # fall through to a temp file + 7-Zip
    with tempfile.NamedTemporaryFile(suffix="." + ifmt, delete=False) as t:
        t.write(data)
    try:
        return fn(t.name, "7z" if ifmt == "zip" else ifmt)
    finally:
        os.unlink(t.name)


def _inner_entries(src, ifmt):
    if ifmt == "zip-bytes":
        with zipfile.ZipFile(src) as z:
            return [i.filename for i in z.infolist() if not i.is_dir()]
    return _entries(src, ifmt)


def _inner_read(name):
    def fn(src, ifmt):
        if ifmt == "zip-bytes":
            with zipfile.ZipFile(src) as z:
                return z.read(name)
        return _read(src, ifmt, name)
    return fn


# ---------------------------------------------------------------- public API

# ---------------------------------------------------------------- EPUB

_IMG_REF = re.compile(r"<(?:img|image)\b[^>]*?\b(?:src|xlink:href|href)\s*=\s*[\"']([^\"']+)", re.I)
_BODY = re.compile(r"<body\b.*?>(.*)</body>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _epub_pages(path):
    """Images of an EPUB in reading order (the book's spine). Raises for text-only books."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            container = ElementTree.fromstring(z.read("META-INF/container.xml"))
            opf_path = container.find(".//{*}rootfile").get("full-path")
            opf = ElementTree.fromstring(z.read(opf_path))
            base = posixpath.dirname(opf_path)
            join = lambda d, href: posixpath.normpath(posixpath.join(d, unquote(href.split("#")[0])))
            manifest = {i.get("id"): (join(base, i.get("href")), i.get("media-type") or "")
                        for i in opf.findall(".//{*}manifest/{*}item")}
            spine = [manifest[r.get("idref")] for r in opf.findall(".//{*}spine/{*}itemref")
                     if r.get("idref") in manifest]
            images, text_pages = [], 0
            for href, media in spine:
                if media.startswith("image/"):
                    images.append(href)
                    continue
                if href not in names:
                    continue
                doc = z.read(href).decode("utf-8", "replace")
                images += [join(posixpath.dirname(href), src) for src in _IMG_REF.findall(doc)]
                body = _BODY.search(doc)
                if len(_TAGS.sub("", body.group(1) if body else doc).strip()) > 400:
                    text_pages += 1
    except (KeyError, AttributeError, ElementTree.ParseError):
        # not a well-formed EPUB: fall back to all images in natural order
        with zipfile.ZipFile(path) as z:
            return _sorted_pages(z.namelist())
    seen, pages = set(), []
    for img in images:
        if img in names and img not in seen and is_image(img):
            seen.add(img)
            pages.append(img)
    if not pages or text_pages > len(pages):
        raise UnsupportedFormat(f"{TEXT_EPUB} (a novel, not a comic). A reader for text books "
                                "comes in a later version.")
    return pages


# ---------------------------------------------------------------- PDF

def _pdf_open(path):
    import pymupdf  # PyMuPDF renders PDF pages to images
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise ArchiveError(f"The PDF file is damaged or can't be opened ({e})")
    if doc.needs_pass:
        doc.close()
        raise ArchiveError("The PDF is password protected")
    return doc


def _pdf_count(path):
    with _pdf_open(path) as doc:
        return doc.page_count


def _pdf_page(path, index):
    import pymupdf
    with _pdf_open(path) as doc:
        page = doc[index]
        zoom = max(1.0, min(4.0, PDF_WIDTH / max(page.rect.width, 1)))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=88)


@lru_cache(maxsize=256)
def _pages_cached(path: str, fmt: str, mtime: float):
    if fmt == "epub":
        return tuple(_epub_pages(path))
    if fmt == "pdf":
        return tuple(_entries(path, "pdf"))
    entries = _entries(path, fmt)
    pages = [n for n in entries if is_image(n)]
    if fmt != "folder":
        for inner in (n for n in entries if is_archive_name(n)):
            data = _inner_bytes(path, fmt, inner)
            try:
                names = _with_inner(data, _inner_entries)
            except Exception:
                continue  # a broken inner archive: skip it, keep the rest
            pages += [f"{inner}{NESTED}{n}" for n in names if is_image(n)]
    pages = tuple(sorted(pages, key=natural_key))
    if not pages:
        raise ArchiveError(_no_images_message(entries))
    return pages


def _no_images_message(entries):
    if not entries:
        return "The archive is empty"
    kinds = Counter((PurePosixPath(n.replace("\\", "/")).suffix.lower() or "(no extension)") for n in entries)
    summary = ", ".join(f"{c} × {k}" for k, c in kinds.most_common(5))
    return f"No pictures found in this file. It contains: {summary} (e.g. '{entries[0]}')"


def list_pages(path: str, fmt: str):
    """Image entries in reading order (natural sort of their path). Raises ArchiveError."""
    return list(_pages_cached(path, fmt, os.path.getmtime(path)))


def list_entries(path: str, fmt: str):
    """All file names inside a book, e.g. to find ComicInfo.xml."""
    return _entries(path, fmt)


def read_entry(path: str, fmt: str, name: str) -> bytes:
    """Raw bytes of one entry (a page, or e.g. ComicInfo.xml). Handles 'inner.zip!/page.jpg'."""
    if NESTED in name and fmt != "folder":
        inner, rest = name.split(NESTED, 1)
        return _with_inner(_inner_bytes(path, fmt, inner), _inner_read(rest))
    return _read(path, fmt, name)


def read_page(path: str, fmt: str, index: int):
    """(bytes, mime) of page number `index` (0-based)."""
    pages = list_pages(path, fmt)
    if not 0 <= index < len(pages):
        raise IndexError(index)
    name = pages[index]
    return read_entry(path, fmt, name), mime_for(name)
