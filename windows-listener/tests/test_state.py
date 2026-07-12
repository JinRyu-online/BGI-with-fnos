import time

import pytest

from state import JobState, JobStore, Job, JobBusy


def test_new_store_is_idle():
    store = JobStore()

    assert store.current is None
    assert store.is_idle() is True


def test_start_transitions_idle_to_running():
    store = JobStore()

    job = store.start("daily", ["日常一条龙", "关闭游戏"])

    assert store.current.id == job.id
    assert job.state == JobState.RUNNING
    assert store.is_idle() is False
    assert store.current.task_id == "daily"


def test_start_while_busy_raises():
    store = JobStore()
    store.start("daily", ["g"])

    with pytest.raises(JobBusy):
        store.start("abyss", ["g2"])


def test_mark_completing_from_running():
    store = JobStore()
    store.start("daily", ["g"])

    store.mark_completing(reason="game_exited")

    assert store.current.state == JobState.COMPLETING
    assert store.current.completion_reason == "game_exited"


def test_finalize_completing_to_done():
    store = JobStore()
    store.start("daily", ["g"])
    store.mark_completing(reason="game_exited")

    store.finalize(JobState.DONE)

    assert store.current.state == JobState.DONE
    assert store.is_idle() is True  # done is terminal -> slot freed


def test_finalize_records_history_and_frees_slot():
    store = JobStore(keep_history=3)
    for i in range(5):
        store.start(f"t{i}", ["g"])
        store.mark_completing("game_exited")
        store.finalize(JobState.DONE)

    history = store.history()
    assert len(history) == 3  # capped at keep_history
    assert all(j.state == JobState.DONE for j in history)
    assert store.is_idle() is True


def test_get_returns_job_by_id():
    store = JobStore()
    job = store.start("daily", ["g"])

    assert store.get(job.id).id == job.id


def test_abort_from_completing_to_aborted():
    store = JobStore()
    store.start("daily", ["g"])
    store.mark_completing("game_exited")

    store.abort()

    assert store.current.state == JobState.ABORTED
    assert store.is_idle() is True


def test_timeout_transitions_running_to_timeout():
    store = JobStore()
    store.start("daily", ["g"])

    store.mark_completing("timeout")  # timeout also routes through completing
    store.finalize(JobState.TIMEOUT)

    assert store.current.state == JobState.TIMEOUT


def test_clear_resets_to_idle_without_history_loss():
    store = JobStore()
    store.start("daily", ["g"])
    store.mark_completing("game_exited")
    store.finalize(JobState.DONE)

    store.clear_current()

    assert store.current is None
    assert store.is_idle() is True
    assert len(store.history()) == 1


def test_job_timestamps_and_elapsed():
    """Job 应在创建时带 created_at,终态时带 finished_at,elapsed 自动计算。"""
    store = JobStore()
    t0 = time.time()
    job = store.start("daily", ["g"])
    assert job.created_at >= t0
    assert job.finished_at is None

    d = job.to_dict()
    assert "created_at" in d and "finished_at" in d and "elapsed" in d
    assert d["finished_at"] is None
    assert d["elapsed"] >= 0

    time.sleep(0.01)
    store.mark_completing("game_exited")
    store.finalize(JobState.DONE)

    assert store.current.finished_at is not None
    assert store.current.finished_at >= store.current.created_at

    d2 = store.current.to_dict()
    assert d2["elapsed"] >= 0.01
    assert d2["finished_at"] >= d2["created_at"]


def test_abort_sets_finished_at():
    store = JobStore()
    store.start("daily", ["g"])
    store.abort()
    assert store.current.finished_at is not None
    assert store.history()[0].state == JobState.ABORTED
