"""Build the online demo (GitHub Pages) from this checkout: `python demo/build.py [out_dir]`

There is no server behind the demo: it's static files. MangaVibe itself makes them, so the demo always
matches the code it's built from:

1. demo/library.py draws a made-up library (series, covers and pages: nobody's manga).
2. The app runs on it (FastAPI's TestClient: no web server, no metadata or update calls), set up through
   its own API the way an owner would: details, posters, favorites, reading progress.
3. The page (GET /), the static files, the plugins' scripts and the answers to the GETs the page makes
   are recorded into out_dir/rec/, listed in rec/manifest.json. Each plugin is switched once and back,
   so the demo can replay what switching it does (or why it can't be).
4. demo/sw.js, a service worker, answers the app's requests in the visitor's browser from that
   recording. What would change something is kept in the browser's memory only (plugin switches,
   settings, reading progress, favorites), or refused with a message (deleting, installing, uploading,
   editing). Nothing is saved anywhere.

The library's filters and sorting are recorded as the ids each filter value and each sort gives
(rec/list.json); sw.js combines them, so the demo has no copy of the library-query plugin's logic.
"""
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

TMP = Path(tempfile.mkdtemp(prefix="mangavibe-demo-"))
os.environ.update({
    "DATA_DIR": str(TMP / "data"),
    "LIBRARY_PATHS": f"{TMP / 'library' / 'Manga'},{TMP / 'library' / 'Comics'}",
    "RESCAN_HOURS": "0",
    "START_WORKERS": "0",   # no metadata lookups: the details come from demo/library.py
    "WATCH_FOLDERS": "0",
    "AUTO_UPDATE": "0",     # no update checks against GitHub
})

from library import SERIES, make_library  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.plugins import manager as plugins  # noqa: E402
from app.scanner import walker  # noqa: E402

# Query parameters that only bust caches or pick an image size: the demo has one copy of each image.
# sw.js drops the same ones (keep the two lists the same).
IGNORED_PARAMS = {"v", "w", "_"}
# Plugins the demo doesn't let you switch: Accounts would ask for a login nobody has, and Remote
# control needs a server to talk to.
NO_SWITCH = {"accounts": "Accounts can't be switched on in the demo: it would ask for a login.",
             "mcp": "Remote control needs a MangaVibe of your own to talk to."}


def key_of(method: str, url: str) -> str:
    """The recording's key for a request: method, path and the sorted query (sw.js: keyOf)."""
    parts = urlsplit(url)
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in IGNORED_PARAMS)
    return f"{method} {parts.path}" + (f"?{urlencode(query)}" if query else "")


def hide_paths(body: bytes) -> bytes:
    """The build's temporary folders as a Docker install shows them: the library under /manga, the
    data folder under /data, with / between folders also when built on Windows. The demo must not show
    where, or by whom, it was built."""
    text = body.decode("utf-8")
    for folder, shown in ((TMP / "library" / "Manga", "/manga"), (TMP / "library" / "Comics", "/comics"),
                          (TMP / "library", "/library"), (TMP / "data", "/data"), (TMP, "/tmp")):
        for form in (json.dumps(str(folder))[1:-1], str(folder)):
            start = 0
            while (i := text.find(form, start)) >= 0:
                end = i + len(form)
                m = re.match(r'(?:\\\\|\\|/)[^"<>\n]*', text[end:])  # the rest of the path
                rest = m.group(0) if m else ""
                new = shown + re.sub(r"\\\\|\\", "/", rest)
                text = text[:i] + new + text[end + len(rest):]
                start = i + len(new)
    return text.encode("utf-8")


def check_private(out: Path):
    """Stop the build when the builder's home folder or login name is anywhere in the demo."""
    import getpass
    words = {str(Path.home()), json.dumps(str(Path.home()))[1:-1]}
    if not os.environ.get("GITHUB_ACTIONS"):  # on GitHub it's "runner", a word the page may well use
        try:
            words.add(getpass.getuser())
        except Exception:
            pass
    words = {w.lower() for w in words if len(w) >= 3}
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".html", ".js", ".css", ".txt", ".svg"):
            data = f.read_bytes().lower()
            for w in words:
                if w.encode() in data:
                    raise SystemExit(f"{f.relative_to(out)} has the home folder or login name of whoever "
                                     "built the demo in it: hide_paths() missed a path")


