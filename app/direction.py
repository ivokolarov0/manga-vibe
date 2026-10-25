"""Which way a series reads: rtl (manga, right to left), ltr (comics, left to right) or vertical (webtoons).

In this order:
  1. the owner's choice for the series (series page → ⋯ → Reading direction)
  2. its metadata (AniList, MangaUpdates or edited by hand): from Japan is manga (rtl); anything else
     (manhwa, manhua, a western comic…) reads left to right
  3. its library folder's default (Settings → Library folders), for series that aren't matched
  4. Settings → Reader → Default direction (manga unless changed)
"""
from app.db import get_setting
from app.models import Library

MODES = ("rtl", "ltr", "vertical")


def reading_direction(db, series) -> tuple[str, str]:
    """(mode, source): source is series, metadata, library or default (what decided it)."""
    if series.reader_mode in MODES:
        return series.reader_mode, "series"
    if series.country_of_origin or series.format:
        return ("rtl" if (series.country_of_origin or "").upper() == "JP" else "ltr"), "metadata"
    lib = db.get(Library, series.library_id)
    if lib is not None and lib.reader_mode in MODES:
        return lib.reader_mode, "library"
    mode = get_setting(db, "reader_mode", "rtl")
    return (mode if mode in MODES else "rtl"), "default"
