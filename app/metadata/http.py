"""HTTP client for metadata providers: per-host throttling, Retry-After on 429, retries."""
import logging
import threading
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger("mangavibe.http")

# minimum seconds between requests per host (AniList: spec says ~30/min)
MIN_INTERVAL = {"graphql.anilist.co": 2.0, "api.mangaupdates.com": 1.0}
DEFAULT_INTERVAL = 1.0
USER_AGENT = "MangaVibe/1.0 (personal self-hosted manga server)"

_last: dict[str, float] = {}
_lock = threading.Lock()
transport = None  # tests put an httpx.MockTransport here


class ProviderError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # the HTTP status code, or None when the site couldn't be reached


def _wait_turn(host):
    with _lock:
        gap = MIN_INTERVAL.get(host, DEFAULT_INTERVAL)
        wait = _last.get(host, 0) + gap - time.monotonic()
        _last[host] = max(time.monotonic(), _last.get(host, 0) + gap)
    if wait > 0 and transport is None:
        time.sleep(wait)


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
