"""Accounts (built-in plugin): everyone logs in, and each account has its own reading progress, reading
status, favorites, Continue reading, saved searches and Settings → Reader (app/users.py). Admins manage
the app (settings, folders, metadata, plugins, deleting) and the accounts, and pick which libraries each
account sees (with the libraries plugin).

Off when MangaVibe is installed or updated; switched on in Settings → Plugins, the next page asks for the
first admin (who gets everything read so far). Switched off again, the app has one user, the owner, with
the first admin's reading.

- core.py: the accounts setting, the login cookie and AccountsGuard (every request but the login page
  needs an account)
- api.py: its API at /api/plugins/accounts/api/...
- accounts.js, *.html, accounts.css: the login, the account menu, Settings → Accounts
"""
from pathlib import Path

from app.plugins import Plugin

from . import api, core

HERE = Path(__file__).parent


def _read(name):
    return (HERE / name).read_text(encoding="utf-8")


class Accounts(Plugin):
    id = "accounts"
    name = "Accounts"
    version = "1.0"
    description = ("Everyone logs in; each account has its own progress, favorites, saved searches and "
                   "reader settings. Admins manage the app and pick the libraries each account sees. Built in.")
    permissions = ["Asks everyone to log in; adds Settings → Accounts and an account menu",
                   "Keeps the accounts (password hashes) in MangaVibe's database; connects to nothing"]
    enabled_by_default = False  # an update mustn't start asking everyone to log in
    router = api.router
    middleware = core.AccountsGuard
    middleware_order = 0  # before every other plugin's guard: they need to know who is asking
    js = _read("accounts.js")
    css = _read("accounts.css")
    html = {"topbar": _read("menu.html"),
            "settings": _read("settings.html"),
            "sheets": _read("login.html")}
