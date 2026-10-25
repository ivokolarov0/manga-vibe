"""Book info, page images, thumbnails, reading progress and plugin overlays."""
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import images
from app.api.common import (book_dict, display_title, emit_reading_events, ensure_inside_library,
                            get_or_404, now, status_of, title_lang, visible_books_query)
from app.db import get_db
from app.direction import reading_direction
from app.models import Book, OverlayHidden, Progress, Series
from app.plugins import Box, Context, Overlay, action_result, implements, manager as plugins
from app.scanner import archives
from app.scanner.walker import apply_order

router = APIRouter(prefix="/api/books")
log = logging.getLogger("mangavibe.plugins")
IMMUTABLE = "private, max-age=31536000, immutable"  # URLs carry ?v=<mtime>


@router.get("/{book_id}")
def get_book(book_id: int, db=Depends(get_db)):
    b = get_or_404(db, Book, book_id)
    s = db.get(Series, b.series_id)
    books = visible_books_query(db, b.series_id).filter(Book.error.is_(None)).all()
    ids = [x.id for x in books]
    i = ids.index(b.id) if b.id in ids else -1
    nxt = books[i + 1] if 0 <= i < len(books) - 1 else None
    prev = books[i - 1] if i > 0 else None
    p = db.query(Progress).filter_by(book_id=b.id).one_or_none()
    return {**book_dict(b, p), "series_title": display_title(s, title_lang(db)),
            "reader_mode": reading_direction(db, s)[0],  # the series' own, or worked out (app/direction.py)
            "next": {"id": nxt.id, "label": book_dict(nxt)["label"]} if nxt else None,
            "prev": {"id": prev.id, "label": book_dict(prev)["label"]} if prev else None,
            # reader toggles for plugins that draw over pages, and their buttons for this book
            "overlays": [{"plugin": p.id, "name": p.name or p.id, "label": p.overlay_label} for p in overlay_plugins()],
            "hidden_boxes": db.query(OverlayHidden).filter_by(book_id=b.id).count(),
            "actions": [{"plugin": p.id, "name": p.name or p.id, "key": a.key, "label": a.label, "confirm": a.confirm}
                        for p in plugins.enabled() for a in p.book_actions]}


def overlay_plugins():
    return [p for p in plugins.enabled() if p.overlay_label and implements(p, "page_overlay")]


def _box_dict(b):
    if isinstance(b, dict):
        b = Box(**{k: b[k] for k in ("x", "y", "w", "h", "text", "title", "cls") if k in b})
    clamp = lambda v: max(0.0, min(1.0, float(v)))  # noqa: E731
    x, y = clamp(b.x), clamp(b.y)
    return {"id": f"{x:.3f},{y:.3f}",  # where it is: what the owner's "hide" remembers
            "x": x, "y": y, "w": clamp(b.w), "h": clamp(b.h),
            "text": str(b.text or ""), "title": str(b.title or ""), "cls": str(b.cls or "")}


@router.get("/{book_id}/overlays/{n}")
def page_overlays(book_id: int, n: int, plugins_: str = Query("", alias="plugins"), db=Depends(get_db)):
    """What the plugins draw over page n. `plugins` (comma-separated ids) limits it to the
    overlays the reader has switched on. A plugin that fails gives a layer with only a message."""
    b = get_or_404(db, Book, book_id)
    if not 0 <= n < b.page_count:
        raise HTTPException(404, "No such page")
    wanted = {x for x in plugins_.split(",") if x}
    hidden = {(h.plugin, h.box) for h in db.query(OverlayHidden).filter_by(book_id=b.id, page=n)}
    layers = []
    for p in overlay_plugins():
        if wanted and p.id not in wanted:
            continue
        try:
            result = p.page_overlay(Context(p, db), b, n)
            db.commit()
        except Exception as e:
            db.rollback()
            log.error("Plugin %s failed in page_overlay:\n%s", p.id, traceback.format_exc())
            result = Overlay(message=f"{p.name or p.id} failed: {type(e).__name__}")
        if result is None:
            continue
        try:
            boxes = [_box_dict(x) for x in result.boxes]
        except (TypeError, ValueError, KeyError, AttributeError):
            log.error("Plugin %s returned a broken box:\n%s", p.id, traceback.format_exc())
            boxes = []
        for box in boxes:
            box["hidden"] = (p.id, box["id"]) in hidden
        layers.append({"plugin": p.id, "pending": bool(result.pending),
                       "message": str(result.message or ""), "note": str(getattr(result, "note", "") or ""),
                       "speech": str(getattr(result, "speech", "") or ""), "boxes": boxes})
    return {"page": n, "layers": layers}


class HideBody(BaseModel):
    plugin: str
    box: str
    hidden: bool = True


@router.put("/{book_id}/overlays/{n}/hidden")
def hide_box(book_id: int, n: int, body: HideBody, db=Depends(get_db)):
    """Hide (or show again) one overlay box on page n. Returns how many are hidden in the book."""
    b = get_or_404(db, Book, book_id)
    if not 0 <= n < b.page_count:
        raise HTTPException(404, "No such page")
    row = db.query(OverlayHidden).filter_by(book_id=b.id, plugin=body.plugin, page=n, box=body.box).one_or_none()
    if body.hidden and row is None:
        db.add(OverlayHidden(book_id=b.id, plugin=body.plugin, page=n, box=body.box))
    elif not body.hidden and row is not None:
        db.delete(row)
    db.commit()
    return {"hidden_boxes": db.query(OverlayHidden).filter_by(book_id=b.id).count()}


@router.delete("/{book_id}/overlays/hidden")
def show_hidden_boxes(book_id: int, db=Depends(get_db)):
    """Show every hidden box of the book again."""
    b = get_or_404(db, Book, book_id)
    count = db.query(OverlayHidden).filter_by(book_id=b.id).delete()
    db.commit()
    return {"shown": count, "hidden_boxes": 0}


