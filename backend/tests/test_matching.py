"""Unit tests for the matching stages on small fixtures."""

from __future__ import annotations

import pandas as pd
import pytest

from app.matching import cluster, deterministic, embedding, fuzzy, normalize
from app.matching.dedupe import run_dedupe
from app.matching.pipeline import MatchConfig, run_pipeline
from app.matching.schema import CanonicalField, norm_col
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


def test_name_abbreviation_expansion():
    # "The Acme Intl Grp" and "Acme International Group" collapse to the same form.
    assert normalize.normalize_name("The Acme Intl Grp") == "acme international group"
    assert normalize.normalize_name("The Acme Intl Grp") == normalize.normalize_name(
        "Acme International Group"
    )
    assert normalize.name_tokens(normalize.normalize_name("The Acme Intl Grp")) == \
        normalize.name_tokens(normalize.normalize_name("Acme International Group"))
    # "&" / "+" become "and"
    assert "and" in normalize.normalize_name("Smith & Co").split()
    assert normalize.normalize_name("Smith & Sons") == normalize.normalize_name("Smith and Sons")


def test_country_normalization():
    for v in ["USA", "U.S.A.", "United States", "us", "US"]:
        assert normalize.normalize_country(v) == "us", v
    assert normalize.normalize_country("Canada") == "ca"
    assert normalize.normalize_country("United Kingdom") == "gb"
    # unresolvable input passes through cleaned, never dropped
    assert normalize.normalize_country("Freedonia") == "freedonia"
    assert normalize.normalize_country("") == ""


def test_state_normalization():
    assert normalize.normalize_state("California") == "ca"
    assert normalize.normalize_state("CA") == "ca"
    assert normalize.normalize_state("Ontario") == "on"
    assert normalize.normalize_state("Bavaria") == "bavaria"  # passthrough


def test_id_normalization_rejects_placeholders_and_weak_ids():
    for weak in ["N/A", "unknown", "TBD", "0", "12", "0000", "----"]:
        assert normalize.normalize_id(weak) == "", weak


def test_generic_domains_are_dropped():
    # A shared free-email/social domain is not evidence of the same company.
    assert normalize.normalize_domain("info@gmail.com") == ""
    assert normalize.normalize_domain("https://linkedin.com/company/x") == ""
    assert normalize.normalize_domain("https://www.acme.com/about") == "acme.com"


def test_name_similarity_kills_subset_false_positive():
    # token_set_ratio alone scores this 1.0; the length-aware metric must not.
    subset = fuzzy.name_similarity(
        normalize.normalize_name("Apple Inc"),
        normalize.normalize_name("Apple Bank for Savings"),
    )
    assert subset < 0.6
    # genuine variants / typos stay high
    assert fuzzy.name_similarity(
        normalize.normalize_name("Globex Corporation"),
        normalize.normalize_name("Globex Corp"),
    ) >= 0.9
    assert fuzzy.name_similarity("umbrella health group", "umbrela health group") >= 0.9


def test_address_conflict_demotes_name_match():
    # Same-ish name, clearly different address -> pushed below a 0.90 match threshold.
    has = {"addr": True, "city": False, "pc": False}
    a = pd.Series({norm_col(CanonicalField.ADDRESS): "1 market st san francisco ca"})
    b = pd.Series({norm_col(CanonicalField.ADDRESS): "900 pine road boston ma"})
    demoted, adj = fuzzy.corroborate(0.90, a, b, has)
    assert demoted < 0.80
    assert adj == ["addr_conflict"]  # provenance records why the score moved
    # agreeing address nudges up instead
    b2 = pd.Series({norm_col(CanonicalField.ADDRESS): "1 market street san francisco ca"})
    boosted, adj2 = fuzzy.corroborate(0.90, a, b2, has)
    assert boosted >= 0.90
    assert adj2 == ["addr_agree"]


def test_phone_normalization():
    assert normalize.normalize_phone("(415) 867-5309", "US") == "+14158675309"
    assert normalize.normalize_phone("+44 20 7946 0958", "US") == "+442079460958"
    # guards: placeholders / too short / all-same-digit / junk -> no signal
    for bad in ["555-555-1234", "123-456-7890", "0000000", "12345", "not a phone"]:
        assert normalize.normalize_phone(bad, "US") == "", bad


