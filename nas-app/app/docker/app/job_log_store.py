"""NAS 端历史任务日志落盘存储（历史日志回看 · B2 录制方案）。

目录布局：{BGI_DATA_DIR}/jobs_log/{job_id}.log（compose 已持久化 /data；
开发环境回退源码旁 var/jobs_log/，与 HistoryStore 的 var/jobs.json 同构）。

写侧：append-only 追加行缓冲——B2 录制线程与 WS 代理 tee 两类写者
经 try_acquire_recorder/release_recorder 仲裁，同一 job 任意时刻至多一个写者。
读侧：read_tail 反向分块读（seek 末尾按 64KB 块回退数换行），
不整文件载入内存；容忍写侧残行（按最后一个换行截断）。

job_id 白名单 ^[A-Za-z0-9_-]+$ 三处同用（path_for / GET /api/logs /
WS 代理拼上游 URL），杜绝路径穿越——非 uuid hex+连字符的一律拒绝。
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

# 与 Windows 端生成的 job_id 格式一致：uuid hex 含连字符。
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# read_tail 反向分块读的块大小（64KB）
_CHUNK = 64 * 1024

# prune 保留策略：文件数超 keep 删最旧（本轮最简版）
PRUNE_KEEP = 100


def valid_job_id(job_id: str) -> bool:
    """job_id 白名单校验：^[A-Za-z0-9_-]+$（防路径穿越/URL 注入）。

    三处同用：path_for、GET /api/logs/{job_id}、WS 代理拼上游 URL。
    """
    return bool(job_id) and bool(JOB_ID_RE.match(job_id))


def default_logs_dir() -> Path:
    """日志目录：BGI_DATA_DIR/jobs_log（容器内 /data 持久化）；
    开发环境回退源码旁 var/jobs_log。"""
    base = os.environ.get("BGI_DATA_DIR")
    if base:
        return Path(base) / "jobs_log"
    return Path(__file__).resolve().parent.parent / "var" / "jobs_log"


class JobLogStore:
    """job 级日志文件存储：追加写 + 反向分块读 + 写者仲裁。

    线程模型：_lock 保护写者仲裁表与 prune 的并发；append_lines 本身
    依赖"同一 job 至多一个写者"的上层约束（try_acquire_recorder 保证）。
    """

    def __init__(self, logs_dir: Path | str | None = None, keep: int = PRUNE_KEEP) -> None:
        self._dir = Path(logs_dir) if logs_dir else default_logs_dir()
        self._keep = keep
        self._lock = threading.Lock()
        # job_id → True：该 job 当前是否有活跃写者（B2 录制者或 tee 接管者）
        self._recorders: dict[str, bool] = {}

    # ---------- 路径 ----------

    def path_for(self, job_id: str) -> Path:
        """job_id → 日志文件路径。调用方须先 valid_job_id 校验。"""
        return self._dir / f"{job_id}.log"

    def has_log(self, job_id: str) -> bool:
        """该 job 是否已有落盘日志。"""
        return self.path_for(job_id).exists()

    # ---------- 写侧 ----------

    def append_lines(self, job_id: str, lines: list[str]) -> None:
        """追加一批日志行（append-only）。新 job 首行时顺手触发 prune。"""
        if not lines:
            return
        path = self.path_for(job_id)
        is_new = not path.exists()
        self._dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        if is_new:
            self._prune()

    def _prune(self) -> None:
        """文件数超 keep 删最旧。调用点：append_lines 新 job 首行。"""
        with self._lock:
            try:
                files = sorted(
                    (p for p in self._dir.glob("*.log") if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                )
            except OSError:
                return
            excess = len(files) - self._keep
            for p in files[:excess]:
                try:
                    p.unlink()
                except OSError:
                    pass

    # ---------- 读侧 ----------

    def read_tail(self, job_id: str, n: int) -> list[str]:
        """读末尾 n 行（反向分块：seek 末尾按 64KB 块回退数换行，
        不整文件载入内存）。返回顺序：文件中出现顺序（旧 → 新）。

        残行处理：写侧 append 行缓冲可能正在写半行——文件末字节不是
        换行符时丢弃末行（半行不完整，下次读取自然补全）。
        """
        path = self.path_for(job_id)
        try:
            with path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                position = f.tell()
                lines: list[bytes] = []   # 倒序收集（新 → 旧），最后反转
                tail = b""                # 跨块拼接的行前缀
                while position > 0 and len(lines) <= n:
                    chunk_size = min(_CHUNK, position)
                    position -= chunk_size
                    f.seek(position)
                    data = f.read(chunk_size) + tail
                    parts = data.split(b"\n")
                    if position == 0:
                        # 已到文件头：所有段都是完整行（空段丢弃）
                        lines.extend(p for p in reversed(parts) if p)
                        break
                    # 未到文件头：首段要么是残缺行前缀（拼进 tail），
                    # 要么恰好是某行结尾——无法区分，保守并入 tail 继续
                    tail = parts[0]
                    for p in parts[1:]:
                        if p:
                            lines.append(p)
                out = [self._decode_line(ln) for ln in reversed(lines)]
        except OSError:
            return []
        if not out:
            return []
        # 残行容忍：文件末字节不是 \n → 末行是写了一半的残行，丢弃。
        try:
            with path.open("rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    out = out[:-1]
        except OSError:
            pass
        return out[-n:] if n > 0 else []

    @staticmethod
    def _decode_line(raw: bytes) -> str:
        """去掉行尾 \r（Windows 上文本模式写出的 \n 会落盘成 \r\n）
        后按 UTF-8 解码（坏字节替换，不抛异常）。"""
        return raw.rstrip(b"\r").decode("utf-8", errors="replace")

    # ---------- 写者仲裁（B2 录制者 ↔ tee 接管者）----------

    def try_acquire_recorder(self, job_id: str) -> bool:
        """原子尝试成为该 job 的唯一写者。成功 True；已有写者 False。"""
        with self._lock:
            if self._recorders.get(job_id):
                return False
            self._recorders[job_id] = True
            return True

    def release_recorder(self, job_id: str) -> None:
        """释放写者身份（B2 finally / tee 断开时调用）。"""
        with self._lock:
            self._recorders.pop(job_id, None)

    def set_recorder(self, job_id: str, active: bool) -> bool:
        """请求路径同步登记/清除写者（api_trigger 与 _execute_schedule 在拿到
        job_id 后、返回/投递前调用——凡 NAS 触发的 job，后续任何 tee 连接
        建立时写者必已就位，无竞态窗口）。

        active=True 时语义等同 try_acquire_recorder（原子抢占）：返回 False
        表示已有写者（B2 重复投递/tee 先接管），调用方据此放弃重复录制；
        active=False 清除登记并返回 True。
        """
        with self._lock:
            if not active:
                self._recorders.pop(job_id, None)
                return True
            if self._recorders.get(job_id):
                return False
            self._recorders[job_id] = True
            return True

    def is_recording(self, job_id: str) -> bool:
        """该 job 当前是否有活跃写者（测试与诊断用）。"""
        with self._lock:
            return bool(self._recorders.get(job_id))
