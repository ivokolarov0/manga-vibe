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
    """The ### headings under "## The pieces", as names: "Setting rows" -> "setting-rows"."""
    part = DOC.split("\n## The pieces\n", 1)[1].split("\n## ", 1)[0]
    return [h.strip().lower().replace(" ", "-") for h in re.findall(r"^### (.+)$", part, re.M)]


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


def test_the_fonts_are_served_by_mangavibe(client):
    """The two typefaces (#302) come from static/fonts, with their licences: the app works offline, so no
    CSS loads anything from another server."""
    fonts = (CSS / "fonts.css").read_text(encoding="utf-8")
    assert '@import url("fonts.css");' in (CSS / "main.css").read_text(encoding="utf-8")
    files = re.findall(r'url\("\.\./fonts/([^"]+)"\)', fonts)
    assert len(files) >= 4
    for name in files:
        assert (CSS.parent / "fonts" / name).is_file(), name
        r = client.get(f"/static/fonts/{name}")
        assert r.status_code == 200 and r.content[:4] == b"wOF2", name
    for family in ("Bricolage Grotesque", "Plus Jakarta Sans"):
        assert f'font-family: "{family}"' in fonts
        assert (CSS.parent / "fonts" / f"OFL-{family.lower().replace(' ', '-')}.txt").is_file()
    tokens = (CSS / "tokens.css").read_text(encoding="utf-8")
    assert '--font-ui: "Plus Jakarta Sans"' in tokens and '--font-display: "Bricolage Grotesque"' in tokens
    for path in CSS.rglob("*.css"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"url\(\s*[\"']?(https?:)?//", text), path.name


def test_the_app_page_loads_the_kit_before_app_js(client):
    page = client.get("/").text
    assert page.index("/static/js/kit.js") < page.index("/static/js/app.js")