def test_phonetic_blocking_catches_first_char_typos():
    # "Jonson" vs "Johnson": 4-char prefixes differ (jons/john) but metaphones agree,
    # so the fuzzy stage still compares them and the typo pair is found.
    mapping = {CanonicalField.NAME: "name"}
    a = _frame([{"name": "Jonson"}], mapping)
    b = _frame([{"name": "Johnson"}], mapping)
    with_phonetic = fuzzy.match_fuzzy(a, b, threshold=0.8, phonetic=True)
    assert len(with_phonetic) == 1
    without = fuzzy.match_fuzzy(a, b, threshold=0.8, phonetic=False)
    assert without.empty  # prefix-only blocking misses it ("jons" != "john")


# ---- address parsing (spec 2.1) --------------------------------------------


def test_address_expansion_fallback(monkeypatch):
    # Without libpostal, token-wise abbreviation expansion makes the variants equal.
    monkeypatch.setattr(normalize, "_libpostal", lambda: None)
    mapping = {CanonicalField.NAME: "name", CanonicalField.ADDRESS: "addr"}
    a = normalize.normalize_frame(
        pd.DataFrame([{"name": "Acme", "addr": "123 Main St, Ste 4"}]), mapping
    )
    b = normalize.normalize_frame(
        pd.DataFrame([{"name": "Acme", "addr": "123 Main Street Suite 4"}]), mapping
    )
    addr = norm_col(CanonicalField.ADDRESS)
    assert a.loc[0, addr] == b.loc[0, addr] == "123 main street suite 4"
    # parsed component columns exist but stay empty in fallback mode
    assert a.loc[0, normalize.NORM_ADDR_ROAD] == ""
    assert a.loc[0, normalize.NORM_ADDR_HOUSE_NUMBER] == ""


def test_use_libpostal_off_keeps_old_behavior():
    mapping = {CanonicalField.ADDRESS: "addr"}
    df = pd.DataFrame([{"addr": "123 Main St"}])
    out = normalize.normalize_frame(df, mapping, use_libpostal=False)
    assert out.loc[0, norm_col(CanonicalField.ADDRESS)] == "123 main st"


class _StubLibpostalParser:
    @staticmethod
    def parse_address(text):
        return [
            ("123", "house_number"), ("main st", "road"), ("ste 4", "unit"),
            ("springfield", "city"), ("illinois", "state"), ("62701", "postcode"),
        ]


class _StubLibpostalExpand:
    @staticmethod
    def expand_address(text):
        return ["main street"]


def test_parse_address_and_backfill_with_stub_libpostal(monkeypatch):
    monkeypatch.setattr(
        normalize, "_libpostal",
        lambda: (_StubLibpostalParser, _StubLibpostalExpand),
    )
    # city IS mapped -> never overwritten; state/postcode unmapped -> backfilled.
    mapping = {
        CanonicalField.NAME: "name",
        CanonicalField.ADDRESS: "addr",
        CanonicalField.CITY: "city",
    }
    df = pd.DataFrame(
        [{"name": "Acme", "addr": "123 Main St Ste 4 Springfield IL 62701",
          "city": "Chicago"}]
    )
    out = normalize.normalize_frame(df, mapping)
    assert out.loc[0, normalize.NORM_ADDR_HOUSE_NUMBER] == "123"
    assert out.loc[0, normalize.NORM_ADDR_ROAD] == "main street"  # libpostal-expanded
    assert out.loc[0, norm_col(CanonicalField.CITY)] == "chicago"  # mapped column wins
    assert out.loc[0, norm_col(CanonicalField.STATE)] == "il"      # backfilled + normalized
    assert out.loc[0, norm_col(CanonicalField.POSTCODE)] == "62701"


