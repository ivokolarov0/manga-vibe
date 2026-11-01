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
# a number on its own, not part of a word: "12" in "Title 12+extra", but not the digits of a
# release group like "(1r0n)" or "(senfgurke2)"
LONE_NUM = re.compile(r"(?<![a-z\d.])(\d+(?:\.\d+)?)(?![a-z\d])", re.I)


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
        """Groups: volume-only books, chapters, extras, unnumbered (spec 4.3). An extra with a
        chapter or volume number stays in its place, right after that chapter or volume."""
        inf = float("inf")
        vol = self.volume if self.volume is not None else inf
        name = natural_key(self.name)
        after = 1 if self.extra else 0
        if self.chapter is not None:
            return (1, self.chapter, vol, after, name)
        if self.volume is not None:
            return (0, self.volume, 0, after, name)
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


def _suffix(letter):
    """'a' -> +0.1, 'b' -> +0.2 so 15a sorts between 15 and 16."""
    return 0.1 * (ord(letter.lower()) - 96) if letter else 0


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def _clean(s):
    """Without [groups], {tags}, (2020) (Digital) and the like, underscores and extra spaces."""
    s = PARENS.sub(" ", BRACKETS.sub(" ", s)).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse(filename: str, series_title: str = "") -> Parsed:
    p = Parsed(name=filename)
    s = _clean(ARCHIVE_EXT.sub("", filename))

    # strip the series title so digits in it don't count ("Mob Psycho 100"). It is the folder
    # name, cleaned like the file name; when the folder's tags differ from the file's
    # ("... (1r0n)" vs "... - 17-21 (1r0n) (f)"), the name without any (...) is tried too.
    if series_title:
        for t in (_clean(series_title), _clean(re.sub(r"\([^)]*\)", " ", series_title))):
            if t and re.search(re.escape(t), s, re.I):
                s = re.sub(re.escape(t), " ", s, flags=re.I).strip()
                break

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
        elif nums := LONE_NUM.findall(s):  # the last one, when text follows it
            p.chapter = float(nums[-1])
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
