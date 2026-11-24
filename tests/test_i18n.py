"""The app in other languages (issue #263, app/i18n.py): .po files, t() / tn(), the page's language."""
import json
import re

import pytest

from app import i18n
from app.plugins import web


def po_files():
    return sorted(i18n.I18N_DIR.glob("*.po"))


# --- the files ----------------------------------------------------------------------------------

def test_messages_pot_is_up_to_date():
    """messages.pot has every text the code marks with t() / tn() / N_(): run `python -m app.i18n` after
    adding or changing one."""
    assert i18n.POT.read_text(encoding="utf-8") == i18n.make_pot(), "run: python -m app.i18n"


@pytest.mark.parametrize("po", po_files(), ids=lambda p: p.name)
def test_po_file_is_up_to_date_and_readable(po):
    text = po.read_text(encoding="utf-8")
    assert i18n.update_po(text, i18n.extract()) == text, "run: python -m app.i18n"
    cat = i18n.Catalog.from_po(po.stem, text)
    assert i18n.header_fields(i18n.parse_po(text)).get("Language") == po.stem
    assert cat.nplurals >= 1


@pytest.mark.parametrize("po", po_files(), ids=lambda p: p.name)
def test_translations_keep_the_placeholders(po):
    """A translation that drops or misspells a {name} would show the wrong thing (or a raw {name})."""
    cat = i18n.Catalog.from_po(po.stem, po.read_text(encoding="utf-8"))
    for msgid, value in cat.messages.items():
        for form in value if isinstance(value, list) else [value]:
            extra = i18n.placeholders(form) - i18n.placeholders(msgid) - {"n"}
            assert not extra, f"{po.name}: {msgid!r} -> {form!r} has {extra}"
            if not isinstance(value, list):
                assert i18n.placeholders(form) == i18n.placeholders(msgid), f"{po.name}: {msgid!r} -> {form!r}"
        if isinstance(value, list):
            assert len(value) == cat.nplurals, f"{po.name}: {msgid!r} needs {cat.nplurals} forms"


def test_no_text_is_marked_twice_as_singular_and_plural():
    found = i18n.extract()
    singular = {mid for mid, pl in found if pl is None}
    plural = {mid for mid, pl in found if pl is not None}
    assert not singular & plural


def test_t_is_not_used_as_a_variable_in_the_markup():
    """t() is a global in the page's Alpine expressions; a loop variable called t (x-for="t in tags") would
    hide it inside that loop."""
    bad = re.compile(r"""x-for="\(?\[?\s*(?:\w+\s*,\s*)*t\s*[,)\]]|x-for="t in |\(t\) =>""")
    for folder in (web.TEMPLATES_DIR, web.TEMPLATES_DIR.parent.parent / "plugins" / "builtin"):
        for path in folder.rglob("*.html"):
            assert not bad.search(path.read_text(encoding="utf-8")), path


# --- reading and translating --------------------------------------------------------------------

PO = r'''msgid ""
msgstr ""
"Language: xx\n"
"Plural-Forms: nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);\n"

#: app/a.html
msgid "Saved"
msgstr "Gespeichert"

msgid "Say \"hi\"\n"
"twice"
msgstr "Sag \"hallo\"\n"
"zweimal"

msgid "{n} book"
msgid_plural "{n} books"
msgstr[0] "{n} kniga"
msgstr[1] "{n} knigi"
msgstr[2] "{n} knig"

msgid "Untranslated"
msgstr ""
'''


def test_reading_a_po_file():
    cat = i18n.Catalog.from_po("xx", PO)
    assert cat.gettext("Saved") == "Gespeichert"
    assert cat.gettext('Say "hi"\ntwice') == 'Sag "hallo"\nzweimal'
    assert cat.gettext("Untranslated") == "Untranslated"  # falls back to English
    assert cat.gettext("Not there") == "Not there"
    assert [cat.ngettext("{n} book", "{n} books", n) for n in (1, 3, 5, 21)] == \
           ["{n} kniga", "{n} knigi", "{n} knig", "{n} kniga"]
    assert cat.ngettext("{n} book", "{n} books", 1.5) == "{n} knigi"  # a fraction: the form of 2
    assert cat.ngettext("{n} chapter", "{n} chapters", 2) == "{n} chapters"  # English for a missing one


def test_writing_and_reading_back():
    entries = [('Say "hi"\ntwice', None, ["a.html"], {0: "x"}), ("{n} book", "{n} books", [], {0: "a", 1: "b"})]
    text = i18n.render_po({"Language": "xx"}, entries)
    cat = i18n.Catalog.from_po("xx", text)
    assert cat.messages == {'Say "hi"\ntwice': "x", "{n} book": ["a", "b"]}


def test_a_plural_rule_that_is_not_one_is_refused():
    with pytest.raises(ValueError):
        i18n.Catalog("xx", {}, "nplurals=2; plural=__import__('os');")


def test_placeholders():
    assert i18n.fill("Scanning {done} of {total}", {"done": 3, "total": 9}) == "Scanning 3 of 9"
    assert i18n.fill("{missing} stays", {}) == "{missing} stays"


def test_t_and_tn_in_the_current_language():
    with i18n.using("bg"):
        assert i18n.t("Settings") == "Настройки"
        assert i18n.tn("{n} second", "{n} seconds", 5) == "5 секунди"
        assert i18n.t("A text nobody translated") == "A text nobody translated"
    assert i18n.t("Settings") == "Settings"
    with i18n.using("ja"):
        assert i18n.tn("{n} second", "{n} seconds", 5) == "5 秒"


