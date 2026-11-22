"""Library filters and sorting (built-in plugin, issue #204): the `library-query` job.

What the library page can be filtered and sorted by, and how: the search box, reading status,
publication, metadata, format, collection, genres and tags, author, length, favorites, the first letter, and the
sorts. The page's filter, sort and letter sheets, the filter chips and saved searches are built from
query_schema(), so a plugin that replaces this one (provides = {"library-query": 1}) changes all of them.

A copy of this file is a good start for your own: keep the parameter names you keep (saved searches and
links store them), and see docs/PLUGINS.md, "The library-query job".
"""
from sqlalchemy import String, and_, cast, exists, func, or_

from app.plugins.sdk import Series, SeriesTag, Tag
from app.plugins import Plugin
from app.plugins.query import flag, number, values

READING = [("unread", "Unread"), ("reading", "Reading"), ("completed", "Completed"), ("on_hold", "On hold"),
           ("dropped", "Dropped"), ("plan_to_read", "Plan to read")]
PUBLICATION = [("RELEASING", "Releasing"), ("FINISHED", "Finished"), ("HIATUS", "Hiatus"),
               ("CANCELLED", "Cancelled"), ("NOT_YET_RELEASED", "Not yet released"), ("UNKNOWN", "Unknown status")]
METADATA = [("matched", "Has metadata"), ("attention", "Needs attention"), ("unmatched", "No metadata")]
FORMAT = [("manga", "Manga"), ("manhwa", "Manhwa"), ("manhua", "Manhua"), ("one_shot", "One-shot"),
          ("novel", "Novel")]
# finished series only: are all volumes/chapters here? (best effort, see collection() in app/api/common.py)
COLLECTION = [("complete", "Complete collection"), ("incomplete", "Incomplete collection"),
              ("unknown", "Can't tell if complete")]

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
LENGTH_UNITS = [("chapters", "Chapters", Series.chapters_total), ("volumes", "Volumes", Series.volumes_total)]

OLDEST = ["Oldest first", "Newest first"]
# key, label, the order it starts in, the order buttons' labels [ascending, descending]
SORTS = [("title", "Title", "asc", ["A → Z", "Z → A"]), ("added", "Recently added", "desc", OLDEST),
         ("updated", "Recently updated", "desc", OLDEST), ("read", "Recently read", "desc", OLDEST),
         ("score", "Score", "desc", ["Lowest first", "Highest first"]), ("year", "Year", "desc", OLDEST),
         ("unread", "Unread count", "desc", ["Fewest first", "Most first"])]


def choice(key, label, options):
    return {"key": key, "label": label, "kind": "choice", "params": [key], "options": [list(o) for o in options]}


SCHEMA = {
    "filters": [
        {"key": "q", "label": "Search", "kind": "text", "params": ["q"]},
        choice("read", "Reading status", READING),
        choice("pub", "Publication", PUBLICATION),
        choice("match", "Metadata", METADATA),
        choice("format", "Format", FORMAT),
        choice("have", "Collection", COLLECTION),
        {"key": "length", "label": "Length", "kind": "length",
         "units": [[u, label] for u, label, _ in LENGTH_UNITS],
         "params": [p for u, _, _ in LENGTH_UNITS for p in (f"{u}_min", f"{u}_max")] + ["length_unknown"]},
        {"key": "genres", "label": "Genres & tags", "kind": "tags", "params": ["genre", "exclude"]},
        # the series page links each author name here (author=A&author=B: by both)
        {"key": "author", "label": "Author", "kind": "names", "params": ["author"]},
        {"key": "fav", "label": "Favorites", "kind": "toggle", "params": ["fav"]},
        {"key": "letter", "label": "Starts with", "kind": "letter", "params": ["letter"]},
        # only through links and assistants for now: the sheets don't show them
        {"key": "year", "label": "Year", "kind": "range", "params": ["year_min", "year_max"], "hidden": True},
        {"key": "score", "label": "Score", "kind": "range", "params": ["score_min"], "hidden": True},
    ],
    "sorts": [{"key": k, "label": label, "order": order, "order_labels": labels} for k, label, order, labels in SORTS],
    "default_sort": "title",
}


def by_author(name):
    """SQL condition: one of the series' authors (Series.authors, a JSON list of {name, role}) is
    called exactly `name`."""
    people = func.json_each(Series.authors).table_valued("value").alias("people")
    return exists().select_from(people).where(func.json_extract(people.c.value, "$.name") == name)


