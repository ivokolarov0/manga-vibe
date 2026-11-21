"""Turn a folder name into a search query and score provider results (spec 6.2): the `metadata:matcher`
job (#210). These ask the plugin doing it, built in app/plugins/builtin/title_matcher/."""


def _builtin():
    from app.plugins.builtin import title_matcher
    return title_matcher


def clean_query(folder_name):
    """'[Group] Berserk (1989) (Digital)' -> ('Berserk', 1989)"""
    from app.plugins import ask_job
    return ask_job("metadata:matcher", "match_query", folder_name,
                   fallback=lambda *a: _builtin().clean_query(*a))


def score(query, titles, year=None, cand_year=None, cand_format=None, novel=False):
    """0-100: how well a candidate with these titles (and year, format) fits the query. novel: the
    series' books are light novels (text EPUBs), so the novel is the right match."""
    from app.plugins import ask_job
    return ask_job("metadata:matcher", "match_score", query, titles, year, cand_year, cand_format, novel,
                   fallback=lambda *a: _builtin().score(*a))


def pick(scored, threshold=90, lead=10):
    """scored: list of (score, item), best first. The best item if confident, else None."""
    from app.plugins import ask_job
    return ask_job("metadata:matcher", "match_pick", scored, threshold, lead,
                   fallback=lambda *a: _builtin().pick(*a))
