"""Screens: the open apps (browser tabs, the Android app) an assistant can control.

Each open page of a logged-in person asks the server for commands (mcp.js, POST screens/poll) and waits
until one comes, or POLL_SECONDS pass, then asks again. A command from an assistant goes to one of that
person's screens: the one asked for, else the one they used last (shown first, touched last). The page
does it and sends the answer (screens/result), which the assistant's tool call is waiting for.

Long polling instead of a WebSocket or event stream: nothing to add to the server (uvicorn has no
WebSocket library here), it gets through every proxy and the Android WebView, and a page that goes
away simply stops asking. Kept in memory: MangaVibe runs as one process.

The waits are futures of whichever event loop asked; they're resolved with call_soon_threadsafe, so a
command can come from another thread or loop (tests use several).
"""
import asyncio
import itertools
import threading
import time
from dataclasses import dataclass, field

POLL_SECONDS = 25      # how long a page's ask waits before it asks again
LIVE_SECONDS = 45      # a page that hasn't asked for this long is gone
FORGET_SECONDS = 600   # ...and is forgotten after this
ANSWER_SECONDS = 20    # how long a tool waits for the page to answer


class NoScreen(Exception):
    """No open app to send the command to."""


class ScreenTimeout(Exception):
    """The page didn't answer in time."""


@dataclass
class Screen:
    id: str
    user_id: int
    name: str = ""
    visible: bool = True
    focused: bool = False
    input_at: float = 0.0          # when the person last touched it (time.time())
    seen_at: float = 0.0           # its last ask (time.monotonic())
    where: dict = field(default_factory=dict)  # view, address, title (what is on it, roughly)
    commands: list = field(default_factory=list)  # the plugins' screen commands it offers
    queue: list = field(default_factory=list)     # commands not handed over yet
    waiter: tuple | None = None    # (loop, future) of the ask that is waiting

    def public(self, chosen=False):
        return {"screen": self.id, "name": self.name, "visible": self.visible, "focused": self.focused,
                "last_touched_seconds_ago": round(time.time() - self.input_at) if self.input_at else None,
                **({"default": True} if chosen else {}), **self.where}


def _resolve(loop, fut, value=None, error=None):
    def done():
        if not fut.done():
            fut.set_exception(error) if error else fut.set_result(value)
    try:
        loop.call_soon_threadsafe(done)
    except RuntimeError:  # that loop is closed: nobody is waiting any more
        pass


