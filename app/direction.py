"""How a series reads: paged (one page at a time, the next page to the right, like every digital reader)
or vertical (webtoons, one long strip).

In this order:
  1. the owner's choice for the series (series page → ⋯ → Reading mode)
  2. its library folder's default (Settings → Library folders)
  3. Settings → Reader → Default reading mode (paged unless changed)
"""
from app.db import get_setting
from app.models import Library

MODES = ("paged", "vertical")


def reading_direction(db, series) -> tuple[str, str]:
    """(mode, source): source is series, library or default (what decided it)."""
    if series.reader_mode in MODES:
        return series.reader_mode, "series"
    lib = db.get(Library, series.library_id)
    if lib is not None and lib.reader_mode in MODES:
        return lib.reader_mode, "library"
    mode = get_setting(db, "reader_mode", "paged")
    return (mode if mode in MODES else "paged"), "default"
