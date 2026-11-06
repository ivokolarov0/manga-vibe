"""What decides which series a list shows: the library filters (search, tags, status...), and what
plugins allow (e.g. the libraries plugin: one library, never a locked one)."""
from sqlalchemy import String, and_, case, cast, exists, func, or_, select

from app.api.common import TITLE_FIELDS, collection_of, numberings
from app.models import Book, Progress, Series, SeriesTag, Tag
from app.plugins import manager as plugins

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


def split(v):
    return [x for x in (v or "").split(",") if x]


def collections(db, series):
    """{series_id: collection} for these series (rows with id, numbering, totals and publication
    status), from the numbering stored on each series."""
    nums = numberings(db, series)
    return {s.id: collection_of(s, nums[s.id]) for s in series}


def _have_filter(db, wanted):
    """SQL condition for the Collection filter. Only finished series (and ones the owner marked
    complete) can be complete or incomplete, so those are worked out (in Python, like the
    chapter-gap check) and everything else is unknown."""
    cond = (or_(Series.publication_status == "FINISHED", Series.collection_complete.is_(True)),
            Series.missing_since.is_(None))
    finished = db.query(Series.id, Series.numbering, Series.chapters_total, Series.volumes_total,
                        Series.publication_status, Series.collection_complete, Series.editions,
                        Series.edition_volumes).filter(*cond).all()
    by_state = {k: set() for k in HAVE}
    for sid, c in collections(db, finished).items():
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
                   func.sum(case((func.coalesce(Book.override_extra, Book.extra).is_(False), 1), else_=0)).label("total_main"),
                   func.sum(case((and_(func.coalesce(Book.override_extra, Book.extra).is_(False), Progress.completed.is_(True)), 1), else_=0)).label("done_main"))
            .outerjoin(Progress, Progress.book_id == Book.id)
            .where(visible).group_by(Book.series_id).subquery())


def title_expr(lang):
    """The displayed title (empty fields skipped, like display_title), lower case for sorting."""
    return func.lower(func.coalesce(*[func.nullif(getattr(Series, f), "")
                                      for f in TITLE_FIELDS.get(lang, TITLE_FIELDS["english"])],
                                    Series.folder_name))


def status_expr(agg):
    total_c, done_c = func.coalesce(agg.c.total, 0), func.coalesce(agg.c.done, 0)
    return case(
        (Series.reading_status.is_not(None), Series.reading_status),
        (and_(total_c > 0, agg.c.total_main > 0, agg.c.done_main == agg.c.total_main), "completed"),
        (or_(done_c > 0, func.coalesce(agg.c.started, 0) > 0), "reading"),
        else_="unread")


def filter_query(db, query, f, title, agg):
    """The query with the filters in f added: the GET /api/series filters (q, genre, exclude, pub, read,
    match, format, have, fav, year_min, year_max, score_min, letter). The read filter needs agg joined."""
    if f.get("q"):
        like = f"%{f['q']}%"
        query = query.filter(or_(Series.folder_name.ilike(like), Series.title_english.ilike(like),
                                 Series.title_romaji.ilike(like), Series.title_native.ilike(like),
                                 cast(Series.synonyms, String).ilike(like),
                                 cast(Series.authors, String).ilike(like)))
    for name in f.get("genre") or []:
        query = query.filter(exists().where(SeriesTag.series_id == Series.id, SeriesTag.tag_id == Tag.id,
                                            Tag.name == name))
    for name in f.get("exclude") or []:
        query = query.filter(~exists().where(SeriesTag.series_id == Series.id, SeriesTag.tag_id == Tag.id,
                                             Tag.name == name))
    if split(f.get("pub")):
        query = query.filter(Series.publication_status.in_(split(f["pub"])))
    if split(f.get("read")):
        query = query.filter(status_expr(agg).in_(split(f["read"])))
    if split(f.get("match")):
        query = query.filter(Series.match_status.in_([m for k in split(f["match"]) for m in MATCH.get(k, [])]))
    if split(f.get("format")):
        query = query.filter(or_(*[FORMATS[x]() for x in split(f["format"]) if x in FORMATS]))
    wanted = [h for h in split(f.get("have")) if h in HAVE]
    if wanted:
        query = query.filter(_have_filter(db, wanted))
    if f.get("fav"):
        query = query.filter(Series.favorite.is_(True))
    if f.get("year_min"):
        query = query.filter(Series.start_year >= f["year_min"])
    if f.get("year_max"):
        query = query.filter(Series.start_year <= f["year_max"])
    if f.get("score_min"):
        query = query.filter(Series.average_score >= f["score_min"])
    letter = (f.get("letter") or "").strip().lower()
    first = func.substr(func.ltrim(title), 1, 1)
    if len(letter) == 1 and "a" <= letter <= "z":
        query = query.filter(first == letter)
    elif letter == "#":
        query = query.filter(~first.between("a", "z"))
    return query


def visible_series(db, request) -> list:
    """SQL conditions on Series for what this request may see, from the plugins' series_filter()
    (e.g. libraries: only the picked library, never a locked one). [] with no such plugin on."""
    return plugins.series_conditions(db, request)


def hidden_from(db, request, series_ids) -> bool:
    """Whether any of these series is hidden from this request (e.g. in a locked library)."""
    conds = visible_series(db, request)
    ids = {i for i in series_ids if i is not None}
    if not conds or not ids:
        return False
    found = db.query(Series.id).filter(Series.id.in_(ids))
    return found.filter(*conds).count() < found.count()
