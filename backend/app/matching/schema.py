"""Canonical field definitions for entity matching.

User-uploaded files have arbitrary column names. During the "map columns" step the
frontend maps each canonical field below to a source column (or leaves it unmapped).
Everything downstream (normalization, blocking, comparison) speaks in these canonical
names, never the raw file headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CanonicalField(str, Enum):
    """Fields the matcher understands. `name` is the only strictly required one."""

    NAME = "name"
    ADDRESS = "address"
    CITY = "city"
    STATE = "state"
    POSTCODE = "postcode"
    COUNTRY = "country"
    WEBSITE = "website"  # URL or email — a domain is extracted from it
    REGISTRATION_ID = "registration_id"  # DUNS / EIN / VAT / company number
    PHONE = "phone"  # normalized to E.164; a strong secondary identifier


@dataclass(frozen=True)
class FieldSpec:
    field: CanonicalField
    label: str
    required: bool
    help: str


# Ordered spec used to render the column-mapping UI and validate mappings.
FIELD_SPECS: list[FieldSpec] = [
    FieldSpec(CanonicalField.NAME, "Company name", True, "Legal or trading name."),
    FieldSpec(CanonicalField.REGISTRATION_ID, "Registration / Tax ID", False,
              "DUNS, EIN, VAT or company number — a near-exact identifier."),
    FieldSpec(CanonicalField.WEBSITE, "Website / Email", False,
              "URL or email; a domain is extracted and used as a strong signal."),
    FieldSpec(CanonicalField.PHONE, "Phone", False,
              "Normalized internationally; a strong secondary identifier."),
    FieldSpec(CanonicalField.ADDRESS, "Street address", False, "Street line(s)."),
    FieldSpec(CanonicalField.CITY, "City", False, ""),
    FieldSpec(CanonicalField.STATE, "State / Region", False, ""),
    FieldSpec(CanonicalField.POSTCODE, "Postal code", False, "Used as a blocking key."),
    FieldSpec(CanonicalField.COUNTRY, "Country", False, ""),
]

REQUIRED_FIELDS = [s.field for s in FIELD_SPECS if s.required]

# Derived columns produced by normalization (prefix keeps them clear of raw data).
NORM_PREFIX = "norm_"


def norm_col(field: CanonicalField) -> str:
    return f"{NORM_PREFIX}{field.value}"


# Extra derived columns not tied 1:1 to a canonical field.
NORM_DOMAIN = "norm_domain"        # from WEBSITE
NORM_NAME_TOKENS = "norm_name_tokens"  # sorted token string, for blocking
NORM_NAME_METAPHONE = "norm_name_metaphone"      # per-token metaphones, space-joined
NORM_FIRST_METAPHONE = "norm_first_metaphone"    # first name-token metaphone (blocking)
