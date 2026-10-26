"""Library grid, series details, continue reading, plugin actions."""
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import String, and_, case, cast, exists, func, or_, select

from app.api.common import (READING_STATUSES, TITLE_FIELDS, book_dict, book_numbers, collection, continue_target,
                            emit_reading_events, get_or_404, now, series_card, series_counts,
                            status_of, title_lang, visible_books_query)
from app import config
from app.db import get_db
from app.direction import MODES, reading_direction
from app.metadata import service as metadata, worker as metadata_worker
from app.metadata.http import ProviderError
from app.models import Book, Link, PluginField, Progress, Series, SeriesTag, Tag
from app.plugins import Context, action_result, implements, manager as plugins

router = APIRouter(prefix="/api")
log = logging.getLogger("mangavibe.plugins")

# 1x1 transparent PNG for series without any readable page
BLANK_PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                          "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _split(v):
    return [x for x in (v or "").split(",") if x]


FORMATS = {  # filter value -> SQL condition (AniList format + country of origin)
    "manga": lambda: and_(or_(Series.format.is_(None), Series.format == "MANGA"),
                          or_(Series.country_of_origin.is_(None), Series.country_of_origin == "JP")),
    "manhwa": lambda: Series.country_of_origin == "KR",
    "manhua": lambda: Series.country_of_origin.in_(["CN", "TW", "HK"]),
    "one_shot": lambda: Series.format == "ONE_SHOT",
    "novel": lambda: Series.format == "NOVEL",
}
MATCH = {"matched": ["auto", "manual"], "attention": ["failed"], "unmatched": ["unmatched", "ignored"]}
HAVE = ("complete", "incomplete", "unknown")


def collections(db, series, ids=None):
    """{series_id: collection()} for these series (rows with id, totals and publication status),
    from one query of their books' numbers. ids: a subquery of the same series ids, for many."""
    if not series:
        return {}
    books = book_numbers(db, Book.series_id.in_(ids if ids is not None else [s.id for s in series]))
    return {s.id: collection(s, books.get(s.id, [])) for s in series}


def _have_filter(db, wanted):
    """SQL condition for the Collection filter. Only finished series can be complete or incomplete,
    so those are worked out (in Python, like the chapter-gap check) and everything else is unknown."""
    cond = (Series.publication_status == "FINISHED", Series.missing_since.is_(None))
    finished = db.query(Series.id, Series.chapters_total, Series.volumes_total,
                        Series.publication_status).filter(*cond).all()
    by_state = {k: set() for k in HAVE}
    for sid, c in collections(db, finished, select(Series.id).where(*cond)).items():
        by_state[c["state"]].add(sid)
    known = by_state["complete"] | by_state["incomplete"]
    conds = [Series.id.in_(by_state[k]) for k in ("complete", "incomplete") if k in wanted]
    if "unknown" in wanted:
        conds.append(~Series.id.in_(known))
    return or_(*conds)


def reading_aggregates():
    """Per-series counts of visible books, used for reading status, unread count and sorting."""
    visible = and_(Book.missing_since.is_(None), Book.hidden.is_(False))
    done = case((Progress.completed.is_(True), 1), else_=0)
    return (select(Book.series_id.label("sid"),
                   func.count(Book.id).label("total"),
                   func.sum(done).label("done"),
                   func.sum(case((and_(Progress.completed.is_not(True), Progress.page > 0), 1), else_=0)).label("started"),
                   func.sum(case((Book.extra.is_(False), 1), else_=0)).label("total_main"),
                   func.sum(case((and_(Book.extra.is_(False), Progress.completed.is_(True)), 1), else_=0)).label("done_main"))
            .outerjoin(Progress, Progress.book_id == Book.id)
            .where(visible).group_by(Book.series_id).subquery())


