"""Editions of a series (regular, kanzenban, bunkoban, omnibus...) and which one the files are from.

AniList has one volume count, usually the regular edition's. MangaUpdates lists every edition
("18 Kanzenban Volumes (Complete)") in its status and in the publishers' notes. The pages per file
tell them apart: a 2-volume perfect edition of a 5-volume manga has much thicker volumes."""
import math
import re

REGULAR_PAGES = 200  # pages in a regular volume, roughly (most are 180-230)
THICK = 1.3          # files at least this much thicker than that are from a bigger edition

_NUMBERED = re.compile(r"^\s*(\d+)\s+(.+)$")
_NOT_VOLUMES = re.compile(r"chapter|episode|webtoon|one[- ]?shot", re.I)
_VOLUMES = re.compile(r"vol|omnibus|edition|ban\b|bunko|wide", re.I)
_FILLER = re.compile(r"\b(physical|print|digital|volumes?|vols?)\b\.?", re.I)
_NOT_A_NAME = re.compile(r"^(complete[d]?|ongoing|hiatus|cancell?ed|discontinued|reprint|incomplete|"
                         r"by\b.*|in \d{4}|\d{4}.*)$", re.I)
_BY = re.compile(r"\bby\s+(.+?)(?:\s+in\s+\d{4})?$", re.I)


def _name(rest):
    """'Kanzenban Volumes (Complete)' -> 'Kanzenban'; 'Volumes (Bunkoban, by Shogakukan in 1991)'
    -> 'Bunkoban'; 'Vols - Complete' -> None (no name: a plain run of volumes)."""
    head = re.split(r"\(|\s-\s|;", rest, maxsplit=1)[0]
    words = " ".join(_FILLER.sub(" ", head).split()).strip(" ,-")
    inside = re.search(r"\(([^)]*)\)", rest)
    parts = [p.strip() for p in inside.group(1).split(",")] if inside else []
    extra = next((p for p in parts if p and not _NOT_A_NAME.match(p)), None)
    publisher = next((m.group(1) for p in parts if (m := _BY.match(p))), None)
    name = " ".join(x for x in (words, extra) if x) or None
    if name and publisher:
        name += f", {publisher}"
    return name


def parse(text, publisher=None):
    """[(volumes, name or None)] from MangaUpdates text: its status, or one publisher's notes."""
    out = []
    for part in re.split(r"[\n|]|;(?=\s*\d)", text or ""):
        m = _NUMBERED.match(part)
        if not m or _NOT_VOLUMES.search(m.group(2)) or not _VOLUMES.search(m.group(2)):
            continue
        name = _name(m.group(2))
        if publisher:
            name = f"{publisher} {name}" if name else publisher
        out.append((int(m.group(1)), name))
    return out


def from_mangaupdates(s):
    """[{"volumes": n, "names": [...]}, ...], biggest first, from a MangaUpdates series record."""
    found = parse(s.get("status"))
    for p in s.get("publishers") or []:
        if p.get("type") == "English" and p.get("publisher_name"):
            found += parse(p.get("notes"), p["publisher_name"])
    by_count = {}
    for n, name in found:
        if n <= 0:
            continue
        names = by_count.setdefault(n, [])
        if name and name not in names:
            names.append(name)
    return [{"volumes": n, "names": by_count[n]} for n in sorted(by_count, reverse=True)]


def pick(regular, editions, pages, top=0):
    """The volume count of the edition the files look like. regular: AniList's count; pages: the
    usual pages per file; top: the highest volume number here (an edition can't have fewer).
    Stays with the regular count unless the files are clearly thicker than regular volumes."""
    if not regular or not pages or pages < REGULAR_PAGES * THICK:
        return regular
    counts = {e["volumes"] for e in editions or [] if top <= e["volumes"] < regular}
    if not counts:
        return regular
    estimate = regular * REGULAR_PAGES / pages  # what the same story needs at this thickness
    return min(counts | {regular}, key=lambda c: abs(math.log(c / estimate)))


def names_of(editions, volumes):
    return next((e["names"] for e in editions or [] if e["volumes"] == volumes), [])
