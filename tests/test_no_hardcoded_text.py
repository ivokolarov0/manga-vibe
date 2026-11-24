"""No text the person sees is written straight into the page (issue #263, step 3): it all goes through t(),
so it can be translated (app/i18n.py, docs/developers.md "Languages"). Checked here:

- the templates and the built-in plugins' markup: text between tags, and the title, aria-label, placeholder
  and alt attributes;
- Alpine bindings that show text (x-text, x-html, :title, :aria-label, :placeholder, :alt): a quoted text
  in them that isn't given to t(), tn() or th();
- the scripts: toast(), confirm() and prompt() called with a quoted text.

That every t() text is in messages.pot (and so in every language's file) is test_i18n.py's
test_messages_pot_is_up_to_date. A word that must stay as it is (a name, a symbol) goes in ALLOWED.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# words that are the same in every language: names, symbols, units, examples of what to type
ALLOWED = {"MangaVibe", "A", "z", "Az", "…", "‹", "›", "×", "–", "·", "#", "/volume1/manga", "github_pat_…",
           "https://anilist.co/manga/…", "https://www.mangaupdates.com/series/…", "–"}

MARKUP = sorted([*(APP / "web" / "templates").rglob("*.html"), *(APP / "plugins" / "builtin").rglob("*.html")])
SCRIPTS = sorted([APP / "web" / "static" / "js" / "app.js", APP / "web" / "static" / "js" / "reader.js",
                  APP / "web" / "static" / "js" / "common.js", *(APP / "plugins" / "builtin").rglob("*.js")])

LETTERS = re.compile(r"[^\W\d_]{2,}")  # two letters in a row: a word, not a symbol or a number


def strip_markup(text: str) -> str:
    """The markup without what isn't shown text: Jinja (its t() calls are what's wanted), comments,
    scripts, styles and icons."""
    text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
    text = re.sub(r"\{%.*?%\}", " ", text, flags=re.S)
    text = re.sub(r"\{\{.*?\}\}", " ", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", text, flags=re.S | re.I)
    return text


TAG = re.compile(r"""<(?:[^>"']|"[^"]*"|'[^']*')*>""")  # a tag, with > inside its quoted attributes


def shown_text(text: str):
    """The text between tags (with letters in it)."""
    body = TAG.sub("\n", strip_markup(text))
    for chunk in body.split("\n"):
        chunk = chunk.strip()
        if chunk and LETTERS.search(chunk) and chunk not in ALLOWED:
            yield chunk


STATIC_ATTR = re.compile(r"""(?<![:\w-])(title|aria-label|placeholder|alt)="([^"]*)\"""")


def static_attributes(text: str):
    """title="…", aria-label="…", placeholder="…", alt="…" with words in them (not bound with :)."""
    for m in STATIC_ATTR.finditer(strip_markup(text)):
        value = re.sub(r"https?://\S+", " ", m[2]).strip()  # example addresses stay as they are
        if value and LETTERS.search(value) and value not in ALLOWED:
            yield f'{m[1]}="{value}"'


BOUND = re.compile(r"""(?:x-text|x-html|:title|:aria-label|:placeholder|:alt)="([^"]*)\"""")
QUOTED = re.compile(r"""'((?:[^'\\]|\\.)*)'|`([^`]*)`""")
CALL = re.compile(r"""(?:\b(?:t|tn|th)|MangaVibe\.t|MV\.t)\(\s*(?:'(?:[^'\\]|\\.)*'|`[^`]*`)(?:\s*,\s*'(?:[^'\\]|\\.)*')?""")


def looks_like_words(s: str) -> bool:
    """A quoted text a person would read: a capital letter and a word, or two words. Not a value
    ('files', 'paged'), a class name or an address."""
    s = re.sub(r"\$\{[^}]*\}", " ", s).strip()  # a template's ${…}
    if not LETTERS.search(s) or s in ALLOWED or s.startswith(("#", "/", "http")):
        return False
    return bool(re.match(r"[A-Z][a-z]", s) or re.search(r"[A-Za-z]{2,} [A-Za-z]{2,}", s))


def bound_texts(text: str):
    """Quoted words in the bindings that show text, not given to t()."""
    for m in BOUND.finditer(text):
        expr = CALL.sub(" ", m[1].replace("&quot;", '"'))
        for q in QUOTED.finditer(expr):
            s = q[1] if q[1] is not None else q[2]
            if looks_like_words(s):
                yield f"{s!r} in {m[0][:80]}"


SCRIPT_CALL = re.compile(r"""(?<![\w.])(?:MV\.|MangaVibe\.)?(toast|confirm|prompt)\(\s*(["'`])((?:(?!\2)[^\\]|\\.)*)\2""")


def script_texts(text: str):
    for m in SCRIPT_CALL.finditer(text):
        if looks_like_words(m[3]):
            yield f"{m[1]}({m[2]}{m[3][:60]}{m[2]})"


def rel(path):
    return path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("path", MARKUP, ids=rel)
def test_markup_has_no_hardcoded_text(path):
    text = path.read_text(encoding="utf-8")
    found = [*shown_text(text), *static_attributes(text), *bound_texts(text)]
    assert not found, f"{rel(path)}: text written straight in; put it in t():\n  " + "\n  ".join(found)


@pytest.mark.parametrize("path", SCRIPTS, ids=rel)
def test_scripts_have_no_hardcoded_messages(path):
    found = list(script_texts(path.read_text(encoding="utf-8")))
    assert not found, f"{rel(path)}: messages written straight in; put them in t():\n  " + "\n  ".join(found)


def test_the_checks_find_hardcoded_text():
    """The checks above catch what they should, and let t() through."""
    assert list(shown_text("<p>Hello there</p><p>{{ t('Fine') }}</p>")) == ["Hello there"]
    assert list(static_attributes('<b title="Close it" :title="x" aria-label="{{ t(\'Fine\') }}">')) == ['title="Close it"']
    assert len(list(bound_texts("""<b x-text="on ? 'Turn it off' : t('Fine')" :title="'files'">"""))) == 1
    assert list(script_texts('toast("Saved it"); toast(t("Fine")); confirm(`Delete ${x}?`)')) == \
        ['toast("Saved it")', "confirm(`Delete ${x}?`)"]
