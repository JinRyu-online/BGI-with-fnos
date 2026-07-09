import shutil
import socket

from fastapi.testclient import TestClient

from app import AppDeps, create_app
from auth import AuthState
from config import ListenerConfig
from state import JobStore
from tasks import TaskRegistry

HERE = __import__("pathlib").Path(__file__).resolve().parent.parent


def _setup(tmp_path):
    cfg = tmp_path / "config.toml"
    tasks = tmp_path / "tasks.json"
    shutil.copy(HERE / "config.toml.example", cfg)
    shutil.copy(HERE / "tasks" / "tasks.json.example", tasks)
    config = ListenerConfig.load(cfg)
    return config, TaskRegistry.load(tasks)


def test_full_wiring_health_and_tasks(tmp_path):
    config, tasks = _setup(tmp_path)
    auth = AuthState(api_key=config.auth.api_key, trusted_ips=[])
    deps = AppDeps(
        hostname=socket.gethostname(),
        version="1.0.0",
        tasks=tasks,
        auth=auth,
        jobs=JobStore(),
        launch=lambda job, task: None,
    )
    client = TestClient(create_app(deps))

    # /health needs no auth
    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["service"] == "bgi-trigger"

    # /tasks with the generated key returns the example tasks
    hdr = {"Authorization": f"Bearer {config.auth.api_key}"}
    t = client.get("/tasks", headers=hdr)
    assert t.status_code == 200
    ids = {x["id"] for x in t.json()}
    assert ids == {"daily", "abyss"}


def test_example_tasks_file_is_valid(tmp_path):
    # The shipped tasks.json.example must load without error.
    _, tasks = _setup(tmp_path)
    assert {t.id for t in tasks.all()} == {"daily", "abyss"}