def test_fuzzy_corroboration_prefers_parsed_address():
    # Raw address strings conflict (unit/PO-box noise) but the parsed house+road
    # agree — with parsed components the adjustment flips from conflict to agree.
    addr = norm_col(CanonicalField.ADDRESS)
    a = pd.Series({addr: "po box 99", normalize.NORM_ADDR_HOUSE_NUMBER: "123",
                   normalize.NORM_ADDR_ROAD: "main street"})
    b = pd.Series({addr: "suite 12 tower plaza", normalize.NORM_ADDR_HOUSE_NUMBER: "123",
                   normalize.NORM_ADDR_ROAD: "main street"})
    _, adj_raw = fuzzy.corroborate(0.90, a, b, {"addr": True, "addr_parsed": False})
    assert adj_raw == ["addr_conflict"]
    _, adj_parsed = fuzzy.corroborate(0.90, a, b, {"addr": True, "addr_parsed": True})
    assert adj_parsed == ["addr_agree"]


# ---- embedding stage (spec 2.2) --------------------------------------------


class _StubEmbedModel:
    """Deterministic stand-in for a SentenceTransformer: 'ibm'-flavored texts map
    to one unit vector, everything else to an orthogonal one."""

    @staticmethod
    def encode(texts, normalize_embeddings=True, show_progress_bar=False):
        import numpy as np

        def vec(text: str):
            name = text.split(",")[0]
            if "ibm" in name or "international business machines" in name:
                return [1.0, 0.0]
            return [0.0, 1.0]

        return np.array([vec(t) for t in texts], dtype="float32")


def _stub_model(monkeypatch):
    monkeypatch.setattr(embedding, "_load_model", lambda name: _StubEmbedModel())


def test_embedding_finds_acronym_but_caps_at_review(monkeypatch):
    _stub_model(monkeypatch)
    mapping = {CanonicalField.NAME: "name"}
    a = _frame([{"name": "IBM"}], mapping)
    b = _frame([{"name": "International Business Machines"}], mapping)
    pairs = embedding.match_embedding(a, b, threshold=0.85)
    assert len(pairs) == 1
    row = pairs.iloc[0]
    assert row["stage"] == "embedding" and row["rule"] == "semantic_cosine"
    # cosine is 1.0 but with no corroboration the score is capped below match
    assert row["score"] == 0.89


def test_embedding_uncapped_when_city_agrees(monkeypatch):
    _stub_model(monkeypatch)
    mapping = {CanonicalField.NAME: "name", CanonicalField.CITY: "city"}
    a = _frame([{"name": "IBM", "city": "Armonk"}], mapping)
    b = _frame([{"name": "International Business Machines", "city": "Armonk"}], mapping)
    pairs = embedding.match_embedding(a, b, threshold=0.85)
    assert len(pairs) == 1
    row = pairs.iloc[0]
    assert row["score"] >= 0.90  # cap lifted; can auto-match
    assert "same_city" in row["adjustments"]


def test_embedding_skips_when_model_unavailable(monkeypatch):
    monkeypatch.setattr(embedding, "_load_model", lambda name: None)
    mapping = {CanonicalField.NAME: "name"}
    a = _frame([{"name": "IBM"}], mapping)
    b = _frame([{"name": "International Business Machines"}], mapping)
    assert embedding.match_embedding(a, b).empty


def test_embedding_within_file(monkeypatch):
    _stub_model(monkeypatch)
    mapping = {CanonicalField.NAME: "name"}
    frame = _frame(
        [{"name": "IBM Corp"}, {"name": "International Business Machines"},
         {"name": "Zeta Plumbing"}],
        mapping,
    )
    pairs = embedding.match_embedding_within(frame, threshold=0.85)
    assert len(pairs) == 1
    assert {int(pairs.iloc[0]["idx_a"]), int(pairs.iloc[0]["idx_b"])} == {0, 1}


def test_pipeline_embedding_runs_on_residuals_only(monkeypatch):
    _stub_model(monkeypatch)
    mapping = {CanonicalField.NAME: "name"}
    raw_a = pd.DataFrame([{"name": "Acme Inc"}, {"name": "IBM"}])
    raw_b = pd.DataFrame([{"name": "Acme Incorporated"},
                          {"name": "International Business Machines"}])
    config = MatchConfig(use_probabilistic=False, use_embedding=True)
    result = run_pipeline(raw_a, raw_b, mapping, mapping, config)
    by_stage = result.pairs.set_index("stage")
    # Acme resolves in the fuzzy stage; only the IBM residual reaches embeddings,
    # where it lands in review (capped), never auto-match.
    assert by_stage.loc["fuzzy", "match_class"] == MATCH
    assert by_stage.loc["embedding", "match_class"] == REVIEW
    # reconciliation invariant holds with the new stage in play
    assert result.counts[MATCH] + result.counts[REVIEW] + result.counts["unique_a"] == 2


