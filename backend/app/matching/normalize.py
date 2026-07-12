"""Stage 0 — normalization.

Turns messy raw values into canonical, comparable forms so that
"Acme, Inc.", "ACME INCORPORATED" and "Acme Inc" all collapse to "acme".

The functions are deliberately pure and string-in/string-out so they are trivial to
unit-test, plus a `normalize_frame` helper that applies them across a mapped
DataFrame.
"""

from __future__ import annotations

import functools
import re

import pandas as pd
import pycountry
import tldextract
from cleanco import basename
from unidecode import unidecode

from .schema import (
    NORM_DOMAIN,
    NORM_FIRST_METAPHONE,
    NORM_NAME_METAPHONE,
    NORM_NAME_TOKENS,
    CanonicalField,
    norm_col,
)

# ══════════════════════════════════════════════════════════════════════════
# SECTION: Module setup — shared regexes, offline TLD parser, abbreviation table
# One-time constants used by the normalizers below.
# ══════════════════════════════════════════════════════════════════════════

# tldextract normally hits the network to refresh the public-suffix list. Pin it to
# the bundled snapshot so the app works offline and deterministically.
_TLD = tldextract.TLDExtract(suffix_list_urls=())

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")

# Company-name abbreviation expansion (applied token-wise after legal-suffix stripping)
# so "Acme Intl Grp" and "Acme International Group" normalize identically. Extendable.
_ABBREVIATIONS = {
    "intl": "international", "natl": "national", "mfg": "manufacturing",
    "svcs": "services", "svc": "service", "grp": "group", "hldgs": "holdings",
    "bros": "brothers", "assoc": "associates", "dept": "department",
    "dev": "development", "tech": "technology", "sys": "systems",
    "sols": "solutions",
}


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Base cleanup + company-name normalization
# `_base` is the first step of EVERY normalizer (accent-fold, lowercase, trim).
# `normalize_name` makes "Acme, Inc." == "ACME INCORPORATED" == "acme";
# `name_tokens` derives the order-independent blocking key.
# ══════════════════════════════════════════════════════════════════════════


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

    Strips legal suffixes (Inc, LLC, Ltd, GmbH, ...) via cleanco, expands common
    abbreviations (Intl -> international, & -> and), drops a leading "the", removes
    punctuation, and collapses whitespace.
    """
    text = _base(value)
    if not text:
        return ""
    # Map & / + to "and" before punctuation is stripped away.
    text = text.replace("&", " and ").replace("+", " and ")
    # cleanco.basename strips corporate suffixes; feed it the pre-lowercased text.
    stripped = basename(text) or text
    stripped = _PUNCT_RE.sub(" ", stripped)
    stripped = _WS_RE.sub(" ", stripped).strip()

    tokens = stripped.split()
    if tokens and tokens[0] == "the":
        tokens = tokens[1:]
    tokens = [_ABBREVIATIONS.get(t, t) for t in tokens]
    return " ".join(tokens)


def name_tokens(normalized_name: str) -> str:
    """Sorted unique tokens of a normalized name — order-independent blocking key.

    "acme global" and "global acme" both -> "acme global".
    """
    if not normalized_name:
        return ""
    toks = sorted(set(normalized_name.split()))
    return " ".join(toks)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Strong identifiers with guards — website domains & registration IDs
# These feed the deterministic (0.99) stage, so weak/shared values must be
# dropped here: generic domains (gmail, linkedin, ...) and placeholder/short
# IDs return "" (= no signal) instead of creating confident false matches.
# ══════════════════════════════════════════════════════════════════════════

# Domains that many unrelated companies share, so a shared value is NOT evidence of
# being the same entity (free email, social, and site-builder/hosting domains).
GENERIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "outlook.com", "live.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me", "gmx.com", "mail.com", "yandex.com", "qq.com",
    "163.com", "126.com", "linkedin.com", "facebook.com", "fb.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "wix.com", "wixsite.com", "squarespace.com",
    "weebly.com", "godaddy.com", "wordpress.com", "blogspot.com", "shopify.com",
    "example.com", "test.com", "sites.google.com",
})

# Registration/tax-id values that are placeholders, not real identifiers.
_PLACEHOLDER_IDS = frozenset({
    "na", "n/a", "none", "null", "nil", "tbd", "unknown", "pending", "notavailable",
    "notapplicable", "xxx", "test",
})
_MIN_ID_LEN = 4  # ids shorter than this are too ambiguous to match on


def normalize_domain(value: object) -> str:
    """Extract a registrable domain from a URL or email. "" if none/generic.

    Returns "" for free-email/social/hosting domains (see GENERIC_DOMAINS) since a
    shared value there is not evidence two companies are the same entity.
    """
    text = _base(value)
    if not text:
        return ""
    if "@" in text and "/" not in text:  # looks like an email
        text = text.split("@", 1)[1]
    ext = _TLD(text)
    if not ext.domain or not ext.suffix:
        return ""
    domain = f"{ext.domain}.{ext.suffix}"
    return "" if domain in GENERIC_DOMAINS else domain


def normalize_id(value: object) -> str:
    """Canonicalize a registration/tax id (alphanumerics, lowercase).

    Returns "" for values too weak to match on: placeholders ("N/A", "unknown"),
    anything shorter than _MIN_ID_LEN, or a single repeated character ("0000").
    """
    text = _base(value)
    if not text:
        return ""
    if text in _PLACEHOLDER_IDS:
        return ""
    cleaned = _NON_ALNUM_RE.sub("", text)
    if len(cleaned) < _MIN_ID_LEN:
        return ""
    if len(set(cleaned)) == 1:  # e.g. "0000", "aaaa"
        return ""
    return cleaned


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Geography — address/city, country (→ ISO code), state (→ postal
# code), postcode. Normalizing these lets "USA" == "United States" and
# "California" == "CA" corroborate matches instead of reading as conflicts.
# ══════════════════════════════════════════════════════════════════════════


def normalize_generic(value: object) -> str:
    """Address/city: punctuation-stripped, collapsed whitespace."""
    text = _base(value)
    if not text:
        return ""
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


@functools.lru_cache(maxsize=4096)
def _country_iso2(cleaned: str) -> str:
    """Resolve a cleaned country string to ISO 3166-1 alpha-2 (lowercase), or "".

    Tries the string as-is and de-spaced (so "u s a" from "U.S.A." resolves via the
    alpha-3 "usa"). Cached — a file usually has only a handful of country values.
    """
    if not cleaned:
        return ""
    for candidate in (cleaned, cleaned.replace(" ", "")):
        try:
            return pycountry.countries.lookup(candidate).alpha_2.lower()
        except LookupError:
            continue
    try:  # fuzzy is slower and can be noisy; only as a last resort
        return pycountry.countries.search_fuzzy(cleaned)[0].alpha_2.lower()
    except Exception:  # noqa: BLE001 - never let this raise
        return ""


def normalize_country(value: object) -> str:
    """Resolve a country to ISO 3166-1 alpha-2 (e.g. "USA"/"United States" -> "us").

    Unresolvable input is passed through cleaned (never drop signal).
    """
    text = _base(value)
    if not text:
        return ""
    cleaned = _WS_RE.sub(" ", re.sub(r"[^a-z ]", " ", text)).strip()
    iso = _country_iso2(cleaned)
    return iso or _WS_RE.sub(" ", _PUNCT_RE.sub(" ", text)).strip()


# US states + DC + territories and Canadian provinces, mapped both full-name and
# abbreviation -> lowercase postal code. (Extend to other countries later.)
def _build_region_map() -> dict[str, str]:
    us = {
        "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
        "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
        "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
        "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
        "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
        "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
        "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
        "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
        "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
        "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
        "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
        "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa",
        "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
        "district of columbia": "dc", "puerto rico": "pr", "guam": "gu",
    }
    ca = {
        "alberta": "ab", "british columbia": "bc", "manitoba": "mb",
        "new brunswick": "nb", "newfoundland and labrador": "nl",
        "nova scotia": "ns", "ontario": "on", "prince edward island": "pe",
        "quebec": "qc", "saskatchewan": "sk", "northwest territories": "nt",
        "nunavut": "nu", "yukon": "yt",
    }
    region = {}
    for full, abbr in {**us, **ca}.items():
        region[full] = abbr
        region[abbr] = abbr
    return region


_REGION_MAP = _build_region_map()


def normalize_state(value: object) -> str:
    """Map a US state / Canadian province (full name or abbreviation) to its postal
    code (lowercase). Unknown values pass through cleaned."""
    cleaned = normalize_generic(value)
    if not cleaned:
        return ""
    return _REGION_MAP.get(cleaned, cleaned)


def normalize_postcode(value: object) -> str:
    """Postcodes: alphanumerics only (handles "SW1A 1AA" vs "sw1a1aa")."""
    text = _base(value)
    return _NON_ALNUM_RE.sub("", text)


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Phone numbers — parse to E.164 with placeholder guards
# A shared phone is a strong identifier (like a domain), so invalid or
# placeholder values must become "" rather than false 0.99 matches.
# ══════════════════════════════════════════════════════════════════════════

# Phone numbers a company would never uniquely own (placeholders / fiction range).
_PHONE_PLACEHOLDER_RE = re.compile(r"555\s*-?\s*555|123\s*-?\s*456\s*-?\s*7890")


def normalize_phone(value: object, region: str = "US") -> str:
    """Parse a phone number to E.164 ("+14155552671"). "" when invalid or placeholder.

    `region` is the country hint for numbers written without a country code.
    Guards (return ""): unparseable/invalid numbers, < 7 significant digits,
    all-same-digit, and 555-555 / 123-456-7890 style placeholders.
    """
    text = _base(value)
    if not text:
        return ""
    if _PHONE_PLACEHOLDER_RE.search(text):
        return ""
    digits = re.sub(r"\D", "", text)
    if len(digits) < 7 or len(set(digits)) == 1:
        return ""
    try:
        import phonenumbers

        parsed = phonenumbers.parse(text, region.upper() or "US")
        if not phonenumbers.is_valid_number(parsed):
            return ""
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:  # unparseable input or phonenumbers missing -> no signal
        return ""


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Phonetic keys (metaphone) — typo-tolerant blocking
# Sound-alike codes let "Jonson" and "Johnson" land in the same candidate
# block even though their first letters differ.
# ══════════════════════════════════════════════════════════════════════════


def name_metaphone(normalized_name: str) -> str:
    """Per-token metaphone codes of a normalized name, space-joined ("" on failure).

    "johnson controls" and "jonson controls" share the same codes, so phonetic
    blocking survives typos that break the 4-char-prefix keys.
    """
    if not normalized_name:
        return ""
    try:
        import jellyfish

        codes = [jellyfish.metaphone(tok) for tok in normalized_name.split()]
        return " ".join(c for c in codes if c)
    except Exception:  # jellyfish missing/odd input -> feature degrades to off
        return ""


# ══════════════════════════════════════════════════════════════════════════
# SECTION: Stage 0 entry point — apply all normalizers to a DataFrame
# Adds one `norm_*` column per canonical field plus the derived columns
# (domain, phone, name tokens, metaphones). Downstream stages only ever read
# these normalized columns, never the raw file headers.
# ══════════════════════════════════════════════════════════════════════════

# Which normalizer to apply per canonical field.
_FIELD_NORMALIZERS = {
    CanonicalField.NAME: normalize_name,
    CanonicalField.ADDRESS: normalize_generic,
    CanonicalField.CITY: normalize_generic,
    CanonicalField.STATE: normalize_state,
    CanonicalField.COUNTRY: normalize_country,
    CanonicalField.POSTCODE: normalize_postcode,
    CanonicalField.REGISTRATION_ID: normalize_id,
}


def normalize_frame(
    df: pd.DataFrame,
    mapping: dict[CanonicalField, str],
    phone_region: str = "US",
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

    # Phone needs the region hint, so it can't sit in the plain normalizer map.
    phone_src = mapping.get(CanonicalField.PHONE)
    phone_target = norm_col(CanonicalField.PHONE)
    if phone_src is not None and phone_src in out.columns:
        out[phone_target] = out[phone_src].map(
            lambda v: normalize_phone(v, phone_region)
        )
    else:
        out[phone_target] = ""

    name_norm = out[norm_col(CanonicalField.NAME)]
    out[NORM_NAME_TOKENS] = name_norm.map(name_tokens)
    metaphones = name_norm.map(name_metaphone)
    out[NORM_NAME_METAPHONE] = metaphones
    out[NORM_FIRST_METAPHONE] = metaphones.map(
        lambda m: m.split(" ", 1)[0] if m else ""
    )
    return out
