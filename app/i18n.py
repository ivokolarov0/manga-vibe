"""The app in other languages (issue #263): gettext .po files, read by the app itself.

- app/web/i18n/messages.pot is the template: every text the app shows, in English. It's made from the
  code by `python -m app.i18n` (below), never by hand.
- app/web/i18n/<lang>.po is one language (bg.po, de.po, ja.po ...): each English text (msgid) and its
  translation (msgstr). The same command adds the new texts to each one, empty, for a translator to fill
  in. A text without a translation is shown in English, so a half-done language still works.
- A folder plugin can ship its own: i18n/<lang>.po next to its __init__.py (Plugin API 27). Its texts
  are added to the app's, for the texts the app's own files don't translate.

Texts are marked in the code with
- t("Saved") and tn("{n} series", "{n} series", n): in Jinja templates (the page is drawn in the
  reader's language) and in the browser (common.js; the page carries its language's texts);
- N_("Display") in Python, for a text that's translated where it's shown (e.g. a Settings page's label).

Placeholders are {name}: t("Scanning {done} of {total}", done=3, total=9) (in JS, a second argument:
t("…", { done, total })). tn() gives {n} too. A translation must keep the msgid's placeholders.
Plurals follow the .po file's Plural-Forms header, so languages with more than two forms work.

The language of a page: the person's setting (Settings → Display → Language), else the first of the
browser's languages (Accept-Language) that the app has, else English.
"""
import gettext as gettext_lib
import re
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from app import config

SOURCE = "en"  # the language the code is written in: msgids are English
I18N_DIR = config.WEB_DIR / "i18n"
POT = I18N_DIR / "messages.pot"
DEFAULT_PLURAL = "nplurals=2; plural=(n != 1);"

_lang: ContextVar["str | Lazy"] = ContextVar("mangavibe_lang", default=SOURCE)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def N_(text: str) -> str:
    """Marks a text for translation where it's defined; it's translated where it's shown (with t())."""
    return text


# --- reading .po files ---------------------------------------------------------------------------

def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise ValueError(f"not a quoted string: {s}")
    return re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), s[1:-1])


def parse_po(text: str) -> list[dict]:
    """The entries of a .po/.pot file: {"id", "plural", "strs" (msgstr or msgstr[i] by index), "refs",
    "ctx"}. The header is the entry with id "". Obsolete entries (#~) are left out."""
    entries, cur, key = [], None, None

    def finish():
        if cur is not None and "id" in cur:
            entries.append(cur)

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        try:
            if not line:
                finish()
                cur, key = None, None
                continue
            if line.startswith("#"):
                if cur is not None and "id" in cur:  # a comment starts the next entry
                    finish()
                    cur, key = None, None
                cur = cur or {"refs": [], "strs": {}}
                if line.startswith("#:"):
                    cur["refs"] += line[2:].split()
                continue
            cur = cur or {"refs": [], "strs": {}}
            m = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[(\d+)\])?)\s+(\".*\")$", line)
            if m:
                word, index, value = m[1], m[2], _unquote(m[3])
                if word == "msgctxt":
                    if "id" in cur:  # a new entry without a blank line between
                        finish()
                        cur = {"refs": [], "strs": {}}
                    key = ("ctx",)
                elif word == "msgid":
                    if "id" in cur:
                        finish()
                        cur = {"refs": [], "strs": {}}
                    key = ("id",)
                elif word == "msgid_plural":
                    key = ("plural",)
                else:
                    key = ("strs", int(index or 0))
                if key[0] == "strs":
                    cur["strs"][key[1]] = value
                else:
                    cur[key[0]] = value
            elif line.startswith('"') and key is not None:
                value = _unquote(line)
                if key[0] == "strs":
                    cur["strs"][key[1]] += value
                else:
                    cur[key[0]] += value
            else:
                raise ValueError(f"can't read: {line}")
        except ValueError as e:
            raise ValueError(f"line {n}: {e}") from None
    finish()
    return entries


def header_fields(entries) -> dict:
    head = next((e for e in entries if e["id"] == "" and "ctx" not in e), None)
    out = {}
    for line in (head["strs"].get(0, "") if head else "").splitlines():
        name, _, value = line.partition(":")
        if value:
            out[name.strip()] = value.strip()
    return out


