"""Unit tests for the matching stages on small fixtures."""

from __future__ import annotations

import pandas as pd

from app.matching import deterministic, fuzzy, normalize
from app.matching.pipeline import MatchConfig, run_pipeline
from app.matching.schema import CanonicalField, norm_col, NORM_DOMAIN
from app.matching.scoring import MATCH, REVIEW, Thresholds, combine_and_classify


# ---- normalization ---------------------------------------------------------

def test_name_normalization_collapses_variants():
    assert normalize.normalize_name("Acme, Inc.") == normalize.normalize_name(
        "ACME INCORPORATED"
    )
    assert normalize.normalize_name("Acme Inc") == "acme"
    assert normalize.normalize_name("Café Ltd.") == "cafe"


def test_name_tokens_are_order_independent():
    a = normalize.name_tokens(normalize.normalize_name("Acme Global"))
    b = normalize.name_tokens(normalize.normalize_name("Global Acme"))
    assert a == b == "acme global"


def test_domain_extraction():
    assert normalize.normalize_domain("https://www.Acme.com/about") == "acme.com"
    assert normalize.normalize_domain("jane@acme.co.uk") == "acme.co.uk"
    assert normalize.normalize_domain("not a url") == ""


def test_id_normalization():
    assert normalize.normalize_id("12-3456789") == "123456789"
    assert normalize.normalize_id("GB 123 4567 89") == "gb123456789"


# ---- deterministic ---------------------------------------------------------

def _frame(records: list[dict], mapping: dict) -> pd.DataFrame:
    return normalize.normalize_frame(pd.DataFrame(records), mapping)


def test_deterministic_matches_on_reg_id():
    mapping = {CanonicalField.NAME: "name", CanonicalField.REGISTRATION_ID: "ein"}
    a = _frame([{"name": "Acme Inc", "ein": "12-3456789"},
                {"name": "Beta LLC", "ein": "99-9999999"}], mapping)
    b = _frame([{"name": "ACME", "ein": "123456789"}], mapping)
    pairs = deterministic.match_deterministic(a, b)
    assert len(pairs) == 1
    assert pairs.iloc[0]["idx_a"] == 0 and pairs.iloc[0]["idx_b"] == 0
    assert pairs.iloc[0]["rule"] == "registration_id"


def test_deterministic_matches_on_domain():
    mapping = {CanonicalField.NAME: "name", CanonicalField.WEBSITE: "site"}
    a = _frame([{"name": "Acme", "site": "https://acme.com"}], mapping)
    b = _frame([{"name": "Acme Global", "site": "www.acme.com/contact"}], mapping)
    pairs = deterministic.match_deterministic(a, b)
    assert len(pairs) == 1
    assert pairs.iloc[0]["rule"] == "domain"


# ---- fuzzy -----------------------------------------------------------------

def test_fuzzy_matches_similar_names():
    mapping = {CanonicalField.NAME: "name"}
    a = _frame([{"name": "Acme Global Holdings"}], mapping)
    b = _frame([{"name": "Acme Global Holding"}, {"name": "Zeta Corp"}], mapping)
    pairs = fuzzy.match_fuzzy(a, b, threshold=0.8)
    assert len(pairs) == 1
    assert pairs.iloc[0]["idx_b"] == 0
    assert pairs.iloc[0]["score"] >= 0.8


# ---- scoring ---------------------------------------------------------------

def test_classification_bands():
    t = Thresholds(match=0.9, review=0.75)
    pairs = pd.DataFrame(
        {
            "idx_a": [0, 1, 2], "idx_b": [0, 1, 2],
            "score": [0.95, 0.80, 0.50],
            "stage": ["x", "x", "x"], "rule": ["r", "r", "r"],
        }
    )
    out = combine_and_classify(pairs, t)
    classes = dict(zip(out["idx_a"], out["match_class"]))
    assert classes[0] == MATCH
    assert classes[1] == REVIEW
    assert 2 not in classes  # no_match dropped


def test_best_per_a_record_wins():
    t = Thresholds()
    pairs = pd.DataFrame(
        {
            "idx_a": [0, 0], "idx_b": [5, 6], "score": [0.80, 0.95],
            "stage": ["fuzzy", "deterministic"], "rule": ["r", "r"],
        }
    )
    out = combine_and_classify(pairs, t)
    assert len(out) == 1
    assert out.iloc[0]["idx_b"] == 6


# ---- full pipeline (deterministic + fuzzy; probabilistic off for speed) ----

def test_pipeline_end_to_end():
    mapping = {CanonicalField.NAME: "name", CanonicalField.REGISTRATION_ID: "ein"}
    a = pd.DataFrame([
        {"name": "Acme, Inc.", "ein": "12-3456789"},
        {"name": "Globex Corporation", "ein": ""},
        {"name": "Initech LLC", "ein": ""},
    ])
    b = pd.DataFrame([
        {"name": "ACME INCORPORATED", "ein": "123456789"},   # id match -> Acme
        {"name": "Globex Corp", "ein": ""},                  # fuzzy -> Globex
        {"name": "Wayne Enterprises", "ein": ""},            # no match
    ])
    config = MatchConfig(use_probabilistic=False)
    result = run_pipeline(a, b, mapping, mapping, config)
    got = {int(r.idx_a): (int(r.idx_b), r.match_class) for r in result.pairs.itertuples()}
    assert got[0][0] == 0  # Acme matched via reg id
    assert got[1][0] == 1  # Globex matched via fuzzy
    assert 2 not in got    # Initech has no counterpart (a unique record)
    assert result.counts["records_a"] == 3

    # Reconciliation: for each file, matched + review + unique == total rows.
    c = result.counts
    assert result.unique_a_idx == [2]        # Initech
    assert 2 in result.unique_b_idx          # Wayne Enterprises (B[2]) unmatched
    assert c["match"] + c["review"] + c["unique_a"] == c["records_a"]
    assert c["matched_b"] + c["unique_b"] == c["records_b"]

    # storage_frame carries the unique rows as single-sided (sentinel -1) entries.
    frame = result.storage_frame()
    ua = frame[frame["match_class"] == "unique_a"]
    assert set(ua["idx_a"]) == {2} and set(ua["idx_b"]) == {-1}
