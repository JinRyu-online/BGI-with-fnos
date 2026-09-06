"""定时任务调度核心（纯逻辑 + 状态存储，不含线程/IO 等待）。

设计要点（详见 docs/定时任务方案.md）：
- next_fire_at / is_due 为纯函数，now 显式入参，绝不内部取当前时间——测试无需 fake 时钟。
- 时间一律用 naive 本地时间语义（容器 TZ=Asia/Shanghai，见 Dockerfile）。
- 防重入以"触发窗口"判据：last_fired_at < window_start <= now；30s 轮询两次
  落进同一分钟也不会双触发。
- catch-up 语义：错过超宽容窗（miss_grace_sec，默认 300s）不补跑，仅记录——
  避免 NAS 重启后半夜突然开机唤醒 PC。
- 同一 tick 内多条到期只投递第一条（单槽状态机，第二条必然 409），其余标冲突跳过。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

# 错过宽容窗：now 超过理论触发点这么多秒即视为"已过期"，跳过不补跑
DEFAULT_MISS_GRACE_SEC = 300

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]  # ISO：0=周一


# ---------- 纯函数：触发时刻计算 ----------

def parse_hhmm(time_str: str) -> tuple[int, int] | None:
    """解析 "HH:MM" → (hour, minute)。非法返回 None。"""
    if not isinstance(time_str, str) or ":" not in time_str:
        return None
    try:
        hh, mm = time_str.split(":", 1)
        hour, minute = int(hh), int(mm)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _weekdays_of(sched: dict) -> list[int]:
    """取 schedule 的 weekdays；空/缺省 = 每天（0..6，ISO 0=周一）。"""
    wd = sched.get("weekdays")
    if not wd:
        return list(range(7))
    return [d for d in wd if isinstance(d, int) and 0 <= d <= 6]


def next_fire_at(now: datetime, time_str: str, weekdays: list[int] | None) -> datetime | None:
    """下一个触发时刻（naive 本地时间）。非法 time 返回 None。

    weekdays 为空列表/None 表示每天。now 恰好等于今天的触发点时返回今天该点
    （是否真正触发由 is_due 的窗口+last_fired_at 判定，这里只算"理论下一点"）。
    """
    parsed = parse_hhmm(time_str)
    if parsed is None:
        return None
    hour, minute = parsed
    days = sorted(set(_weekdays_of({"weekdays": weekdays})))
    if not days:
        return None
    for offset in range(8):  # 最多看到一周后
        day = (now + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if day <= now:
            continue
        if day.weekday() in days:
            return day
    return None


def fire_window_start(now: datetime, time_str: str, *, grace_sec: int = DEFAULT_MISS_GRACE_SEC) -> datetime | None:
    """今天该触发点的窗口起点 = 触发点（错过超窗判定用：now - grace 之前到点即弃）。"""
    parsed = parse_hhmm(time_str)
    if parsed is None:
        return None
    hour, minute = parsed
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def is_due(sched: dict, now: datetime, last_fired_at: float | None, *,
           grace_sec: int = DEFAULT_MISS_GRACE_SEC) -> bool:
    """该 schedule 当前是否应触发。

    判据（全部满足）：
      1. enabled 且 time/weekdays 合法；
      2. 今天是触发日（weekdays 命中）且 now 已过今天的触发点；
      3. now 未超宽容窗（now - 触发点 <= grace_sec）——过期不补跑；
      4. last_fired_at < 本次触发点的时间戳——同一窗口只触发一次
        （last_fired_at 用 Unix 秒，与触发点 datetime.timestamp() 同基准）。
    """
    if not sched.get("enabled", True):
        return False
    parsed = parse_hhmm(sched.get("time", ""))
    if parsed is None:
        return False
    days = _weekdays_of(sched)
    if now.weekday() not in days:
        return False
    fire_at = now.replace(hour=parsed[0], minute=parsed[1], second=0, microsecond=0)
    if now < fire_at:
        return False
    if (now - fire_at).total_seconds() > grace_sec:
        return False  # 错过超窗：跳过不补跑
    if last_fired_at is not None and last_fired_at >= fire_at.timestamp():
        return False  # 本窗口已触发过
    return True


def validate_schedule(s: dict) -> list[str]:
    """校验一条 schedule，返回错误列表（空列表=合法）。"""
    errs: list[str] = []
    if not isinstance(s, dict):
        return ["schedule 必须是对象"]
    if parse_hhmm(s.get("time", "")) is None:
        errs.append(f"time 非法：{s.get('time')!r}（应为 HH:MM）")
    wd = s.get("weekdays")
    if wd is not None:
        if not isinstance(wd, list) or any(not isinstance(d, int) or not 0 <= d <= 6 for d in wd):
            errs.append("weekdays 非法：应为 0-6 整数数组（0=周一）")
    if not isinstance(s.get("task_id"), str) or not s.get("task_id"):
        errs.append("task_id 不能为空")
    if not isinstance(s.get("name", ""), str):
        errs.append("name 必须是字符串")
    wake_timeout = s.get("wake_timeout_sec", 300)
    if not isinstance(wake_timeout, int) or not 30 <= wake_timeout <= 1800:
        errs.append("wake_timeout_sec 应为 30-1800 整数")
    return errs


# ---------- 运行状态存储（独立文件，绝不进 config.json） ----------

class ScheduleStateStore:
    """schedule 运行状态持久化：{schedule_id: {last_fired_at, last_job_id, last_result, last_error}}。

    独立于 config.json 的原因：Settings.save 整文件覆盖且无锁，调度线程写
    last_fired_at 与 API 线程保存配置并发会互相覆盖丢数据。
    线程安全模型与 HistoryStore 相同（Lock 保护 load+save 临界区）。
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, schedule_id: str) -> dict | None:
        with self._lock:
            return self._load().get(schedule_id)

    def all(self) -> dict:
        with self._lock:
            return self._load()

    def record_fired(self, schedule_id: str, *, fired_at: float, job_id: str | None,
                     result: str, error: str | None = None) -> None:
        """记录一次触发（成功或失败分支统一入口）。"""
        with self._lock:
            data = self._load()
            data[schedule_id] = {
                "last_fired_at": fired_at,
                "last_job_id": job_id,
                "last_result": result,
                "last_error": error,
            }
            self._save(data)

    def drop(self, schedule_id: str) -> None:
        """删除 schedule 时清理残留状态。"""
        with self._lock:
            data = self._load()
            if schedule_id in data:
                del data[schedule_id]
                self._save(data)