@router.get("/series")
def list_series(cursor: int = 0, limit: int = 60, q: str = "", library: int | None = None,
                sort: str = "title", order: str = "", genre: list[str] = Query(default=[]),
                exclude: list[str] = Query(default=[]),
                pub: str = "", read: str = "", match: str = "", format: str = "", have: str = "",
                fav: bool = False, year_min: int | None = None, year_max: int | None = None,
                score_min: int | None = None, letter: str = "", db=Depends(get_db)):
    """Library grid. Multi-value filters are comma-separated (genre=Romance,Comedy).
    letter=a..z keeps titles starting with that letter, letter=# the rest (numbers, symbols, other scripts).
    have=complete,incomplete,unknown: finished series with every volume/chapter here, or something missing."""
    limit = max(1, min(limit, 200))
    lang = title_lang(db)
    agg = reading_aggregates()
    total_c, done_c = func.coalesce(agg.c.total, 0), func.coalesce(agg.c.done, 0)
    status = case(
        (Series.reading_status.is_not(None), Series.reading_status),
        (and_(total_c > 0, agg.c.total_main > 0, agg.c.done_main == agg.c.total_main), "completed"),
        (or_(done_c > 0, func.coalesce(agg.c.started, 0) > 0), "reading"),
        else_="unread")
    # the displayed title (empty fields skipped, like display_title), lower case for sorting
    title = func.lower(func.coalesce(*[func.nullif(getattr(Series, f), "")
                                       for f in TITLE_FIELDS.get(lang, TITLE_FIELDS["english"])],
                                     Series.folder_name))

    # the same aggregate row gives the card numbers: no second pass over books/progress
    zero = lambda c: func.coalesce(c, 0)
    query = (db.query(Series, total_c, done_c, zero(agg.c.started), zero(agg.c.done_main), zero(agg.c.total_main))
             .outerjoin(agg, agg.c.sid == Series.id).filter(Series.missing_since.is_(None)))
    if library:
        query = query.filter(Series.library_id == library)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Series.folder_name.ilike(like), Series.title_english.ilike(like),
                                 Series.title_romaji.ilike(like), Series.title_native.ilike(like),
                                 cast(Series.synonyms, String).ilike(like),
                                 cast(Series.authors, String).ilike(like)))
    for name in genre:  # one param per tag: names may contain commas
        query = query.filter(exists().where(SeriesTag.series_id == Series.id, SeriesTag.tag_id == Tag.id,
                                            Tag.name == name))
    for name in exclude:
        query = query.filter(~exists().where(SeriesTag.series_id == Series.id, SeriesTag.tag_id == Tag.id,
                                             Tag.name == name))
    if _split(pub):
        query = query.filter(Series.publication_status.in_(_split(pub)))
    if _split(read):
        query = query.filter(status.in_(_split(read)))
    if _split(match):
        query = query.filter(Series.match_status.in_([m for k in _split(match) for m in MATCH.get(k, [])]))
    if _split(format):
        query = query.filter(or_(*[FORMATS[f]() for f in _split(format) if f in FORMATS]))
    wanted = [h for h in _split(have) if h in HAVE]
    if wanted:
        query = query.filter(_have_filter(db, wanted))
    if fav:
        query = query.filter(Series.favorite.is_(True))
    if year_min:
        query = query.filter(Series.start_year >= year_min)
    if year_max:
        query = query.filter(Series.start_year <= year_max)
    if score_min:
        query = query.filter(Series.average_score >= score_min)
    letter = letter.strip().lower()
    first = func.substr(func.ltrim(title), 1, 1)
    if len(letter) == 1 and "a" <= letter <= "z":
        query = query.filter(first == letter)
    elif letter == "#":
        query = query.filter(~first.between("a", "z"))

    sorts = {"title": title, "added": Series.added_at, "updated": Series.updated_at,
             "read": Series.last_read_at, "score": Series.average_score, "year": Series.start_year,
             "unread": total_c - done_c}
    col = sorts.get(sort, title)
    desc = order == "desc" or (order == "" and sort not in ("title",))
    query = query.order_by(col.desc().nulls_last() if desc else col.asc().nulls_last(), title, Series.id)
    total = query.count()
    rows = query.offset(cursor).limit(limit).all()
    nxt = cursor + len(rows)
    # counts tuple order as series_counts(): total, done, started, done_main, total_main
    have_of = collections(db, [s for s, *_ in rows if s.publication_status == "FINISHED"])
    items = []
    for s, *counts in rows:
        card = series_card(s, tuple(counts), lang)
        card["incomplete"] = have_of.get(s.id, {}).get("state") == "incomplete"
        items.append(card)
    return {"items": items,
            "next_cursor": nxt if nxt < total else None, "total": total}


@router.get("/tags")
def list_tags(db=Depends(get_db)):
    """Genres and tags with the number of series using them (for the filter panel)."""
    rows = (db.query(Tag.name, Tag.kind, func.count(SeriesTag.series_id))
            .join(SeriesTag, SeriesTag.tag_id == Tag.id)
            .join(Series, Series.id == SeriesTag.series_id)
            .filter(Series.missing_since.is_(None))
            .group_by(Tag.id).order_by(Tag.name).all())
    return [{"name": n, "kind": k, "count": c} for n, k, c in rows]


