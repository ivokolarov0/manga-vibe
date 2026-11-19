"""Remote control (built-in plugin): an MCP server, so an assistant (and, through the Manga Accessibility
plugin, a voice) can use the whole app: search and filter the library, open series, read, turn pages,
change settings, and press any button on a screen where MangaVibe is open.

Off when MangaVibe is installed or updated (it's a new way in); switched on in Settings → Plugins, each
person makes keys in Settings → Remote control and gives one to their assistant with the address
http://<the app's address>/mcp. A key acts as its person (app/users.py).

- keys.py: the keys, and KeyGuard in front of /mcp
- protocol.py: MCP's JSON-RPC over HTTP
- tools.py: the tools: data ones through the app's own API, screen ones through screens.py
- screens.py: the open pages that wait for commands (long polling)
- api.py: its API at /api/plugins/mcp/api/...
- mcp.js, settings.html, mcp.css: the page's side (a "screen"), Settings → Remote control

Plugins add tools: `mcp_tools` and run_mcp_tool() on the server, MangaVibe.screenCommand() in the page.
See docs/remote-control.md.
"""
from pathlib import Path

from app.plugins import Plugin

from . import api, keys

HERE = Path(__file__).parent


def _read(name):
    return (HERE / name).read_text(encoding="utf-8")


class RemoteControl(Plugin):
    id = "mcp"
    category = "system"
    name = "Remote control (MCP)"
    version = "1.0"
    description = ("Lets an assistant use MangaVibe for you through MCP: search, read, turn pages, change "
                   "settings and press any button on an open screen. The base for voice control. Built in.")
    permissions = ["Answers assistants at /mcp that bring one of your keys; each acts as the person who made it",
                   "Open pages ask the server for an assistant's commands and do them",
                   "Keeps the keys (only their hashes) in MangaVibe's database; connects to nothing"]
    enabled_by_default = False  # a new way into the app: the owner switches it on
    router = api.router
    middleware = keys.KeyGuard
    middleware_order = -10  # before the accounts plugin's login: /mcp brings a key instead
    js = _read("mcp.js")
    css = _read("mcp.css")
    html = {"settings": _read("settings.html")}
