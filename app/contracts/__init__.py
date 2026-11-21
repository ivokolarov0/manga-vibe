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
register(Job("library-query", "Library filters and sorting", required=True,
             description="What the library can be filtered and sorted by, and how"))


# Views (#205 on): a plugin draws a part of the page from its templates/ folder (VIEWS in
# app/plugins/web.py says which template and which slots it must keep). Contract: docs/PLUGINS.md,
# "Replacing a view".
register(Job("view:card", "Series cards", required=True,
             description="How a series looks in the library grid, its list view and Continue reading"))


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
