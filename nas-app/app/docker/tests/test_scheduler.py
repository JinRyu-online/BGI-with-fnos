"""定时任务调度测试。

覆盖（对应 docs/定时任务方案.md 验收）：
- 纯函数：parse_hhmm / next_fire_at / is_due（跨日、周日、窗口、防重入、超窗）
- Scheduler.tick：防重入、同 tick 冲突、投递后 last_fired 落盘
- ScheduleStateStore：记录/读取/清理
- API CRUD：GET/PUT/DELETE/run + 校验失败 400
- 执行链：WOL 失败 / health 超时 / 409 跳过 / task 404 / 成功触发 history 溯源
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from main import create_app
from scheduler import (
    ScheduleStateStore,
    is_due,
    next_fire_at,
    parse_hhmm,
    validate_schedule,
)


# ---------- 纯函数 ----------

def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def test_parse_hhmm():
    assert parse_hhmm("12:00") == (12, 0)
    assert parse_hhmm("4:05") == (4, 5)
    assert parse_hhmm("24:00") is None
    assert parse_hhmm("ab:cd") is None
    assert parse_hhmm("") is None
    assert parse_hhmm(None) is None


def test_next_fire_at_basic():
    # 周三 12:00 问下一次周一 12:00 → 下周一
    now = _dt("2026-09-02 15:00:00")  # 周三
    nf = next_fire_at(now, "12:00", [0])  # 周一
    assert nf == _dt("2026-09-07 12:00:00")


def test_next_fire_at_today_future():
    # 今天（周一）10:00 问 12:00 → 今天 12:00
    now = _dt("2026-08-31 10:00:00")  # 周一
    assert next_fire_at(now, "12:00", [0]) == _dt("2026-08-31 12:00:00")


def test_next_fire_at_daily():
    now = _dt("2026-09-02 15:00:00")  # 周三
    nf = next_fire_at(now, "04:00", None)  # 每天
    assert nf == _dt("2026-09-03 04:00:00")


def test_next_fire_at_invalid():
    now = _dt("2026-09-02 15:00:00")
    assert next_fire_at(now, "xx", None) is None
    # weekdays=[] 语义 = 每天（与 is_due/_weekdays_of 一致），返回明天同时刻
    nf = next_fire_at(now, "12:00", [])
    assert nf == _dt("2026-09-03 12:00:00")


def test_is_due_window_and_dedup():
    sched = {"enabled": True, "time": "12:00", "weekdays": [0], "id": "s1"}
    # 周一 12:00:30，从未触发 → 到期
    t = _dt("2026-08-31 12:00:30").timestamp()
    assert is_due(sched, _dt("2026-08-31 12:00:30"), None)
    # 同窗口已触发（last_fired >= 触发点）→ 不再触发
    assert not is_due(sched, _dt("2026-08-31 12:02:00"), t)
    # 错过超窗（12:00 后 6 分钟 > 300s 宽容）→ 跳过不补跑
    assert not is_due(sched, _dt("2026-08-31 12:06:01"), None)
    # disabled → 不触发
    assert not is_due({**sched, "enabled": False}, _dt("2026-08-31 12:00:30"), None)
    # 非触发日（周二）→ 不触发
    assert not is_due(sched, _dt("2026-09-01 12:00:30"), None)


def test_validate_schedule():
    ok = {"time": "12:00", "weekdays": [0, 4], "task_id": "mining"}
    assert validate_schedule(ok) == []
    bad = {"time": "25:00", "weekdays": [7], "task_id": ""}
    errs = validate_schedule(bad)
    assert len(errs) == 3


# ---------- ScheduleStateStore ----------

def test_state_store_roundtrip(tmp_path):
    st = ScheduleStateStore(tmp_path / "state.json")
    assert st.get("s1") is None
    st.record_fired("s1", fired_at=123.0, job_id="j1", result="triggered")
    st.record_fired("s2", fired_at=456.0, job_id=None, result="skipped_busy", error="忙")
    assert st.get("s1") == {"last_fired_at": 123.0, "last_job_id": "j1",
                            "last_result": "triggered", "last_error": None}
    assert st.all()["s2"]["last_result"] == "skipped_busy"
    st.drop("s1")
    assert st.get("s1") is None
    assert "s2" in st.all()


# ---------- Scheduler.tick ----------

def _make_sched(sid: str, **kw) -> dict:
    base = {"id": sid, "enabled": True, "name": sid, "time": "12:00",
            "weekdays": [0], "task_id": "t1", "wake": False}
    base.update(kw)
    return base


def test_tick_dispatch_and_dedup(tmp_path):
    st = ScheduleStateStore(tmp_path / "state.json")
    executed = []
    sched = Scheduler_stub = None
    from scheduler import Scheduler
    s = Scheduler(None, lambda: st, execute_fn=lambda sc: executed.append(sc["id"]))
    monday_noon = _dt("2026-08-31 12:01:00")

    r1 = s.tick(monday_noon, [_make_sched("s1")])
    assert r1["fired"] == ["s1"] and not r1["skipped_conflict"]
    assert executed == ["s1"]
    # 同窗口再 tick → 不重复
    r2 = s.tick(_dt("2026-08-31 12:02:00"), [_make_sched("s1")])
    assert r2["fired"] == []
    assert executed == ["s1"]


def test_tick_conflict_same_time(tmp_path):
    st = ScheduleStateStore(tmp_path / "state.json")
    from scheduler import Scheduler
    s = Scheduler(None, lambda: st, execute_fn=lambda sc: None)
    monday_noon = _dt("2026-08-31 12:01:00")
    r = s.tick(monday_noon, [_make_sched("a"), _make_sched("b")])
    assert r["fired"] == ["a"]
    assert r["skipped_conflict"] == ["b"]
    assert st.get("b")["last_result"] == "skipped_conflict"


def test_tick_disabled_skipped(tmp_path):
    st = ScheduleStateStore(tmp_path / "state.json")
    from scheduler import Scheduler
    s = Scheduler(None, lambda: st, execute_fn=lambda sc: None)
    r = s.tick(_dt("2026-08-31 12:01:00"), [_make_sched("a", enabled=False)])
    assert r["fired"] == []


# ---------- API CRUD ----------

def _app(tmp_path, **kw):
    return create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        state_path=str(tmp_path / "sched_state.json"),
        reconcile_interval=0,
        scheduler_interval=0,
        **kw,
    )


def test_schedules_crud_roundtrip(tmp_path):
    client = TestClient(_app(tmp_path))
    # 空
    assert client.get("/api/schedules").json() == []
    # PUT 两条
    scheds = [
        {"id": "s1", "enabled": True, "name": "挖矿", "time": "12:00",
         "weekdays": [0, 3], "task_id": "mining", "wake": True},
        {"id": "s2", "enabled": False, "name": "日常", "time": "04:10",
         "weekdays": [], "task_id": "daily", "wake": False},
    ]
    r = client.put("/api/schedules", json={"schedules": scheds})
    assert r.status_code == 200, r.text
    got = r.json()
    assert len(got) == 2
    s1 = next(x for x in got if x["id"] == "s1")
    assert s1["next_fire_at"] is not None  # 后端算好下次触发
    assert "last_result" in s1
    # 持久化进 config.json
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert len(raw["schedules"]) == 2
    # 校验失败 400
    bad = client.put("/api/schedules", json={"schedules": [{"time": "99:99", "task_id": "x"}]})
    assert bad.status_code == 400


def test_schedules_put_cleans_deleted_state(tmp_path):
    client = TestClient(_app(tmp_path))
    client.put("/api/schedules", json={"schedules": [_make_sched("s1")]})
    # 伪造一条残留状态
    st = ScheduleStateStore(tmp_path / "sched_state.json")
    st.record_fired("ghost", fired_at=1.0, job_id=None, result="error")
    client.put("/api/schedules", json={"schedules": [_make_sched("s1")]})
    assert st.get("ghost") is None


# ---------- 执行链 ----------

class _FakeClient:
    """可编程 fake：health_ok 控制就绪探测，trigger 行为可控。"""

    def __init__(self, health_ok=True, trigger=None):
        self.health_ok = health_ok
        self._custom_trigger = trigger
        self.health_calls = 0
        self.trigger_calls = []

    def health(self):
        self.health_calls += 1
        if not self.health_ok:
            raise RuntimeError("unreachable")

    def trigger(self, task_id):
        self.trigger_calls.append(task_id)
        if self._custom_trigger is not None:
            return self._custom_trigger(task_id)
        return {"job_id": "j-1", "task_id": task_id, "display_name": task_id}


def _wire_execute(tmp_path, fake, trigger_side_effect=None):
    """构造 app 并直接拿内部 _execute_schedule（经 lifespan 后不可达，改用注入 Scheduler 的 execute_fn）。"""
    captured = {}
    import main as main_mod

    def injector(client_getter, state_getter):
        from scheduler import Scheduler
        captured["client_getter"] = client_getter
        captured["state"] = state_getter()
        # 复用 main 里的默认执行链：从闭包里拿不到，这里直接通过 create_app 的
        # scheduler_injector 拿到 Scheduler；执行链通过调用 app 内部不可达——
        # 改为校验注入通道本身，执行链逻辑用下面 _execute_via_api 走 HTTP 触发
        return Scheduler(client_getter, state_getter, execute_fn=lambda sc: None)

    app = _app(tmp_path, scheduler_injector=injector)
    return app, captured


def test_execute_success_records_history_and_state(tmp_path):
    """端到端：手动 run → 执行链成功 → history 带 schedule_id、state=triggered。"""
    fake = _FakeClient(health_ok=True)

    def factory(url, key):
        return fake

    app = _app(tmp_path, client_factory=factory)
    # 预置配对 + schedule
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "10.0.0.5", "port": 8765, "hostname": "PC"}
    cfg["api_key"] = "k"
    cfg["target_mac"] = "AA-BB-CC-DD-EE-FF"
    cfg["schedules"] = [_make_sched("s1", time="12:00", weekdays=[0])]
    s.save(cfg)

    client = TestClient(app)
    r = client.post("/api/schedules/s1/run")
    assert r.status_code == 200
    # worker 是 daemon 线程：轮询等待终态
    st_store = ScheduleStateStore(tmp_path / "sched_state.json")
    deadline = time.time() + 5
    while time.time() < deadline:
        st = st_store.get("s1")
        if st and st["last_result"] != "dispatched":
            break
        time.sleep(0.02)
    st = st_store.get("s1")
    assert st["last_result"] == "triggered", st
    assert st["last_job_id"] == "j-1"
    # history 带 schedule_id
    jobs = client.get("/api/jobs").json()
    assert jobs and jobs[0]["schedule_id"] == "s1"


def test_execute_wol_failure(tmp_path):
    """WOL 抛异常 → state=wake_failed，不触发任务。"""
    fake = _FakeClient(health_ok=False)

    def factory(url, key):
        return fake

    def bad_wake(mac):
        raise OSError("no route")

    app = _app(tmp_path, client_factory=factory, wake_fn=bad_wake)
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "10.0.0.5", "port": 8765, "hostname": "PC"}
    cfg["api_key"] = "k"
    cfg["target_mac"] = "AA-BB-CC-DD-EE-FF"
    cfg["schedules"] = [_make_sched("s1", wake=True)]  # 显式开 WOL（默认 False 是 PC 常开场景）
    s.save(cfg)

    client = TestClient(app)
    client.post("/api/schedules/s1/run")
    st_store = ScheduleStateStore(tmp_path / "sched_state.json")
    deadline = time.time() + 5
    while time.time() < deadline:
        st = st_store.get("s1")
        if st and st["last_result"] != "dispatched":
            break
        time.sleep(0.02)
    assert st_store.get("s1")["last_result"] == "wake_failed"
    assert not fake.trigger_calls


def test_execute_busy_skipped(tmp_path):
    """Windows 409 → skipped_busy，不落 history。"""

    class _Busy(Exception):
        pass

    from listener_client import ListenerError

    def busy_trigger(task_id):
        raise ListenerError("409 no active job: busy")

    fake = _FakeClient(health_ok=True, trigger=busy_trigger)

    def factory(url, key):
        return fake

    app = _app(tmp_path, client_factory=factory)
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "10.0.0.5", "port": 8765, "hostname": "PC"}
    cfg["api_key"] = "k"
    cfg["schedules"] = [_make_sched("s1")]
    s.save(cfg)

    client = TestClient(app)
    client.post("/api/schedules/s1/run")
    st_store = ScheduleStateStore(tmp_path / "sched_state.json")
    deadline = time.time() + 5
    while time.time() < deadline:
        st = st_store.get("s1")
        if st and st["last_result"] != "dispatched":
            break
        time.sleep(0.02)
    assert st_store.get("s1")["last_result"] == "skipped_busy"
    assert client.get("/api/jobs").json() == []


def test_execute_task_not_found(tmp_path):
    """404 两次 → task_not_found。"""
    from listener_client import ListenerNotFound

    def nf_trigger(task_id):
        raise ListenerNotFound("not found")

    fake = _FakeClient(health_ok=True, trigger=nf_trigger)

    def factory(url, key):
        return fake

    app = _app(tmp_path, client_factory=factory)
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "10.0.0.5", "port": 8765, "hostname": "PC"}
    cfg["api_key"] = "k"
    cfg["schedules"] = [_make_sched("s1")]
    s.save(cfg)

    client = TestClient(app)
    client.post("/api/schedules/s1/run")
    st_store = ScheduleStateStore(tmp_path / "sched_state.json")
    deadline = time.time() + 8
    while time.time() < deadline:
        st = st_store.get("s1")
        if st and st["last_result"] != "dispatched":
            break
        time.sleep(0.02)
    assert st_store.get("s1")["last_result"] == "task_not_found"


def test_execute_unpaired(tmp_path):
    """未配对 → error 落 state。"""
    app = _app(tmp_path)
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["schedules"] = [_make_sched("s1")]
    s.save(cfg)

    client = TestClient(app)
    client.post("/api/schedules/s1/run")
    st_store = ScheduleStateStore(tmp_path / "sched_state.json")
    deadline = time.time() + 5
    while time.time() < deadline:
        st = st_store.get("s1")
        if st and st["last_result"] != "dispatched":
            break
        time.sleep(0.02)
    assert st_store.get("s1")["last_result"] == "error"
    assert "配对" in (st_store.get("s1")["last_error"] or "")


# ---------- 调度线程集成（lifespan） ----------

def test_scheduler_thread_fires_when_due(tmp_path):
    """scheduler_interval 极短 + 到期任务 → lifespan 线程自动触发。"""
    fake = _FakeClient(health_ok=True)

    def factory(url, key):
        return fake

    app = create_app(
        config_path=str(tmp_path / "config.json"),
        history_path=str(tmp_path / "jobs.json"),
        state_path=str(tmp_path / "sched_state.json"),
        reconcile_interval=0,
        scheduler_interval=0.05,
        client_factory=factory,
    )
    from settings import Settings
    s = Settings(tmp_path / "config.json")
    cfg = s.load()
    cfg["default_target"] = {"ip": "10.0.0.5", "port": 8765, "hostname": "PC"}
    cfg["api_key"] = "k"
    # 触发点=1 分钟前（naive 本地时间在窗口内），weekdays 全天
    now = datetime.now()
    fire = now.replace(second=0, microsecond=0)
    from datetime import timedelta
    fire = fire - timedelta(minutes=1)
    sched = _make_sched("s1", time=fire.strftime("%H:%M"), weekdays=list(range(7)))
    cfg["schedules"] = [sched]
    s.save(cfg)

    with TestClient(app):
        st_store = ScheduleStateStore(tmp_path / "sched_state.json")
        deadline = time.time() + 5
        while time.time() < deadline:
            st = st_store.get("s1")
            if st and st["last_result"] in ("triggered", "error", "skipped_busy"):
                break
            time.sleep(0.02)
        assert st["last_result"] == "triggered", st
        assert fake.trigger_calls == ["t1"]
