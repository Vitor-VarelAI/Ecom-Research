"""Pack classification + canonical fingerprint for the SQLite projection.

Covers the fail-closed contract: current classified packs are recognized,
valid legacy packs are skipped read-only with a clear reason, malformed
JSON and packs that claim the current matching schema but violate the
required current-pack shape raise actionable errors instead of being
mislabeled legacy.
"""

from __future__ import annotations

import json

import pytest

from pipeline.matching.product_ads import MATCHING_SCHEMA_VERSION
from pipeline.projection.validation import (
    CurrentPackShapeError,
    MalformedPackError,
    classify_pack,
    load_pack_file,
    pack_fingerprint,
)
from tests.fixtures.projection_helpers import (
    current_pack,
    legacy_pack,
    not_a_pack,
)


def _write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


class TestCurrentClassification:
    def test_complete_current_pack_is_recognized(self, tmp_path):
        path = _write(tmp_path, "current.json", current_pack())
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "current"
        assert classification.run_id == "a2ab7c70195aa762987a3e2e07ce9b49dcd15c09a27204f494a5a9158e5aa739"
        assert classification.product_id == "e942e8bf7f30"
        assert classification.fingerprint

    def test_empty_candidates_list_is_still_current(self, tmp_path):
        """Zero candidates is a valid classified success (zero-ads pack)."""
        pack = current_pack(facebook_ads=current_pack()["facebook_ads"] | {"candidates": [], "candidate_count": 0,
                                                                           "matched_count": 0, "total_ads": 0})
        path = _write(tmp_path, "zero.json", pack)
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "current"

    def test_all_score_labels_accepted(self, tmp_path):
        for label in ("AVANÇAR", "OBSERVAR", "DESCARTAR", "DADOS INSUFICIENTES"):
            pack = current_pack(score={"score": 10, "label": label, "breakdown": {}})
            path = _write(tmp_path, f"{label}.json", pack)
            assert classify_pack(load_pack_file(path), path).kind == "current"


class TestLegacyAndNonPackSkips:
    def test_legacy_pack_skipped_read_only_with_reason(self, tmp_path):
        path = _write(tmp_path, "legacy.json", legacy_pack())
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "legacy"
        assert "matching_schema_version" in classification.reason

    def test_older_matching_schema_version_is_legacy(self, tmp_path):
        pack = current_pack()
        pack["facebook_ads"] = dict(pack["facebook_ads"], matching_schema_version=0)
        path = _write(tmp_path, "old.json", pack)
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "legacy"
        assert "0" in classification.reason

    @pytest.mark.parametrize("version", [True, 1.0])
    def test_bool_and_float_schema_versions_do_not_count(self, tmp_path, version):
        """Only an actual int equal to MATCHING_SCHEMA_VERSION claims the
        current schema — ``true`` and ``1.0`` are not the current version
        and the pack is skipped read-only as legacy, never imported."""
        pack = current_pack()
        pack["facebook_ads"] = dict(pack["facebook_ads"], matching_schema_version=version)
        path = _write(tmp_path, f"v{type(version).__name__}.json", pack)
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "legacy"
        assert "matching_schema_version" in classification.reason

    def test_not_a_pack_json_is_skipped(self, tmp_path):
        path = _write(tmp_path, "quota.json", not_a_pack())
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "not_a_pack"


