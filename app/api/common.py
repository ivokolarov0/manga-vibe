"""Shared helpers for the API: titles, reading status, serialisation."""
import functools
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError

from app import users
from app.db import get_setting
from app.i18n import N_, t
from app.metadata import editions
from app.models import Book, Library, Progress, Series, SeriesRead, SeriesState, SeriesTotal


# ---- the current user's own (app/users.py) in SQL
def my_progress_join():
    """Join condition for Book -> the current user's Progress row."""
    return and_(Progress.book_id == Book.id, Progress.user_id == users.current_id())


def my_progress():
    """Filter for Progress rows: the current user's."""
    return Progress.user_id == users.current_id()


def my_state(name):
    """The current user's SeriesState column (reading_status, favorite, last_read_at) for each Series row,
    as SQL (NULL without a row)."""
    return (select(getattr(SeriesState, name))
            .where(SeriesState.series_id == Series.id, SeriesState.user_id == users.current_id())
            .scalar_subquery())


def retry_on_conflict(route):
    """Run a route once more when another request made the same per-user row (Progress, SeriesState)
    at the same moment, e.g. pages turned quickly in two tabs: the second insert fails, and the second
    run finds the row. For routes that get the session as `db`."""
    @functools.wraps(route)
    def run(*args, **kwargs):
        try:
            return route(*args, **kwargs)
        except IntegrityError:
            db = kwargs["db"]
            db.rollback()
            db.expunge_all()  # fresh rows (and no per-user cache) for the second run
            return route(*args, **kwargs)
    return run


def load_states(db, series_list):
    """Fetch the current user's SeriesState of many series at once, so reading series.favorite etc. on
    each doesn't ask the database again."""
    uid = users.current_id()
    ids = [s.id for s in series_list]
    found = {st.series_id: st for st in db.query(SeriesState).filter(SeriesState.user_id == uid,
                                                                      SeriesState.series_id.in_(ids))} if ids else {}
    for s in series_list:
        s.__dict__.setdefault("_mv_states", {})[uid] = found.get(s.id)

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
    # the stored counts (kept by the database's triggers, app/db.py): one row per series. Fetching a row
    # per book was slow, and far slower while other requests ran: Python's sqlite3 lets go of the GIL
    # for every row, and waits to get it back each time.
    mine = and_(SeriesRead.series_id == SeriesTotal.series_id, SeriesRead.user_id == users.current_id())
    rows = (db.query(SeriesTotal.series_id, SeriesTotal.total, SeriesRead.done, SeriesRead.started,
                     SeriesRead.done_main, SeriesTotal.main)
            .outerjoin(SeriesRead, mine).filter(SeriesTotal.series_id.in_(series_ids)).all())
    out = {sid: [0, 0, 0, 0, 0] for sid in series_ids}
    for sid, *counts in rows:
        out[sid] = [n or 0 for n in counts]
    return out


def latest_labels(db, series_ids):
    """{series_id: label of its last book in reading order, e.g. "Vol 12 Ch 120"} (the library's list
    view). Extras and books without a volume or chapter number are skipped unless a series has
    nothing else."""
    if not series_ids:
        return {}
    shown = and_(Book.series_id.in_(series_ids), Book.missing_since.is_(None), Book.hidden.is_(False))
    numbered = and_(func.coalesce(Book.override_extra, Book.extra).is_not(True),
                    (func.coalesce(Book.override_volume, Book.volume).is_not(None)
                     | func.coalesce(Book.override_chapter, Book.chapter).is_not(None)))
    # per series: the highest sort_index, of the numbered books first
    ranked = (select(Book.id, func.row_number().over(
        partition_by=Book.series_id, order_by=(case((numbered, 0), else_=1), Book.sort_index.desc())).label("n"))
              .where(shown).subquery())
    books = db.query(Book).join(ranked, and_(ranked.c.id == Book.id, ranked.c.n == 1)).all()
    return {b.series_id: book_label(b) for b in books}


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
    """Tell plugins what the owner just changed. Call after commit; plugins run in the background.
    Only the owner's reading (user 1, the first admin with accounts): plugins like tracker syncs are set
    up once for the app, so another account's reading must not go to the owner's tracker."""
    from app.plugins import manager as plugins
    if users.current_id() != users.OWNER.id:
        return
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
    v = poster_version(series)  # a look at the poster file: once per card
    return {
        "id": series.id,
        "title": display_title(series, lang),
        "book_count": total,
        "unread_count": total - done,
        "progress": round(done / total, 3) if total else 0,
        "publication_status": series.publication_status,
        "reading_status": derived_status(series, counts),
        "favorite": series.favorite,
        "poster": f"/api/series/{series.id}/poster?size=sm&v={v}",
        "poster_large": f"/api/series/{series.id}/poster?v={v}",
        "match_status": series.match_status,
    }


