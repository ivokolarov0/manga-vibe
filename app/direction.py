"""How a series reads: paged (one page at a time, the next page to the right, like every digital reader)
or vertical (webtoons, one long strip).

In this order:
  1. the owner's choice for the series (series page → ⋯ → Reading mode)
  2. a plugin's default (Plugin.reading_mode), e.g. the libraries plugin: its library's mode
  3. Settings → Reader → Default reading mode (paged unless changed)
"""
from app import users
from app.db import get_setting

MODES = ("paged", "vertical")


def reading_direction(db, series) -> tuple[str, str, str]:
    """(mode, source, why): source is series, plugin or default (what decided it), why says it in
    words for the series page, e.g. "the mode of Webtoons"."""
    if series.reader_mode in MODES:
        return series.reader_mode, "series", "set for this series"
    from app.plugins import manager as plugins
    found = plugins.reading_mode(db, series)
    if found and found[0] in MODES:
        return found[0], "plugin", found[1]
    # Settings → Reader: the user's own (app/users.py), else the shared one
    mode = get_setting(db, users.personal_key("reader_mode"), get_setting(db, "reader_mode", "paged"))
    return (mode if mode in MODES else "paged"), "default", "Settings → Reader"
