"""The website next to the demo (demo/pages.py says what's on it): the homepage at docs/, the developer
pages, redirects from the addresses the site used to have, and the files they share. demo/build.py
calls write_site(); `python demo/website.py [out_dir]` writes only the website, to look at it without
building the demo (serve out_dir's parent, as for the demo, see docs/developers.md)."""
import json
import re
import shutil
import sys
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from pages import DEV_PAGES, HOME, ON_HOME, REDIRECTS, links  # noqa: E402

# The line each page has that write_site() fills in (home.html, docs.html)
SITE_CONFIG = re.compile(r"/\*MV_SITE\*/\{[^}]*\}")
# A doc drawn into the homepage
MARKDOWN = re.compile(r'<script type="text/markdown" data-src="([^"]+)"></script>')
ASSETS = ("site.css", "site.js", "md.js")


def api_version() -> int:
    """The plugin API's version, read from the code (importing the app would start it)."""
    text = (ROOT / "app" / "plugins" / "__init__.py").read_text(encoding="utf-8")
    return int(re.search(r"^API_VERSION = (\d+)", text, re.M).group(1))


def fill(template: str, path: str, **config) -> str:
    """A page at `path` (from the site's root): its way back to the root, and its MV_SITE."""
    base = "../" * path.count("/")
    if not SITE_CONFIG.search(template):
        raise SystemExit(f"a page for {path} no longer has the /*MV_SITE*/ line website.py fills in")
    data = json.dumps({"base": base, "links": links(), **config}, ensure_ascii=False).replace("</", "<\\/")
    return SITE_CONFIG.sub(lambda _: data, template, count=1).replace("%BASE%", base)


def home_page() -> str:
    on_home = {src for src, _ in ON_HOME}
    html = fill((HERE / "home.html").read_text(encoding="utf-8"), HOME)

    def embed(m):
        src = m.group(1)
        if src not in on_home:
            raise SystemExit(f"home.html draws {src}: add it to ON_HOME in demo/pages.py")
        text = (ROOT / src).read_text(encoding="utf-8")
        if "</script" in text.lower():
            raise SystemExit(f"{src} has </script in it, which can't go inside the homepage's <script>")
        return f'<script type="text/markdown" data-src="{src}">\n{text}</script>'

    icons = (HERE / "icons.svg").read_text(encoding="utf-8")
    if "<!--MV_ICONS-->" not in html:
        raise SystemExit("home.html no longer has the <!--MV_ICONS--> line website.py puts the icons in")
    html = html.replace("<!--MV_ICONS-->", icons, 1)
    return MARKDOWN.sub(embed, html).replace("%API_VERSION%", str(api_version()))


def redirect_page(target: str, path: str) -> str:
    """A page that sends the visitor on to `target` (a path from the site's root, or a full address),
    with the #part of the old address: the homepage's parts have the doc's id in front of it."""
    to = target if "://" in target else "../" * path.count("/") + target
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>This page moved</title>
<link rel="canonical" href="{escape(to)}">
<script>
  const to = {json.dumps(to)}, part = location.hash.slice(1);
  location.replace(!part ? to : to.includes("#") && !to.includes("://") ? `${{to}}-${{part}}` : `${{to.split("#")[0]}}#${{part}}`);
</script>
<meta http-equiv="refresh" content="0; url={escape(to)}">
<p>This page moved: <a href="{escape(to)}">{escape(to)}</a></p>
</html>
"""


def write_site(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    for name in ASSETS:
        shutil.copy2(HERE / name, out / "assets" / name)
    shutil.copy2(ROOT / "app" / "web" / "static" / "icons" / "icon.svg", out / "assets" / "icon.svg")  # the app's logo
    if (out / "media").exists():
        shutil.rmtree(out / "media")
    shutil.copytree(ROOT / "docs" / "media", out / "media")

    (out / HOME).mkdir(parents=True, exist_ok=True)
    (out / HOME / "index.html").write_text(home_page(), encoding="utf-8")

    viewer = (HERE / "docs.html").read_text(encoding="utf-8")
    pages = [{"src": src, "path": path, "title": title} for src, path, title in DEV_PAGES]
    for page in pages:
        folder = out / page["path"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(fill(viewer, page["path"], src=page["src"], pages=pages), encoding="utf-8")
        shutil.copy2(ROOT / page["src"], folder / "page.md")

    for old, target in REDIRECTS.items():
        folder = out / old
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(redirect_page(target, old), encoding="utf-8")


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "site"
    write_site(target)
    print(f"Website written to {target}")