def test_csv_sanitize_neutralizes_formula_injection():
    from app.api.routes import _csv_sanitize

    df = pd.DataFrame(
        {
            "name": ["=cmd|' /C calc'!A0", "+HYPERLINK(evil)", "-2+3", "@SUM(A1)", "Acme"],
            "score": [0.9, 0.8, 0.7, 0.6, 0.5],  # numeric column untouched
        }
    )
    out = _csv_sanitize(df)
    assert all(v.startswith("'") for v in out["name"][:4])
    assert out["name"].iloc[4] == "Acme"
    assert list(out["score"]) == list(df["score"])


def test_reclassify_without_rerun(monkeypatch):
    import app.storage as storage
    from app import service

    allpairs = pd.DataFrame(
        {
            "idx_a": [0, 1], "idx_b": [0, 1], "score": [0.95, 0.82],
            "stage": ["fuzzy", "fuzzy"], "rule": ["r", "r"], "adjustments": ["", ""],
        }
    )
    meta = {"request": {"mode": "link", "file_id_a": "fa", "file_id_b": "fb",
                        "mapping_a": {"name": "n"}, "mapping_b": {"name": "n"},
                        "config": {"thresholds": {"match": 0.9, "review": 0.75}}}}
    raw = pd.DataFrame({"n": ["a", "b"]})
    saved: dict = {}
    monkeypatch.setattr(storage, "load_allpairs", lambda sid: allpairs)
    monkeypatch.setattr(storage, "load_session_meta", lambda sid: meta)
    monkeypatch.setattr(storage, "load_upload", lambda fid: raw)
    monkeypatch.setattr(storage, "save_result", lambda sid, f: saved.__setitem__("result", f))
    monkeypatch.setattr(storage, "save_session_meta", lambda sid, m: saved.__setitem__("meta", m))

    # default thresholds: 1 match (0.95) + 1 review (0.82)
    out = service.reclassify("s", 0.90, 0.75)
    assert out["match"] == 1 and out["review"] == 1
    # lower the match bar to 0.80 -> both become matches, no re-matching involved
    out = service.reclassify("s", 0.80, 0.75)
    assert out["match"] == 2 and out["review"] == 0
    assert saved["meta"]["request"]["config"]["thresholds"]["match"] == 0.80
    # reconciliation still holds in the stored frame
    frame = saved["result"]
    assert (frame["match_class"] == "unique_a").sum() == 0  # both A rows matched


def test_per_stage_threshold_override():
    from app.matching.scoring import classify_pair

    t = Thresholds(match=0.90, review=0.75)
    # global: 0.85 fuzzy -> review; with a fuzzy-specific match floor of 0.80 -> match
    assert classify_pair(0.85, "fuzzy", t) == REVIEW
    assert classify_pair(0.85, "fuzzy", t, {"fuzzy": (0.80, None)}) == MATCH
    # override on one stage leaves others on the global thresholds
    assert classify_pair(0.85, "probabilistic", t, {"fuzzy": (0.80, None)}) == REVIEW


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
    # names agree, so the corroboration guard passes it through at full confidence
    assert pairs.iloc[0]["rule"] == "domain+name_ok"
    assert pairs.iloc[0]["score"] == deterministic.DETERMINISTIC_SCORE


def test_deterministic_corroboration_demotes_conflicting_names():
    # Same domain, totally unrelated names (e.g. portfolio companies) -> demoted to
    # the review band instead of a confident 0.99 match.
    mapping = {CanonicalField.NAME: "name", CanonicalField.WEBSITE: "site"}
    a = _frame([{"name": "Blue Harbor Dental", "site": "holdco.com"}], mapping)
    b = _frame([{"name": "Quantum Freight Logistics", "site": "holdco.com"}], mapping)
    pairs = deterministic.match_deterministic(a, b)
    assert len(pairs) == 1
    assert pairs.iloc[0]["rule"] == "domain_name_conflict"
    assert pairs.iloc[0]["score"] == 0.80
    # guard off -> back to a confident match
    pairs_off = deterministic.match_deterministic(a, b, corroboration=False)
    assert pairs_off.iloc[0]["score"] == deterministic.DETERMINISTIC_SCORE


