"""Shared helpers for the API: titles, reading status, serialisation."""
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.db import get_setting
from app.models import Book, Library, Progress

TITLE_FIELDS = {"english": ["title_english", "title_romaji", "title_native"],
                "romaji": ["title_romaji", "title_english", "title_native"],
                "native": ["title_native", "title_romaji", "title_english"]}


def now():
    return datetime.now(timezone.utc)


def display_title(series, lang="english"):
    for f in TITLE_FIELDS.get(lang, TITLE_FIELDS["english"]):
        if getattr(series, f):
            return getattr(series, f)
    return series.folder_name


def title_lang(db):
    return get_setting(db, "title_language", "english")


def visible_books_query(db, series_id):
    return (db.query(Book)
            .filter(Book.series_id == series_id, Book.missing_since.is_(None), Book.hidden.is_(False))
            .order_by(Book.sort_index))


def series_counts(db, series_ids):
    """{series_id: (total, completed, in_progress, completed_non_extra, total_non_extra)}"""
    if not series_ids:
        return {}
    rows = (db.query(Book.series_id, Book.extra, Progress.completed, Progress.page)
            .outerjoin(Progress, Progress.book_id == Book.id)
            .filter(Book.series_id.in_(series_ids), Book.missing_since.is_(None),
                    Book.hidden.is_(False))
            .all())
    out = {sid: [0, 0, 0, 0, 0] for sid in series_ids}
    for sid, extra, completed, page in rows:
        c = out[sid]
        c[0] += 1
        if completed:
            c[1] += 1
        elif page:
            c[2] += 1
        if not extra:
            c[4] += 1
            if completed:
                c[3] += 1
    return out


def derived_status(series, counts):
    if series.reading_status:
        return series.reading_status
    total, done, started, done_main, total_main = counts
    if total and total_main and done_main == total_main:
        return "completed"
    if done or started:
        return "reading"
    return "unread"


def poster_version(series):
    """Changes whenever the poster file changes, so browsers fetch the new one."""
    if series.poster_path:
        try:
            return int(os.path.getmtime(series.poster_path))
        except OSError:
            pass
    t = series.matched_at or series.updated_at
    return int(t.timestamp()) if t else 0


def series_card(series, counts, lang):
    total, done = counts[0], counts[1]
    return {
        "id": series.id,
        "title": display_title(series, lang),
        "book_count": total,
        "unread_count": total - done,
        "progress": round(done / total, 3) if total else 0,
        "publication_status": series.publication_status,
        "reading_status": derived_status(series, counts),
        "favorite": series.favorite,
        "poster": f"/api/series/{series.id}/poster?size=sm&v={poster_version(series)}",
        "poster_large": f"/api/series/{series.id}/poster?v={poster_version(series)}",
        "match_status": series.match_status,
    }


def book_label(b: Book):
    if b.eff_extra:
        return "Extra" + ("" if b.extra_index is None else f" {_fmt(b.extra_index)}")
    parts = []
    if b.eff_volume is not None:
        parts.append(f"Vol {_fmt(b.eff_volume)}")
    if b.eff_chapter is not None:
        end = b.eff_chapter_end
        parts.append(f"Ch {_fmt(b.eff_chapter)}" + (f"-{_fmt(end)}" if end is not None else ""))
    return " ".join(parts) or Path(b.file_name).stem


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def book_dict(b: Book, progress: Progress | None = None):
    return {
        "id": b.id, "series_id": b.series_id, "label": book_label(b), "file_name": b.file_name,
        "format": b.format, "page_count": b.page_count, "error": b.error,
        "volume": b.volume, "chapter": b.chapter, "chapter_end": b.chapter_end,
        "extra": b.extra, "extra_index": b.extra_index, "revision": b.revision,
        "hidden": b.hidden, "duplicate": b.duplicate_flag, "sort_index": b.sort_index,
        "hidden_by_owner": b.hidden_by_owner, "manual": b.has_override,
        "override": {"volume": b.override_volume, "chapter": b.override_chapter,
                     "chapter_end": b.override_chapter_end, "extra": b.override_extra},
        "file_size": b.file_size, "version": int(b.mtime),
        "thumbnail": f"/api/books/{b.id}/thumbnail?v={int(b.mtime)}",
        "page": progress.page if progress else 0,
        "completed": bool(progress and progress.completed),
    }


def continue_target(db, series_id):
    """(book, page) to continue reading, or (None, 0) when everything is read."""
    books = visible_books_query(db, series_id).filter(Book.error.is_(None)).all()
    if not books:
        return None, 0
    prog = {p.book_id: p for p in db.query(Progress).filter(Progress.book_id.in_([b.id for b in books]))}
    if not prog:
        return books[0], 0
    latest = max(prog.values(), key=lambda p: p.updated_at)
    idx = next(i for i, b in enumerate(books) if b.id == latest.book_id)
    if not latest.completed:
        return books[idx], latest.page
    for b in books[idx + 1:]:
        p = prog.get(b.id)
        if not (p and p.completed):
            return b, p.page if p else 0
    return None, 0


def get_or_404(db, model, id_):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def ensure_inside_library(db, path: str):
    """Path safety: refuse any file outside configured library roots."""
    p = Path(path).resolve()
    for (root,) in db.query(Library.root_path):
        r = Path(root).resolve()
        if p == r or r in p.parents:
            return p
    raise HTTPException(403, "File is outside the library folders")