PLURAL_RE = re.compile(r"nplurals\s*=\s*(\d+)\s*;\s*plural\s*=\s*([^;]+);?")
# what a plural expression may contain: n, numbers, C operators and brackets (checked before the browser
# makes a function of it, too)
PLURAL_CHARS = re.compile(r"^[n0-9\s?:%<>=!&|()+\-*/]+$")


def plural_parts(forms: str) -> tuple[int, str]:
    m = PLURAL_RE.search(forms or DEFAULT_PLURAL) or PLURAL_RE.search(DEFAULT_PLURAL)
    expr = m[2].strip()
    if not PLURAL_CHARS.match(expr):
        raise ValueError(f"Plural-Forms: {expr!r} isn't a plural expression")
    return int(m[1]), expr


class Catalog:
    """One language's translations: msgid -> msgstr, or msgid -> [forms] for plurals."""

    def __init__(self, lang: str, messages=None, plural: str = DEFAULT_PLURAL):
        self.lang = lang
        self.messages: dict[str, str | list[str]] = dict(messages or {})
        self.nplurals, self.plural_expr = plural_parts(plural)
        self._plural = gettext_lib.c2py(self.plural_expr)

    @classmethod
    def from_po(cls, lang: str, text: str):
        entries = parse_po(text)
        messages = {}
        for e in entries:
            if e["id"] == "" or "ctx" in e:  # the header; contexts aren't used (yet)
                continue
            if "plural" in e:
                forms = [e["strs"].get(i, "") for i in range(max(e["strs"], default=-1) + 1)]
                if forms and all(forms):
                    messages[e["id"]] = forms
            elif e["strs"].get(0):
                messages[e["id"]] = e["strs"][0]
        return cls(lang, messages, header_fields(entries).get("Plural-Forms", DEFAULT_PLURAL))

    def plural_index(self, n) -> int:
        """The form for n. A fraction takes the form of 2, as in English and German ("1.5 seconds") and the
        Slavic languages; gettext's rules only count whole things."""
        try:
            n = abs(float(n))
            i = int(self._plural(int(n) if n.is_integer() else 2))
        except (TypeError, ValueError):
            i = 0
        return i if 0 <= i < self.nplurals else 0

    def gettext(self, msgid: str) -> str:
        v = self.messages.get(msgid)
        return v if isinstance(v, str) else msgid

    def ngettext(self, singular: str, plural: str, n) -> str:
        forms = self.messages.get(singular)
        if isinstance(forms, list):
            i = self.plural_index(n)
            if i < len(forms):
                return forms[i]
        return singular if n == 1 else plural

    def to_json(self) -> dict:
        """What the browser gets (MV_I18N in the page): the language, its plural rule and its texts."""
        return {"lang": self.lang, "nplurals": self.nplurals, "plural": self.plural_expr, "messages": self.messages}


# --- the languages there are, and their catalogs ------------------------------------------------

_cache: dict[tuple, Catalog | None] = {}  # (path, mtime) -> its texts; None: the file can't be read
_ENGLISH = Catalog(SOURCE)  # no texts: every msgid is shown as it is
RECHECK_SECONDS = 1.0  # how often catalog() looks at the files again (a plugin switched on, a .po changed)
# lang -> (when the files were last looked at, what they were: paths and mtimes, the catalog made of them)
_catalogs: dict[str, tuple[float, tuple, Catalog]] = {}
_languages: tuple = (None, [])  # (I18N_DIR's mtime, languages()) - the folder changes when a .po is added


