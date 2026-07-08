"""Normalize a company name into a canonical key for cross-source grouping.

Different job boards write the same company differently — legal form placement
and spelling ("UAB „Biuro“" vs "Biuro, UAB" vs "SIA \"Biuro\""), quotes, case,
and diacritics. ``normalize_company_name`` strips these so variants of one
company share a single key.

The key is intentionally aggressive (drops legal forms) to maximise grouping;
borderline over-merges are expected to be corrected manually in the admin by
reassigning a company's group.
"""

from __future__ import annotations

import re
import unicodedata

# Legal-form tokens (deaccented, lowercased) dropped from the key.
_LEGAL_FORMS: frozenset[str] = frozenset(
    {
        # Lithuanian
        "uab",
        "ab",
        "mb",
        "vsi",
        "ii",
        "kub",
        "zub",
        "tub",
        "kb",
        "ib",
        "filialas",
        "imone",
        # Baltics / EU / common
        "sia",
        "ou",
        "oy",
        "as",
        "ltd",
        "llc",
        "inc",
        "plc",
        "gmbh",
        "co",
        "corp",
        "company",
        "oue",
        # Russian
        "ooo",
        "oao",
        "zao",
        "pao",
        "ip",
        "nko",
        "ao",
    }
)

# Punctuation / quotes replaced with a space before tokenising.
_PUNCT_RE = re.compile(r"""["'`„“”«»‚’.,/()\-–—_:;+&]""")


def _deaccent(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_company_name(name: str | None) -> str:
    """Return a canonical grouping key, or "" when the name is empty/only a legal form."""
    if not name:
        return ""
    s = _deaccent(name).casefold()
    s = _PUNCT_RE.sub(" ", s)
    tokens = [t for t in s.split() if t and t not in _LEGAL_FORMS]
    return " ".join(tokens)