def have_filter(lq, wanted):
    """SQL condition for the Collection filter. Only finished series (and ones the owner marked
    complete) can be complete or incomplete, so those are worked out (in Python, like the chapter-gap
    check) and everything else is unknown."""
    cond = (or_(Series.publication_status == "FINISHED", Series.collection_complete.is_(True)),
            Series.missing_since.is_(None))
    finished = lq.db.query(Series.id, Series.numbering, Series.chapters_total, Series.volumes_total,
                           Series.publication_status, Series.collection_complete, Series.editions,
                           Series.edition_volumes).filter(*cond).all()
    by_state = {k: set() for k in HAVE}
    for sid, c in lq.collections(finished).items():
        by_state[c["state"]].add(sid)
    known = by_state["complete"] | by_state["incomplete"]
    conds = [Series.id.in_(by_state[k]) for k in ("complete", "incomplete") if k in wanted]
    if "unknown" in wanted:
        conds.append(~Series.id.in_(known))
    return or_(*conds)


class LibraryQuery(Plugin):
    id = "library-query"
    name = "Library filters and sorting"
    category = "library"
    version = "1.0"
    description = ("What the library can be filtered and sorted by: reading status, publication, genres "
                   "and tags, author, length, favorites, first letter and the sorts. Built in; replace it with a "
                   "plugin that does the same job.")
    permissions = ["Decides which series the library lists and in what order; connects to nothing"]
    provides = {"library-query": 1}

    def query_schema(self, ctx):
        return SCHEMA

    def filter_query(self, ctx, lq, params):
        """The GET /api/series filters (q, genre, exclude, author, pub, read, match, format, have, fav, year_min,
        year_max, score_min, chapters_min/max, volumes_min/max, length_unknown, letter).
        The chapter/volume ranges are on the published totals (metadata), not the books here; a series
        with no known total (ongoing, no metadata) only matches them with length_unknown on."""
        q = lq.query
        if params.get("q"):
            like = f"%{params['q']}%"
            q = q.filter(or_(Series.folder_name.ilike(like), Series.title_english.ilike(like),
                             Series.title_romaji.ilike(like), Series.title_native.ilike(like),
                             cast(Series.synonyms, String).ilike(like), cast(Series.authors, String).ilike(like)))
        for name in params.getlist("genre"):
            if name:
                q = q.filter(exists().where(SeriesTag.series_id == Series.id, SeriesTag.tag_id == Tag.id,
                                            Tag.name == name))
        for name in params.getlist("exclude"):
            if name:
                q = q.filter(~exists().where(SeriesTag.series_id == Series.id, SeriesTag.tag_id == Tag.id,
                                             Tag.name == name))
        for name in params.getlist("author"):
            if name.strip():
                q = q.filter(by_author(name.strip()))
        if values(params, "pub"):
            q = q.filter(Series.publication_status.in_(values(params, "pub")))
        if values(params, "match"):
            q = q.filter(Series.match_status.in_([m for k in values(params, "match") for m in MATCH.get(k, [])]))
        if values(params, "format"):
            q = q.filter(or_(*[FORMATS[x]() for x in values(params, "format") if x in FORMATS]))
        wanted = [h for h in values(params, "have") if h in HAVE]
        if wanted:
            q = q.filter(have_filter(lq, wanted))
        if flag(params, "fav"):
            q = q.filter(lq.my_state("favorite").is_(True))
        for key, col, op in (("year_min", Series.start_year, "ge"), ("year_max", Series.start_year, "le"),
                             ("score_min", Series.average_score, "ge")):
            n = number(params, key)
            if n:  # 0 is no limit, as before (a score of at least 0 would leave out unscored series)
                q = q.filter(col >= n if op == "ge" else col <= n)
        unknown = flag(params, "length_unknown")
        for unit, _, col in LENGTH_UNITS:
            lo, hi = number(params, f"{unit}_min"), number(params, f"{unit}_max")
            if lo is None and hi is None:
                continue
            cond = and_(col.is_not(None), *([col >= lo] if lo is not None else []),
                        *([col <= hi] if hi is not None else []))
            q = q.filter(or_(col.is_(None), cond) if unknown else cond)
        letter = (params.get("letter") or "").strip().lower()
        first = func.substr(func.ltrim(lq.title), 1, 1)
        if len(letter) == 1 and "a" <= letter <= "z":
            q = q.filter(first == letter)
        elif letter == "#":
            q = q.filter(~first.between("a", "z"))
        if values(params, "read"):
            q = q.filter(lq.reading_status().in_(values(params, "read")))
        lq.query = q

    def sort_order(self, ctx, lq, params):
        """sort=title|added|updated|read|score|year|unread, order=asc|desc (else the sort's own)."""
        sort = params.get("sort") or "title"
        found = next((s for s in SORTS if s[0] == sort), SORTS[0])
        cols = {"title": lambda: lq.title, "added": lambda: Series.added_at, "updated": lambda: Series.updated_at,
                "read": lambda: lq.my_state("last_read_at"), "score": lambda: Series.average_score,
                "year": lambda: Series.start_year, "unread": lq.unread_count}
        col = cols[found[0]]()
        order = params.get("order") or found[2]
        return [col.desc().nulls_last() if order == "desc" else col.asc().nulls_last()]