def official_source(request):
    """MangaVibe's own plugin source (Settings → Plugins → Get plugins), read from this checkout's
    plugins.json instead of GitHub: no network needed, and it lists what this version lists."""
    import httpx
    if request.url.path.endswith("/plugins.json"):
        return httpx.Response(200, content=(ROOT / "plugins.json").read_bytes())
    return httpx.Response(404)


def ok(r):
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:300]}")
    return r


class Recorder:
    def __init__(self, client: TestClient, out: Path):
        self.c, self.out = client, out
        self.files = out / "rec" / "r"
        self.files.mkdir(parents=True, exist_ok=True)
        self.manifest: dict[str, dict] = {}

    def save(self, key, status, ctype, body: bytes):
        ext = mimetypes.guess_extension((ctype or "").split(";")[0].strip()) or ".bin"
        if ctype.startswith(("application/json", "text/")):
            body = hide_paths(body)
        name = hashlib.sha1(key.encode()).hexdigest()[:16] + ext
        (self.files / name).write_bytes(body)
        self.manifest[key] = {"f": name, "t": ctype, "s": status}

    def get(self, url, must=False):
        """Record GET url; returns the response (or None when it failed and must is False)."""
        key = key_of("GET", url)
        r = self.c.get(url)
        if must:
            ok(r)
        if r.status_code >= 500:
            print(f"  skipped {url}: {r.status_code}")
            return None
        if key not in self.manifest:  # the first answer is the one the demo starts with
            self.save(key, r.status_code, r.headers.get("content-type", "application/octet-stream"), r.content)
        return r

    def json(self, url, must=True):
        r = self.get(url, must)
        return r.json() if r is not None and r.status_code == 200 else None

    def write(self):
        (self.out / "rec" / "manifest.json").write_text(json.dumps(self.manifest, separators=(",", ":")),
                                                        encoding="utf-8")


def set_up(c: TestClient):
    """The demo library's details, posters, favorites and progress, through the app's own API."""
    by_folder = {}
    for item in ok(c.get("/api/series", params={"limit": 200})).json()["items"]:
        by_folder[ok(c.get(f"/api/series/{item['id']}")).json()["folder_name"]] = item["id"]
    for i, s in enumerate(SERIES):
        sid = by_folder[s["folder"]]
        ok(c.put(f"/api/series/{sid}/metadata", json={
            "authors": [{"name": "Demo Studio", "role": "Story & Art"}],
            "links": [{"site": "MangaVibe", "url": "https://github.com/ivokolarov0/manga-vibe"}],
            **s["meta"]}))
        with (TMP / "library" / f"_poster_{i}.webp").open("rb") as f:
            ok(c.post(f"/api/series/{sid}/poster/upload", files={"file": ("poster.webp", f, "image/webp")}))
        patch = {**({"favorite": True} if s["favorite"] else {}),
                 **({"reader_mode": "vertical"} if s["strip"] else {})}
        if patch:
            ok(c.patch(f"/api/series/{sid}", json=patch))
        books = ok(c.get(f"/api/series/{sid}/books")).json()
        books = books["books"] if isinstance(books, dict) else books
        for n, state in s["progress"].items():
            b = books[n - 1]
            body = {"page": b["page_count"] - 1, "completed": True} if state == "done" else {"page": state}
            ok(c.put(f"/api/books/{b['id']}/progress", json=body))
            time.sleep(0.01)  # distinct "last read" times, so Continue reading has an order
    # the details were typed in (no network here), so nothing was matched: say they were, as AniList
    # would have, instead of every series showing "No metadata"
    from app.models import Series
    from app.plugins.sdk import SessionLocal
    with SessionLocal() as db:
        for s in db.query(Series):
            s.match_status, s.match_confidence = "auto", 100
        db.commit()


