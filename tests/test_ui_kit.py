"""The UI kit (issue #294): its CSS files are in the kit layer and imported, every piece is documented in
docs/UI-KIT.md and shown on the kit page (/kit), and MangaVibe.kit lets a plugin change a piece."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "app" / "web" / "static" / "css"
KIT_JS = ROOT / "app" / "web" / "static" / "js" / "kit.js"
KIT_PAGE = ROOT / "app" / "web" / "kit" / "kit.html"
DOC = (ROOT / "docs" / "UI-KIT.md").read_text(encoding="utf-8")


def documented_pieces():
    """The ### headings under "## The pieces", as names: "Buttons" -> "buttons"."""
    part = DOC.split("\n## The pieces\n", 1)[1].split("\n## ", 1)[0]
    return [h.strip().lower() for h in re.findall(r"^### (.+)$", part, re.M)]


def test_every_piece_is_documented_and_on_the_kit_page():
    documented = documented_pieces()
    shown = re.findall(r'data-piece="([a-z-]+)"', KIT_PAGE.read_text(encoding="utf-8"))
    assert len(documented) >= 10
    assert sorted(documented) == sorted(shown)


def test_kit_css_is_imported_in_the_kit_layer():
    main = (CSS / "main.css").read_text(encoding="utf-8")
    assert re.search(r"^@layer tokens, base, kit, layout,", main, re.M)  # after base, before the pages
    files = sorted(p.name for p in (CSS / "kit").glob("*.css"))
    assert files
    for name in files:
        assert f'@import url("kit/{name}");' in main, name
        assert re.search(r"^@layer kit \{", (CSS / "kit" / name).read_text(encoding="utf-8"), re.M), name


def test_the_kit_page(client):
    r = client.get("/kit")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    page = r.text
    assert page.startswith("<!doctype html>")
    assert 'id="i-star"' in page  # the app's icons
    assert "/static/js/kit.js" in page and "/static/css/main.css" in page
    assert client.get("/static/js/kit.js").status_code == 200
    assert client.get("/static/css/kit/buttons.css").status_code == 200


def test_the_app_page_loads_the_kit_before_app_js(client):
    page = client.get("/").text
    assert page.index("/static/js/kit.js") < page.index("/static/js/app.js")


# The kit's script with a pretend Alpine: a dialog closes on Escape and on its backdrop, a plugin's
# extend() changes every dialog while the plugin is on, data-kit-options changes one, kit-close can stop it.
HARNESS = r"""
const listeners = {}, directives = {};
const target = () => {
  const on = {};
  return { on, addEventListener: (t, f) => ((on[t] ||= []).push(f)), removeEventListener: (t, f) => (on[t] = (on[t] || []).filter((g) => g !== f)),
           fire(t, e) { (on[t] || []).forEach((f) => f(e)); } };
};
globalThis.window = Object.assign(globalThis, target());
globalThis.document = { addEventListener: (t, f) => ((listeners[t] ||= []).push(f)) };
globalThis.Alpine = { directive: (n, f) => (directives[n] = f), evaluate: (el, expr) => Function(`return (${expr})`)() };
globalThis.CustomEvent = class { constructor(type, init) { Object.assign(this, init, { type }); this.defaultPrevented = false; }
                                 preventDefault() { this.defaultPrevented = true; } };
globalThis.stoppedPlugins = new Set();
require(process.argv[2]);
listeners["alpine:init"].forEach((f) => f());

function dialog(options) {
  const el = Object.assign(target(), { dataset: options ? { kitOptions: options } : {}, getClientRects: () => [1] });
  el.dispatchEvent = (e) => { el.fire(e.type, e); return !e.defaultPrevented; };
  let closed = 0, undo = null;
  directives.dialog(el, { expression: "x", modifiers: [], value: "" },
                    { evaluateLater: () => () => closed++, cleanup: (f) => (undo = f) });
  return { el, closed: () => closed, undo: () => undo() };
}
const out = {};
let d = dialog();
d.el.fire("click", { target: d.el });
window.fire("keydown", { key: "Escape" });
out.plain = d.closed();
d.undo();
window.fire("keydown", { key: "Escape" });
out.afterUndo = d.closed();

window.MangaVibe.kit.extend("p", "dialog", (base) => ({ ...base, options: { ...base.options, backdrop: false } }));
d = dialog();
d.el.fire("click", { target: d.el });
out.extendedBackdrop = d.closed();
window.fire("keydown", { key: "Escape" });
out.extendedEscape = d.closed();
d.undo();

stoppedPlugins.add("p");
d = dialog();
d.el.fire("click", { target: d.el });
out.pluginOff = d.closed();
d.undo();

d = dialog("{ escape: false }");
window.fire("keydown", { key: "Escape" });
out.optionEscape = d.closed();
d.undo();

d = dialog();
d.el.addEventListener("kit-close", (e) => e.preventDefault());
window.fire("keydown", { key: "Escape" });
out.prevented = d.closed();
out.names = window.MangaVibe.kit.names();
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_plugin_can_change_a_piece(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(KIT_JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["plain"] == 2 and out["afterUndo"] == 2   # backdrop and Escape; nothing once undone
    assert out["extendedBackdrop"] == 0 and out["extendedEscape"] == 1
    assert out["pluginOff"] == 1                         # switched off: the plain dialog again
    assert out["optionEscape"] == 0                      # data-kit-options on one dialog
    assert out["prevented"] == 0                         # kit-close with preventDefault() keeps it open
    assert out["names"] == ["dialog"]