def test_deterministic_phone_key():
    mapping = {CanonicalField.NAME: "name", CanonicalField.PHONE: "tel"}
    a = normalize.normalize_frame(
        pd.DataFrame([{"name": "Acme Inc", "tel": "(415) 867-5309"}]), mapping
    )
    b = normalize.normalize_frame(
        pd.DataFrame([{"name": "Acme", "tel": "+1 415-867-5309"}]), mapping
    )
    pairs = deterministic.match_deterministic(a, b)
    assert len(pairs) == 1
    assert pairs.iloc[0]["rule"].startswith("phone")


def test_registration_id_type_guard():
    mapping = {CanonicalField.NAME: "name", CanonicalField.REGISTRATION_ID: "id"}
    a = _frame([{"name": "Acme", "id": "123456789"}], mapping)
    b = _frame([{"name": "Zeta", "id": "123456789"}], mapping)
    # incompatible declared types -> the numeric collision does NOT join
    pairs = deterministic.match_deterministic(a, b, id_type_a="duns", id_type_b="ein")
    assert pairs[pairs["rule"] == "registration_id"].empty
    # unknown on one side -> joins as before
    pairs = deterministic.match_deterministic(a, b, id_type_a="duns", id_type_b="unknown")
    assert len(pairs[pairs["rule"] == "registration_id"]) == 1


# ---- fuzzy -----------------------------------------------------------------

def test_fuzzy_matches_similar_names():
    mapping = {CanonicalField.NAME: "name"}
    a = _frame([{"name": "Acme Global Holdings"}], mapping)
    b = _frame([{"name": "Acme Global Holding"}, {"name": "Zeta Corp"}], mapping)
    pairs = fuzzy.match_fuzzy(a, b, threshold=0.8)
    assert len(pairs) == 1
    assert pairs.iloc[0]["idx_b"] == 0
    assert pairs.iloc[0]["score"] >= 0.8


def test_candidate_floor_follows_review_threshold():
    """Lowering the Review slider must lower the stages' candidate-generation
    floors, so review-band pairs below the stage defaults actually surface."""
    cfg = MatchConfig(thresholds=Thresholds(match=0.9, review=0.6))
    assert cfg.candidate_floor("fuzzy", 0.80) == 0.6
    assert cfg.candidate_floor("probabilistic", 0.75) == 0.6

    # Review above the stage default: the stage default stays (floor never rises).
    cfg_hi = MatchConfig(thresholds=Thresholds(match=0.95, review=0.9))
    assert cfg_hi.candidate_floor("fuzzy", 0.80) == 0.80

    # A per-stage review override beats the global for that stage only.
    cfg_stage = MatchConfig(
        thresholds=Thresholds(match=0.9, review=0.75),
        stage_thresholds={"fuzzy": (None, 0.55)},
    )
    assert cfg_stage.candidate_floor("fuzzy", 0.80) == 0.55
    assert cfg_stage.candidate_floor("probabilistic", 0.75) == 0.75


def test_pipeline_passes_lowered_floor_to_fuzzy(monkeypatch):
    """End-to-end wiring: run_pipeline hands the effective (lowered) floor to
    the fuzzy stage instead of the raw config default."""
    from app.matching import pipeline as pipeline_mod

    seen: dict[str, float] = {}
    orig = fuzzy.match_fuzzy

    def spy(*args, **kwargs):
        seen["threshold"] = kwargs["threshold"]
        return orig(*args, **kwargs)

    monkeypatch.setattr(pipeline_mod.fuzzy, "match_fuzzy", spy)

    mapping = {CanonicalField.NAME: "name"}
    a = _frame([{"name": "Acme Global Holdings"}], mapping)
    b = _frame([{"name": "Totally Different Ltd"}], mapping)
    cfg = MatchConfig(
        use_deterministic=False,
        use_probabilistic=False,
        thresholds=Thresholds(match=0.9, review=0.6),
    )
    run_pipeline(a, b, mapping, mapping, cfg)
    assert seen["threshold"] == 0.6


