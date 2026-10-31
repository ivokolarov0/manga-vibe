"""Filename parser for manga files: detects volume, chapter and version. Extras are never guessed
from names (they are written too many ways: 12+extra, 12.5, omake...); the owner marks them."""
import re
from dataclasses import dataclass
from typing import Optional

from natsort import natsort_keygen, ns

natural_key = natsort_keygen(alg=ns.IGNORECASE)  # "file2" < "File10"

ARCHIVE_EXT = re.compile(r"\.(cbz|zip|cbr|rar|cb7|7z|epub|pdf)$", re.I)
BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")           # [Group], {tags}
PARENS = re.compile(r"\((?:digital|\d{4}|[^)]*scan[^)]*|[^)]*rip[^)]*)\)", re.I)

VOL = re.compile(r"(?:\bvolume|\bvol|\bv)[ ._-]*(\d+(?:\.\d+)?)|第\s*(\d+)\s*巻", re.I)
# ranges like ch01-05 / c001-c010
CH_RANGE = re.compile(r"(?:\bchapter|\bch|\bc|#)[ ._-]*(\d+(?:\.\d+)?)[ ]*[-~][ ]*(?:ch|c)?[ ._]*(\d+(?:\.\d+)?)", re.I)
CH = re.compile(r"(?:\bchapter|\bch|\bc|#)[ ._-]*(\d+(?:\.\d+)?)([a-z])?\b|第\s*(\d+)\s*話()", re.I)
# revision marker glued to a number: ch03v2 -> version 2 (NOT volume 2)
VERSION = re.compile(r"(?<=\d)v(\d+)\b", re.I)
# "Chapter 053 - Title" / "Ch.5: Title": what follows the separator is the chapter's title
TITLE_SEP = re.compile(r"\s*[-–—:]\s")
TRAILING_NUM = re.compile(r"(\d+(?:\.\d+)?)([a-z])?\s*$", re.I)
# the last number in the name, when text follows it ("Title 12+extra")
LAST_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?!.*\d)")
# the last number in the name, when text follows it ("Title 12+extra")
LAST_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?!.*\d)")


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
        """Groups: volume-only books, chapters, extras, unnumbered (spec 4.3)."""
        inf = float("inf")
        vol = self.volume if self.volume is not None else inf
        name = natural_key(self.name)
        if self.extra:
            return (2, self.extra_index if self.extra_index is not None else inf, 0, name)
        if self.chapter is not None:
            return (1, self.chapter, vol, name)
        if self.volume is not None:
            return (0, self.volume, 0, name)
        return (3, 0, 0, name)

    def label(self):
        if self.extra:
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
        return " ".join(parts) or "?"


def _suffix(letter):
    """'a' -> +0.1, 'b' -> +0.2 so 15a sorts between 15 and 16."""
    return 0.1 * (ord(letter.lower()) - 96) if letter else 0


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def parse(filename: str, series_title: str = "") -> Parsed:
    p = Parsed(name=filename)
    s = ARCHIVE_EXT.sub("", filename)
    s = BRACKETS.sub(" ", s)
    s = PARENS.sub(" ", s)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()

    # strip the series title so digits in it don't count
    if series_title:
        t = re.sub(r"[\s_]+", " ", series_title).strip()
        s = re.sub(re.escape(t), " ", s, flags=re.I).strip()

    m = VERSION.search(s)
    if m:
        p.version = int(m.group(1))
        s = s[:m.start()] + s[m.end():]

    # drop a chapter's title, so numbers in it ("Around Volume 15") don't give it a volume
    m = CH.search(s)
    if m:
        end = (CH_RANGE.match(s, m.start()) or m).end()
        if TITLE_SEP.match(s, end):
            s = s[:end]

    m = CH_RANGE.search(s)
    if m:
        p.chapter, p.chapter_end = float(m.group(1)), float(m.group(2))
        s = s[:m.start()] + " " + s[m.end():]
    else:
        m = CH.search(s)
        if m:
            num = m.group(1) or m.group(3)
            p.chapter = float(num) + (_suffix(m.group(2)))
            s = s[:m.start()] + " " + s[m.end():]

    m = VOL.search(s)
    if m:
        p.volume = float(m.group(1) or m.group(2))
        s = s[:m.start()] + " " + s[m.end():]

    # bare number ("Title52", "Title 12+omake") -> chapter, the most common convention
    if p.chapter is None and p.volume is None:
        m = TRAILING_NUM.search(s)
        if m:
            p.chapter = float(m.group(1)) + _suffix(m.group(2))
        elif m := LAST_NUM.search(s):
            p.chapter = float(m.group(1))
    return p


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
            key = ("ch", p.chapter, p.chapter_end)
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
