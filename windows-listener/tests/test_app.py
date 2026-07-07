from fastapi.testclient import TestClient

from app import create_app, AppDeps
from auth import AuthState
from state import JobStore
from tasks import Task, TaskRegistry

AUTH = {"Authorization": "Bearer secret"}


def _registry():
    return TaskRegistry([
        Task(id="daily", display_name="日常一条龙",
             groups=["日常一条龙", "关闭游戏"], timeout_min=90, after_done="sleep"),
    ])


def _deps(launch=None, jobs=None):
    return AppDeps(
        hostname="DESKTOP-TEST",
        version="1.0.0",
        tasks=_registry(),
        auth=AuthState(api_key="secret", trusted_ips=[]),
        jobs=jobs or JobStore(),
        launch=launch or (lambda job, task: None),
    )


def test_health_no_auth_returns_signature():
    client = TestClient(create_app(_deps()))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "bgi-trigger"
    assert body["hostname"] == "DESKTOP-TEST"
    assert body["version"] == "1.0.0"


def test_tasks_without_auth_rejected():
    client = TestClient(create_app(_deps()))
    r = client.get("/tasks")
    assert r.status_code == 401


def test_tasks_with_correct_key_returns_list():
    client = TestClient(create_app(_deps()))
    r = client.get("/tasks", headers=AUTH)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["id"] == "daily"
    assert items[0]["display_name"] == "日常一条龙"
    assert items[0]["groups"] == ["日常一条龙", "关闭游戏"]


def test_wrong_key_rejected():
    client = TestClient(create_app(_deps()))
    r = client.get("/tasks", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_trigger_starts_job_and_returns_id():
    client = TestClient(create_app(_deps()))
    r = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r.status_code == 202
    assert "job_id" in r.json()


def test_trigger_unknown_task_404():
    client = TestClient(create_app(_deps()))
    r = client.post("/trigger", json={"task_id": "nope"}, headers=AUTH)
    assert r.status_code == 404


def test_trigger_while_busy_returns_409():
    launched = []
    def fake_launch(job, task):
        # leave the job RUNNING (don't finalize)
        launched.append(job.id)

    client = TestClient(create_app(_deps(launch=fake_launch)))
    r1 = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r1.status_code == 202
    r2 = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    assert r2.status_code == 409


def test_status_returns_job_state():
    def fake_launch(job, task):
        job.state  # already RUNNING from jobs.start

    deps = _deps(launch=fake_launch)
    client = TestClient(create_app(deps))
    r = client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)
    job_id = r.json()["job_id"]

    s = client.get("/status", params={"job_id": job_id}, headers=AUTH)
    assert s.status_code == 200
    assert s.json()["state"] == "running"
    assert s.json()["task_id"] == "daily"


def test_status_unknown_job_404():
    client = TestClient(create_app(_deps()))
    s = client.get("/status", params={"job_id": "bogus"}, headers=AUTH)
    assert s.status_code == 404


def test_launch_is_called_with_job_and_task():
    captured = {}
    def fake_launch(job, task):
        captured["job_id"] = job.id
        captured["task_id"] = task.id
        captured["groups"] = task.groups

    client = TestClient(create_app(_deps(launch=fake_launch)))
    client.post("/trigger", json={"task_id": "daily"}, headers=AUTH)

    assert captured["task_id"] == "daily"
    assert captured["groups"] == ["日常一条龙", "关闭游戏"]
    assert captured["job_id"]