class TestFailClosed:
    def test_malformed_json_raises(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(MalformedPackError):
            load_pack_file(str(path))

    def test_non_dict_json_raises(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(MalformedPackError):
            load_pack_file(str(path))

    @pytest.mark.parametrize("mutator", [
        lambda p: p.pop("run_id"),
        lambda p: p.update(run_id="   "),
        lambda p: p.pop("product_id"),
        lambda p: p.update(product_id=""),
        lambda p: p.update(artifact_complete=False),
        lambda p: p.update(date="2026-13-99"),
        lambda p: p.update(date="16-08-2026"),
        lambda p: p.update(score={"score": 50, "label": "INVALIDO", "breakdown": {}}),
        lambda p: p["facebook_ads"].pop("candidates"),
        lambda p: p["facebook_ads"].update(candidates="nope"),
        lambda p: p["facebook_ads"].update(candidates=[p["facebook_ads"]["candidates"][0].copy() | {"match": None}]),
        lambda p: p["facebook_ads"].update(candidates=[{"ad_archive_id": "x"}]),  # unclassified candidate
    ])
    def test_claims_current_schema_but_invalid_shape_fails_closed(self, tmp_path, mutator):
        pack = current_pack()
        mutator(pack)
        path = _write(tmp_path, "bad.json", pack)
        with pytest.raises(CurrentPackShapeError):
            classify_pack(load_pack_file(path), path)

    def test_top_level_marker_only_does_not_claim_current(self, tmp_path):
        """The authoritative claim is facebook_ads.matching_schema_version —
        the additive top-level marker alone (legacy packs can carry it) is
        not enough to claim current, and a facebook_ads payload without the
        marker is a read-only legacy skip, never an import."""
        pack = legacy_pack()
        pack["matching_schema_version"] = MATCHING_SCHEMA_VERSION
        path = _write(tmp_path, "toponly.json", pack)
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "legacy"

        pack = current_pack()
        pack["facebook_ads"] = {"source": "x", "total_ads": 0}
        path = _write(tmp_path, "mixed.json", pack)
        classification = classify_pack(load_pack_file(path), path)
        assert classification.kind == "legacy"
        assert "matching_schema_version" in classification.reason


class TestStrictTypes:
    @pytest.mark.parametrize("date_value", [
        "2026-1-5",        # not zero-padded
        "2026-08-16T00:00:00",  # not a plain date
        "16-08-2026",      # reordered
        "2026-13-99",      # not a calendar date
        "2026-08-16 ",     # trailing whitespace
        "0800-01-01",      # not round-trip stable (year < 1000)
    ])
    def test_non_canonical_dates_rejected(self, date_value):
        pack = current_pack(date=date_value)
        with pytest.raises(CurrentPackShapeError) as exc:
            classify_pack(pack, "inline.json")
        assert "date" in str(exc.value)

    @pytest.mark.parametrize("score_value", [True, "50", float("nan"), float("inf"), -float("inf")])
    def test_invalid_score_values_rejected(self, score_value):
        """score.score must be a finite non-boolean number when present —
        never persisted as NULL and never passed to sqlite."""
        pack = current_pack(score={"score": score_value, "label": "OBSERVAR", "breakdown": {}})
        with pytest.raises(CurrentPackShapeError) as exc:
            classify_pack(pack, "inline.json")
        assert "score" in str(exc.value)

    def test_non_finite_nested_value_rejected_by_canonical_serialization(self):
        """A NaN in a non-projected field passes the projected-field checks
        but fails the strict canonical fingerprint (allow_nan=False)."""
        pack = current_pack()
        pack["trends"] = {"slope_pct": float("nan")}
        with pytest.raises(CurrentPackShapeError) as exc:
            classify_pack(pack, "inline.json")
        assert "não finitos" in str(exc.value) or "NaN" in str(exc.value)

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_json_literals_are_malformed(self, tmp_path, literal):
        """NaN/Infinity are not standard JSON: rejected at parse time."""
        path = tmp_path / "nonfinite.json"
        path.write_text(f'{{"x": {literal}}}', encoding="utf-8")
        with pytest.raises(MalformedPackError) as exc:
            load_pack_file(str(path))
        assert "JSON inválido" in str(exc.value)


class TestCandidateShape:
    def test_valid_candidate_with_optional_fields_is_current(self):
        """The canonical matcher candidate (optional platforms/countries/
        evidence lists and verdict audit lists) stays importable."""
        pack = current_pack()
        assert classify_pack(pack, "inline.json").kind == "current"

    def test_rejected_candidate_with_null_head_token_is_current(self):
        """The matcher's rejected verdict may carry head_token None — that
        is the produced shape, not a violation."""
        pack = current_pack()
        candidate = pack["facebook_ads"]["candidates"][0]
        candidate["match"] = {
            "matcher_version": "1.0.0",
            "head_token": None,
            "geo_tokens": [],
            "missing_tokens": [],
            "weak_only_tokens": [],
            "label": "rejected",
            "confidence": "low",
            "reasons": ["head_unproven"],
        }
        assert classify_pack(pack, "inline.json").kind == "current"

    @pytest.mark.parametrize("mutation", [
        {"match": {"label": "matched"}},  # only a label, no verdict shape
        {"match": None},
        {"match": {"label": "matched", "confidence": "high", "reasons": ["head_proven"],
                    "matcher_version": "1.0.0", "head_token": "mochila", "geo_tokens": [],
                    "missing_tokens": [], "weak_only_tokens": "nope"}},
        {"match": {"label": "matched", "confidence": "high", "reasons": ["head_proven"],
                    "matcher_version": "1.0.0", "head_token": "mochila", "geo_tokens": [1],
                    "missing_tokens": [], "weak_only_tokens": []}},
        {"match_evidence": None},
        {"match_evidence": ["commercial"]},
        {"match_evidence": {"commercial": ["x"], "geo": "g", "weak": "w"}},
        {"landing_url": {"url": "https://shop.example"}},
        {"landing_url": 42},
        {"link": ["https://x"]},
        {"advertiser": 7},
        {"copy": None},
        {"ad_archive_id": 1},
        {"page_id": {"id": "p1"}},
        {"days_active": True},
        {"days_active": "30"},
        {"days_active": float("nan")},
        {"reported_active_time": 30},
        {"publisher_platforms": "facebook"},
        {"publisher_platforms": ["facebook", 1]},
        {"countries": "PT"},
        {"countries": ["PT", None]},
    ])
    def test_unsupported_projected_candidate_types_fail_closed(self, mutation):
        pack = current_pack()
        candidate = pack["facebook_ads"]["candidates"][0]
        candidate.update(mutation)
        with pytest.raises(CurrentPackShapeError) as exc:
            classify_pack(pack, "inline.json")
        assert "candidato 0" in str(exc.value)

    @pytest.mark.parametrize("mutation", [
        {"keyword": 123},
        {"mode": ["sunday"]},
        {"scoring_version": 1.0},
        {"generated_at": 123456},
        {"source_states": "ok"},
        {"scoring_config": []},
    ])
    def test_unsupported_pack_projected_types_fail_closed(self, mutation):
        pack = current_pack()
        pack.update(mutation)
        with pytest.raises(CurrentPackShapeError):
            classify_pack(pack, "inline.json")

    @pytest.mark.parametrize("key", ["candidate_count", "matched_count", "needs_review_count",
                                     "rejected_count", "total_ads"])
    def test_non_integer_counts_rejected(self, key):
        pack = current_pack()
        pack["facebook_ads"][key] = "1"
        with pytest.raises(CurrentPackShapeError) as exc:
            classify_pack(pack, "inline.json")
        assert key in str(exc.value)

    @pytest.mark.parametrize("target", ["count", "score", "days_active"])
    @pytest.mark.parametrize("value", [1 << 63, -(1 << 63) - 1])
    def test_integer_fields_stay_within_sqlite_signed_range(self, target, value):
        pack = current_pack()
        if target == "count":
            pack["facebook_ads"]["total_ads"] = value
        elif target == "score":
            pack["score"]["score"] = value
        else:
            pack["facebook_ads"]["candidates"][0]["days_active"] = value
        with pytest.raises(CurrentPackShapeError) as exc:
            classify_pack(pack, "inline.json")
        assert "signed 64-bit" in str(exc.value)

    def test_boolean_count_rejected(self):
        pack = current_pack()
        pack["facebook_ads"]["total_ads"] = True
        with pytest.raises(CurrentPackShapeError):
            classify_pack(pack, "inline.json")

    def test_legacy_reason_does_not_echo_schema_version(self):
        secret = "PACK_SECRET_VERSION"
        pack = current_pack()
        pack["facebook_ads"] = dict(pack["facebook_ads"], matching_schema_version=secret)
        classification = classify_pack(pack, "inline.json")
        assert classification.kind == "legacy"
        assert secret not in (classification.reason or "")


class TestFingerprint:
    def test_stable_canonical_fingerprint(self):
        pack = current_pack()
        first = pack_fingerprint(pack)
        # Key insertion order must not change the fingerprint.
        reordered = {key: pack[key] for key in reversed(list(pack.keys()))}
        assert pack_fingerprint(reordered) == first
        assert len(first) == 64  # sha256 hex

    def test_content_change_changes_fingerprint(self):
        pack = current_pack()
        changed = current_pack(score={"score": 90, "label": "AVANÇAR", "breakdown": {}})
        assert pack_fingerprint(pack) != pack_fingerprint(changed)
