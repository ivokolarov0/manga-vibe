"""HTTP client for metadata providers: per-host throttling, Retry-After on 429, retries."""
import logging
import threading
import time
from collections import deque
from urllib.parse import urlparse

import httpx

log = logging.getLogger("mangavibe.http")

# seconds between requests per host, on average (AniList: spec says ~30/min)
MIN_INTERVAL = {"graphql.anilist.co": 2.0, "api.mangaupdates.com": 1.0}
DEFAULT_INTERVAL = 1.0
USER_AGENT = "MangaVibe/1.0 (personal self-hosted manga server)"

# The interval is the average rate a site allows (AniList: 30 a minute). The background worker
# (matching a newly added library can take hours) uses at most BACKGROUND_SHARE of it, so a
# search or match the owner starts goes out right away instead of queueing behind the worker.
WINDOW = 60.0
BACKGROUND_SHARE = 0.7
BURST_GAP = 0.25  # the shortest gap between the owner's requests, as a fraction of the interval

_sent: dict[str, deque] = {}  # host -> time.monotonic() of the requests in the last WINDOW seconds
_waiting: dict[str, int] = {}  # host -> the owner's requests waiting for their turn
_cond = threading.Condition()
_thread = threading.local()
transport = None  # tests put an httpx.MockTransport here


class ProviderError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # the HTTP status code, or None when the site couldn't be reached


def run_in_background():
    """Mark the calling thread as background work: its requests give way to the owner's."""
    _thread.background = True


def _delay(host, background, now):
    """Seconds until this request may go out (0: now), or None to wait for the owner's requests."""
    gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
    sent = _sent.setdefault(host, deque())
    while sent and sent[0] <= now - WINDOW:
        sent.popleft()
    if background:
        if _waiting.get(host):
            return None
        limit, spacing = max(1, int(WINDOW / gap * BACKGROUND_SHARE)), gap / BACKGROUND_SHARE
    else:
        limit, spacing = max(1, int(WINDOW / gap)), gap * BURST_GAP
    wait = (sent[-1] + spacing - now) if sent else 0
    if len(sent) >= limit:  # wait until enough of the last minute's requests are older than WINDOW
        wait = max(wait, sent[len(sent) - limit] + WINDOW - now)
    return max(wait, 0)


def _wait_turn(host):
    if transport is not None:
        return
    background = getattr(_thread, "background", False)
    with _cond:
        if not background:
            _waiting[host] = _waiting.get(host, 0) + 1
        try:
            while True:
                now = time.monotonic()
                wait = _delay(host, background, now)
                if wait == 0:
                    _sent[host].append(now)
                    return
                _cond.wait(wait if wait is not None else WINDOW)
        finally:
            if not background:
                _waiting[host] -= 1
            _cond.notify_all()


def request(method, url, retries=4, **kw):
    host = urlparse(url).hostname
    delay = 5
    for attempt in range(retries + 1):
        _wait_turn(host)
        try:
            with httpx.Client(timeout=20, transport=transport,
                              headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
                r = c.request(method, url, **kw)
        except httpx.HTTPError as e:
            if attempt == retries:
                raise ProviderError(f"{host} unreachable: {e}") from e
            log.warning("%s: %s, retrying in %ss", host, e, delay)
        else:
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries:
                    raise ProviderError(f"{host} answered {r.status_code}", r.status_code)
                try:
                    wait = int(r.headers.get("Retry-After", delay)) if r.status_code == 429 else delay
                except ValueError:  # Retry-After given as a date
                    wait = delay
                log.warning("%s answered %s, waiting %ss", host, r.status_code, wait)
                if transport is None:
                    time.sleep(wait)
                delay = min(delay * 2, 120)
                continue
            if r.status_code >= 400:
                raise ProviderError(f"{host} answered {r.status_code}: {_short_error(r)}", r.status_code)
            return r
        if transport is None:
            time.sleep(delay)
        delay = min(delay * 2, 120)


def _short_error(r):
    """First error message from a JSON error body, else the start of the text."""
    try:
        body = r.json()
        errs = body.get("errors") if isinstance(body, dict) else None
        if errs:
            return errs[0].get("message", "")[:200]
        return str(body.get("reason") or body.get("message") or body)[:200]
    except (ValueError, AttributeError):  # not JSON, or JSON that isn't an object
        return r.text[:200].strip()


def get_json(url, **kw):
    return request("GET", url, **kw).json()


def post_json(url, body, **kw):
    return request("POST", url, json=body, **kw).json()


def get_bytes(url, **kw):
    return request("GET", url, **kw).content
