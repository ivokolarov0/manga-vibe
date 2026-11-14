"""The order of plugins' markup in a slot: html = {"slot": ("<markup>", order)} (app/plugins/web.py)."""
from types import SimpleNamespace

from app.plugins import web


def plugin(pid, **html):
    return SimpleNamespace(id=pid, html={k.replace("_", "-"): v for k, v in html.items()}, js="", css="")


def boxes(markup, slot):
    inside = markup.split(f'data-slot="{slot}">', 1)[1].split('</div></div>', 1)[0] + "</div>"
    return [part.split('"', 1)[0] for part in inside.split('data-plugin="')[1:]]


def test_order_in_a_slot():
    page = "<header><!-- slot:topbar --></header>"
    plugins = [plugin("account", topbar=("<a>me</a>", 90)), plugin("first", topbar=("<b>1</b>", 10)),
               plugin("plain", topbar="<i>x</i>"), plugin("also-plain", topbar="<i>y</i>"),
               plugin("empty", topbar=("", 5))]
    out = web.fill_slots(page, plugins)
    # lower first; the same order (50 when not given) keeps the plugins' order; empty markup is left out
    assert boxes(out, "topbar") == ["first", "plain", "also-plain", "account"]
    assert 'data-plugin="account" data-order="90"' in out and 'data-plugin="plain" data-order="50"' in out


def test_ui_of_sends_the_order(monkeypatch):
    p = plugin("notes", topbar=("<b>n</b>", 20), sheets="<div>s</div>", library_end=("", 1))
    monkeypatch.setattr(web.manager, "enabled", lambda: [p])
    ui = web.ui_of(p)
    assert ui["html"] == {"topbar": "<b>n</b>", "sheets": "<div>s</div>"}
    assert ui["order"] == {"topbar": 20, "sheets": 50}