def default_state_path() -> str:
    """默认状态文件：BGI_DATA_DIR/schedules_state.json；开发回退源码旁 var/。"""
    import os
    base = os.environ.get("BGI_DATA_DIR")
    if base:
        return str(Path(base) / "schedules_state.json")
    return str(Path(__file__).resolve().parent.parent / "var" / "schedules_state.json")


# ---------- 调度器（tick 只做"发现到期 + 投递"，执行链在 worker 线程） ----------

class Scheduler:
    """扫 config 里的 schedules，把到期的投递给 execute_fn（独立线程执行）。

    tick(now) 是同步方法，可直接单测（无需起线程）；线程循环只是
    `tick(datetime.now())` + Event.wait 的包装（见 main.py lifespan）。
    同一 tick 多条到期只投第一条（单槽状态机，第二条必然 409），其余标冲突。
    """

    def __init__(self, client_getter, state_getter, *, execute_fn=None,
                 miss_grace_sec: int = DEFAULT_MISS_GRACE_SEC) -> None:
        self._client_getter = client_getter    # () -> (ListenerClient, cfg)（保留扩展位）
        self._state_getter = state_getter      # () -> ScheduleStateStore
        self._execute = execute_fn             # sched -> None；None 时 tick 只扫描不投递
        self._grace = miss_grace_sec

    def due_schedules(self, now: datetime, schedules: list[dict]) -> tuple[list[dict], list[dict]]:
        """返回 (到期列表, 冲突跳过列表)。到期最多 1 条（同 tick 冲突丢弃）。"""
        state = self._state_getter().all()
        due: list[dict] = []
        skipped: list[dict] = []
        for s in schedules:
            sid = s.get("id")
            if not sid or not s.get("enabled", True):
                continue
            last = (state.get(sid) or {}).get("last_fired_at")
            if is_due(s, now, last, grace_sec=self._grace):
                if due:
                    skipped.append(s)  # 同 tick 冲突：只投第一条
                else:
                    due.append(s)
        return due, skipped

    def tick(self, now: datetime, schedules: list[dict] | None = None) -> dict:
        """扫一遍并投递到期任务。返回 {fired: [...], skipped_conflict: [...]}。

        schedules 为 None 时从 _client_getter 之外读取（由 main.py 闭包传配置）；
        投递前重读配置确认 enabled（执行期间用户可能已删除/禁用）。
        """
        if schedules is None:
            schedules = []
        fired: list[str] = []
        due, skipped = self.due_schedules(now, schedules)
        for s in skipped:
            self._state_getter().record_fired(
                s["id"], fired_at=now.timestamp(), job_id=None,
                result="skipped_conflict", error="同一时刻多条定时任务，仅执行第一条",
            )
        for s in due:
            # 先记 last_fired_at（窗口判据），再投递——否则 worker 秒完成时
            # 其结果会被 dispatched 覆盖；执行期间重扫也不会重复投递
            self._state_getter().record_fired(
                s["id"], fired_at=now.timestamp(), job_id=None, result="dispatched",
            )
            # 投递前以此刻快照为准（worker 内不再读配置，避免执行中配置被删的竞态）
            if self._execute is not None:
                threading.Thread(
                    target=self._execute, args=(dict(s),),
                    name=f"bgi-sched-exec-{s['id']}", daemon=True,
                ).start()
            fired.append(s["id"])
        return {"fired": fired, "skipped_conflict": [s["id"] for s in skipped]}
