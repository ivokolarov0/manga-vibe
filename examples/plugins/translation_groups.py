"""Example MangaVibe plugin. Copy this file into /data/plugins/ to use it.

Shows the plugin features:
- extra field:   "Translation groups" (taken from [Group] tags in file names)
- scan hook:     a `series.json` sidecar file can fix chapter numbers, e.g.
                 {"chapters": {"weird_file_name.zip": 12}}
- custom action: "Find missing chapters" lists gaps like 14 -> 52
"""
import json
import re

from app.plugins import Action, FieldDef, Plugin

GROUP = re.compile(r"\[([^\]]+)\]")


class TranslationGroups(Plugin):
    id = "translation-groups"
    name = "Translation groups"
    version = "1.0"
    description = "Shows scanlation groups from file names and finds missing chapters."
    permissions = ["Reads series.json in series folders", "Changes chapter ordering during scans"]
    fields = [FieldDef("groups", "Translation groups")]
    actions = [Action("gaps", "Find missing chapters")]

    def on_book_scanned(self, ctx, book, parsed):
        raw = ctx.read_series_file(book.series, "series.json")
        if raw:
            fixes = json.loads(raw).get("chapters", {})
            if book.file_name in fixes:
                parsed.chapter = float(fixes[book.file_name])
                parsed.extra = False

    def on_series_scanned(self, ctx, series, books):
        groups = sorted({g for b in books for g in GROUP.findall(b.file_name)})
        ctx.set_field(series, "groups", ", ".join(groups) or None)

    def run_action(self, ctx, action_key, series):
        chapters = sorted({int(b.chapter) for b in series.books
                           if b.chapter is not None and not b.hidden and b.missing_since is None})
        gaps = [f"{a} -> {b}" for a, b in zip(chapters, chapters[1:]) if b - a > 1]
        return "Missing chapters between: " + "; ".join(gaps) if gaps else "No gaps found"
