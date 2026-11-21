"""The docs site (demo/pages.py, demo/docs.html): every page it lists exists, every Markdown doc of the
repo is on it, and links between the docs point at files that are there."""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo"))
from pages import PAGES  # noqa: E402

DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def test_every_listed_page_exists_once():
    sources = [src for src, *_ in PAGES]
    paths = [path for _, path, *_ in PAGES]
    assert len(set(sources)) == len(sources) and len(set(paths)) == len(paths)
    for src in sources:
        assert (ROOT / src).is_file(), src
    for path in paths:
        assert path.endswith("/") and not path.startswith("/"), path


def test_every_doc_is_on_the_site():
    listed = {src for src, *_ in PAGES}
    for doc in DOCS:
        assert doc.relative_to(ROOT).as_posix() in listed, f"add {doc.relative_to(ROOT)} to demo/pages.py"


def test_links_between_docs_point_at_files_that_are_there():
    for doc in DOCS:
        text = re.sub(r"^```.*?^```", "", doc.read_text(encoding="utf-8"), flags=re.S | re.M)  # not in code
        for href in re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:href|src)="([^"]+)"', text):
            if href.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:|^//", href):
                continue
            target = (doc.parent / urlsplit(href).path).resolve()
            assert target.exists(), f"{doc.relative_to(ROOT)} links to {href}, which isn't there"


# demo/build.py runs the app when imported: read its DOC_CONFIG from the file
def _doc_config():
    m = re.search(r"^DOC_CONFIG = '(.+)'", (ROOT / "demo" / "build.py").read_text(encoding="utf-8"), re.M)
    return m.group(1)


def test_docs_html_has_the_config_line():
    assert _doc_config() in (ROOT / "demo" / "docs.html").read_text(encoding="utf-8")
