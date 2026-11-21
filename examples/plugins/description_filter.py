"""Library filters with one more: Description (has one, or not). An example of replacing the
library-query job (Plugin API 16, docs/PLUGINS.md "The library-query job").

It does the whole job (provides = {"library-query": 1}), so switching it on switches off the built-in
"Library filters and sorting"; switching that one on again brings it back. It starts from the built-in
plugin and adds a filter: a "choice" in query_schema() shows up in the filter sheet, as a chip and in
saved searches by itself, without any markup or script.
"""
from sqlalchemy import func, or_

from app.models import Series
from app.plugins.builtin.library_query import SCHEMA, LibraryQuery, choice
from app.plugins.query import values

DESCRIPTION = choice("desc", "Description", [("yes", "Has a description"), ("no", "No description")])


class DescriptionFilter(LibraryQuery):
    id = "description-filter"
    name = "Library filters + Description"
    category = "library"
    version = "1.0"
    description = "The built-in library filters and sorting, with a filter for series with or without a description."
    permissions = ["Decides which series the library lists and in what order; connects to nothing"]
    provides = {"library-query": 1}

    def query_schema(self, ctx):
        # the built-in filters, with ours after the other choice groups
        filters = list(SCHEMA["filters"])
        last_choice = max(i for i, f in enumerate(filters) if f["kind"] == "choice")
        filters.insert(last_choice + 1, DESCRIPTION)
        return {**SCHEMA, "filters": filters}

    def filter_query(self, ctx, lq, params):
        super().filter_query(ctx, lq, params)
        wanted = set(values(params, "desc"))
        empty = or_(Series.description.is_(None), func.trim(Series.description) == "")
        if wanted == {"yes"}:
            lq.query = lq.query.filter(~empty)
        elif wanted == {"no"}:
            lq.query = lq.query.filter(empty)
