"""Turn a folder name into a search query and score provider results (spec 6.2)."""
import re

from rapidfuzz import fuzz

from app.metadata.text import normalize_title

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


def score(query, titles, year=None, cand_year=None, cand_format=None):
    s = title_similarity(query, titles)
    if year and cand_year:
        s += 5 if abs(year - cand_year) <= 1 else -10
    if cand_format in ("NOVEL", "Novel") and "novel" not in query.lower():
        s -= 15  # the library is manga; a light novel with the same name is the wrong match
    return max(0, min(100, round(s)))


def pick(scored, threshold=90, lead=10):
    """scored: list of (score, item), best first. The best item if confident, else None."""
    if not scored or scored[0][0] < threshold:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < lead:
        return None
    return scored[0][1]
