"""Title matcher (built-in plugin, issue #210): does the `metadata:matcher` job. Turns a folder name into
a search query, scores the providers' results against it and picks the best one if it's sure (spec 6.2).
The providers themselves stay plugins of their own (is_provider).

Replace it with a plugin of your own that provides {"metadata:matcher": 1} and has match_query(),
match_score() and match_pick(); tests/contracts/test_matcher_job.py shows what this one does.
"""
import re

from rapidfuzz import fuzz

from app.plugins import Plugin
from app.plugins.sdk import normalize_title

BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
YEAR = re.compile(r"\((\d{4})\)")
NOISE = re.compile(r"\((?:digital|official|complete|[^)]*scan[^)]*|[^)]*rip[^)]*)\)", re.I)
PARENS = re.compile(r"\([^)]*\)")


def clean_query(folder_name):
    """'[Group] Berserk (1989) (Digital)' -> ('Berserk', 1989)"""
    m = YEAR.search(folder_name)
    year = int(m.group(1)) if m else None
    s = BRACKETS.sub(" ", folder_name)
    s = YEAR.sub(" ", s)
    s = NOISE.sub(" ", s)
    s = s.replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" -")
    stripped = PARENS.sub(" ", s).strip()  # drop any other (...) if something is left
    return (re.sub(r"\s+", " ", stripped) if stripped else s), year


def title_similarity(query, titles):
    q = normalize_title(query)
    best = 0
    for t in titles:
        n = normalize_title(t)
        if not n:
            continue
        best = max(best, fuzz.ratio(q, n), fuzz.token_sort_ratio(q, n) - 2)
    return best


def score(query, titles, year=None, cand_year=None, cand_format=None, novel=False):
    """novel: the series' books are light novels (text EPUBs), so the novel is the right match."""
    s = title_similarity(query, titles)
    if year and cand_year:
        s += 5 if abs(year - cand_year) <= 1 else -10
    is_novel = cand_format in ("NOVEL", "Novel")
    if novel:
        if cand_format and not is_novel:
            s -= 15  # the books are the light novel; its manga adaptation is the wrong match
    elif is_novel and "novel" not in query.lower():
        s -= 15  # the library is manga; a light novel with the same name is the wrong match
    return max(0, min(100, round(s)))


def pick(scored, threshold=90, lead=10):
    """scored: list of (score, item), best first. The best item if confident, else None."""
    if not scored or scored[0][0] < threshold:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < lead:
        return None
    return scored[0][1]


class TitleMatcher(Plugin):
    id = "title-matcher"
    name = "Title matcher"
    category = "metadata"
    version = "1.0"
    description = ("Finds a series' entry from its folder name: cleans the name, scores each provider's "
                   "results by title, year and format, and takes the best one only when it's sure. Built in.")
    permissions = ["Reads series folder names; connects to nothing (the providers do)"]
    provides = {"metadata:matcher": 1}

    def match_query(self, folder_name):
        return clean_query(folder_name)

    def match_score(self, query, titles, year=None, cand_year=None, cand_format=None, novel=False):
        return score(query, titles, year, cand_year, cand_format, novel)

    def match_pick(self, scored, threshold=90, lead=10):
        return pick(scored, threshold, lead)