def book_label(b: Book):
    """What the lists, the reader and Continue call a book ("Vol 3", "Ch 12-14"): the `book:label` job
    (#210), built in app/plugins/builtin/book_labels/. With that job off, the file's name."""
    from app.plugins import ask_job
    label = ask_job("book:label", "book_label", b, fallback=_file_label)
    return label if isinstance(label, str) and label else _file_label(b)


def _file_label(b: Book):
    return Path(b.file_name).stem


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
    return continue_targets(db, [series_id])[series_id]


def continue_targets(db, series_ids):
    """{series id: (book, page)} to continue reading in each, (None, 0) when everything is read: the book
    last read if it isn't finished, else the next one not read yet; nothing read yet, the first. Of the
    readable books (visible, no error), in reading order. A few queries for any number of series, without
    loading every book of each (Continue reading asks for dozens of long series at once)."""
    ids = list(dict.fromkeys(series_ids))
    if not ids:
        return {}
    readable = and_(Book.missing_since.is_(None), Book.hidden.is_(False), Book.error.is_(None))
    # per series, the user's progress row read last. SQLite takes the other columns from the row with
    # the max() (its "bare columns"), so this is one pass over the series' progress rows.
    latest = {sid: (book_id, page, done) for sid, book_id, page, done, _ in (
        db.query(Book.series_id, Progress.book_id, Progress.page, Progress.completed, func.max(Progress.updated_at))
        .join(Progress, my_progress_join()).filter(Book.series_id.in_(ids), readable)
        .group_by(Book.series_id))}
    books = {b.id: b for b in db.query(Book).filter(Book.id.in_([v[0] for v in latest.values()]))} if latest else {}
    out = {}
    for sid in ids:
        found = latest.get(sid)
        if found is None:  # nothing read yet: the first book
            out[sid] = (visible_books_query(db, sid).filter(Book.error.is_(None)).order_by(Book.id).first(), 0)
            continue
        book_id, page, done = found
        last = books[book_id]
        if not done:
            out[sid] = (last, page)
            continue
        # finished: the next book in reading order that isn't read
        after = or_(Book.sort_index > last.sort_index, and_(Book.sort_index == last.sort_index, Book.id > last.id))
        row = (db.query(Book, Progress.page).outerjoin(Progress, my_progress_join())
               .filter(Book.series_id == sid, readable, after, Progress.completed.is_not(True))
               .order_by(Book.sort_index, Book.id).first())
        out[sid] = (row[0], row[1] or 0) if row else (None, 0)
    return out


def get_or_404(db, model, id_):
    obj = db.get(model, id_)
    if obj is None:
        # whole sentences for the usual ones (a translation can't decline an English class name)
        known = {"Series": N_("Series not found"), "Book": N_("Book not found"), "Library": N_("Library not found")}
        raise HTTPException(404, t(known[model.__name__]) if model.__name__ in known
                            else t("{what} not found", what=model.__name__))
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
    page_count: int = 0


