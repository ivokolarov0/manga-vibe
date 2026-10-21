"""Book info, page images, thumbnails and reading progress."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app import images
from app.api.common import (book_dict, display_title, ensure_inside_library, get_or_404, now,
                            title_lang, visible_books_query)
from app.db import get_db
from app.models import Book, Progress, Series
from app.scanner import archives
from app.scanner.walker import apply_order

router = APIRouter(prefix="/api/books")
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
            "reader_mode": s.reader_mode,
            "next": {"id": nxt.id, "label": book_dict(nxt)["label"]} if nxt else None,
            "prev": {"id": prev.id, "label": book_dict(prev)["label"]} if prev else None}


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
    if body.completed is False and page == 0:
        # "mark unread": forget the book entirely, so Continue doesn't jump to it
        if p is not None:
            db.delete(p)
            db.commit()
        return {"page": 0, "completed": False}
    if p is None:
        p = Progress(book_id=book_id)
        db.add(p)
    p.page = page
    if body.completed is not None:
        p.completed = body.completed
    elif b.page_count and page >= b.page_count - 1:
        p.completed = True  # reaching the last page marks the book read
    p.updated_at = now()
    db.get(Series, b.series_id).last_read_at = p.updated_at
    db.commit()
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
