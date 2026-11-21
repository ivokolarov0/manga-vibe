"""Needs attention: lists of things in the library worth a look, each a plugin's (the shared
`attention:list` job, Plugin API 21, issue #219). The app's own (unreadable files, missing metadata,
duplicates...) are the built-in "Needs attention" plugin's (app/plugins/builtin/attention_lists).

A plugin doing the job gives:
- `attention_lists`: [{key, label, help, alert, view}]. alert: its rows count towards the badge on Settings.
  view: its template, templates/attention/<view>.html in the plugin (else a plain list).
- `attention_rows(ctx, key, request)`: the rows of one list, of the series the request may see. Each row a
  dict; `key` (a string like "err:12") lets the owner ignore it, `series` sorts it.
- optionally `attention_count(ctx, key, request, ignored)`: the badge's share, cheaper than the rows.

The core keeps what's the same for every list: ignoring a row (a false alarm) and "Show ignored" (the
row keys in the `attention_ignored` setting), the count, and the frame in Settings.
"""
import logging
import re
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import get_db, get_setting, set_setting
from app.plugins import Context, manager as plugins
from app.users import manage

router = APIRouter(prefix="/api")
log = logging.getLogger("mangavibe.plugins")
JOB = "attention:list"
KEY_RE = re.compile(r"^[a-z0-9-]{1,30}:[0-9a-z:._-]{1,80}$")
LIST_KEY_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
RESERVED = {"count", "lists", "ignored_counts"}  # names in GET /api/attention's answer
MAX_IGNORED = 5000


def attention_lists(running=None):
    """[(plugin, list)] of the plugins doing the job, in their order. A list key another plugin has
    already, or that isn't a plain name, is left out (and logged)."""
    out, seen = [], set()
    for p in (running if running is not None else plugins.providers_of(JOB)):
        for spec in getattr(p, "attention_lists", None) or []:
            key = spec.get("key", "")
            if not LIST_KEY_RE.match(key) or key in RESERVED or key in seen:
                log.warning("Plugin %s: attention list %r left out (not a plain name, or taken)", p.id, key)
                continue
            seen.add(key)
            out.append((p, {"label": key, "help": "", "alert": False, "view": "", **spec, "plugin": p.id}))
    return out


def _rows(p, spec, db, request):
    """One list's rows, or [] when the plugin fails (logged): one broken list doesn't take the others."""
    try:
        return list(p.attention_rows(Context(p, db), spec["key"], request) or [])
    except HTTPException:
        raise
    except Exception:
        log.error("Plugin %s failed in its %s list:\n%s", p.id, spec["key"], traceback.format_exc())
        return []


def _ignored(db):
    return set(get_setting(db, "attention_ignored", []) or [])


@router.get("/attention")
def attention(request: Request, ignored: bool = False, db=Depends(get_db)):
    """Every list's rows, of the series the request may see (e.g. one library, never a locked one): the
    list key -> its rows. With ignored=true, ignored rows are included (marked "ignored": true).
    `lists` describes them, `count` is the badge, `ignored_counts` how many each list hides."""
    skip = _ignored(db)
    out, hidden, lists = {}, {}, []
    for p, spec in attention_lists():
        key = spec["key"]
        rows = _rows(p, spec, db, request)
        for row in rows:
            row["ignored"] = row.get("key") in skip
        hidden[key] = sum(1 for r in rows if r["ignored"])
        shown = rows if ignored else [r for r in rows if not r["ignored"]]
        out[key] = sorted(shown, key=lambda x: (str(x.get("series", "")).lower(), str(x.get("key", ""))))
        lists.append({k: spec[k] for k in ("key", "label", "help", "alert", "plugin")})
    out["count"] = sum(sum(1 for r in out[x["key"]] if not r["ignored"]) for x in lists if x["alert"])
    out["ignored_counts"] = hidden
    out["lists"] = lists
    return out


@router.get("/attention/count")
def attention_count(request: Request, db=Depends(get_db)):
    """Just the badge number (the alert lists' rows not ignored), from each plugin's attention_count()
    when it has one, so polling it every minute stays cheap on a big library."""
    skip, total = _ignored(db), 0
    for p, spec in attention_lists():
        if not spec["alert"]:
            continue
        if hasattr(p, "attention_count"):
            try:
                total += int(p.attention_count(Context(p, db), spec["key"], request, skip))
                continue
            except HTTPException:
                raise
            except Exception:
                log.error("Plugin %s failed counting its %s list:\n%s", p.id, spec["key"], traceback.format_exc())
        total += sum(1 for r in _rows(p, spec, db, request) if r.get("key") not in skip)
    return {"count": total}


class IgnoreBody(BaseModel):
    key: str = Field(max_length=120)
    ignore: bool = True
    list: str = Field("", max_length=40)  # the list it's in: only that one is looked at (else all)


@router.post("/attention/ignore", dependencies=[manage])
def ignore_row(body: IgnoreBody, request: Request, db=Depends(get_db)):
    """Hide one row as a false alarm (or bring it back with ignore=false). Only a row this request can see
    in a list (so not one about a series in a locked library): the row is looked up, not guessed from its key."""
    if not KEY_RE.match(body.key):
        raise HTTPException(400, "Unknown row")
    found = False
    for p, spec in attention_lists():
        if body.list and spec["key"] != body.list:
            continue
        if any(r.get("key") == body.key for r in _rows(p, spec, db, request)):
            found = True
            break
    if not found:  # restoring too: it's done from "Show ignored", where the row shows
        raise HTTPException(403, "Not a row you can see")
    keys = _ignored(db)
    (keys.add if body.ignore else keys.discard)(body.key)
    if len(keys) > MAX_IGNORED:
        raise HTTPException(400, "Too many ignored rows")
    set_setting(db, "attention_ignored", sorted(keys))
    db.commit()
    return {"ok": True}
