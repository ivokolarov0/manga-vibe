"""Remote control's API, at /api/plugins/mcp/api/...

- mcp: the MCP endpoint. Assistants use /mcp, which keys.KeyGuard checks and hands on to here.
- screens/poll, screens/state, screens/result: the open pages (mcp.js) wait for commands and answer them
- screens, keys, info: Settings → Remote control (everyone's own)
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app import users
from app.plugins import Context
from app.plugins.web import plugin_ctx

from . import keys, protocol
from .screens import hub

router = APIRouter()


# ---------------------------------------------------------------- MCP

@router.post("/mcp")
async def mcp(request: Request):
    if request.scope.get(users.SCOPE_KEY) is None:  # not through /mcp and its key check
        raise HTTPException(404, "Connect to /mcp with a key")
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(protocol._error(None, -32700, "Not JSON"), status_code=400)
    answer = await protocol.handle(body)
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(answer)


@router.get("/mcp")
@router.delete("/mcp")
def mcp_no_stream():
    # no event stream from the server, no sessions to end (protocol.py)
    return Response(status_code=405, headers={"Allow": "POST"})


# ---------------------------------------------------------------- the open pages

class ScreenInfo(BaseModel):
    screen: str = Field(min_length=1, max_length=64)
    name: str = Field("", max_length=80)
    visible: bool = True
    focused: bool = False
    input_ago: float | None = None
    where: dict = {}
    commands: list = []


@router.post("/screens/poll")
async def screens_poll(body: ScreenInfo):
    """Wait for commands for this page (screens.POLL_SECONDS at most)."""
    return {"commands": await hub.poll(users.current_id(), body.model_dump())}


@router.post("/screens/state")
def screens_state(body: ScreenInfo):
    hub.report(users.current_id(), body.model_dump())
    return {"ok": True}


class Answer(BaseModel):
    screen: str
    id: int
    ok: bool = True
    result: object = None
    error: str = ""


@router.post("/screens/result")
def screens_result(body: Answer):
    if body.id:
        hub.answer(users.current_id(), body.screen, body.id, body.ok, body.result if body.ok else body.error)
    return {"ok": True}


@router.get("/screens")
def screens_list():
    live = hub.live(users.current_id())
    return [s.public(chosen=(i == 0)) for i, s in enumerate(live)]


# ---------------------------------------------------------------- keys (each person's own)

@router.get("/keys")
def list_keys(ctx: Context = Depends(plugin_ctx)):
    return [keys.public(k) for k in keys.list_keys(ctx.db, users.current_id())]


class NewKey(BaseModel):
    name: str = Field("", max_length=60)


@router.post("/keys")
def create_key(body: NewKey, ctx: Context = Depends(plugin_ctx)):
    """A new key; the key itself is in the answer only this once."""
    if users.current().id == 0:
        raise HTTPException(401, "Log in first")
    try:
        row, token = keys.create(ctx.db, users.current_id(), body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ctx.db.commit()
    return {**keys.public(row), "key": token}


@router.delete("/keys/{key_id}")
def delete_key(key_id: str, ctx: Context = Depends(plugin_ctx)):
    if not keys.delete(ctx.db, users.current_id(), key_id):
        raise HTTPException(404, "No such key")
    ctx.db.commit()
    return {"ok": True}


@router.get("/info")
def info(request: Request):
    """What the settings page shows: where to connect."""
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}{keys.PATH}"}
