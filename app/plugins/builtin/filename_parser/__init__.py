"""Filename parser (built-in plugin, issue #210): does the `scanner:parser` job. A book's file name (and
its series' folder name) -> volume, chapter, chapter_end, version (the scanner sets extra only when
the owner marks one: extras are written too many ways to guess, 12+extra, 12.5, omake...).

Replace it with a plugin of your own that provides {"scanner:parser": 1} and has parse_name(); the
contract tests in tests/contracts/test_parser_job.py show what this one does.
"""
import re

from app.plugins import Plugin
from app.plugins.sdk import Parsed

ARCHIVE_EXT = re.compile(r"\.(cbz|zip|cbr|rar|cb7|7z|epub|pdf)$", re.I)
BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")           # [Group], {tags}
PARENS = re.compile(r"\((?:digital|\d{4}|[^)]*scan[^)]*|[^)]*rip[^)]*)\)", re.I)
# "v01", "vol.1", "volume 1", "Vol_01", "第1巻"
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


def _suffix(letter):
    """'a' -> +0.1, 'b' -> +0.2 so 15a sorts between 15 and 16."""
    return 0.1 * (ord(letter.lower()) - 96) if letter else 0


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


class FilenameParser(Plugin):
    id = "filename-parser"
    name = "File name parser"
    category = "library"
    version = "1.0"
    description = ("Reads volume, chapter and revision from file names (Vol 3, c012-014, c012v2, 第1巻). "
                   "Built in.")
    permissions = ["Reads book file names; connects to nothing"]
    provides = {"scanner:parser": 1}

    def parse_name(self, filename, series_title=""):
        return parse(filename, series_title)
