"""Run identity, atomic writes and RunStore manifest persistence."""

from __future__ import annotations

import json
import os

from pipeline import run_state


def test_run_id_is_deterministic_and_excludes_digest_and_clock():
    base = dict(logical_date="2026-08-02", mode="sunday", keywords=["a", "b"], csv_sha256=None, scoring_version="1.0.0")
    first = run_state.compute_run_id(**base)
    second = run_state.compute_run_id(**base)
    assert first == second
    # There is no digest/clock/credential parameter in the identity at all:
    # the same logical action always maps to the same run id.
    assert "digest" not in first and "time" not in first


def test_run_id_changes_with_each_identity_input():
    base = dict(logical_date="2026-08-02", mode="sunday", keywords=["a"], csv_sha256=None, scoring_version="1.0.0")
    ids = [
        run_state.compute_run_id(**base),
        run_state.compute_run_id(**{**base, "mode": "all"}),
        run_state.compute_run_id(**{**base, "keywords": ["b"]}),
        run_state.compute_run_id(**{**base, "keywords": ["a", "b"]}),
        run_state.compute_run_id(**{**base, "logical_date": "2026-08-09"}),
        run_state.compute_run_id(**{**base, "csv_sha256": "abc"}),
        run_state.compute_run_id(**{**base, "scoring_version": "2.0.0"}),
    ]
    assert len(set(ids)) == len(ids)


def test_atomic_write_json_leaves_no_partial_files(tmp_path):
    path = os.path.join(str(tmp_path), "runs", "abc.json")
    run_state.atomic_write_json(path, {"key": "valor", "n": 3})
    assert json.load(open(path, encoding="utf-8")) == {"key": "valor", "n": 3}
    leftovers = [name for name in os.listdir(os.path.dirname(path)) if ".part" in name or name.startswith(".tmp")]
    assert leftovers == []


def test_atomic_write_text(tmp_path):
    path = os.path.join(str(tmp_path), "digest.txt")
    run_state.atomic_write_text(path, "linha um\nlinha dois\n")
    assert open(path, encoding="utf-8").read() == "linha um\nlinha dois\n"


def test_atomic_write_fsyncs_file_and_parent_directory(monkeypatch, tmp_path):
    real_fsync = run_state.os.fsync
    fsynced_modes = []

    def record_fsync(fd):
        fsynced_modes.append(os.fstat(fd).st_mode)
        return real_fsync(fd)

    monkeypatch.setattr(run_state.os, "fsync", record_fsync)
    run_state.atomic_write_text(os.path.join(str(tmp_path), "durable.txt"), "ok")

    assert len(fsynced_modes) == 2
    assert os.path.isfile(os.path.join(str(tmp_path), "durable.txt"))
    # The first fsync is the temporary file; the second is the parent dir.
    import stat
    assert stat.S_ISREG(fsynced_modes[0])
    assert stat.S_ISDIR(fsynced_modes[1])


def test_file_sha256_missing_returns_none(tmp_path):
    assert run_state.file_sha256(os.path.join(str(tmp_path), "absent.csv")) is None


def test_store_round_trip_and_corruption(tmp_path):
    store = run_state.RunStore(str(tmp_path))
    run_id = "deadbeef"
    store.save(run_id, {
        "run_id": run_id, "state": "completed", "mode": "sunday",
        "logical_date": "2026-08-02", "keywords": ["a"],
        "results": [{"keyword": "a"}],
        "digest": {"text": "digest", "hash": "h", "delivery": {"status": "success"}},
        "artifacts": {"md": [], "json": []},
    })
    manifest = store.load(run_id)
    assert manifest["state"] == "completed"
    assert manifest["results"] == [{"keyword": "a"}]
    # Corrupted manifest is treated as absent.
    with open(os.path.join(str(tmp_path), "runs", f"{run_id}.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert store.load(run_id) is None
    assert store.load("missing-id") is None


def _completed_manifest(run_id: str, **overrides) -> dict:
    return {
        "run_id": run_id, "state": "completed", "mode": "sunday",
        "logical_date": "2026-08-02", "keywords": ["a"],
        "results": [{"keyword": "a"}],
        "digest": {"text": "digest", "hash": "h", "delivery": {"status": "success"}},
        "artifacts": {"md": ["packs/a.md"], "json": ["data/a.json"]},
        **overrides,
    }


def test_store_loads_valid_completed_manifest(tmp_path):
    store = run_state.RunStore(str(tmp_path))
    run_id = "good"
    store.save(run_id, _completed_manifest(run_id))
    assert store.load(run_id)["state"] == "completed"


def test_store_rejects_completed_manifest_with_invalid_nested_state(tmp_path):
    store = run_state.RunStore(str(tmp_path))
    invalid = [
        _completed_manifest("r", results=["not-a-dict"]),
        _completed_manifest("r", results=[1]),
        _completed_manifest("r", digest={"text": 5, "delivery": {}}),
        _completed_manifest("r", digest={"text": "x", "delivery": "nope"}),
        _completed_manifest("r", digest={"text": "x"}),
        _completed_manifest("r", artifacts={"md": "x", "json": []}),
        _completed_manifest("r", artifacts={"md": []}),
        _completed_manifest("r", artifacts=[]),
    ]
    for index, case in enumerate(invalid):
        run_id = f"bad-{index}"
        store.save(run_id, {**case, "run_id": run_id})
        assert store.load(run_id) is None, run_id


def test_store_rejects_partial_manifest_without_basic_fields(tmp_path):
    store = run_state.RunStore(str(tmp_path))
    run_id = "partial"
    store.save(run_id, {"run_id": run_id, "state": "running"})
    assert store.load(run_id) is None


def test_store_accepts_partial_running_manifest_with_basic_fields(tmp_path):
    store = run_state.RunStore(str(tmp_path))
    run_id = "checkpoint"
    store.save(run_id, {
        "run_id": run_id, "state": "running", "mode": "all",
        "logical_date": "2026-08-02", "keywords": ["a", "b"],
        "results": [{"keyword": "a", "status": "ok", "md_path": "x.md"}],
    })
    loaded = store.load(run_id)
    assert loaded is not None
    assert loaded["state"] == "running"
    assert len(loaded["results"]) == 1


def test_lock_acquire_and_release(tmp_path):
    store = run_state.RunStore(str(tmp_path))
    run_id = "lockme"
    with store.lock(run_id):
        assert os.path.exists(os.path.join(str(tmp_path), "runs", f"{run_id}.lock"))
    # Released: a fresh lock can be re-acquired immediately.
    with store.lock(run_id):
        pass
