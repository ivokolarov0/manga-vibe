"""MangaVibe entry point: `uvicorn app.main:app`."""
import logging
import mimetypes
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import config, db as database, updater
from app.api import attention, books, edit, series, system, update
from app.metadata import worker as metadata_worker
from app.plugins import manager as plugins
from app.posters import MAX_UPLOAD
from app.scanner import walker

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
        plugins.load()
        plugins.disabled = set(database.get_setting(s, "plugins_disabled", []) or [])
    if config.START_WORKERS:
        metadata_worker.start()
    walker.start_scan_in_background()
    if config.RESCAN_HOURS > 0:
        threading.Thread(target=_periodic_rescan, daemon=True, name="rescan").start()
    updater.load_cached()
    if updater.supported():
        threading.Thread(target=updater.daily_loop, daemon=True, name="update-check").start()
    updater.mark_started()
    yield


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


app = FastAPI(title="MangaVibe", lifespan=lifespan)
app.add_middleware(UploadSizeLimit, limit=MAX_UPLOAD + 64 * 1024)  # + room for the multipart headers
app.include_router(system.router)
app.include_router(series.router)
app.include_router(books.router)
app.include_router(attention.router)
app.include_router(edit.router)
app.include_router(update.router)
class StaticNoCache(StaticFiles):
    """The browser re-checks static files on every load (a quick 304 when unchanged),
    so a new version of the app shows up without ?v= numbers in the HTML."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", StaticNoCache(directory=config.STATIC_DIR), name="static")


@app.get("/custom.css")
def custom_css():
    """Plugin CSS, then the owner's /data/custom.css. Served unlayered so it overrides built-in styles."""
    parts = []
    for p in plugins.enabled():
        if p.css:
            parts.append(f"/* plugin: {p.id} */\n{p.css.strip()}")
    if config.CUSTOM_CSS.exists():
        parts.append("/* custom.css */\n" + config.CUSTOM_CSS.read_text(encoding="utf-8", errors="replace"))
    return Response("\n\n".join(parts), media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
def manifest():
    return FileResponse(config.STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
