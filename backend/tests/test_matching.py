"""Unit tests for the matching stages on small fixtures."""

from __future__ import annotations

import pandas as pd

from app.matching import cluster, deterministic, fuzzy, normalize
from app.matching.dedupe import run_dedupe
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


# ---- clustering ------------------------------------------------------------

def test_union_find_connected_components():
    # links 0-1 and 1-2 form one cluster; 3 is a singleton.
    links = pd.DataFrame({"idx_a": [0, 1], "idx_b": [1, 2]})
    out = cluster.assign_master_ids(4, links)
    m = dict(zip(out["row_idx"], out["master_id"]))
    assert m[0] == m[1] == m[2]
    assert m[3] != m[0]
    sizes = dict(zip(out["row_idx"], out["cluster_size"]))
    assert sizes[0] == 3 and sizes[3] == 1


# ---- single-file dedupe ----------------------------------------------------

def test_dedupe_clusters_duplicates():
    mapping = {CanonicalField.NAME: "name", CanonicalField.REGISTRATION_ID: "ein"}
    df = pd.DataFrame([
        {"name": "Acme, Inc.", "ein": "12-3456789"},      # 0 \
        {"name": "ACME INCORPORATED", "ein": "123456789"}, # 1 / same via reg id
        {"name": "Globex Corporation", "ein": ""},         # 2 \
        {"name": "Globex Corp", "ein": ""},                # 3 / same via fuzzy name
        {"name": "Wayne Enterprises", "ein": ""},          # 4  unique
    ])
    result = run_dedupe(df, mapping, MatchConfig(use_probabilistic=False))
    m = dict(zip(result.clusters["row_idx"], result.clusters["master_id"]))
    assert m[0] == m[1]           # Acme duplicates share a master id
    assert m[2] == m[3]           # Globex duplicates share a master id
    assert len({m[0], m[2], m[4]}) == 3   # three distinct entities

    c = result.counts
    assert c["records"] == 5
    assert c["clusters"] == 3
    assert c["duplicate_rows"] == 4
    assert c["duplicate_clusters"] == 2
    assert c["unique_rows"] == 1


# ---- review decisions (confirm / dismiss) ----------------------------------

def test_review_confirm_and_dismiss(monkeypatch):
    import app.storage as storage
    from app import review

    base = pd.DataFrame([
        {"idx_a": 0, "idx_b": 0, "score": 0.99, "stage": "deterministic", "rule": "r", "match_class": "match"},
        {"idx_a": 1, "idx_b": 1, "score": 0.80, "stage": "fuzzy", "rule": "r", "match_class": "review"},
        {"idx_a": 2, "idx_b": 2, "score": 0.78, "stage": "fuzzy", "rule": "r", "match_class": "review"},
        {"idx_a": 3, "idx_b": -1, "score": 0.0, "stage": "", "rule": "", "match_class": "unique_a"},
        {"idx_a": -1, "idx_b": 3, "score": 0.0, "stage": "", "rule": "", "match_class": "unique_b"},
    ])
    store = {"s": base}
    decs: dict = {}
    monkeypatch.setattr(storage, "load_result", lambda sid: store[sid])
    monkeypatch.setattr(storage, "load_decisions", lambda sid: decs.get(sid, {}))
    monkeypatch.setattr(storage, "save_decisions", lambda sid, d: decs.__setitem__(sid, d))

    sid = "s"
    c = review.effective_counts(sid)
    assert c["match"] == 1 and c["review"] == 2
    assert c["records_a"] == 4 and c["records_b"] == 4

    # confirm one review -> becomes a match
    review.set_decision(sid, 1, 1, "confirmed")
    c = review.effective_counts(sid)
    assert c["match"] == 2 and c["review"] == 1 and c["confirmed"] == 1

    # dismiss the other -> its records fall back to unique
    review.set_decision(sid, 2, 2, "dismissed")
    c = review.effective_counts(sid)
    assert c["match"] == 2 and c["review"] == 0 and c["dismissed"] == 1
    eff = review.effective_frame(sid)
    assert 2 in set(eff.loc[eff["match_class"] == "unique_a", "idx_a"])
    assert 2 in set(eff.loc[eff["match_class"] == "unique_b", "idx_b"])
    # reconciliation holds after decisions
    assert c["match"] + c["review"] + c["unique_a"] == c["records_a"]
    assert c["matched_b"] + c["unique_b"] == c["records_b"]

    # dismissed audit view + undo
    assert len(review.dismissed_frame(sid)) == 1
    review.set_decision(sid, 2, 2, "pending")
    c = review.effective_counts(sid)
    assert c["review"] == 1 and c["dismissed"] == 0

    # invalid pair rejected
    import pytest
    with pytest.raises(KeyError):
        review.set_decision(sid, 0, 0, "confirmed")  # 0,0 is an auto match, not review


# ---- ground-truth evaluation (debug) ---------------------------------------

def test_evaluation_precision_recall(monkeypatch):
    import app.storage as storage
    from app import evaluation

    # A real ground-truth pair from the repo file + one fabricated false match.
    raw_a = pd.DataFrame({"company_name": ["Willowbrook Technologies Co.", "Decoy A Inc"]})
    raw_b = pd.DataFrame({"company_name": ["Willowbrook Technologies, Co.", "Decoy B Inc"]})
    base = pd.DataFrame([
        {"idx_a": 0, "idx_b": 0, "score": 0.99, "stage": "det", "rule": "r", "match_class": "match"},
        {"idx_a": 1, "idx_b": 1, "score": 0.99, "stage": "det", "rule": "r", "match_class": "match"},
    ])
    meta = {"request": {"mode": "link", "file_id_a": "fa", "file_id_b": "fb",
                        "mapping_a": {"name": "company_name"}, "mapping_b": {"name": "company_name"}}}

    monkeypatch.setattr(storage, "load_session_meta", lambda sid: meta)
    monkeypatch.setattr(storage, "load_result", lambda sid: base)
    monkeypatch.setattr(storage, "load_decisions", lambda sid: {})
    monkeypatch.setattr(storage, "load_upload", lambda fid: raw_a if fid == "fa" else raw_b)

    if not evaluation.GROUND_TRUTH_PATH.exists():
        pytest.skip("ground_truth_matches.csv not present")

    r = evaluation.evaluate("s")
    assert r["available"] and r["applicable"]
    assert r["ground_truth_total"] == 200
    assert r["tp"] == 1 and r["fp"] == 1          # Willowbrook correct, Decoy false
    assert r["fn"] == r["ground_truth_total"] - 1
    assert r["precision"] == 0.5
