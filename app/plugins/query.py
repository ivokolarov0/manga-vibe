"""What a plugin doing the `library-query` job gets from MangaVibe (Plugin API 16, issue #204).

That plugin decides what the library can be filtered and sorted by (docs/PLUGINS.md, "The
library-query job"). It gives three things:

- `query_schema(ctx)`: its filters and sorts as data, for the page's sheets, chips and saved searches
- `filter_query(ctx, lq, params)`: adds its conditions to `lq.query`
- `sort_order(ctx, lq, params)`: the ORDER BY, as a list of SQL expressions

`lq` is a `ListQuery`: the query (`lq.query`, on `models.Series`) and what a filter needs from the core,
so a plugin doesn't import `app.api`. `params` are the request's query parameters (a Starlette
`QueryParams`: `.get(key)`, `.getlist(key)`); `values()`, `number()`, `day()`, `day_range()` and `flag()`
below read them.

The core does the rest: what the person may see at all (plugins' `series_filter()`, e.g. a locked
library; a filter plugin never sees or changes those conditions), the last sort on the title and the
series' id (paging needs a stable order), paging, counts and the cards.
"""
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func

from app.api import filters as _core
from app.i18n import t
from app.api.common import my_state
from app.models import Series

__all__ = ["ListQuery", "values", "number", "day", "day_range", "time_zone", "flag", "schema_params",
           "search_params"]


class ListQuery:
    """The library list being built. Change `query` (filter, join); don't take conditions away."""

    def __init__(self, db, query, lang):
        self.db = db
        self.query = query    # on models.Series
        self.lang = lang      # the title language picked in Settings -> Display
        # the displayed title in lower case (as the cards show it), for sorting and the first letter
        self.title = _core.title_expr(lang)
        self._reading = None

    def reading(self):
        """The current user's per-series reading counts (a subquery with sid, total, done, started,
        total_main, done_main). The core joins it onto the query once the plugin is done (result()), so
        use it anywhere in a filter or sort. The counts are stored (the database keeps them up to date),
        so it's cheap; it's still only joined when a filter or sort asks for it."""
        if self._reading is None:
            self._reading = _core.reading_aggregates()
        return self._reading

    def result(self):
        """The query with what the plugin asked for joined (the core calls this)."""
        if self._reading is None:
            return self.query
        return self.query.outerjoin(self._reading, self._reading.c.sid == Series.id)

    def reading_status(self):
        """The reading status the app shows (unread, reading, completed, plan_to_read, on_hold, dropped)
        as SQL. Joins the reading counts."""
        return _core.status_expr(self.reading())

    def unread_count(self):
        """How many visible books aren't read yet, as SQL. Joins the reading counts."""
        r = self.reading()
        return func.coalesce(r.c.total, 0) - func.coalesce(r.c.done, 0)

    def my_state(self, name):
        """The current user's own value for a series as SQL: favorite, last_read_at, reading_status (the
        one picked by hand)."""
        return my_state(name)

    def collections(self, series):
        """{series_id: collection} for these series (rows with id, numbering, totals, publication status):
        collection["state"] is complete, incomplete or unknown."""
        return _core.collections(self.db, series)


def values(params, key) -> list[str]:
    """A comma-separated parameter as a list: read=unread,reading -> ["unread", "reading"]."""
    return [x for x in (params.get(key) or "").split(",") if x]


def number(params, key) -> int | None:
    """A whole-number parameter, None when it isn't given. Anything else is refused (422)."""
    raw = params.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(422, t("{key} must be a whole number", key=key))


def day(params, key) -> date | None:
    """A day parameter (2026-11-27), None when it isn't given. Anything else is refused (422)."""
    raw = (params.get(key) or "").strip()
    if not raw:
        return None
    try:
        if len(raw) != 10:
            raise ValueError
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(422, t("{key} must be a day like 2026-11-27", key=key))


def time_zone(params) -> tzinfo:
    """The person's time zone: the page sends its own (tz=Europe/Sofia) with the library's requests. UTC
    when there's none or this server doesn't know it (an assistant's request)."""
    name = (params.get("tz") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc


def day_range(params, start_key, end_key):
    """The two days of a "from … to …" filter as times to compare a stored (UTC) time with: (from, until),
    where `from` is the start of the first day and `until` the start of the day after the last one, in the
    person's time zone (time_zone()), so whole days count: stored >= from and stored < until. Each is None
    when that day isn't given."""
    tz = time_zone(params)

    def start(d):
        return datetime.combine(d, time(), tz).astimezone(timezone.utc).replace(tzinfo=None)

    first, last = day(params, start_key), day(params, end_key)
    return (start(first) if first else None, start(last + timedelta(days=1)) if last else None)


def flag(params, key) -> bool:
    """An on/off parameter: 1, true, yes or on."""
    return (params.get(key) or "").lower() in ("1", "true", "yes", "on")


def search_params(db) -> set[str] | None:
    """The query parameters of the library's filters and sort, from the plugin doing the library-query
    job (e.g. what a saved search keeps). None if no plugin can say."""
    from app.plugins import Context, manager
    for p in manager.doing("library-query"):
        try:
            return set(schema_params(p.query_schema(Context(p, db)))) | {"sort", "order"}
        except Exception:
            continue
    return None


def schema_params(schema: dict) -> list[str]:
    """Every query parameter a schema's filters read, in order: what saved searches keep (plus sort and
    order)."""
    out = []
    for f in schema.get("filters") or []:
        for p in f.get("params") or []:
            if p not in out:
                out.append(p)
    return out