def _stamp(path: Path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _load(lang: str, path: Path) -> Catalog | None:
    """A .po file's texts, read again only when it changed. A file that can't be read is logged once
    (until it changes) and gives None."""
    stamp = _stamp(path)
    if stamp is None:
        return None
    key = (str(path), stamp)
    if key not in _cache:
        try:
            _cache[key] = Catalog.from_po(lang, path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            import logging
            logging.getLogger("mangavibe.i18n").exception("Translations in %s could not be read", path)
            _cache[key] = None
    return _cache[key]


def languages() -> list[str]:
    """The languages the app has: English, then one per .po file in app/web/i18n, by code."""
    global _languages
    stamp = _stamp(I18N_DIR)
    if stamp is None or _languages[0] != stamp:
        _languages = (stamp, [SOURCE] + sorted(p.stem for p in I18N_DIR.glob("*.po") if p.stem != SOURCE))
    return list(_languages[1])


def plugin_po_files(lang: str) -> list[Path]:
    """The switched-on folder plugins' i18n/<lang>.po (Plugin API 27). Built-in plugins' texts are in the
    app's own files."""
    from app.plugins import manager
    out = []
    for p in manager.enabled():
        path = manager.paths.get(p.id)
        if path is not None and path.name == "__init__.py" and not manager.is_builtin(p.id):
            po = path.parent / "i18n" / f"{lang}.po"
            if po.is_file():
                out.append(po)
    return out


def catalog(lang: str) -> Catalog:
    """A language's texts: the app's, then the plugins' for texts the app doesn't translate. A language
    the app doesn't have is English (no texts: every msgid is shown as it is).

    t() calls this for every text, so the result is kept: the files are looked at again at most once a
    second, and the catalog is only made again when one of them changed (or a plugin was switched on
    or off)."""
    now = time.monotonic()
    kept = _catalogs.get(lang)
    if kept is not None and now - kept[0] < RECHECK_SECONDS:
        return kept[2]
    try:
        plugin_files = plugin_po_files(lang)
    except Exception:  # noqa: BLE001 - e.g. while the plugins load: the app's own texts
        plugin_files = []
    files = ([] if lang == SOURCE else [I18N_DIR / f"{lang}.po"]) + plugin_files
    stamps = tuple((str(p), _stamp(p)) for p in files)
    if kept is not None and kept[1] == stamps:
        _catalogs[lang] = (now, stamps, kept[2])
        return kept[2]
    cat = _make_catalog(lang, plugin_files)
    _catalogs[lang] = (now, stamps, cat)
    return cat


def forget():
    """Make the catalogs again at the next t() (plugins were loaded, or switched on or off)."""
    _catalogs.clear()


def _make_catalog(lang: str, plugin_files) -> Catalog:
    base = _load(lang, I18N_DIR / f"{lang}.po") if lang != SOURCE else None
    if base is None:
        base = _ENGLISH if lang == SOURCE else Catalog(lang)
    # a plugin's broken file is left out (and logged once by _load): the others still count
    extra = [c for c in (_load(lang, p) for p in plugin_files) if c is not None]
    if not extra:
        return base
    merged = Catalog(base.lang, {}, f"nplurals={base.nplurals}; plural={base.plural_expr};")
    for c in extra:
        merged.messages.update(c.messages)
    merged.messages.update(base.messages)  # the app's own words win
    return merged


def match_language(accept: str | None, available=None) -> str:
    """The first of the browser's languages (an Accept-Language header) the app has, else English.
    "de-AT" matches "de"; quality values are respected."""
    available = available or languages()
    wanted = []
    for i, part in enumerate((accept or "").split(",")):
        code, _, params = part.strip().partition(";")
        q = 1.0
        m = re.search(r"q\s*=\s*([0-9.]+)", params)
        if m:
            try:
                q = float(m[1])
            except ValueError:
                q = 0
        if code and q > 0:
            wanted.append((-q, i, code.strip().lower()))
    lower = {a.lower(): a for a in available}
    for _, _, code in sorted(wanted):
        if code in lower:
            return lower[code]
        base = code.split("-")[0]
        if base in lower:
            return lower[base]
    return SOURCE


def pick_language(setting: str | None, accept: str | None) -> str:
    """The page's language: the saved one when the app has it, else from the browser."""
    available = languages()
    if setting and setting in available:
        return setting
    return match_language(accept, available)


# --- translating --------------------------------------------------------------------------------

class Lazy:
    """A language worked out the first time it's needed (a request's: the person's setting needs the
    database, and most requests, like saving progress, never translate anything). Then kept."""

    def __init__(self, find):
        self._find, self._lang = find, None

    def get(self) -> str:
        if self._lang is None:
            self._lang = self._find()
        return self._lang


def current() -> str:
    lang = _lang.get()
    return lang.get() if isinstance(lang, Lazy) else lang


@contextmanager
def using(lang: "str | Lazy"):
    """Translate into this language inside (a page being drawn), or a Lazy one (a request)."""
    token = _lang.set(lang)
    try:
        yield lang
    finally:
        _lang.reset(token)


def fill(text: str, values: dict) -> str:
    """Put the values into a text's {name} placeholders; an unknown name stays as it is."""
    if not values:
        return text
    return PLACEHOLDER.sub(lambda m: str(values[m[1]]) if m[1] in values else m[0], text)


def t(msgid: str, **values) -> str:
    return fill(catalog(current()).gettext(msgid), values)


def tn(singular: str, plural: str, n, **values) -> str:
    return fill(catalog(current()).ngettext(singular, plural, n), {"n": n, **values})


# --- making messages.pot from the code, and updating the .po files (python -m app.i18n) ----------

ROOT = Path(__file__).resolve().parent.parent
SCAN = [("app", ("*.html", "*.js", "*.py"))]
SKIP_PARTS = {"vendor", "__pycache__"}
_STR = r"""(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')"""
# t("…") and tn("…", "…", n) in templates and scripts (th("…") for HTML), N_("…") in Python; not obj.t( or
# format(; MangaVibe.t( and MV.t( (the built-in plugins' name for it) in plugins' scripts
_START = r"(?:(?<![\w.$])|(?<=MangaVibe\.)|(?<=\bMV\.))"
CALL_T = re.compile(_START + r"(?:t|th|N_)\(\s*" + _STR)
CALL_TN = re.compile(_START + r"tn\(\s*" + _STR + r"\s*,\s*" + _STR)


def _literal(m, first: int) -> str:
    s = m[first] if m[first] is not None else m[first + 1]
    return re.sub(r"\\(.)", lambda x: {"n": "\n", "t": "\t"}.get(x[1], x[1]), s)


def source_files():
    for folder, patterns in SCAN:
        for pattern in patterns:
            for path in sorted((ROOT / folder).rglob(pattern)):
                if not SKIP_PARTS & set(path.parts) and path.resolve() != Path(__file__).resolve():
                    yield path


def extract() -> dict[tuple[str, str | None], list[str]]:
    """{(msgid, msgid_plural or None): [files it's in]} from the app's templates, scripts and Python."""
    found: dict[tuple, list[str]] = {}
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if path.suffix == ".py":
            for key in python_texts(text):
                found.setdefault(key, []).append(rel)
            continue
        for m in CALL_TN.finditer(text):
            found.setdefault((_literal(m, 1), _literal(m, 3)), []).append(rel)
        for m in CALL_T.finditer(text):
            found.setdefault((_literal(m, 1), None), []).append(rel)
    found.pop(("", None), None)
    return found


# Python is read with ast (so "a" "b" across lines is one text, and comments don't count). Besides t(), tn()
# and N_(): the messages of HTTPException (translated on their way out, app/main.py), and what a plugin
# shows in Settings -> Plugins: its name, description and permissions, and the labels of its settings,
# buttons, fields and dropdowns (translated in app/api/system.py and app/api/series.py)
PLUGIN_TEXTS = {"name", "description", "permissions", "overlay_label"}
LABELLED = {"SettingDef": ("help",), "Action": ("confirm",), "FieldDef": (), "Choice": ()}


def python_texts(text: str):
    import ast

    def const(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def name_of(func):
        return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""

    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn, args = name_of(node.func), node.args
            if fn in ("t", "N_") and args and const(args[0]):
                out.append((const(args[0]), None))
            elif fn == "tn" and len(args) >= 2 and const(args[0]) and const(args[1]):
                out.append((const(args[0]), const(args[1])))
            elif fn == "HTTPException":
                detail = args[1] if len(args) > 1 else next((k.value for k in node.keywords if k.arg == "detail"), None)
                if detail is not None and const(detail):
                    out.append((const(detail), None))
            elif fn in LABELLED:
                if len(args) > 1 and const(args[1]):
                    out.append((const(args[1]), None))
                for k in node.keywords:
                    if k.arg in LABELLED[fn] and const(k.value):
                        out.append((const(k.value), None))
                    if k.arg == "options" and isinstance(k.value, ast.List):
                        for opt in k.value.elts:
                            if isinstance(opt, ast.Tuple) and len(opt.elts) == 2 and const(opt.elts[1]):
                                out.append((const(opt.elts[1]), None))
        elif isinstance(node, ast.ClassDef) and any(name_of(b) == "Plugin" for b in node.bases):
            for item in node.body:
                if not (isinstance(item, ast.Assign) and len(item.targets) == 1
                        and isinstance(item.targets[0], ast.Name) and item.targets[0].id in PLUGIN_TEXTS):
                    continue
                values = item.value.elts if isinstance(item.value, ast.List) else [item.value]
                out += [(const(v), None) for v in values if const(v)]
    return out


def _quote(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    if "\n" not in s:
        return f'"{s}"'
    lines = s.split("\n")
    parts = [line + "\\n" for line in lines[:-1]] + ([lines[-1]] if lines[-1] else [])
    return '""\n' + "\n".join(f'"{p}"' for p in parts)


def render_po(header: dict, entries) -> str:
    """A .po/.pot file's text. entries: [(msgid, plural or None, refs, strs {index: text})]."""
    head = "".join(f"{k}: {v}\\n" for k, v in header.items())
    out = ['msgid ""', 'msgstr ""'] + [f'"{line}\\n"' for line in head.split("\\n") if line]
    out.append("")
    for msgid, plural, refs, strs in entries:
        if refs:
            out.append("#: " + " ".join(sorted(set(refs))))
        out.append("msgid " + _quote(msgid))
        if plural is not None:
            out.append("msgid_plural " + _quote(plural))
            for i in range(len(strs) or 2):  # the language's forms (update_po); the template: two
                out.append(f"msgstr[{i}] " + _quote(strs.get(i, "")))
        else:
            out.append("msgstr " + _quote(strs.get(0, "")))
        out.append("")
    return "\n".join(out)


POT_HEADER = {"Project-Id-Version": "MangaVibe", "MIME-Version": "1.0",
              "Content-Type": "text/plain; charset=UTF-8", "Content-Transfer-Encoding": "8bit"}


def make_pot(found=None) -> str:
    found = extract() if found is None else found
    entries = [(mid, pl, refs, {}) for (mid, pl), refs in sorted(found.items(), key=lambda kv: kv[0][0].lower())]
    return render_po(POT_HEADER, entries)


def update_po(po_text: str, found) -> str:
    """A language's .po for the texts the code has now: the translations kept, new texts added empty,
    texts no longer in the code left out (git has them)."""
    old = parse_po(po_text)
    header = header_fields(old)
    done = {e["id"]: e["strs"] for e in old if e["id"] and "ctx" not in e}
    nplurals, _ = plural_parts(header.get("Plural-Forms", DEFAULT_PLURAL))
    entries = []
    for (mid, pl), refs in sorted(found.items(), key=lambda kv: kv[0][0].lower()):
        strs = dict(done.get(mid, {}))
        if pl is not None:
            strs = {i: strs.get(i, "") for i in range(nplurals)}
        entries.append((mid, pl, refs, strs))
    return render_po(header or {**POT_HEADER, "Plural-Forms": DEFAULT_PLURAL}, entries)


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def main(argv=None) -> int:
    """python -m app.i18n: write messages.pot from the code and bring each <lang>.po up to date.
    python -m app.i18n new <lang>: start a language (an empty <lang>.po; set its Plural-Forms)."""
    argv = sys.argv[1:] if argv is None else argv
    found = extract()
    if argv[:1] == ["new"] and len(argv) == 2:
        path = I18N_DIR / f"{argv[1]}.po"
        if path.exists():
            print(f"{path} is there already")
            return 1
        header = {**POT_HEADER, "Language": argv[1], "Plural-Forms": DEFAULT_PLURAL}
        path.write_text(update_po(render_po(header, []), found), encoding="utf-8", newline="\n")
        print(f"Started {path.relative_to(ROOT)}: check its Plural-Forms, then translate")
        return 0
    POT.write_text(make_pot(found), encoding="utf-8", newline="\n")
    print(f"{POT.relative_to(ROOT)}: {len(found)} texts")
    for po in sorted(I18N_DIR.glob("*.po")):
        po.write_text(update_po(po.read_text(encoding="utf-8"), found), encoding="utf-8", newline="\n")
        cat = Catalog.from_po(po.stem, po.read_text(encoding="utf-8"))
        print(f"{po.relative_to(ROOT)}: {len(cat.messages)} of {len(found)} translated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
