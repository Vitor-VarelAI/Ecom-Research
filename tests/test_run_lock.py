"""RunLock: fail-closed behaviour and cross-process mutual exclusion.

A second same-run process must block on the blocking exclusive flock until
the first finishes — never proceed unlocked after a timeout, and never run
the same action concurrently (which would duplicate paid work).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time

import pytest

from pipeline import run_state


def _mp_ctx():
    if "fork" in mp.get_all_start_methods():
        return mp.get_context("fork")
    return mp.get_context()


def _lock_worker(lock_path, log_path, entered, released):
    from pipeline import run_state as rs

    lock = rs.RunLock(lock_path)
    with lock:
        entered.value = time.monotonic()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("work\n")
        time.sleep(0.3)  # hold the lock to widen the overlap window
    released.value = time.monotonic()


def _same_run_worker(data_dir, run_id, log_path, executed_count):
    """Simulate one same-run execution: manifest idempotency under the lock."""
    from pipeline import run_state as rs

    store = rs.RunStore(data_dir)
    with store.lock(run_id):
        manifest = store.load(run_id)
        if manifest and manifest.get("state") == "completed":
            return  # second execution: reuse, do no work
        store.save(run_id, {
            "run_id": run_id, "state": "completed", "mode": "sunday",
            "logical_date": "2026-08-02", "keywords": ["a"],
            "results": [{"keyword": "a"}],
            "digest": {"text": "d", "hash": "h", "delivery": {"status": "success"}},
            "artifacts": {"md": [], "json": []},
        })
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("executed\n")
        executed_count.value += 1


# ── Fail-closed ────────────────────────────────────────────────────────────


def test_lock_fails_closed_without_fcntl(monkeypatch, tmp_path):
    monkeypatch.setattr(run_state, "fcntl", None)
    with pytest.raises(RuntimeError, match="fcntl"):
        with run_state.RunLock(str(tmp_path / "runs" / "x.lock")):
            pass  # pragma: no cover


def test_lock_fails_closed_when_flock_fails(monkeypatch, tmp_path):
    class BrokenFcntl:
        LOCK_EX = 2
        LOCK_UN = 8
        LOCK_NB = 4

        def flock(self, *args, **kwargs):
            raise OSError("flock indisponível")

    monkeypatch.setattr(run_state, "fcntl", BrokenFcntl())
    with pytest.raises(RuntimeError, match="adquirir o lock"):
        with run_state.RunLock(str(tmp_path / "x.lock")):
            pass  # pragma: no cover


def test_lock_fails_closed_when_lock_file_cannot_be_created(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="abrir o lock"):
        with run_state.RunLock(str(blocker / "sub" / "x.lock")):
            pass  # pragma: no cover


def test_lock_reacquirable_after_release(tmp_path):
    lock = run_state.RunLock(str(tmp_path / "runs" / "y.lock"))
    with lock:
        pass
    with lock:  # a fresh acquire succeeds immediately after release
        pass


# ── Cross-process mutual exclusion ─────────────────────────────────────────


def test_second_same_run_process_blocks_until_first_releases(tmp_path):
    ctx = _mp_ctx()
    lock_path = str(tmp_path / "runs" / "same-run.lock")
    log_path = str(tmp_path / "runs" / "same-run.log")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    entered = [ctx.Value("d", 0.0), ctx.Value("d", 0.0)]
    released = [ctx.Value("d", 0.0), ctx.Value("d", 0.0)]
    procs = [
        ctx.Process(target=_lock_worker, args=(lock_path, log_path, entered[i], released[i]))
        for i in range(2)
    ]
    procs[0].start()
    time.sleep(0.15)  # process A acquires the lock first
    procs[1].start()
    for proc in procs:
        proc.join(30)
    assert all(proc.exitcode == 0 for proc in procs)
    # Strict serialization: B could not enter until A released the lock.
    assert entered[1].value >= released[0].value - 0.05
    # Each process did its critical-section work exactly once.
    with open(log_path, encoding="utf-8") as f:
        assert f.read().splitlines() == ["work", "work"]


def test_second_same_run_cannot_duplicate_work(tmp_path):
    ctx = _mp_ctx()
    data_dir = str(tmp_path / "data")
    run_id = "same-run-dup-work"
    log_path = str(tmp_path / "same-run.log")
    executed = ctx.Value("i", 0)
    procs = [
        ctx.Process(target=_same_run_worker, args=(data_dir, run_id, log_path, executed))
        for _ in range(2)
    ]
    procs[0].start()
    time.sleep(0.15)  # A wins the race and completes the run
    procs[1].start()
    for proc in procs:
        proc.join(30)
    assert all(proc.exitcode == 0 for proc in procs)
    # Exactly one execution: the second process saw the completed manifest
    # and reused it instead of running the research again.
    assert executed.value == 1
    with open(log_path, encoding="utf-8") as f:
        assert f.read().splitlines() == ["executed"]