def record_lists(rec: Recorder, scopes):
    """rec/list.json: what sw.js needs to answer GET /api/series for any filters and sort."""
    c = rec.c
    ids = lambda params: ok(c.get("/api/series", params={**params, "ids_only": "true"})).json()["ids"]  # noqa: E731
    schema = ok(c.get("/api/library/schema")).json()
    cards, facts = {}, {}
    for item in ok(c.get("/api/series", params={"limit": 200})).json()["items"]:
        cards[item["id"]] = item
        d = ok(c.get(f"/api/series/{item['id']}")).json()
        facts[item["id"]] = {
            "text": " ".join(str(x) for x in [d.get("folder_name"), d.get("title_english"), d.get("title_romaji"),
                                               d.get("title_native"), *(d.get("synonyms") or []),
                                               *[a.get("name") for a in d.get("authors") or []]] if x).lower(),
            "year": d.get("start_year"), "score": d.get("average_score"),
            "chapters": d.get("chapters_total"), "volumes": d.get("volumes_total")}
    # each filter value on its own: {param: {value: [ids]}}
    values: dict[str, dict[str, list]] = {}
    for f in schema["filters"]:
        if f["kind"] == "choice":
            for param in f["params"]:
                values[param] = {v: ids({param: v}) for v, _ in f.get("options", [])}
        elif f["kind"] == "toggle":
            for param in f["params"]:
                values[param] = {"1": ids({param: "1"})}
        elif f["kind"] == "letter":
            for param in f["params"]:
                values[param] = {ch: ids({param: ch}) for ch in "abcdefghijklmnopqrstuvwxyz#"}
        elif f["kind"] == "tags":
            names = [t["name"] for t in ok(c.get("/api/tags")).json()]
            include = f["params"][0]
            values[include] = {n: ids({include: n}) for n in names}
    sorts = {}
    for s in schema["sorts"]:
        key = s[0] if isinstance(s, (list, tuple)) else s["key"]
        sorts[key] = {o: ids({"sort": key, "order": o}) for o in ("asc", "desc")}
        sorts[key]["default"] = ids({"sort": key})
    scope_ids = {f"{k}={v}": ids({k: v}) for k, v in scopes}
    (rec.out / "rec" / "list.json").write_text(json.dumps({
        "cards": cards, "facts": facts, "values": values, "sorts": sorts, "scopes": scope_ids,
        "default_sort": schema.get("default_sort") or "title", "all": ids({}),
    }, separators=(",", ":")), encoding="utf-8")


def record_switches(rec: Recorder):
    """Switch each plugin once and back. rec/switches.json has, per plugin: what PUT /api/plugins/<id>
    answered (`s`, `body`), its /ui while it was on (`ui`: its markup and script for the page), and
    the page as it was put together with it switched (`page`, a file in rec/r/), for the plugins
    whose switch makes the app load the page again."""
    c, out = rec.c, {}
    default_page = c.get("/").text
    for p in ok(c.get("/api/plugins")).json()["plugins"]:
        pid, was = p["id"], p["enabled"]
        if pid in NO_SWITCH:
            out[pid] = {"s": 409, "body": {"detail": NO_SWITCH[pid]}}
            continue
        r = c.put(f"/api/plugins/{pid}", json={"enabled": not was})
        entry = out[pid] = {"s": r.status_code, "body": r.json()}
        if r.status_code != 200:
            continue
        if not was:
            ui = c.get(f"/api/plugins/{pid}/ui")
            if ui.status_code == 200:
                entry["ui"] = ui.json()
        page = c.get("/").text
        if page != default_page:
            key = f"PAGE {pid}"
            rec.save(key, 200, "text/html; charset=utf-8", page.encode())
            entry["page"] = rec.manifest.pop(key)["f"]
        replaced = [other for other in r.json().get("stopped", []) if other != pid]
        if not was and replaced:
            # it took over a job from them, maybe one MangaVibe needs (the sidebar's Navigation), so it
            # can't just be switched off again: switching them back on is what replaces it
            for other in replaced:
                ok(c.put(f"/api/plugins/{other}", json={"enabled": True}))
            if any(q["id"] == pid and q["enabled"] for q in ok(c.get("/api/plugins")).json()["plugins"]):
                ok(c.put(f"/api/plugins/{pid}", json={"enabled": False}))
            continue
        r2 = c.put(f"/api/plugins/{pid}", json={"enabled": was})
        if r2.status_code != 200:
            raise SystemExit(f"Couldn't switch {pid} back: {r2.text}")
        # switching it back on doesn't bring back what it replaced: switch those on again
        for other in replaced:
            ok(c.put(f"/api/plugins/{other}", json={"enabled": True}))
    if c.get("/").text != default_page:
        raise SystemExit("The plugins aren't as they were after switching each one and back")
    (rec.out / "rec" / "switches.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")


