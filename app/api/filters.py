"""What decides which series a list shows: what plugins allow (e.g. the libraries plugin: one library,
never a locked one), and the SQL pieces the library filters use. The filters themselves are the
`library-query` job's (issue #204): app/plugins/builtin/library_query/, reached through
app/plugins/query.py."""
from sqlalchemy import and_, case, func, or_, select

from app.api.common import TITLE_FIELDS, collection_of, my_progress_join, my_state, numberings
from app.models import Book, Progress, Series
from app.plugins import manager as plugins


def collections(db, series):
    """{series_id: collection} for these series (rows with id, numbering, totals and publication
    status), from the numbering stored on each series."""
    nums = numberings(db, series)
    return {s.id: collection_of(s, nums[s.id]) for s in series}


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
            .outerjoin(Progress, my_progress_join())  # the current user's reading
            .where(visible).group_by(Book.series_id).subquery())


def title_expr(lang):
    """The displayed title (empty fields skipped, like display_title), lower case for sorting."""
    return func.lower(func.coalesce(*[func.nullif(getattr(Series, f), "")
                                      for f in TITLE_FIELDS.get(lang, TITLE_FIELDS["english"])],
                                    Series.folder_name))


def status_expr(agg):
    total_c, done_c = func.coalesce(agg.c.total, 0), func.coalesce(agg.c.done, 0)
    manual = my_state("reading_status")
    return case(
        (manual.is_not(None), manual),
        (and_(total_c > 0, agg.c.total_main > 0, agg.c.done_main == agg.c.total_main), "completed"),
        (or_(done_c > 0, func.coalesce(agg.c.started, 0) > 0), "reading"),
        else_="unread")


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