def book_numbers(db, *conditions):
    """{series id: [BookNumbers]} for the visible books matching the conditions. Reads only the
    number columns: much cheaper than loading whole Book rows for many series."""
    chapter = func.coalesce(Book.override_chapter, Book.chapter)
    q = (db.query(Book.series_id, func.coalesce(Book.override_volume, Book.volume), chapter,
                  case((Book.override_chapter.is_not(None), Book.override_chapter_end), else_=Book.chapter_end),
                  func.coalesce(Book.override_extra, Book.extra), Book.page_count)
         .filter(Book.missing_since.is_(None), Book.hidden.is_(False), *conditions))
    out = {}
    for sid, vol, ch, end, extra, pages in q:
        out.setdefault(sid, []).append(BookNumbers(vol, ch, end, bool(extra), page_count=pages or 0))
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
    the unit's whole numbers here, "chapters": the same for every chapter covered, and for volumes
    "pages": the usual (median) pages per volume file, which tells editions apart, and "single":
    true when the series is one file without a number (a one-shot, see _single_file_unit())}.
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
    out = {"unit": unit, "have": have, "chapters": chapters}
    pages = sorted(n for b in by_volume if (n := getattr(b, "page_count", 0) or 0) > 0)
    if unit == "volume" and pages:
        out["pages"] = pages[len(pages) // 2]
    if len(main) == 1 and unit is None:
        out["single"] = True
    return out


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
    gives "unknown". A series the owner marked complete (collection_complete) is always "complete".
    Volumes count against the edition the files are from (edition_of()); when that isn't the
    regular one, "edition": {"volumes", "names", "regular", "manual"} says which."""
    out = _counted(series, num)
    out["manual"] = bool(getattr(series, "collection_complete", False))
    if out["manual"]:
        out["state"] = "complete"
    return out


def edition_of(series, num):
    """The volume count of the edition the files are from: the owner's pick, or the MangaUpdates
    edition the pages per file point to, or the regular count (editions.regular_of())."""
    picked = getattr(series, "edition_volumes", None)
    if picked:
        return picked
    have = num.get("have") or []
    listed = getattr(series, "editions", None)
    return editions.pick(editions.regular_of(series.volumes_total, listed), listed, num.get("pages"),
                         have[-1][1] if have else 0)


def _single_file_unit(series, num):
    """A series that is one file without a number ("Title (2025) (Digital).cbz") is its one volume
    (or chapter) when the metadata says it has just one: "volume" | "chapter" | None."""
    if not num.get("single"):
        return None
    if edition_of(series, num) == 1:
        return "volume"
    if not series.volumes_total and series.chapters_total == 1:
        return "chapter"
    return None


def _counted(series, num):
    unit, have = num.get("unit"), num.get("have") or []
    if unit is None and (unit := _single_file_unit(series, num)):
        have = [[1, 1]]
    if unit is None:
        return {"unit": None, "owned": 0, "total": None, "state": "unknown"}
    extra = {}
    if unit == "chapter":
        total = series.chapters_total
    else:
        total = edition_of(series, num)
        picked = bool(getattr(series, "edition_volumes", None))
        listed = getattr(series, "editions", None)
        regular = editions.regular_of(series.volumes_total, listed)
        if total and (total != regular or picked):
            extra = {"edition": {"volumes": total, "names": editions.names_of(listed, total),
                                 "regular": regular, "manual": picked}}
    if not total:
        return {"unit": unit, "owned": sum(b - a + 1 for a, b in have), "total": None, "state": "unknown", **extra}
    owned = sum(max(0, min(b, total) - max(a, 1) + 1) for a, b in have)
    state = "unknown"
    if series.publication_status == "FINISHED":
        state = "complete" if owned == total else "incomplete"
    return {"unit": unit, "owned": owned, "total": total, "state": state, **extra}


def collection(series, books):
    """collection_of() from the books themselves."""
    return collection_of(series, numbering(books))
