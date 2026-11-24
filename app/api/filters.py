"""What decides which series a list shows: what plugins allow (e.g. the libraries plugin: one library,
never a locked one), and the SQL pieces the library filters use. The filters themselves are the
`library-query` job's (issue #204): app/plugins/builtin/library_query/, reached through
app/plugins/query.py."""
from sqlalchemy import and_, case, func, or_, select

from app import users
from app.api.common import TITLE_FIELDS, collection_of, my_state, numberings
from app.models import Series, SeriesRead, SeriesTotal
from app.plugins import manager as plugins


def collections(db, series):
    """{series_id: collection} for these series (rows with id, numbering, totals and publication
    status), from the numbering stored on each series."""
    nums = numberings(db, series)
    return {s.id: collection_of(s, nums[s.id]) for s in series}


def reading_aggregates():
    """Per-series counts of visible books (sid, total, done, started, total_main, done_main), used for
    reading status, unread count and sorting: the stored ones (SeriesTotal, and the current user's
    SeriesRead, kept by the database's triggers), not a count of every book."""
    mine = and_(SeriesRead.series_id == SeriesTotal.series_id, SeriesRead.user_id == users.current_id())
    return (select(SeriesTotal.series_id.label("sid"), SeriesTotal.total.label("total"),
                   func.coalesce(SeriesRead.done, 0).label("done"),
                   func.coalesce(SeriesRead.started, 0).label("started"),
                   SeriesTotal.main.label("total_main"),
                   func.coalesce(SeriesRead.done_main, 0).label("done_main"))
            .outerjoin(SeriesRead, mine).subquery())


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
