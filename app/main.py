"""MangaVibe entry point: `uvicorn app.main:app`."""
import hashlib
import logging
import mimetypes
import re
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers

from app import activity, config, db as database, i18n, plugin_updates, updater
from app.api import attention, backup, books, edit, series, system, update
from app.lanes import QuickLane
from app.metadata import worker as metadata_worker
from app.plugins import manager as plugins, web as plugin_web
from app.posters import MAX_UPLOAD
from app.scanner import walker, watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("mangavibe")

# slim Linux images have no /etc/mime.types: without this, .webp covers are sent as octet-stream
for _type, _ext in (("image/webp", ".webp"), ("image/avif", ".avif"), ("application/manifest+json", ".webmanifest")):
    mimetypes.add_type(_type, _ext)


def _periodic_rescan():
    while True:
        time.sleep(config.RESCAN_HOURS * 3600)
        walker.start_scan_in_background()


@asynccontextmanager
async def lifespan(app):
    log.info("Starting MangaVibe. Data folder: %s, libraries: %s", config.DATA_DIR,
             ", ".join(config.LIBRARY_PATHS))
    database.init_db()
    with database.SessionLocal() as s:
        walker.ensure_libraries(s, config.LIBRARY_PATHS)
        walker.fill_numbering(s)
        plugins.load()
        plugins.load_switches(s)
        plugins.start(s)  # e.g. libraries: every folder gets one, CLEAR_LIBRARY_PASSWORD
        plugins.log_load_errors()
        log_update_finished(s)
    if config.START_WORKERS:
        metadata_worker.start()
    walker.start_scan_in_background()
    watcher.start()  # changed series are rescanned within seconds; the periodic rescan catches the rest
    if config.RESCAN_HOURS > 0:
        threading.Thread(target=_periodic_rescan, daemon=True, name="rescan").start()
    updater.load_cached()
    if updater.supported():
        threading.Thread(target=updater.daily_loop, daemon=True, name="update-check").start()
    if config.AUTO_UPDATE and config.START_WORKERS:  # AUTO_UPDATE=0 turns off all update checks
        threading.Thread(target=plugin_updates.daily_loop, daemon=True, name="plugin-updates").start()
    updater.mark_started()
    yield


RUNNING_VERSION = "running_version"  # the version the app started with last time: {sha, version}


def log_update_finished(db):
    """Another version runs than at the last start (an update, going back, a new Docker image): the
    activity log says from which to which."""
    try:
        now = updater.current()
        mine = {"sha": now.get("sha"), "version": now.get("version")}
        if not mine["sha"]:
            return  # not a build that knows its version (running from a checkout)
        before = database.get_setting(db, RUNNING_VERSION)
        if before == mine:
            return
        if before and before.get("sha") != mine["sha"]:
            activity.log("update.finished", mine["version"] or mine["sha"][:7],
                         details={"from": before.get("version") or (before.get("sha") or "")[:7],
                                  "to": mine["version"] or mine["sha"][:7]})
        database.set_setting(db, RUNNING_VERSION, mine)
        db.commit()
    except Exception:  # noqa: BLE001 - only the log's business: never stop the app starting
        log.exception("Could not note the running version")


class _TooLarge(Exception):
    pass


