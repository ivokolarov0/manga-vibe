"""Shared helpers for the API: titles, reading status, serialisation."""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException
from sqlalchemy import case, func

from app.db import get_setting
from app.models import Book, Library, Progress

READING_STATUSES = {"plan_to_read", "reading", "completed", "on_hold", "dropped"}  # manual choices
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


def status_of(db, series):
    """The reading status the app shows for one series."""
    return derived_status(series, series_counts(db, [series.id])[series.id])


def emit_reading_events(db, series, old_status, started=(), completed=()):
    """Tell plugins what the owner just changed. Call after commit; plugins run in the background."""
    from app.plugins import manager as plugins
    for book_id in started:
        plugins.emit("on_book_started", book_id)
    for book_id in completed:
        plugins.emit("on_book_completed", book_id)
    new = status_of(db, series)
    if new != old_status:
        plugins.emit("on_series_status_changed", series.id, old_status, new)


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


class BookNumbers(NamedTuple):
    """The numbering of a visible book: what covered_chapters() and collection() read."""
    eff_volume: float | None
    eff_chapter: float | None
    eff_chapter_end: float | None
    eff_extra: bool
    hidden: bool = False


def book_numbers(db, *conditions):
    """{series id: [BookNumbers]} for the visible books matching the conditions. Reads only the
    number columns: much cheaper than loading whole Book rows for many series."""
    chapter = func.coalesce(Book.override_chapter, Book.chapter)
    q = (db.query(Book.series_id, func.coalesce(Book.override_volume, Book.volume), chapter,
                  case((Book.override_chapter.is_not(None), Book.override_chapter_end), else_=Book.chapter_end),
                  func.coalesce(Book.override_extra, Book.extra))
         .filter(Book.missing_since.is_(None), Book.hidden.is_(False), *conditions))
    out = {}
    for sid, vol, ch, end, extra in q:
        out.setdefault(sid, []).append(BookNumbers(vol, ch, end, bool(extra)))
    return out


def covered_chapters(books):
    """The whole chapter numbers the visible, non-extra books cover (ranges included)."""
    covered = set()
    for b in books:
        ch = b.eff_chapter
        if ch is None or b.eff_extra or b.hidden:
            continue
        end = b.eff_chapter_end if b.eff_chapter_end is not None else ch
        covered.update(range(int(ch), int(end) + 1))
    return covered


def _ranges(nums):
    """Sorted whole numbers as [[first, last], ...] runs: {1, 2, 3, 7} -> [[1, 3], [7, 7]]."""
    out = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def numbering(books):
    """What the visible books' numbers say about the series, apart from its metadata. Worked out
    when the books change (walker.apply_order) and stored on the series, so lists of many series
    don't read every book: {"unit": "volume" | "chapter" | None, "have": [[first, last], ...] of
    the unit's whole numbers here, "chapters": the same for every chapter covered}.
    Books numbered by volume only count by volume, books with a chapter number by chapter;
    a mix of both (or no numbers) has no unit."""
    main = [b for b in books if not b.hidden and not b.eff_extra]
    by_chapter = [b for b in main if b.eff_chapter is not None]
    by_volume = [b for b in main if b.eff_chapter is None and b.eff_volume is not None]
    chapters = _ranges(covered_chapters(by_chapter))
    if by_chapter and not by_volume:
        unit, have = "chapter", chapters
    elif by_volume and not by_chapter:
        unit = "volume"
        have = _ranges({int(b.eff_volume) for b in by_volume if float(b.eff_volume).is_integer()})
    else:
        unit, have = None, []
    return {"unit": unit, "have": have, "chapters": chapters}


def numberings(db, series):
    """{series id: numbering()} for these series (objects or rows with id and numbering): the stored
    one, or worked out now for a series that has none yet."""
    out = {s.id: s.numbering for s in series if s.numbering is not None}
    todo = [s.id for s in series if s.numbering is None]
    if todo:
        books = book_numbers(db, Book.series_id.in_(todo))
        out.update({sid: numbering(books.get(sid, [])) for sid in todo})
    return out


def chapter_gaps(num):
    """[(after, before)] where whole chapters are missing, e.g. [(14, 52)], from a numbering()."""
    runs = num.get("chapters") or []
    return [(a[1], b[0]) for a, b in zip(runs, runs[1:])]


def collection_of(series, num):
    """How much of the series is here, compared with the source's (or the owner's) total. Best effort:
    {"unit": "volume" | "chapter" | None, "owned": n, "total": N | None,
     "state": "complete" | "incomplete" | "unknown", "manual": bool}. num is the series' numbering().
    No unit (a mix of volumes and chapters), a missing total, or a series that isn't finished
    gives "unknown". A series the owner marked complete (collection_complete) is always "complete"."""
    out = _counted(series, num)
    out["manual"] = bool(getattr(series, "collection_complete", False))
    if out["manual"]:
        out["state"] = "complete"
    return out


def _counted(series, num):
    unit, have = num.get("unit"), num.get("have") or []
    if unit is None:
        return {"unit": None, "owned": 0, "total": None, "state": "unknown"}
    total = series.chapters_total if unit == "chapter" else series.volumes_total
    if not total:
        return {"unit": unit, "owned": sum(b - a + 1 for a, b in have), "total": None, "state": "unknown"}
    owned = sum(max(0, min(b, total) - max(a, 1) + 1) for a, b in have)
    state = "unknown"
    if series.publication_status == "FINISHED":
        state = "complete" if owned == total else "incomplete"
    return {"unit": unit, "owned": owned, "total": total, "state": state}


def collection(series, books):
    """collection_of() from the books themselves."""
    return collection_of(series, numbering(books))
