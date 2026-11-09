"""Optimize (built-in plugin): makes books smaller by converting their PNG, JPEG, BMP and TIFF pages to
JPEG XL (or AVIF, WebP), lossy. "Optimize this series" on a series page, or "Optimize all series"
in Settings -> Plugins -> Optimize. Starts switched off: it replaces the owner's files.

How it converts and swaps files safely is in core.py. MangaVibe shows every format in any browser
(pages are sent to the reader as WebP), but other comic apps may not open JPEG XL or AVIF.
"""
from pathlib import Path

from app.plugins import Action, Plugin, SettingDef
from app.plugins.builtin.optimize import core

FORMAT_OPTIONS = [("jxl", "JPEG XL (smallest)"), ("avif", "AVIF"), ("webp", "WebP (opens in the most apps)")]
QUALITY_OPTIONS = [("high", "High (bigger files)"), ("normal", "Normal"), ("small", "Small (smaller files)")]
REPLACES = ("Pages are converted to {fmt} and the old files are replaced. The original pages can't be "
            "brought back. Continue?")


def settings_of(ctx):
    """(format, quality number) from the plugin's settings."""
    fmt = ctx.setting("format") if ctx.setting("format") in core.FORMATS else "jxl"
    level = ctx.setting("quality") if ctx.setting("quality") in core.QUALITY[fmt] else "normal"
    return fmt, core.QUALITY[fmt][level]


def _percent(rec):
    before, after = rec.get("before") or 0, rec.get("after") or 0
    return round(100 * (before - after) / before) if before else 0


class Optimize(Plugin):
    id = "optimize"
    name = "Optimize"
    version = "1.0"
    description = ("Makes books smaller: converts PNG and JPEG pages to JPEG XL (or AVIF, WebP), lossy. "
                   "For one series from its page, or for all of them here. It replaces your files. Built in.")
    permissions = ["Replaces book files in your manga folder when you ask it to (never on its own)",
                   "Connects to nothing"]
    enabled_by_default = False
    settings_fields = [
        SettingDef("format", "Convert pages to", options=FORMAT_OPTIONS, default="jxl",
                   help="MangaVibe shows all three in every browser. Other comic apps may not open "
                        "JPEG XL or AVIF."),
        SettingDef("quality", "Quality", options=QUALITY_OPTIONS, default="normal",
                   help="Lossy: Normal looks the same as the original when reading."),
    ]
    actions = [Action("optimize", "Optimize this series", confirm=REPLACES.format(fmt="the format picked "
                                                                                     "in Settings → Plugins → Optimize"))]
    global_actions = [
        Action("all", "Optimize all series", confirm=REPLACES.format(fmt="the format picked above")
               + " This can take hours; reading works meanwhile."),
        Action("stop", "Stop"),
    ]

    def on_start(self, ctx):
        if core.queue(ctx.db):  # the app restarted during "Optimize all": carry on
            core.start(ctx)

    def run_action(self, ctx, action_key, series):
        books = [b for b in series.books if core.candidate(b)]
        if not books:
            return "This series has no books that can be optimized (EPUB and PDF aren't)"
        try:
            core.writable(Path(series.folder_path))
        except core.Skip as e:
            return str(e)
        recs = core.records(ctx, series)
        if not any(core.todo(b, recs.get(str(b.id)), retry=True) for b in books):
            return "This series is optimized already"
        ctx.set_series_data(series, "retry", True)
        core.add_to_queue(ctx, [series.id], first=True)
        core.start(ctx)
        return "Optimizing started. Each book shows how much it saved"

    def run_global_action(self, ctx, action_key):
        if action_key == "stop":
            if not core.running() and not core.queue(ctx.db):
                return "Nothing is being optimized"
            core.stop(ctx)
            return "Stopping after the page being converted"
        ids = core.series_to_optimize(ctx)
        if not ids:
            return "Every series is optimized already"
        added = core.add_to_queue(ctx, ids)
        core.start(ctx)
        return f"Optimizing {len(ids)} series" if added else "Optimizing is running already"

    def status(self, ctx):
        t = core.totals(ctx.db)
        saved = t.get("before", 0) - t.get("after", 0)
        done = (f"Saved {core._size(saved)} in {t['books']} books" if t.get("books")
                else "Nothing optimized yet")
        if not core.running():
            return done
        waiting = max(0, len(core.queue(ctx.db)) - 1)
        s = core.state
        line = f"Optimizing {s['series'] or '…'}, book {s['book']} of {s['books']}"
        if waiting:
            line += f" · {waiting} series waiting"
        return f"{line} · {done[0].lower()}{done[1:]}"

    def book_badges(self, ctx, books):
        if not books:
            return {}
        recs = core.records(ctx, books[0].series_id)
        out = {}
        for b in books:
            rec = recs.get(str(b.id))
            if core.running() and core.state["book_id"] == b.id:
                out[b.id] = "Optimizing…"
            elif rec and rec.get("size") == b.file_size and rec.get("mtime") == b.mtime:
                if "error" in rec:
                    out[b.id] = "Not optimized"
                elif _percent(rec) > 0:
                    out[b.id] = f"−{_percent(rec)}%"
        return out