class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.screens: dict[tuple[int, str], Screen] = {}
        self.pending: dict[int, tuple] = {}  # command id -> (loop, future, user id, screen id)
        self.ids = itertools.count(1)

    # ------------------------------------------------------------ the pages

    def _update(self, user_id, info) -> Screen:
        sid = str(info.get("screen") or "")[:64]
        if not sid:
            raise ValueError("No screen id")
        s = self.screens.get((user_id, sid))
        if s is None:
            s = self.screens[(user_id, sid)] = Screen(sid, user_id)
        s.name = str(info.get("name") or "")[:80] or "Browser"
        s.visible = bool(info.get("visible", True))
        s.focused = bool(info.get("focused", False))
        ago = info.get("input_ago")
        if isinstance(ago, (int, float)) and ago >= 0:
            s.input_at = max(s.input_at, time.time() - ago)
        where = info.get("where")
        if isinstance(where, dict):
            s.where = {k: where[k] for k in ("view", "address", "title") if k in where}
        commands = info.get("commands")
        if isinstance(commands, list):
            s.commands = [c for c in commands if isinstance(c, dict) and c.get("plugin") and c.get("name")][:100]
        s.seen_at = time.monotonic()
        return s

    def report(self, user_id, info):
        """A page says how it is now (shown, touched, what it shows), without waiting for commands."""
        with self.lock:
            self._update(user_id, info)

    async def poll(self, user_id, info, timeout=None) -> list:
        """A page asks for commands: those waiting, else the ones that come within `timeout` seconds."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        with self.lock:
            self._forget_old()
            s = self._update(user_id, info)
            if s.waiter:  # an older ask of the same page (it was reloaded): it gets nothing more
                _resolve(*s.waiter, value=[])
                s.waiter = None
            if s.queue:
                out, s.queue = s.queue, []
                return out
            s.waiter = (loop, fut)
        try:
            return await asyncio.wait_for(fut, POLL_SECONDS if timeout is None else timeout)
        except asyncio.TimeoutError:
            return []
        finally:
            with self.lock:
                if s.waiter and s.waiter[1] is fut:
                    s.waiter = None
                    s.seen_at = time.monotonic()  # it asked until now

    def answer(self, user_id, screen_id, command_id, ok, value):
        """The page did a command: hand its answer to the tool waiting for it."""
        with self.lock:
            found = self.pending.get(command_id)
            if found is None or found[2] != user_id or found[3] != screen_id:
                return False
            del self.pending[command_id]
        loop, fut = found[0], found[1]
        if ok:
            _resolve(loop, fut, value=value)
        else:
            _resolve(loop, fut, error=RuntimeError(str(value or "The app couldn't do it")))
        return True

    # ------------------------------------------------------------ the assistant's side

    def live(self, user_id) -> list[Screen]:
        """This person's open apps, the default one first."""
        with self.lock:
            now = time.monotonic()
            found = [s for (uid, _), s in self.screens.items()
                     if uid == user_id and (s.waiter or now - s.seen_at < LIVE_SECONDS)]
        return sorted(found, key=lambda s: (not s.visible, not s.focused, -s.input_at, -s.seen_at))

    def pick(self, user_id, which=None) -> Screen:
        """The screen asked for (its id, or a part of its name), else the one used last."""
        live = self.live(user_id)
        if not live:
            raise NoScreen("MangaVibe isn't open anywhere for you right now. Open it in a browser or the "
                           "Android app (logged in as you), with Remote control switched on.")
        if not which:
            return live[0]
        which = str(which).strip()
        for s in live:
            if s.id == which:
                return s
        named = [s for s in live if which.lower() in s.name.lower()]
        if named:
            return named[0]
        raise NoScreen(f"No open screen {which!r}. Open ones: " + ", ".join(f"{s.name} ({s.id})" for s in live))

    async def send(self, user_id, command: dict, which=None, timeout=None):
        """Send a command to a screen and wait for its answer. Returns (screen, answer)."""
        s = self.pick(user_id, which)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        with self.lock:
            cid = next(self.ids)
            self.pending[cid] = (loop, fut, user_id, s.id)
            s.queue.append({**command, "id": cid})
            if s.waiter:
                waiter, s.waiter = s.waiter, None
                out, s.queue = s.queue, []
                _resolve(*waiter, value=out)
        try:
            return s, await asyncio.wait_for(fut, ANSWER_SECONDS if timeout is None else timeout)
        except asyncio.TimeoutError:
            raise ScreenTimeout(f"{s.name} didn't answer. Is the app still open there?") from None
        finally:
            with self.lock:
                self.pending.pop(cid, None)
                if s.queue:  # not handed over: don't do it later, when nobody is waiting
                    s.queue = [c for c in s.queue if c.get("id") != cid]

    def tell(self, user_id, command: dict):
        """Send a command to every open screen of this person, without waiting (e.g. settings changed)."""
        with self.lock:
            now = time.monotonic()
            for (uid, _), s in self.screens.items():
                if uid != user_id or not (s.waiter or now - s.seen_at < LIVE_SECONDS):
                    continue  # someone else's, or closed: nothing to pile up for it
                s.queue.append({**command, "id": 0})  # id 0: no answer wanted
                if s.waiter:
                    waiter, s.waiter = s.waiter, None
                    out, s.queue = s.queue, []
                    _resolve(*waiter, value=out)

    def _forget_old(self):
        now = time.monotonic()
        for key in [k for k, s in self.screens.items() if not s.waiter and now - s.seen_at > FORGET_SECONDS]:
            del self.screens[key]

    def clear(self):
        with self.lock:
            for s in self.screens.values():
                if s.waiter:
                    _resolve(*s.waiter, value=[])
            self.screens.clear()
            for loop, fut, *_ in self.pending.values():
                _resolve(loop, fut, error=NoScreen("Remote control was switched off"))
            self.pending.clear()


hub = Hub()