@router.get("/series/{series_id}")
def get_series(series_id: int, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    counts = series_counts(db, [s.id])[s.id]
    lang = title_lang(db)
    book, page = continue_target(db, s.id)
    tags = (db.query(Tag, SeriesTag).join(SeriesTag, SeriesTag.tag_id == Tag.id)
            .filter(SeriesTag.series_id == s.id).order_by(SeriesTag.rank.desc().nulls_last()).all())
    choices = _series_choices(db, s)
    shown = {(c["plugin"], c["key"]) for c in choices}  # a dropdown replaces the field with its key
    fields = [f for f in db.query(PluginField).filter_by(series_id=s.id) if (f.plugin, f.key) not in shown]
    return {
        **series_card(s, counts, lang),
        "folder_name": s.folder_name, "folder_path": s.folder_path,
        "titles": {"english": s.title_english, "romaji": s.title_romaji, "native": s.title_native},
        "synonyms": s.synonyms or [], "description": s.description, "start_year": s.start_year,
        "format": s.format, "chapters_total": s.chapters_total, "volumes_total": s.volumes_total,
        "collection": collection(s, visible_books_query(db, s.id).all()),
        "average_score": s.average_score, "authors": s.authors or [],
        "match_status": s.match_status, "match_confidence": s.match_confidence,
        "metadata_source": s.metadata_source, "match_candidates": s.match_candidates or [],
        "publication_status": s.publication_status, "country_of_origin": s.country_of_origin,
        "banner_url": s.banner_url, "has_duplicates": s.has_duplicates,
        "reader_mode": s.reader_mode, "manual_reading_status": s.reading_status,
        # the reading mode the reader uses, and what decided it: series, library or default
        "direction": dict(zip(("mode", "source"), reading_direction(db, s))),
        "tags": [{"name": t.name, "kind": t.kind, "spoiler": st.is_spoiler} for t, st in tags],
        "links": [{"id": l.id, "site": l.site, "url": l.url} for l in db.query(Link).filter_by(series_id=s.id)],
        "locked_fields": s.locked_fields or [], "poster_source": s.poster_source,
        "plugin_fields": [{"plugin": f.plugin, "key": f.key, "label": f.label, "value": f.value}
                          for f in fields],
        "actions": [{"plugin": p.id, "key": a.key, "label": a.label, "confirm": a.confirm}
                    for p in plugins.enabled() for a in p.actions],
        "choices": choices,
        "continue": {"book_id": book.id, "page": page, "label": book_dict(book)["label"],
                     "started": bool(counts[1] or counts[2])} if book else None,
    }


class SeriesPatch(BaseModel):
    reading_status: str | None = None
    reader_mode: str | None = None
    favorite: bool | None = None
    clear_reading_status: bool = False


READER_MODES = set(MODES)


@router.patch("/series/{series_id}")
def patch_series(series_id: int, body: SeriesPatch, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    old_status = status_of(db, s)
    if body.reading_status is not None:
        if body.reading_status not in READING_STATUSES:
            raise HTTPException(400, "Unknown reading status")
        s.reading_status = body.reading_status
    if body.clear_reading_status:
        s.reading_status = None
    if body.reader_mode == "default":
        s.reader_mode = None  # work it out again (app/direction.py)
    elif body.reader_mode is not None:
        if body.reader_mode not in READER_MODES:
            raise HTTPException(400, "Unknown reader mode")
        s.reader_mode = body.reader_mode
    if body.favorite is not None:
        s.favorite = body.favorite
    db.commit()
    emit_reading_events(db, s, old_status)
    return {"ok": True}


@router.get("/series/{series_id}/books")
def series_books(series_id: int, all: bool = False, db=Depends(get_db)):
    get_or_404(db, Series, series_id)
    q = db.query(Book).filter(Book.series_id == series_id, Book.missing_since.is_(None))
    if not all:
        q = q.filter(Book.hidden.is_(False))
    books = q.order_by(Book.sort_index).all()
    prog = {p.book_id: p for p in db.query(Progress).filter(Progress.book_id.in_([b.id for b in books]))}
    badges = _book_badges(db, books)
    return [{**book_dict(b, prog.get(b.id)), "badges": badges.get(b.id, [])} for b in books]


@router.get("/series/{series_id}/badges")
def series_badges(series_id: int, all: bool = False, db=Depends(get_db)):
    """Only the plugins' labels on the books, so the series page can keep them live (e.g. a translation
    going "Translated 12/40", "13/40", …) without reloading the whole list. `live` is False when no
    plugin labels books, so there's nothing to keep asking for."""
    get_or_404(db, Series, series_id)
    q = db.query(Book).filter(Book.series_id == series_id, Book.missing_since.is_(None))
    if not all:
        q = q.filter(Book.hidden.is_(False))
    live = any(implements(p, "book_badges") for p in plugins.enabled())
    badges = _book_badges(db, q.all()) if live else {}
    return {"live": live, "badges": {str(k): v for k, v in badges.items()}}


def _book_badges(db, books):
    """{book_id: [{"plugin", "text"}]} from the plugins' book_badges(). A broken one shows no badges."""
    out = {}
    for p in plugins.enabled():
        if not implements(p, "book_badges"):
            continue
        try:
            labels = p.book_badges(Context(p, db), books) or {}
        except Exception:
            log.error("Plugin %s failed in book_badges:\n%s", p.id, traceback.format_exc())
            continue
        for book_id, text in labels.items():
            if text:
                out.setdefault(book_id, []).append({"plugin": p.id, "name": p.name, "text": str(text)})
    return out


@router.get("/series/{series_id}/poster")
def series_poster(series_id: int, size: str = "", db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    path = s.poster_path
    if path and size == "sm":
        small = config.COVERS_DIR / f"{s.id}_sm.webp"
        path = str(small) if small.exists() else path
    if not path:
        first = visible_books_query(db, s.id).filter(Book.thumbnail_path.is_not(None)).first()
        path = first.thumbnail_path if first else None
    if not path:
        return Response(BLANK_PNG, media_type="image/png")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


def _set_read(db, series_id, completed: bool, up_to_book: int | None = None):
    series = get_or_404(db, Series, series_id)
    old_status = status_of(db, series)
    newly_read = []
    books = visible_books_query(db, series_id).all()
    if up_to_book is not None:
        idx = next((i for i, b in enumerate(books) if b.id == up_to_book), None)
        if idx is None:
            raise HTTPException(404, "Book not in this series")
        books = books[:idx]
    for b in books:
        p = db.query(Progress).filter_by(book_id=b.id).one_or_none()
        if completed:
            if p is None:
                p = Progress(book_id=b.id)
                db.add(p)
            if not p.completed:
                newly_read.append(b.id)
            p.completed, p.page, p.updated_at = True, max(b.page_count - 1, 0), now()
        elif p is not None:
            db.delete(p)
    db.commit()
    emit_reading_events(db, series, old_status, completed=newly_read)
    return {"ok": True, "count": len(books)}


@router.post("/series/{series_id}/mark-read")
def mark_read(series_id: int, before_book: int | None = None, db=Depends(get_db)):
    """Mark all books read, or only those before `before_book` ("mark previous as read")."""
    return _set_read(db, series_id, True, before_book)


@router.post("/series/{series_id}/mark-unread")
def mark_unread(series_id: int, db=Depends(get_db)):
    """Clear all progress; the series also drops out of Continue reading."""
    s = get_or_404(db, Series, series_id)
    s.last_read_at = None
    return _set_read(db, series_id, False)


@router.get("/continue-reading")
def continue_reading(limit: int = 30, db=Depends(get_db)):
    rows = (db.query(Series).filter(Series.last_read_at.is_not(None), Series.missing_since.is_(None))
            .order_by(Series.last_read_at.desc()).limit(limit * 2).all())
    lang = title_lang(db)
    counts = series_counts(db, [s.id for s in rows])
    out = []
    for s in rows:
        book, page = continue_target(db, s.id)
        if book is None:
            continue
        card = series_card(s, counts[s.id], lang)
        card["continue"] = {"book_id": book.id, "page": page, "label": book_dict(book)["label"],
                            "page_count": book.page_count}
        out.append(card)
        if len(out) >= limit:
            break
    return out


def _series_choices(db, series):
    """The plugins' dropdowns for the series page. A broken get_choice() only hides its dropdown."""
    out = []
    for p in plugins.enabled():
        for c in p.choices:
            try:
                value = p.get_choice(Context(p, db), c.key, series)
            except Exception:
                log.error("Plugin %s failed in get_choice:\n%s", p.id, traceback.format_exc())
                continue
            if value is False:
                continue
            out.append({"plugin": p.id, "key": c.key, "label": c.label,
                        "options": [[str(v), str(label)] for v, label in c.options],
                        "value": None if value is None else str(value)})
    return out


class ChoiceBody(BaseModel):
    value: str


@router.put("/series/{series_id}/choices/{plugin_id}/{key}")
def set_choice(series_id: int, plugin_id: str, key: str, body: ChoiceBody, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    p = plugins.get(plugin_id)
    c = next((c for c in p.choices if c.key == key), None) if p else None
    if c is None:
        raise HTTPException(404, "Dropdown not found")
    if body.value not in {str(v) for v, _ in c.options}:
        raise HTTPException(400, "Not one of the options")
    try:
        result = p.set_choice(Context(p, db), key, s, body.value)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Plugin error: {type(e).__name__}: {e}")
    return action_result(result)


@router.post("/series/{series_id}/actions/{plugin_id}/{action_key}")
def run_action(series_id: int, plugin_id: str, action_key: str, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    p = next((p for p in plugins.enabled() if p.id == plugin_id), None)
    if p is None or action_key not in {a.key for a in p.actions}:
        raise HTTPException(404, "Action not found")
    try:
        result = p.run_action(Context(p, db), action_key, s)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Plugin error: {type(e).__name__}: {e}")
    return action_result(result)


# ---------------------------------------------------------------- metadata matching

@router.get("/providers")
def providers():
    return [{"id": p.id, "name": p.name, "available": not metadata._is_down(p.id)}
            for p in metadata.ordered_providers()]


@router.get("/metadata/providers")
def metadata_providers():
    """All provider plugins for the Settings page: enabled ones in the order they are tried
    (the first is the main source, the rest are fallbacks), then disabled ones."""
    out = [{"id": p.id, "name": p.name, "description": p.description, "url": p.url or None,
            "enabled": True, "role": "main" if i == 0 else "fallback",
            "available": not metadata._is_down(p.id)}
           for i, p in enumerate(metadata.ordered_providers())]
    out += [{"id": p.id, "name": p.name, "description": p.description, "url": p.url or None,
             "enabled": False, "role": None, "available": False}
            for p in plugins.plugins.values() if p.is_provider and p.id in plugins.disabled]
    return out


@router.get("/metadata/status")
def metadata_status():
    return metadata_worker.status


class SearchBody(BaseModel):
    query: str
    provider: str = "anilist"


@router.post("/series/{series_id}/match/search")
def match_search(series_id: int, body: SearchBody, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    try:
        return metadata.search(db, s, body.provider, body.query.strip() or s.folder_name)
    except ProviderError as e:
        metadata._mark_down(metadata.provider(body.provider), e)
        raise HTTPException(503, f"Provider unavailable: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))


class MatchBody(BaseModel):
    provider: str | None = None
    provider_id: str | None = None
    url: str | None = None


def _metadata_call(db, fn, *args, **kw):
    try:
        fn(db, *args, **kw)
        db.commit()
    except ProviderError as e:
        db.rollback()
        raise HTTPException(503, f"Provider unavailable: {e}")
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))


@router.post("/series/{series_id}/match")
def match(series_id: int, body: MatchBody, db=Depends(get_db)):
    s = get_or_404(db, Series, series_id)
    if not body.url and not (body.provider and body.provider_id):
        raise HTTPException(400, "Give a provider and id, or a URL")
    _metadata_call(db, metadata.manual_match, s, body.provider, body.provider_id, body.url)
    return get_series(series_id, db)


@router.post("/series/{series_id}/unmatch")
def unmatch(series_id: int, db=Depends(get_db)):
    _metadata_call(db, metadata.unmatch, get_or_404(db, Series, series_id))
    return {"ok": True}


@router.post("/series/{series_id}/ignore-match")
def ignore_match(series_id: int, db=Depends(get_db)):
    _metadata_call(db, metadata.unmatch, get_or_404(db, Series, series_id), ignore=True)
    return {"ok": True}


@router.post("/series/{series_id}/auto-match")
def retry_auto_match(series_id: int, db=Depends(get_db)):
    """Queue an automatic match again (e.g. after 'Unmatch')."""
    s = get_or_404(db, Series, series_id)
    s.match_status = "unmatched"
    db.commit()
    metadata_worker.enqueue("match", s.id)
    return {"ok": True}


@router.post("/series/{series_id}/refresh")
def refresh(series_id: int, db=Depends(get_db)):
    _metadata_call(db, metadata.refresh, get_or_404(db, Series, series_id))
    return get_series(series_id, db)


@router.post("/series/{series_id}/rescan")
def rescan(series_id: int, db=Depends(get_db)):
    """Re-read this series' folder now: new files, removed files, changed names, errors.
    If the folder itself was deleted, the series is removed from the library."""
    get_or_404(db, Series, series_id)
    from app.scanner import walker
    try:
        result = walker.rescan_series(series_id)
    except walker.Busy as e:
        raise HTTPException(409, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return result
