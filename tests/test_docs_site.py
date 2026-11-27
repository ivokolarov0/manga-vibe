"""The website (demo/pages.py, demo/website.py): every doc it lists exists, every Markdown doc of the repo
is on it or deliberately only on GitHub, the links between docs point at files that are there, the
homepage draws the docs it says, and the addresses the site used to have still lead somewhere."""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "demo"))
from pages import DEV_PAGES, HOME, ON_GITHUB, ON_HOME, REDIRECTS, links  # noqa: E402
from website import write_site  # noqa: E402

DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
# what the site had before the homepage (#264): each must still be there or redirect
OLD_PATHS = ["docs/", "plugins/", "docs/install-windows/", "docs/install-mac/", "docs/install-linux/",
             "docs/install-synology/", "docs/install-docker/", "docs/phone/", "docs/away-from-home/",
             "docs/plugins/", "docs/troubleshooting/", "docs/qa/", "docs/plugin-guide/", "docs/developers/"]


def test_every_listed_doc_exists_once():
    sources = [src for src, _ in ON_HOME] + [src for src, *_ in DEV_PAGES] + ON_GITHUB
    assert len(set(sources)) == len(sources)
    for src in sources:
        assert (ROOT / src).is_file(), src
    paths = [HOME] + [path for _, path, _ in DEV_PAGES] + list(REDIRECTS)
    assert len(set(paths)) == len(paths)
    for path in paths:
        assert path.endswith("/") and not path.startswith("/"), path


def test_every_doc_is_on_the_site_or_on_github():
    placed = set(links()) | set(ON_GITHUB)
    for doc in DOCS:
        assert doc.relative_to(ROOT).as_posix() in placed, \
            f"put {doc.relative_to(ROOT)} on the site, or in ON_GITHUB, in demo/pages.py"


def test_links_between_docs_point_at_files_that_are_there():
    for doc in DOCS:
        text = re.sub(r"^```.*?^```", "", doc.read_text(encoding="utf-8"), flags=re.S | re.M)  # not in code
        for href in re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'(?:href|src)="([^"]+)"', text):
            if href.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:|^//", href):
                continue
            target = (doc.parent / urlsplit(href).path).resolve()
            assert target.exists(), f"{doc.relative_to(ROOT)} links to {href}, which isn't there"


def test_the_site_is_written(tmp_path):
    write_site(tmp_path)
    home = (tmp_path / HOME / "index.html").read_text(encoding="utf-8")
    assert "%BASE%" not in home and "%API_VERSION%" not in home and "/*MV_SITE*/" not in home
    # every doc the homepage stands for is drawn into it, with its id there
    for src, anchor in ON_HOME:
        if src == "README.md":
            continue
        assert f'data-src="{src}">' in home and f'id="{anchor}"' in home, src
    for src, path, _ in DEV_PAGES:
        page = (tmp_path / path / "index.html").read_text(encoding="utf-8")
        assert f'"src": "{src}"' in page and "%BASE%" not in page
        assert (tmp_path / path / "page.md").read_text(encoding="utf-8") == (ROOT / src).read_text(encoding="utf-8")
    # every icon the homepage uses is in demo/icons.svg, which is drawn into it
    for name in set(re.findall(r'href="#i-([a-z0-9-]+)"', home)):
        assert f'<symbol id="i-{name}"' in home, f"add the {name} icon to demo/icons.svg"
    for name in ("site.css", "site.js", "md.js", "icon.svg"):
        assert (tmp_path / "assets" / name).is_file()


def test_the_ui_kit_page_shows_each_piece_live(tmp_path):
    """The UI kit's page on the site has the app's own /kit page beside it, with relative addresses and the
    files it loads, so each piece shows live under its heading (demo/docs.html, kitPreviews)."""
    write_site(tmp_path)
    folder = tmp_path / next(path for src, path, _ in DEV_PAGES if src == "docs/UI-KIT.md")
    assert '"kit": "kit.html"' in (folder / "index.html").read_text(encoding="utf-8")
    kit = (folder / "kit.html").read_text(encoding="utf-8")
    assert "{{" not in kit and "{%" not in kit and 'id="i-star"' in kit  # rendered, with the app's icons
    refs = re.findall(r'(?:src|href)="(static/[^"]+)"', kit)
    assert {"static/css/main.css", "static/js/kit.js", "static/js/vendor/alpine.min.js"} <= set(refs)
    assert '"/static/' not in kit
    for ref in refs:
        assert (folder / ref).is_file(), ref
    css = (folder / "static" / "css" / "fonts.css").read_text(encoding="utf-8")
    for font in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        assert (folder / "static" / "fonts" / font).is_file(), font
    # one piece alone: what the frame asks for, with each piece the docs list (their ids are the slugs)
    assert "kit-embed" in kit and "mv-kit-height" in kit and "mv-kit-theme" in kit
    viewer = (folder / "index.html").read_text(encoding="utf-8")
    assert "kitPreviews(page)" in viewer and '"the-pieces"' in viewer


def test_old_addresses_still_work(tmp_path):
    write_site(tmp_path)
    for path in OLD_PATHS:
        assert (tmp_path / path / "index.html").is_file(), path
    for old, target in REDIRECTS.items():
        if "://" in target:
            continue
        page, _, anchor = target.partition("#")
        assert (tmp_path / page / "index.html").is_file(), target
        if anchor:
            assert f'id="{anchor}"' in (tmp_path / page / "index.html").read_text(encoding="utf-8"), target