class UploadSizeLimit:
    """Refuse oversized poster uploads before the multipart body is parsed and spooled to disk
    (the handler's own size check only runs after Starlette has stored the whole upload)."""

    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].endswith("/poster/upload"):
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length")
        try:
            if length is not None and int(length) > self.limit:
                return await self._reject(send)
        except ValueError:
            return await self._reject(send)
        received, too_large, replied = 0, False, False

        async def limited_receive():
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:  # no or wrong Content-Length: count as it arrives
                    too_large = True
                    raise _TooLarge()      # stop reading here; nothing more is spooled
            return message

        async def guarded_send(message):
            # FastAPI turns the error above into a 400 "error parsing the body": answer 413 instead
            nonlocal replied
            if too_large:
                if not replied:
                    replied = True
                    await self._reject(send)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _TooLarge:
            if not replied:
                await self._reject(send)

    @staticmethod
    async def _reject(send):
        body = b'{"detail":"The image is larger than 10 MB"}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class CrossSiteGuard:
    """Refuses changes (POST, PUT, PATCH, DELETE) that another website's page sends. Without accounts
    nothing else stops them: any page open in a browser on the home network could otherwise post to
    MangaVibe, e.g. add a plugin source and install a plugin from it, or import a backup over the
    database (a fetch() in "no-cors" mode needs no permission, and a body without a Content-Type is
    read as JSON). The browser says where a request comes from (Sec-Fetch-Site; older browsers: Origin).
    Requests without either, from scripts, the Android app's own code or the server itself, pass."""

    SAFE = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in self.SAFE and not scope.get("mangavibe.internal"):
            headers = Headers(scope=scope)
            site = headers.get("sec-fetch-site")
            if site == "cross-site" or (site is None and self.foreign_origin(headers)):
                detail = {"message": "Refused: this request came from another website"}
                return await JSONResponse({"detail": detail}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)

    @staticmethod
    def foreign_origin(headers) -> bool:
        """Whether the Origin header names another site than the one asked (a browser without
        Sec-Fetch-Site). Behind a reverse proxy the address asked can be in X-Forwarded-Host."""
        origin = headers.get("origin")
        if not origin:
            return False
        host = urlsplit(origin).netloc.lower()
        asked = {h.strip().lower() for h in (headers.get("host", ""), *headers.get("x-forwarded-host", "").split(","))}
        return origin == "null" or host not in asked


