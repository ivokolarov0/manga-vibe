"""Book labels (built-in plugin, issue #210): does the `book:label` job, what the lists, the reader and
Continue call a book: "Vol 3", "Ch 12-14", "Vol 2 Ch 9", "Extra 2", "Ch 5 extra". A book without numbers
is called by its file name. With the job off (no plugin doing it), every book is called by its file name.

Replace it with a plugin of your own that provides {"book:label": 1} and has book_label(book), e.g.
"Volume 3" or another language.
"""
from pathlib import Path

from app.plugins import Plugin


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def label(b):
    """b: a Book. Its numbers with the owner's changes (eff_*)."""
    if b.eff_extra and b.eff_chapter is None and b.eff_volume is None:
        return "Extra" + ("" if b.extra_index is None else f" {_fmt(b.extra_index)}")
    parts = []
    if b.eff_volume is not None:
        parts.append(f"Vol {_fmt(b.eff_volume)}")
    if b.eff_chapter is not None:
        end = b.eff_chapter_end
        parts.append(f"Ch {_fmt(b.eff_chapter)}" + (f"-{_fmt(end)}" if end is not None else ""))
    if b.eff_extra:
        parts.append("extra")
    return " ".join(parts) or Path(b.file_name).stem


class BookLabels(Plugin):
    id = "book-labels"
    name = "Book labels"
    category = "library"
    version = "1.0"
    description = "Calls books by their numbers (Vol 3, Ch 12-14, Extra 2) instead of their file names. Built in."
    permissions = ["Reads book numbers; connects to nothing"]
    provides = {"book:label": 1}

    def book_label(self, book):
        return label(book)