@router.post("/{book_id}/actions/{plugin_id}/{action_key}")
def run_book_action(book_id: int, plugin_id: str, action_key: str, db=Depends(get_db)):
    b = get_or_404(db, Book, book_id)
    p = plugins.get(plugin_id)
    if p is None or action_key not in {a.key for a in p.book_actions}:
        raise HTTPException(404, "Action not found")
    try:
        result = p.run_book_action(Context(p, db), action_key, b)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Plugin error: {type(e).__name__}: {e}")
    return action_result(result)


@router.get("/{book_id}/thumbnail")
def thumbnail(book_id: int, db=Depends(get_db)):
    b = get_or_404(db, Book, book_id)
    if not b.thumbnail_path:
        raise HTTPException(404, "No thumbnail")
    return FileResponse(b.thumbnail_path, headers={"Cache-Control": IMMUTABLE})


@router.get("/{book_id}/pages/{n}")
def page(book_id: int, n: int, request: Request, w: int | None = None, db=Depends(get_db)):
    b = get_or_404(db, Book, book_id)
    path = str(ensure_inside_library(db, b.path))
    if b.error:
        raise HTTPException(409, b.error)
    etag = f'"{b.id}-{int(b.mtime)}-{n}-{w or 0}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    headers = {"Cache-Control": IMMUTABLE, "ETag": etag}
    try:
        if w:
            w = max(200, min(w, 4000))
            data = images.cached_resized_page(
                b.id, n, w, b.mtime, lambda: archives.read_page(path, b.format, n)[0])
            return Response(data, media_type="image/webp", headers=headers)
        data, mime = archives.read_page(path, b.format, n)
        if mime not in archives.WEB_MIME:  # e.g. TIFF: browsers can't show it, convert
            data, mime = images.resize(data, 100_000), "image/webp"
        return Response(data, media_type=mime, headers=headers)
    except archives.ArchiveError as e:
        raise HTTPException(409, str(e))
    except IndexError:
        raise HTTPException(404, "No such page")
    except FileNotFoundError:
        raise HTTPException(404, "File is missing (moved or share not mounted?)")


class ProgressBody(BaseModel):
    page: int
    completed: bool | None = None


@router.get("/{book_id}/progress")
def get_progress(book_id: int, db=Depends(get_db)):
    get_or_404(db, Book, book_id)
    p = db.query(Progress).filter_by(book_id=book_id).one_or_none()
    return {"page": p.page if p else 0, "completed": bool(p and p.completed)}


@router.put("/{book_id}/progress")
def put_progress(book_id: int, body: ProgressBody, db=Depends(get_db)):
    b = get_or_404(db, Book, book_id)
    page = max(0, min(body.page, max(b.page_count - 1, 0)))
    p = db.query(Progress).filter_by(book_id=book_id).one_or_none()
    series = db.get(Series, b.series_id)
    old_status = status_of(db, series)
    if body.completed is False and page == 0:
        # "mark unread": forget the book entirely, so Continue doesn't jump to it
        if p is not None:
            db.delete(p)
            db.flush()
            # nothing left read in the series: drop it from Continue reading
            others = (db.query(Progress.id).join(Book, Book.id == Progress.book_id)
                      .filter(Book.series_id == b.series_id).first())
            if others is None:
                series.last_read_at = None
            db.commit()
            emit_reading_events(db, series, old_status)
        return {"page": 0, "completed": False}
    started, was_completed = p is None, bool(p and p.completed)
    if p is None:
        p = Progress(book_id=book_id)
        db.add(p)
    p.page = page
    if body.completed is not None:
        p.completed = body.completed
    elif b.page_count and page >= b.page_count - 1:
        p.completed = True  # reaching the last page marks the book read
    p.updated_at = now()
    series.last_read_at = p.updated_at
    db.commit()
    emit_reading_events(db, series, old_status, started=[b.id] if started else [],
                        completed=[b.id] if p.completed and not was_completed else [])
    return {"page": p.page, "completed": p.completed}


class BookOverride(BaseModel):
    """Manual values for one file. Missing fields are left alone; reset=true clears everything."""
    volume: float | None = None
    chapter: float | None = None
    chapter_end: float | None = None
    extra: bool | None = None
    hidden: bool | None = None
    reset: bool = False


@router.patch("/{book_id}")
def patch_book(book_id: int, body: BookOverride, db=Depends(get_db)):
    """Set the volume/chapter (or chapter range) of a file by hand, mark it extra or hidden.
    Stored in the database only: the file is never renamed. Survives rescans."""
    b = get_or_404(db, Book, book_id)
    if body.reset:
        b.override_volume = b.override_chapter = b.override_chapter_end = b.override_extra = None
        b.hidden_by_owner = False
    else:
        fields = body.model_fields_set
        if body.chapter_end is not None and body.chapter is None and "chapter" not in fields:
            raise HTTPException(400, "Give the first chapter of the range too")
        if body.chapter is not None and body.chapter_end is not None and body.chapter_end < body.chapter:
            raise HTTPException(400, "The last chapter must be after the first one")
        if "volume" in fields:
            b.override_volume = body.volume
        if "chapter" in fields:
            b.override_chapter = body.chapter
            b.override_chapter_end = body.chapter_end if body.chapter is not None else None
        if "extra" in fields:
            b.override_extra = body.extra
        if "hidden" in fields:
            b.hidden_by_owner = bool(body.hidden)
    series = db.get(Series, b.series_id)
    apply_order(series)  # re-sort the series with the new numbers
    db.commit()
    p = db.query(Progress).filter_by(book_id=b.id).one_or_none()
    return book_dict(b, p)
