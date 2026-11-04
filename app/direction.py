"""How a series reads: paged (one page at a time, the next page to the right, like every digital reader)
or vertical (webtoons, one long strip).

In this order:
  1. the account's choice for the series (series page → ⋯ → Reading mode)
  2. its library's default (Settings → Libraries; the series' own library, else its folder's)
  3. the account's Settings → Reader → Default reading mode (paged unless changed)
"""
from app.db import get_user_setting
from app.models import Shelf, UserSeries
from app.shelves import shelf_id_of

MODES = ("paged", "vertical")


def reading_direction(db, series, user_id) -> tuple[str, str]:
    """(mode, source) for this account: source is series, library or default (what decided it)."""
    state = db.get(UserSeries, (user_id, series.id)) if user_id is not None else None
    if state is not None and state.reader_mode in MODES:
        return state.reader_mode, "series"
    shelf_id = shelf_id_of(db, series)
    shelf = db.get(Shelf, shelf_id) if shelf_id is not None else None
    if shelf is not None and shelf.reader_mode in MODES:
        return shelf.reader_mode, "library"
    mode = get_user_setting(db, user_id, "reader_mode", "paged")
    return (mode if mode in MODES else "paged"), "default"