class RememberAddress:
    """Plugins build links back to the app (e.g. a login callback) from the address in use.
    A plain ASGI middleware: @app.middleware("http") wrapped every response (every thumbnail and
    page) in an extra task and stream for this one line."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # not the server's own calls (the MCP plugin calls the API on someone's behalf): no address there
        if scope["type"] == "http" and scope["path"].startswith("/api/") and not scope.get("mangavibe.internal"):
            plugins.base_url = str(Request(scope).base_url).rstrip("/")
        return await self.app(scope, receive, send)


class Language:
    """Every request runs in the language of the person asking (app/i18n.py, issue #263): their Settings →
    Display → Language, else the browser's. So what the server says (book labels, error messages, plugin
    descriptions) can use t(). Inside the guards, after the accounts plugin knows who is asking. Not for the
    server's own calls on someone's behalf (an assistant: it gets the app's words, English), nor for
    files (static files, page images and thumbnails have no words)."""

    SKIP = re.compile(r"^/static/|^/api/books/\d+/(pages|thumbnail)|^/api/series/\d+/poster|^/api/links/\d+/icon")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("mangavibe.internal") or self.SKIP.match(scope["path"]):
            return await self.app(scope, receive, send)
        # worked out when something is translated, not for every request (saving progress, polling a
        # scan's status, a plugin's script): finding the person's setting opens the database
        request = Request(scope)
        with i18n.using(i18n.Lazy(lambda: request_language(request))):
            return await self.app(scope, receive, send)


def request_language(request: Request) -> str:
    """The language for this request: the person's saved one, else the browser's (Accept-Language)."""
    try:
        with database.SessionLocal() as db:
            saved = str(system.personal_setting(db, "language"))
    except Exception:  # noqa: BLE001 - e.g. before the database is ready: the browser's language
        saved = None
    return i18n.pick_language(saved, request.headers.get("accept-language"))


app = FastAPI(title="MangaVibe", lifespan=lifespan)


@app.exception_handler(StarletteHTTPException)
async def translated_error(request: Request, exc: StarletteHTTPException):
    """An error's message in the language of the person asking (#263): HTTPException(404, "…") texts are in
    messages.pot (python -m app.i18n finds them); one with a {"message": …} keeps its other fields. A text
    that isn't a known one (with values in it) is shown as it is."""
    detail = exc.detail
    if isinstance(detail, str):
        detail = i18n.t(detail)
    elif isinstance(detail, dict) and isinstance(detail.get("message"), str):
        detail = {**detail, "message": i18n.t(detail["message"])}
    return await http_exception_handler(request, StarletteHTTPException(exc.status_code, detail, exc.headers))


# innermost: once the guards let a request through, the quick ones queue for a slot (app/lanes.py)
app.add_middleware(QuickLane)
app.add_middleware(Language)
app.add_middleware(UploadSizeLimit, limit=MAX_UPLOAD + 64 * 1024)  # + room for the multipart headers
app.add_middleware(RememberAddress)
# what a request does is the person's (or key's) in the activity log, not System's (app/activity.py)
app.add_middleware(activity.Requests)
# the plugins' own guards (Plugin.middleware), e.g. libraries: a locked library's series, books and pages
app.add_middleware(plugin_web.PluginMiddleware)
# before everything else (but compression): a change another website sends goes no further
app.add_middleware(CrossSiteGuard)
# Outermost, so it compresses the final response. The JS/CSS ship unminified; gzip makes them
# ~75% smaller over the wire. Page images are on Starlette's skip list (already compressed).
# Level 6 instead of the default 9: nearly the same size, much less CPU on a small NAS.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)


app.include_router(system.router)
app.include_router(series.router)
app.include_router(books.router)
app.include_router(attention.router)
app.include_router(edit.router)
app.include_router(update.router)
app.include_router(backup.router)
# a plugin's own API (Plugin.router), e.g. the built-in saved searches
app.mount("/api/plugins/{plugin_id}/api", plugin_web.PluginApi())

class StaticNoCache(StaticFiles):
    """The browser re-checks static files on every load (a quick 304 when unchanged),
    so a new version of the app shows up without ?v= numbers in the HTML."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", StaticNoCache(directory=config.STATIC_DIR), name="static")


SAFE_COOKIE = "mv_safe"  # set by /?safe=1: the page's styles leave out the owner's plugins and custom.css


@app.get("/custom.css")
def custom_css(request: Request):
    """Plugin CSS, then the owner's /data/custom.css. Served unlayered so it overrides built-in styles.
    In safe mode only the built-in plugins' CSS (main.css imports this file, so the page can't leave
    it out: the cookie the safe page set says so)."""
    safe = request.cookies.get(SAFE_COOKIE) == "1"
    parts = []
    for p in plugin_web.page_plugins(safe):
        if p.css:
            parts.append(f"/* plugin: {p.id} */\n{p.css.strip()}")
    if config.CUSTOM_CSS.exists() and not safe:
        parts.append("/* custom.css */\n" + config.CUSTOM_CSS.read_text(encoding="utf-8", errors="replace"))
    return checked_text(request, "\n\n".join(parts), "text/css")


def checked_text(request: Request, body: str, media_type: str) -> Response:
    """A script or style made by the server (plugins', custom.css). The browser checks it again on every
    load (no-cache), and gets 304 Not Modified without the body while it hasn't changed: a page load used
    to download every plugin's script again."""
    etag = '"' + hashlib.sha1(body.encode()).hexdigest()[:20] + '"'
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    sent = {t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")}
    if etag in sent:  # a proxy that compresses may have made it weak (W/"...")
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)


@app.get("/manifest.json")
def manifest():
    return FileResponse(config.STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/")
def index(request: Request, safe: str = ""):
    """The app's page, with the switched-on plugins' markup and scripts (app/plugins/web.py).
    /?safe=1: safe mode, without the owner's own plugins in the page (only the built-in ones), so a
    plugin that breaks the page can still be switched off in Settings -> Plugins."""
    on = safe not in ("", "0", "false")
    lang = i18n.current()  # the person's language, else the browser's (the Language middleware, #263)
    page = plugin_web.render_index(safe=on)
    response = HTMLResponse(page, headers={"Cache-Control": "no-cache", "Content-Language": lang,
                                           "Vary": "Accept-Language"})
    was_safe = SAFE_COOKIE in request.cookies
    if on:
        response.set_cookie(SAFE_COOKIE, "1", httponly=True, samesite="strict")  # for /custom.css
        if not was_safe:
            activity.log("safe_mode.on")
    elif was_safe:  # out of safe mode
        response.delete_cookie(SAFE_COOKIE)
        activity.log("safe_mode.off")
    return response


@app.get("/plugins/{plugin_id}.js")
def plugin_script(plugin_id: str, request: Request):
    # a switched-on plugin's, or a built-in fallback's even while another does its job (web.fallback_scripts)
    p = plugins.get(plugin_id) or next((b for b in plugin_web.fallback_scripts() if b.id == plugin_id), None)
    if p is None or not p.js:
        raise HTTPException(404, "No such plugin script")
    return checked_text(request, p.js, "text/javascript")


@app.get("/plugins/{plugin_id}.css")
def plugin_style(plugin_id: str, request: Request):
    """One plugin's CSS, for a plugin switched on after the page loaded (it's in /custom.css too)."""
    p = plugins.get(plugin_id)
    if p is None or not p.css:
        raise HTTPException(404, "No such plugin style")
    return checked_text(request, p.css, "text/css")
