"""Jobs: the parts of the app a plugin can do, and replace (Plugin API 15, issue #203).

MangaVibe is a hub: a small core, and plugins that together make the app (issue #202). A plugin
says which jobs it does, `provides = {"library-query": 1}`, and which it needs another plugin to do,
`requires = {"library-query": 1}`. The number is the version of the job's contract: what the plugin
gets, what it must support and what it gives back (docs/PLUGINS.md, Jobs).

- An **exclusive** job is done by one plugin at a time, e.g. the series page: switching one on
  switches the other off. Any number of plugins can do a shared job.
- A **required** job is one the app needs: the last plugin doing it can't be switched off, only
  replaced by switching on another.

The plugin manager (app/plugins/__init__.py) leaves out a plugin it can't run: a job or contract
version this MangaVibe doesn't have, or a need no switched-on plugin meets.

Jobs are added here as parts of the app become plugins (#204 the library filters, #205 the views...).
"""
from dataclasses import dataclass

from app.i18n import N_  # labels and descriptions are shown in the page's language (#263)


@dataclass(frozen=True)
class Job:
    key: str               # e.g. "library-query", "view:series"
    label: str             # shown in Settings -> Plugins, e.g. "Library filters and sorting"
    versions: tuple = (1,)  # the contract versions this MangaVibe has
    exclusive: bool = True  # one plugin at a time
    required: bool = False  # the app needs a plugin doing it
    description: str = ""


# key -> Job, in the order Settings shows them
JOBS: dict[str, Job] = {}


def register(job: Job) -> Job:
    JOBS[job.key] = job
    return job


# --- the jobs ---------------------------------------------------------------------------------

# The library's filters and sorting (#204): what GET /api/series can filter and sort by, and what the
# filter, sort and letter sheets show. Contract: docs/PLUGINS.md, "The library-query job";
# the SDK side is app/plugins/query.py. Built in: app/plugins/builtin/library_query/.
register(Job("library-query", N_("Library filters and sorting"), required=True,
             description=N_("What the library can be filtered and sorted by, and how")))


# Views (#205 on): a plugin draws a part of the page from its templates/ folder (VIEWS in
# app/plugins/web.py says which template and which slots it must keep). Contract: docs/PLUGINS.md,
# "Replacing a view".
register(Job("view:card", N_("Series cards"), required=True,
             description=N_("How a series looks in the library grid, its list view and Continue reading")))
register(Job("view:library", N_("Library page"), required=True,
             description=N_("The library: its filters' buttons, the cards, paging and multi-select, from MangaVibe.library")))
register(Job("view:continue", N_("Continue reading page"), required=True,
             description=N_("The series in progress, from MangaVibe.continue")))
register(Job("view:home", N_("Home page"), required=True,
             description=N_("The first page's rows of what's new, from MangaVibe.home")))
register(Job("view:series", N_("Series page"), required=True,
             description=N_("The page a series opens on, drawn from MangaVibe.series")))


# Plugin API 20 (#208): the dialogs, the top bar and Settings' sections
register(Job("dialog:edit", N_("Edit dialog"), required=True,
             description=N_("The series' Edit dialog (info and poster), drawn with editSeries()")))
register(Job("dialog:fix-match", N_("Fix match dialog"), required=True,
             description=N_("Picking the right metadata for a series, drawn with fixMatch()")))
register(Job("view:nav", N_("Navigation"), required=True,
             description=N_("The top bar and Library / Continue (a bar, or a sidebar); it must keep the Settings button")))
# A Settings section's contract is the setting keys it changes (GET / PUT /api/settings). Not required: with
# its plugin off, the section and its menu entry are gone, and the keys keep their values.
for _key, _label, _description in (
        ("library", N_("Settings: Library folders"), N_("The Library folders page of Settings")),
        ("display", N_("Settings: Display"), N_("The Display page of Settings")),
        ("reader", N_("Settings: Reader"), N_("The Reader page of Settings")),
        ("metadata", N_("Settings: Metadata"), N_("The Metadata page of Settings")),
        ("updates", N_("Settings: Updates"), N_("The Updates page of Settings"))):
    register(Job(f"settings:{_key}", _label, required=False, description=_description))


# Plugin API 21 (#219): the Needs attention lists, a shared job: every plugin doing it adds lists
register(Job("attention:list", N_("Needs attention lists"), exclusive=False,
             description=N_("Lists of things in the library worth a look, in Settings → Needs attention")))

# Plugin API 22 (#209): saved searches. The data is the job's, not the plugin's, so a replacement finds
# every list: setting `jobs:saved-searches` (each account's own under user:<id>:jobs:saved-searches),
# [{"id": int, "name": str, "query": "genre=Isekai&sort=year"}]. Shared: every plugin doing it sees it.
register(Job("saved-searches", N_("Saved searches"), exclusive=False,
             description=N_("Named library filters and sort, kept in the jobs:saved-searches setting")))

# Plugin API 23 (#210): server logic people may want done differently. Contracts in docs/PLUGINS.md.
# parse_name(filename, series_title) -> Parsed. A rescan after switching re-reads every book's numbers.
register(Job("scanner:parser", N_("File name parser"), required=True,
             description=N_("Volume, chapter and revision from a book's file name")))
# match_query(folder_name) -> (query, year); match_score(query, titles, year, cand_year, cand_format,
# novel) -> 0-100; match_pick([(score, candidate)...], threshold, lead) -> the candidate or None
register(Job("metadata:matcher", N_("Metadata matcher"), required=True,
             description=N_("Which provider result is the series, from its folder name")))
# Plugin API 24 (#211): the page reader, a script: MangaVibe.registerReader(<id>, "images", {...}). The
# reader host (reader.js) keeps the bars, progress, overlays, read aloud and the Android keys.
register(Job("reader:images", N_("Image reader"), required=True,
             description=N_("Draws the pages in the reader: paged or vertical, zoom, swipes, the fit")))
# book_label(book) -> str. Off: books are called by their file names.
register(Job("book:label", N_("Book labels"),
             description=N_("What a book is called in the lists, the reader and Continue (Vol 3, Ch 12-14)")))


def job_label(key: str) -> str:
    job = JOBS.get(key)
    return job.label if job else key


def jobs_of(value) -> dict[str, int]:
    """A plugin's `provides` or `requires` as {job: version}. A list of keys means version 1 of each.
    Raises ValueError for anything else, so a plugin with a typo there doesn't run half set up."""
    if not value:
        return {}
    if isinstance(value, (list, tuple, set)):
        value = {k: 1 for k in value}
    if not isinstance(value, dict):
        raise ValueError("must be a dict of job -> contract version, e.g. {\"library-query\": 1}")
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError(f"{k!r} isn't a job name")
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ValueError(f"the version of {k!r} must be a whole number from 1, not {v!r}")
        out[k.strip()] = v
    return out
