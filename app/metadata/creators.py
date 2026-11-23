"""Who made a series: the writers and artists among Series.authors, without the translators, letterers,
assistants and other staff a provider lists (AniList's staff list has them all)."""
import re

# role names (lower case, a provider's note in brackets dropped) that make someone a creator:
# AniList's Story & Art / Story / Art / Original Story / Original Creator, MangaUpdates' Author / Artist
CREATOR_ROLES = {"story & art", "story and art", "story", "art", "original story", "original creator",
                 "original work", "author", "artist", "writer", "illustration", "illustrator"}


def creator_roles(role):
    """The creator roles in an author's role text ("Story & Art, Translator" -> ["Story & Art"]), or
    None when there are none, so the person isn't a creator. No role at all (a name typed in the editor,
    a provider that doesn't say) counts as a creator, with no roles."""
    parts = [re.sub(r"\s*\(.*?\)", "", p).strip() for p in (role or "").split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return []
    kept = [p for p in parts if p.lower() in CREATOR_ROLES]
    return kept or None


def creators(authors):
    """Series.authors' creators, each name once with its creator roles: [{"name", "roles": [...]}]."""
    out = {}
    for a in authors or []:
        name = (a.get("name") or "").strip()
        roles = creator_roles(a.get("role")) if name else None
        if roles is None:
            continue
        mine = out.setdefault(name, [])
        mine += [r for r in roles if r not in mine]
    return [{"name": n, "roles": r} for n, r in out.items()]
