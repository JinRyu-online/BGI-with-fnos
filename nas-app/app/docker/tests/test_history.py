import json

from history import HistoryStore


def test_empty_when_no_file(tmp_path):
    h = HistoryStore(tmp_path / "jobs.json")
    assert h.all() == []


def test_record_and_retrieve(tmp_path):
    h = HistoryStore(tmp_path / "jobs.json")
    h.record({"job_id": "a", "task_id": "daily", "state": "running"})

    items = h.all()
    assert len(items) == 1
    assert items[0]["job_id"] == "a"


def test_update_existing_job_by_id(tmp_path):
    h = HistoryStore(tmp_path / "jobs.json")
    h.record({"job_id": "a", "task_id": "daily", "state": "running"})
    h.record({"job_id": "a", "task_id": "daily", "state": "done"})  # 同 id 更新

    items = h.all()
    assert len(items) == 1
    assert items[0]["state"] == "done"


def test_persists_across_instances(tmp_path):
    p = tmp_path / "jobs.json"
    HistoryStore(p).record({"job_id": "a", "state": "done"})

    reloaded = HistoryStore(p).all()
    assert reloaded[0]["job_id"] == "a"


def test_caps_history(tmp_path):
    h = HistoryStore(tmp_path / "jobs.json", keep=3)
    for i in range(5):
        h.record({"job_id": f"j{i}", "state": "done"})

    items = h.all()
    assert len(items) == 3
    assert items[0]["job_id"] == "j4"  # 最新在前
    assert items[-1]["job_id"] == "j2"  # 截断后最旧的一条


def test_newest_first(tmp_path):
    h = HistoryStore(tmp_path / "jobs.json")
    h.record({"job_id": "a", "state": "done"})
    h.record({"job_id": "b", "state": "done"})

    items = h.all()
    assert items[0]["job_id"] == "b"  # 最新在前
