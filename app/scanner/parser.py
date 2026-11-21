"""What a file name says (Parsed) and the order of a series' books. Reading the numbers from the name
is the `scanner:parser` job (#210): parse() asks the plugin doing it, built in
app/plugins/builtin/filename_parser/. The order stays the core's."""
from dataclasses import dataclass
from typing import Optional

from natsort import natsort_keygen, ns

natural_key = natsort_keygen(alg=ns.IGNORECASE)  # "file2" < "File10"


def parse(filename: str, series_title: str = "") -> "Parsed":
    """The numbers in a book's file name (series_title: its folder's name), from the parser plugin."""
    from app.plugins import ask_job
    p = ask_job("scanner:parser", "parse_name", filename, series_title, fallback=_builtin_parse)
    return p if isinstance(p, Parsed) else _builtin_parse(filename, series_title)  # not the contract


def _builtin_parse(filename, series_title=""):
    from app.plugins.builtin.filename_parser import parse as builtin
    return builtin(filename, series_title)


@dataclass
class Parsed:
    name: str
    volume: Optional[float] = None
    chapter: Optional[float] = None
    chapter_end: Optional[float] = None
    version: int = 1
    extra: bool = False
    extra_index: Optional[float] = None
    ref: object = None  # caller's object (e.g. a Book row), carried through order()

    def sort_key(self):
        """Groups: numbered books, extras, unnumbered (spec 4.3). Numbered books sort by volume,
        then chapter: a whole volume comes before its chapters, chapters without a volume after
        all volumes. An extra with a chapter or volume number stays in its place, right after
        that chapter or volume (after the volume's chapters)."""
        inf = float("inf")
        name = natural_key(self.name)
        after = 1 if self.extra else 0
        if self.chapter is not None or self.volume is not None:
            vol = self.volume if self.volume is not None else inf
            if self.chapter is not None:
                ch = self.chapter
            else:
                ch = inf if self.extra else -inf
            return (0, vol, ch, after, name)
        if self.extra:
            return (2, self.extra_index if self.extra_index is not None else inf, 0, 0, name)
        return (3, 0, 0, 0, name)

    def label(self):
        if self.extra and self.chapter is None and self.volume is None:
            return f"Extra{'' if self.extra_index is None else ' ' + _fmt(self.extra_index)}"
        parts = []
        if self.volume is not None:
            parts.append(f"Vol {_fmt(self.volume)}")
        if self.chapter is not None:
            c = f"Ch {_fmt(self.chapter)}"
            if self.chapter_end is not None:
                c += f"-{_fmt(self.chapter_end)}"
            parts.append(c)
        if self.version > 1:
            parts.append(f"(v{self.version} revision)")
        if self.extra:
            parts.append("extra")
        return " ".join(parts) or "?"


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def order(files, series_title=""):
    """Return (ordered, skipped, duplicates).

    A file is only skipped when a newer revision (v2, v3...) of the same
    chapter exists. Same-numbered files without a revision difference are all
    kept and reported as duplicates, never hidden.
    """
    return order_parsed([parse(f, series_title) for f in files])


def order_parsed(parsed):
    """Same as order(), for already-parsed (possibly overridden) items."""
    groups = {}
    for p in parsed:
        if p.extra or (p.chapter is None and p.volume is None):
            key = ("file", p.name)
        elif p.chapter is not None:
            key = ("ch", p.volume, p.chapter, p.chapter_end)  # v01 c01 and v07 c01 aren't the same
        else:
            key = ("vol", p.volume)
        groups.setdefault(key, []).append(p)

    kept, skipped, duplicates = [], [], []
    for items in groups.values():
        top = max(i.version for i in items)
        newest = [i for i in items if i.version == top]
        skipped += [i for i in items if i.version < top]
        kept += newest
        if len(newest) > 1:
            duplicates.append(newest)
    kept.sort(key=Parsed.sort_key)
    return kept, skipped, duplicates