def record(rec: Recorder):
    c = rec.c
    page = rec.get("/", must=True).text
    for src in re.findall(r'src="(/plugins/[^"]+\.js)"', page):
        rec.get(src, must=True)
    for url in ("/custom.css", "/manifest.json", "/api/settings", "/api/plugins", "/api/library/schema",
                "/api/libraries", "/api/providers", "/api/metadata/providers", "/api/metadata/status",
                "/api/scan/status", "/api/update", "/api/update/versions", "/api/health",
                "/api/plugins/libraries/api/shelves", "/api/plugins/libraries/api/access",
                "/api/plugins/saved-searches/api/searches", "/api/plugins/accounts/api/me",
                "/api/plugins/available"):
        rec.get(url)
    for p in rec.json("/api/plugins")["plugins"]:
        rec.get(f"/api/plugins/{p['id']}/ui")
    shelves = rec.json("/api/plugins/libraries/api/shelves", must=False) or []
    scopes = [("shelf", str(s["id"])) for s in shelves]
    for scope in [None, *scopes]:
        q = f"{scope[0]}={scope[1]}&" if scope else ""
        for url in (f"/api/tags?{q}", f"/api/continue-reading?{q}", f"/api/attention/count?{q}",
                    f"/api/attention?{q}", f"/api/attention?{q}ignored=true"):
            rec.get(url, must=True)
    for sid in ok(c.get("/api/series", params={"ids_only": "true"})).json()["ids"]:
        for url in (f"/api/series/{sid}", f"/api/series/{sid}/books", f"/api/series/{sid}/books?all=1",
                    f"/api/series/{sid}/badges", f"/api/series/{sid}/badges?all=1",
                    f"/api/series/{sid}/poster", f"/api/series/{sid}/poster?size=sm"):
            rec.get(url, must=True)
        books = c.get(f"/api/series/{sid}/books?all=1").json()
        for b in books["books"] if isinstance(books, dict) else books:
            rec.get(f"/api/books/{b['id']}")
            rec.get(f"/api/books/{b['id']}/progress")
            rec.get(f"/api/books/{b['id']}/thumbnail")
            for n in range(b.get("page_count") or 0):
                rec.get(f"/api/books/{b['id']}/pages/{n}", must=True)
    record_lists(rec, scopes)
    record_switches(rec)
    # every plugin's script and style, for switching one on in the demo
    for p in plugins.plugins.values():
        if p.js:
            rec.get(f"/plugins/{p.id}.js")
        if p.css:
            rec.get(f"/plugins/{p.id}.css")


DOC_CONFIG = '/*MV_DOC*/{ src: "docs/PLUGIN-REFERENCE.md", base: "../", pages: [] }'  # in demo/docs.html


def write_docs(out: Path):
    """The docs pages (demo/pages.py): demo/docs.html at each page's path, told which Markdown file it
    shows and where the site's root is, with that file next to it as page.md. The README's pictures go
    to media/."""
    from pages import PAGES
    viewer = (HERE / "docs.html").read_text(encoding="utf-8")
    if DOC_CONFIG not in viewer:
        raise SystemExit("demo/docs.html no longer has the MV_DOC line build.py fills in")
    pages = [{"src": src, "path": path, "title": title, "group": group} for src, path, title, group in PAGES]
    for page in pages:
        folder = out / page["path"]
        folder.mkdir(parents=True, exist_ok=True)
        base = "../" * page["path"].count("/")
        config = json.dumps({"src": page["src"], "base": base, "pages": pages}, ensure_ascii=False)
        (folder / "index.html").write_text(viewer.replace(DOC_CONFIG, config), encoding="utf-8")
        shutil.copy2(ROOT / page["src"], folder / "page.md")
    shutil.copytree(ROOT / "docs" / "media", out / "media")


def main():
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE / "site"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    make_library(TMP / "library")
    import httpx

    from app import plugin_sources
    plugin_sources.transport = httpx.MockTransport(official_source)
    with TestClient(app) as c:
        while walker.status["running"]:
            time.sleep(0.05)
        walker.scan_all()
        set_up(c)
        rec = Recorder(c, out)
        record(rec)
        rec.write()
    shutil.copytree(ROOT / "app" / "web" / "static", out / "rec" / "static")
    for name in ("index.html", "sw.js", "demo.js", "demo.css"):
        shutil.copy2(HERE / name, out / name)
    write_docs(out)
    (out / ".nojekyll").write_text("")
    check_private(out)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Demo built in {out}: {len(rec.manifest)} recorded answers, {size / 1e6:.1f} MB")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
