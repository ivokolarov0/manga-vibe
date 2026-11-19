"""The Model Context Protocol, the part MangaVibe needs: JSON-RPC 2.0 over Streamable HTTP, stateless.

An assistant POSTs a message (or a batch, for older clients) to /mcp and gets the answer back as JSON;
a notification gets 202 and no body. No event stream: every tool answers its own call, so the server
never has to start a conversation. No sessions either: each request carries its key (keys.py).
Written out here (~100 lines) instead of adding the `mcp` package and its dependencies.
https://modelcontextprotocol.io/specification
"""
import json
import logging
import traceback

from app import users

from . import tools

log = logging.getLogger("mangavibe.mcp")

# newest first; an older client gets the one it asks for, if it's one of these
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """MangaVibe is a manga server: a library of series, each with books (volumes, chapters) read in its reader.
You act as the person whose key this is, with their rights.
- Data tools (library_search, series_get, continue_reading, settings_get...) work without any screen.
- Screen tools (screen_*, reader_*) control an app the person has open (screens_list says which). To help
  someone who can't see the screen, use screen_describe: it says where they are and lists every control
  with a ref, which screen_press and screen_fill use. Anything a person can do with a tap can be done
  that way, also in plugins' parts of the app.
- Tools named <plugin>__<name> come from plugins."""


def _version():
    try:
        from app import updater
        return updater.current().get("version") or "dev"
    except Exception:
        return "dev"


def _error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def as_content(value) -> dict:
    """A tool's answer as MCP content: text as it is, anything else as JSON (and structured too)."""
    if isinstance(value, str):
        return {"content": [{"type": "text", "text": value}]}
    text = json.dumps(value, ensure_ascii=False, indent=1, default=str)
    out = {"content": [{"type": "text", "text": text}]}
    if isinstance(value, dict):
        out["structuredContent"] = value
    return out


async def call_tool(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return {"content": [{"type": "text", "text": "arguments must be an object"}], "isError": True}
    found = tools.available().get(name)
    if found is None:
        return None
    if found.admin and not users.current().can_manage:
        return {"content": [{"type": "text", "text": "Only an admin can use this"}], "isError": True}
    missing = [k for k in found.required if args.get(k) in (None, "")]
    if missing:
        return {"content": [{"type": "text", "text": f"Missing: {', '.join(missing)}"}], "isError": True}
    try:
        return as_content(await found.run(args))
    except tools.ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except (ValueError, TypeError) as e:
        return {"content": [{"type": "text", "text": f"Wrong arguments: {e}"}], "isError": True}
    except Exception as e:
        log.error("MCP tool %s failed:\n%s", name, traceback.format_exc())
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}


async def handle_one(msg):
    """One JSON-RPC message -> its answer, or None for a notification (or an answer sent to us)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Not a JSON-RPC 2.0 message")
    method, id_ = msg.get("method"), msg.get("id")
    if method is None or "id" not in msg:  # a notification (initialized, cancelled) or a client's answer
        return None
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(id_, {"protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                             "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": "MangaVibe", "title": "MangaVibe", "version": _version()},
                             "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": [t.schema() for t in tools.available().values()]})
    if method == "tools/call":
        result = await call_tool(params)
        if result is None:
            return _error(id_, -32602, f"Unknown tool {params.get('name')!r}")
        return _result(id_, result)
    if method in ("resources/list", "prompts/list"):
        return _result(id_, {method.split("/")[0]: []})
    return _error(id_, -32601, f"Method {method!r} not found")


async def handle(body):
    """A POSTed body (one message or a batch) -> what to answer, or None for 202 Accepted."""
    if isinstance(body, list):
        answers = [a for a in [await handle_one(m) for m in body] if a is not None]
        return answers or None
    return await handle_one(body)
