"""Example: listen to reading events, and add settings and buttons to Settings -> Plugins.

Copy this file into the `plugins` folder of your data folder and press Settings -> Reload plugins.
It keeps a short log of what you read and shows the newest entry on the series page.
A tracker sync (MyAnimeList, AniList...) is built the same way: on_series_status_changed()
pushes the status, a global action pulls your list, handle_web() receives the login callback.
"""
from app.plugins import Action, FieldDef, OpenUrl, Plugin, SettingDef


class ReadingLog(Plugin):
    id = "reading-log"
    name = "Reading log"
    version = "1.0"
    description = "Example: keeps a log of what you start and finish."
    permissions = ["Sees what you start and finish reading", "Has its own web page"]
    fields = [FieldDef("last_event", "Last reading event")]
    settings_fields = [SettingDef("prefix", "Log prefix", help="Put in front of every log line")]
    global_actions = [Action("clear", "Clear the log", confirm="Clear the reading log?"),
                      Action("page", "Open the log page")]
    actions = [Action("show", "Show the newest log line")]

    def _log(self, ctx, series, text):
        line = f"{ctx.settings.get('prefix', '')}{text}"
        log = ctx.settings.get("log", [])[-49:] + [line]
        ctx.save_settings({**ctx.settings, "log": log})
        ctx.set_field(series, "last_event", line)
        ctx.log.info(line)

    # reading events: run in the background, after the change is saved
    def on_book_started(self, ctx, book, series):
        self._log(ctx, series, f"started {book.file_name}")

    def on_book_completed(self, ctx, book, series):
        self._log(ctx, series, f"finished {book.file_name}")

    def on_series_status_changed(self, ctx, series, old, new):
        ended = series.publication_status == "FINISHED"
        self._log(ctx, series, f"{series.folder_name}: {old} -> {new}" + (" (series has ended)" if ended else ""))

    # buttons
    def run_action(self, ctx, action_key, series):
        return ctx.get_field(series, "last_event") or "Nothing logged yet"

    def run_global_action(self, ctx, action_key):
        if action_key == "page":
            return OpenUrl(ctx.web_url("log"))
        ctx.save_settings({**ctx.settings, "log": []})
        return "Log cleared"

    def status(self, ctx):
        return f"{len(ctx.settings.get('log', []))} entries"

    # a page of its own: GET /api/plugins/reading-log/web/log
    def handle_web(self, ctx, path, params):
        if path != "log":
            return None  # 404
        return " | ".join(ctx.settings.get("log", [])) or "The log is empty."