# The kit's script with a pretend Alpine and DOM. A dialog: when it shows, its box is a named dialog, gets
# the focus, the page beside it is inert and doesn't scroll; Escape and the backdrop close only the top one;
# when it hides, the focus goes back. A plugin's extend() changes every dialog while the plugin is on,
# data-kit-options changes one, kit-close can stop it.
HARNESS = r"""
const listeners = {}, directives = {}, observers = [];
const target = () => {
  const on = {};
  return { on, addEventListener: (t, f) => ((on[t] ||= []).push(f)), removeEventListener: (t, f) => (on[t] = (on[t] || []).filter((g) => g !== f)),
           fire(t, e) { (on[t] || []).forEach((f) => f(e)); } };
};
const classes = new Set();
globalThis.window = Object.assign(globalThis, target(), { innerWidth: 1000 });
globalThis.document = {
  addEventListener: (t, f) => ((listeners[t] ||= []).push(f)), activeElement: null, body: { tagName: "BODY" },
  documentElement: { clientWidth: 985, classList: { add: (...c) => c.forEach((x) => classes.add(x)),
    remove: (...c) => c.forEach((x) => classes.delete(x)), toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } },
};
globalThis.Alpine = { directive: (n, f) => (directives[n] = f), evaluate: (el, expr) => Function(`return (${expr})`)() };
globalThis.CustomEvent = class { constructor(type, init) { Object.assign(this, init, { type }); this.defaultPrevented = false; }
                                 preventDefault() { this.defaultPrevented = true; } };
globalThis.MutationObserver = class { constructor(f) { this.f = f; observers.push(this); } observe() {} disconnect() { this.off = true; } };
globalThis.stoppedPlugins = new Set();
require(process.argv[2]);
listeners["alpine:init"].forEach((f) => f());
const tick = () => new Promise((r) => setTimeout(r));
const esc = () => ({ key: "Escape", defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
const notify = async () => { observers.forEach((o) => !o.off && o.f()); await tick(); };

function element(more = {}) {
  const attrs = {};
  return Object.assign(target(), { attrs, tagName: "DIV", isConnected: true, inert: false, dataset: {},
    hasAttribute: (k) => k in attrs, setAttribute: (k, v) => (attrs[k] = String(v)), matches: () => false,
    getClientRects: () => [1], focus() { document.activeElement = this; }, closest: () => null,
    querySelector: () => null, querySelectorAll: () => [], contains(x) { return x === this; } }, more);
}
const opener = element();
const page = element();  // what's beside the dialogs
const parent = element({ children: [page], parentElement: null });

function dialog(options) {
  let visible = false, closed = 0, undo = null;
  const title = element({ id: "" });
  const box = element({ querySelector: (s) => (s.includes(".sheet-head") ? title : null) });
  box.contains = (x) => x === box || x === title;
  const el = element({ getClientRects: () => (visible ? [1] : []), parentElement: parent,
                       querySelector: (s) => (s === ":scope > div" ? box : null), contains: (x) => x === el || box.contains(x) });
  if (options) el.dataset.kitOptions = options;
  parent.children.push(el);
  el.dispatchEvent = (e) => { el.fire(e.type, e); return !e.defaultPrevented; };
  const events = [];
  for (const t of ["kit-open", "kit-close", "kit-closed"]) el.addEventListener(t, () => events.push(t));
  directives.dialog(el, { expression: "x", modifiers: [], value: "" },
                    { evaluateLater: () => () => closed++, cleanup: (f) => (undo = f) });
  return { el, box, title, events, closed: () => closed, undo: () => undo(),
           show: async (on = true) => { visible = on; await notify(); } };
}

(async () => {
const out = {};
opener.focus();
let d = dialog();
await d.show();
out.role = d.box.attrs.role;
out.modal = d.box.attrs["aria-modal"];
out.named = !!d.title.id && d.box.attrs["aria-labelledby"] === d.title.id;
out.focusInBox = document.activeElement === d.box;
out.pageInert = page.inert;
out.locked = [...classes].sort();
d.el.fire("click", { target: d.el });
window.fire("keydown", esc());
out.plain = d.closed();
await d.show(false);
out.focusBack = document.activeElement === opener;
out.pageFree = !page.inert && classes.size === 0;
out.events = d.events;
d.undo();
window.fire("keydown", esc());
out.afterUndo = d.closed();

let a = dialog(), b = dialog();
await a.show();
await b.show();
window.fire("keydown", esc());
out.topOnly = [a.closed(), b.closed()];
a.undo(); b.undo();

window.MangaVibe.kit.extend("p", "dialog", (base) => ({ ...base, options: { ...base.options, backdrop: false } }));
d = dialog();
await d.show();
d.el.fire("click", { target: d.el });
out.extendedBackdrop = d.closed();
window.fire("keydown", esc());
out.extendedEscape = d.closed();
d.undo();

stoppedPlugins.add("p");
d = dialog();
await d.show();
d.el.fire("click", { target: d.el });
out.pluginOff = d.closed();
d.undo();

d = dialog("{ escape: false }");
await d.show();
window.fire("keydown", esc());
out.optionEscape = d.closed();
d.undo();

d = dialog();
await d.show();
d.el.addEventListener("kit-close", (e) => e.preventDefault());
window.fire("keydown", esc());
out.prevented = d.closed();
d.undo();
out.unlockedAtTheEnd = classes.size === 0 && !page.inert;
out.names = window.MangaVibe.kit.names();
console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_plugin_can_change_a_piece(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(KIT_JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["role"] == "dialog" and out["modal"] == "true" and out["named"]
    assert out["focusInBox"] and out["pageInert"]
    assert out["locked"] == ["kit-gutter", "kit-modal-open"]  # the page had a scrollbar: its room is kept
    assert out["focusBack"] and out["pageFree"]
    assert out["events"] == ["kit-open", "kit-close", "kit-close", "kit-closed"]
    assert out["topOnly"] == [0, 1]                      # Escape closes the top dialog only
    assert out["unlockedAtTheEnd"]
    assert out["plain"] == 2 and out["afterUndo"] == 2   # backdrop and Escape; nothing once undone
    assert out["extendedBackdrop"] == 0 and out["extendedEscape"] == 1
    assert out["pluginOff"] == 1                         # switched off: the plain dialog again
    assert out["optionEscape"] == 0                      # data-kit-options on one dialog
    assert out["prevented"] == 0                         # kit-close with preventDefault() keeps it open
    assert out["names"] == ["dialog", "menu", "choice", "tabs", "field", "toast", "tooltip"]


MARKUP = sorted([*(ROOT / "app" / "web" / "templates").rglob("*.html"),
                 *(ROOT / "app" / "plugins" / "builtin").rglob("*.html"), KIT_PAGE])


def test_menus_use_x_menu():
    """Every .menu is the kit's (#297): x-menu opens and closes it, so no @click toggle on its button and
    no @click.outside or @keydown.escape of its own."""
    found = 0
    for path in MARKUP:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'<div class="menu[ "][^>]*>\s*<button[^>]*>', text):
            found += 1
            tag = m.group(0)
            where = f"{path.relative_to(ROOT).as_posix()}: {tag[:80]}"
            assert "x-menu" in tag, where
            assert "@click.outside" not in tag and "@keydown.escape" not in tag, where
            assert not re.search(r"<button[^>]*@click=", tag), where
    assert found >= 6  # series ⋯, the library's select menu, the account menu, the kit page's


# .options that are a list of actions (each button does something), not one choice: no x-choice
ACTION_LISTS = {"app/plugins/builtin/libraries/move_dialog.html"}


def test_tabs_and_choices_use_the_kit():
    """Every .tabs has x-tabs, every .segmented and .options x-choice (#298): the kit gives the roles,
    the picked state and the keys, so the markup has none of its own left."""
    counts = {"tabs": 0, "choice": 0}
    for path in MARKUP:
        name = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'<div class="(tabs|segmented|options)[ "][^>]*>', text):
            want = "x-tabs" if m.group(1) == "tabs" else "x-choice"
            if m.group(1) == "options" and name in ACTION_LISTS:
                continue
            counts["tabs" if want == "x-tabs" else "choice"] += 1
            assert want in m.group(0), f"{name}: {m.group(0)[:80]}"
            assert 'role="' not in m.group(0), f"{name}: the kit sets the role: {m.group(0)[:80]}"
        assert not re.search(r'role="(tab|radio)"|:aria-selected=|:aria-checked=', text), \
            f"{name}: the kit sets the tabs' and choices' roles and state"
    assert counts["tabs"] >= 6 and counts["choice"] >= 6, counts


BUILTIN = ROOT / "app" / "plugins" / "builtin"
# the kit's classes, and what makes a piece look the way it does: a plugin changes those with the piece's
# custom properties (--btn-bg…), not by drawing the piece again (#301)
KIT_CLASSES = {"btn", "iconbtn", "icon-btn", "link-btn", "chip", "chips", "tabs", "segmented", "options", "panel",
               "menu", "menu-pop", "modal", "sheet", "sheet-head", "setting-row", "settings-card", "field", "empty",
               "loading-line", "progress", "skeleton", "skeleton-card", "tooltip"}
LOOK = {"background", "background-color", "border", "border-color", "border-radius", "border-width", "color",
        "padding", "height", "min-height", "font-size", "font-weight", "box-shadow"}


def css_rules(css):
    """(selector, set of properties) for every rule, CSS nesting resolved (& and descendants)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, stack, buf = [], [], ""
    for ch in css:
        if ch == "{":
            sel, parent = buf.strip(), (stack[-1][0] if stack else "")
            if sel.startswith("@"):
                full = parent
            else:
                full = ", ".join(s.replace("&", parent) if "&" in s else f"{parent} {s}".strip()
                                 for s in (x.strip() for x in sel.split(",")))
            stack.append([full, set()])
            rules.append(stack[-1])
            buf = ""
        elif ch == "}":
            if ":" in buf:
                stack[-1][1].add(buf.split(":")[0].strip())
            stack.pop()
            buf = ""
        elif ch == ";":
            if stack and ":" in buf:
                stack[-1][1].add(buf.split(":")[0].strip())
            buf = ""
        else:
            buf += ch
    return [(sel, props) for sel, props in rules]


def test_built_in_plugins_restyle_kit_pieces_only_with_custom_properties():
    wrong = []
    for path in sorted(BUILTIN.rglob("*.css")):
        for sel, props in css_rules(path.read_text(encoding="utf-8")):
            look = props & LOOK
            for s in sel.split(","):
                subject = re.split(r"\s+|>|\+|~", s.strip())[-1]
                if look and set(re.findall(r"\.([a-z][\w-]*)", subject)) & KIT_CLASSES:
                    wrong.append(f"{path.relative_to(ROOT).as_posix()}: {s.strip()} sets {sorted(look)}")
    assert not wrong, "Use the piece's custom properties (docs/UI-KIT.md):\n" + "\n".join(wrong)


def test_css_rules_reads_nesting():
    rules = dict(css_rules(".a { color: red; & .btn { background: x; --btn-bg: y; } }"))
    assert rules[".a .btn"] == {"background", "--btn-bg"} and rules[".a"] == {"color"}


def test_built_in_plugins_draw_icons_from_the_sprite():
    """The app's icons are one sprite (templates/shell/icons.html); a plugin's own <svg viewBox> copy of one
    goes out of step with it. The image reader keeps its own set (its controls' icons aren't in the sprite)."""
    own = [p.relative_to(ROOT).as_posix() for p in sorted([*BUILTIN.rglob("*.html"), *BUILTIN.rglob("*.py")])
           if "<svg viewBox" in p.read_text(encoding="utf-8")]
    assert not own, own


def test_setting_rows_and_fields_use_x_field():
    """Every .setting-row and .field has x-field (#299), and a setting row's name is an element of its own
    (the .label's first element), not a bare text the hint would be read with."""
    rows = fields = 0
    for path in MARKUP:
        name = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'<(label|div|form)\b[^>]*class="(setting-row|field)[ "][^>]*>(\s*<(?:span|div) class="label">(.))?', text):
            if m.group(2) == "setting-row":
                rows += 1
            else:
                fields += 1
            assert "x-field" in m.group(0), f"{name}: {m.group(0)[:90]}"
            if m.group(4) is not None:
                assert m.group(4) == "<", f"{name}: put the setting's name in a <span>: {m.group(0)[:120]}"
    assert rows >= 25 and fields >= 15, (rows, fields)