def test_templates_t_escapes_the_text_and_keeps_markup_values():
    """{{ t(text, name=markup|safe) }}: the translation is escaped, a markup value goes in as it is, any
    other value escaped."""
    tpl = web.templates.from_string("""{{ t('a <b> {x} {y}', x='<code>c</code>'|safe, y='<i>') }}""")
    assert tpl.render() == "a &lt;b&gt; <code>c</code> &lt;i&gt;"
    tpl = web.templates.from_string("""<p title="{{ t("Don't show again") }}">""")
    assert tpl.render() == '<p title="Don&#x27;t show again">'


@pytest.mark.parametrize("header, lang", [
    ("bg-BG,bg;q=0.9,en;q=0.8", "bg"),
    ("de-AT", "de"),
    ("fr-FR,ja;q=0.5", "ja"),
    ("fr, de;q=0", "en"),          # q=0: not wanted
    ("en;q=0.5, de;q=0.9", "de"),  # by quality, not order
    ("", "en"), (None, "en"), ("*", "en"),
])
def test_language_from_the_browser(header, lang):
    assert i18n.match_language(header) == lang


def test_saved_language_wins_unless_the_app_lacks_it():
    assert i18n.pick_language("ja", "de") == "ja"
    assert i18n.pick_language("auto", "de") == "de"
    assert i18n.pick_language("xx", "de") == "de"


def test_a_plugins_own_file_adds_texts_but_does_not_change_the_apps(tmp_path, monkeypatch):
    po = tmp_path / "bg.po"
    po.write_text('msgid "Hello from a plugin"\nmsgstr "Здравей от добавка"\n\n'
                  'msgid "Settings"\nmsgstr "Something else"\n', encoding="utf-8")
    monkeypatch.setattr(i18n, "plugin_po_files", lambda lang: [po] if lang == "bg" else [])
    cat = i18n.catalog("bg")
    assert cat.gettext("Hello from a plugin") == "Здравей от добавка"
    assert cat.gettext("Settings") == "Настройки"
    # English has no file of its own: a plugin's English texts are its msgids anyway
    assert i18n.catalog("en").gettext("Hello from a plugin") == "Hello from a plugin"


# --- the page -----------------------------------------------------------------------------------

def test_page_in_the_browsers_language(client):
    r = client.get("/", headers={"Accept-Language": "bg-BG,bg;q=0.9"})
    assert r.headers["content-language"] == "bg"
    page = r.text
    assert '<html lang="bg"' in page
    assert "Търсене в библиотеката" in page  # the search box, drawn on the server
    # Settings' menu (a plugin's settings_pages label) and the texts for t() in the browser
    pages = json.loads(re.search(r"window\.MV_SETTINGS_PAGES = (.*?);\n", page)[1])
    assert {"Изглед", "Добавки"} <= {p["label"] for p in pages}
    texts = json.loads(re.search(r"window\.MV_I18N = (.*?);  //", page)[1])
    assert texts["lang"] == "bg" and texts["messages"]["Saved"] == "Запазено"
    assert texts["languages"] == ["en", "bg", "de", "ja"]


def test_page_in_english_by_default(client):
    page = client.get("/", headers={"Accept-Language": "fr"}).text
    assert '<html lang="en"' in page and "Search your library" in page


def test_the_server_answers_in_the_language_of_the_person_asking(client):
    """Every request runs in the person's language (the Language middleware, app/main.py): error messages,
    book labels and what plugins say."""
    de = {"Accept-Language": "de-DE"}
    r = client.get("/api/series/999999", headers=de)
    assert r.status_code == 404 and r.json()["detail"] == "Serie nicht gefunden"
    assert client.get("/api/series/999999").json()["detail"] == "Series not found"
    series = client.get("/api/series?q=Hana", headers=de).json()["items"][0]
    labels = [b["label"] for b in client.get(f"/api/series/{series['id']}/books", headers=de).json()]
    assert "Bd. 1 Kap. 1" in labels, labels
    plugins = {p["id"]: p for p in client.get("/api/plugins", headers={"Accept-Language": "bg"}).json()["plugins"]}
    assert plugins["book-labels"]["name"] == "Имена на книгите"
    assert plugins["book-labels"]["permissions"] == ["Чете номерата на книгите; не се свързва никъде"]


def test_error_messages_and_plugin_texts_are_found_in_python():
    """python -m app.i18n reads Python with ast: HTTPException messages (also split over lines) and the
    built-in plugins' names, descriptions and permissions are in messages.pot."""
    found = i18n.extract()
    assert ("Only an admin can do this", None) in found
    assert ("Book labels", None) in found
    assert ("Calls books by their numbers (Vol 3, Ch 12-14, Extra 2) instead of their file names. Built in.", None) in found
    assert ("Vol {n}", None) in found


def test_saved_language_is_each_persons_own(client):
    try:
        client.put("/api/settings", json={"language": "de"})
        assert client.get("/api/settings").json()["language"] == "de"
        page = client.get("/", headers={"Accept-Language": "bg"}).text
        assert '<html lang="de"' in page and "Bibliothek durchsuchen" in page
    finally:
        client.put("/api/settings", json={"language": "auto"})
