"""Small text helpers shared by providers and the matcher."""
import html
import re
import unicodedata

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)


def clean_html(s):
    """Provider descriptions contain <br>, <i>, entities. Turn them into plain text."""
    if not s:
        return None
    s = _BR.sub("\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None


def has_script(s, script):
    """script: 'ja' (kana), 'ko' (hangul), 'zh' (han without kana)."""
    kana = any("぀" <= c <= "ヿ" for c in s)
    hangul = any("가" <= c <= "힯" for c in s)
    han = any("一" <= c <= "鿿" for c in s)
    return {"ja": kana or (han and not hangul), "ko": hangul, "zh": han and not kana}[script]


def normalize_title(s):
    """Lowercase, no accents/punctuation, so 'Hana to Akuma!' == 'hana to akuma'."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[\s_\-–—:;,.!?'\"’`~()\[\]{}/\\&+]+", " ", s)
    return s.strip()