def test_score_histogram_bins_by_stage(monkeypatch):
    import app.storage as storage
    from app import service

    allpairs = pd.DataFrame(
        {
            "idx_a": [0, 1, 2, 3],
            "idx_b": [0, 1, 2, 3],
            "score": [0.99, 0.86, 0.72, 0.40],  # 0.40 clamps into the first bin
            "stage": ["deterministic", "fuzzy", "fuzzy", "probabilistic"],
            "rule": ["domain", "name_token_set", "name_token_set", "splink"],
            "adjustments": ["", "", "", ""],
        }
    )
    meta = {"request": {"config": {"thresholds": {"match": 0.9, "review": 0.75}}}}
    monkeypatch.setattr(storage, "load_allpairs", lambda sid: allpairs)
    monkeypatch.setattr(storage, "load_session_meta", lambda sid: meta)

    out = service.score_histogram("s")
    assert out["total"] == 4
    assert out["stages"] == ["deterministic", "probabilistic", "fuzzy"]  # cascade order
    assert out["thresholds"] == {"match": 0.9, "review": 0.75}
    assert sum(sum(b["counts"].values()) for b in out["bins"]) == 4  # nothing lost
    # 0.99 lands in the last bin (inclusive upper edge region)
    assert out["bins"][-1]["counts"]["deterministic"] == 1
    # the clamped 0.40 probabilistic score sits in the first bin
    assert out["bins"][0]["counts"]["probabilistic"] == 1


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
    classes = dict(zip(out["idx_a"], out["match_class"], strict=False))
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
    m = dict(zip(out["row_idx"], out["master_id"], strict=False))
    assert m[0] == m[1] == m[2]
    assert m[3] != m[0]
    sizes = dict(zip(out["row_idx"], out["cluster_size"], strict=False))
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
    m = dict(zip(result.clusters["row_idx"], result.clusters["master_id"], strict=False))
    assert m[0] == m[1]           # Acme duplicates share a master id
    assert m[2] == m[3]           # Globex duplicates share a master id
    assert len({m[0], m[2], m[4]}) == 3   # three distinct entities

    c = result.counts
    assert c["records"] == 5
    assert c["clusters"] == 3
    assert c["duplicate_rows"] == 4
    assert c["duplicate_clusters"] == 2
    assert c["unique_rows"] == 1
    assert c["possible_duplicates"] == 0  # all confident; nothing to review


def test_dedupe_review_band_becomes_candidate(monkeypatch):
    import app.storage as storage
    from app import dedupe_review
    from app.matching.dedupe import run_dedupe

    mapping = {CanonicalField.NAME: "name"}
    df = pd.DataFrame([
        {"name": "Pinnacle Freight Services"},   # 0 \ borderline (~0.82) -> candidate
        {"name": "Pinnacle Freight Systems"},    # 1 /  NOT auto-merged
        {"name": "Globex Corporation"},          # 2   singleton
    ])
    res = run_dedupe(df, mapping, MatchConfig(use_probabilistic=False))
    assert res.counts["clusters"] == 3            # borderline pair NOT auto-merged
    assert res.counts["possible_duplicates"] == 1
    assert len(res.candidates) == 1

    store = {"s": res.clusters}
    cands = {"s": res.candidates}
    decs: dict = {}
    monkeypatch.setattr(storage, "load_result", lambda sid: store[sid])
    monkeypatch.setattr(storage, "load_candidates", lambda sid: cands[sid])
    monkeypatch.setattr(storage, "load_decisions", lambda sid: decs.get(sid, {}))
    monkeypatch.setattr(storage, "save_decisions", lambda sid, d: decs.__setitem__(sid, d))

    # Confirming the candidate merges the two rows.
    dedupe_review.set_candidate_decision("s", 0, 1, "confirmed")
    assert dedupe_review.counts("s")["clusters"] == 2
    eff = dedupe_review.effective_clusters("s")
    m = dict(zip(eff["row_idx"], eff["master_id"], strict=False))
    assert m[0] == m[1] and m[0] != m[2]

    # Dismissing keeps them apart.
    dedupe_review.set_candidate_decision("s", 0, 1, "dismissed")
    assert dedupe_review.counts("s")["clusters"] == 3
    assert dedupe_review.counts("s")["dismissed"] == 1


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
