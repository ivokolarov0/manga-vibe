"""Passwords, for the plugins that use them (library passwords, accounts): only a scrypt hash is stored,
and wrong guesses slow down.

    stored = hash_password("secret")      # "scrypt$n$r$p$salt$hash"
    check_password("secret", stored)       # True
    guard = Throttle()                    # one per kind of password
    guard.verify(key, password, stored)   # bool, or raises TooManyTries after MAX_TRIES wrong in a row
"""
import hashlib
import hmac
import secrets
import threading
import time

SCRYPT = (2 ** 14, 8, 1)  # n, r, p: about 50 ms and 16 MB per try
MAX_TRIES = 5             # wrong passwords before each try has to wait (30 s, then doubling, up to 15 min)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = SCRYPT
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        kind, n, r, p, salt, digest = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(digest) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), digest)


class TooManyTries(Exception):
    def __init__(self, seconds):
        super().__init__(f"Too many wrong passwords. Try again in {seconds} seconds.")
        self.seconds = seconds


class Throttle:
    """Counts wrong passwords per key (a library, an account name), in memory. After MAX_TRIES wrong ones
    in a row, each further try must wait (TooManyTries), so it can't be guessed quickly. The right one
    resets the count."""

    def __init__(self):
        self.fails = {}  # key -> (wrong tries in a row, no tries before this time)
        self._lock = threading.Lock()

    def verify(self, key, password: str, stored: str | None) -> bool:
        with self._lock:
            count, until = self.fails.get(key, (0, 0.0))
            if until > time.time():
                raise TooManyTries(int(until - time.time()) + 1)
        ok = check_password(password or "", stored)
        with self._lock:
            if ok:
                self.fails.pop(key, None)
            else:
                count = self.fails.get(key, (0, 0.0))[0] + 1
                wait = min(30 * 2 ** (count - MAX_TRIES), 900) if count >= MAX_TRIES else 0
                self.fails[key] = (count, time.time() + wait)
        return ok

    def clear(self):
        with self._lock:
            self.fails.clear()
