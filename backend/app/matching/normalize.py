"""Stage 0 — normalization.

Turns messy raw values into canonical, comparable forms so that
"Acme, Inc.", "ACME INCORPORATED" and "Acme Inc" all collapse to "acme".

The functions are deliberately pure and string-in/string-out so they are trivial to
unit-test, plus a `normalize_frame` helper that applies them across a mapped
DataFrame.
"""

from __future__ import annotations

import re

import pandas as pd
import tldextract
from cleanco import basename
from unidecode import unidecode

from .schema import (
    NORM_DOMAIN,
    NORM_NAME_TOKENS,
    CanonicalField,
    norm_col,
)

# tldextract normally hits the network to refresh the public-suffix list. Pin it to
# the bundled snapshot so the app works offline and deterministically.
_TLD = tldextract.TLDExtract(suffix_list_urls=())

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _base(value: object) -> str:
    """Common cleanup: unidecode -> lowercase -> strip. Empty for NaN/None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return unidecode(text).lower().strip()


def normalize_name(value: object) -> str:
    """Company name -> comparable token string.

    Strips legal suffixes (Inc, LLC, Ltd, GmbH, ...) via cleanco, removes
    punctuation, and collapses whitespace.
    """
    text = _base(value)
    if not text:
        return ""
    # cleanco.basename strips corporate suffixes; feed it the pre-lowercased text.
    stripped = basename(text) or text
    stripped = _PUNCT_RE.sub(" ", stripped)
    return _WS_RE.sub(" ", stripped).strip()


def name_tokens(normalized_name: str) -> str:
    """Sorted unique tokens of a normalized name — order-independent blocking key.

    "acme global" and "global acme" both -> "acme global".
    """
    if not normalized_name:
        return ""
    toks = sorted(set(normalized_name.split()))
    return " ".join(toks)


def normalize_domain(value: object) -> str:
    """Extract a registrable domain from a URL or email. "" if none found."""
    text = _base(value)
    if not text:
        return ""
    if "@" in text and "/" not in text:  # looks like an email
        text = text.split("@", 1)[1]
    ext = _TLD(text)
    if not ext.domain or not ext.suffix:
        return ""
    return f"{ext.domain}.{ext.suffix}"


def normalize_id(value: object) -> str:
    """Canonicalize a registration/tax id: keep alphanumerics only, lowercase."""
    text = _base(value)
    if not text:
        return ""
    return _NON_ALNUM_RE.sub("", text)


def normalize_generic(value: object) -> str:
    """Address/city/state/country: punctuation-stripped, collapsed whitespace."""
    text = _base(value)
    if not text:
        return ""
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def normalize_postcode(value: object) -> str:
    """Postcodes: alphanumerics only (handles "SW1A 1AA" vs "sw1a1aa")."""
    text = _base(value)
    return _NON_ALNUM_RE.sub("", text)


# Which normalizer to apply per canonical field.
_FIELD_NORMALIZERS = {
    CanonicalField.NAME: normalize_name,
    CanonicalField.ADDRESS: normalize_generic,
    CanonicalField.CITY: normalize_generic,
    CanonicalField.STATE: normalize_generic,
    CanonicalField.COUNTRY: normalize_generic,
    CanonicalField.POSTCODE: normalize_postcode,
    CanonicalField.REGISTRATION_ID: normalize_id,
}


def normalize_frame(
    df: pd.DataFrame, mapping: dict[CanonicalField, str]
) -> pd.DataFrame:
    """Add `norm_*` columns to a copy of `df` based on the canonical->source mapping.

    `mapping` maps a CanonicalField to the raw column name in `df`. Unmapped fields
    yield empty normalized columns so downstream code can rely on them existing.
    """
    out = df.copy()

    for field, normalizer in _FIELD_NORMALIZERS.items():
        target = norm_col(field)
        src = mapping.get(field)
        if src is not None and src in out.columns:
            out[target] = out[src].map(normalizer)
        else:
            out[target] = ""

    # Website is special: it feeds a derived domain column, not a same-named one.
    website_src = mapping.get(CanonicalField.WEBSITE)
    if website_src is not None and website_src in out.columns:
        out[NORM_DOMAIN] = out[website_src].map(normalize_domain)
    else:
        out[NORM_DOMAIN] = ""

    out[NORM_NAME_TOKENS] = out[norm_col(CanonicalField.NAME)].map(name_tokens)
    return out
