"""监听器配置加载模块。

职责：
- 读取 config.toml 配置文件；
- 对缺失字段填充默认值；
- 首次启动时（api_key 为空）随机生成 32 位 hex 密钥并回写到磁盘，
  使其成为「一次生成、后续只读」的持久化凭据。

配置文件采用 TOML 格式，Python 3.11 内置 tomllib 可读取。
回写时使用本模块自带的简易 TOML 序列化器（仅支持本配置所需的扁平分节结构）。
"""
from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# 默认配置：当 config.toml 中某字段缺失时，用此处的值兜底。
# 修改默认值需同步更新 config.toml.example，保持一致。
DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 8765},
    "auth": {"api_key": "", "trusted_ips": []},
    "bettergi": {
        "exe_path": "",          # BetterGI.exe 路径，留空则启动任务时会失败（开发期可留空）
        "config_path": "",       # BetterGI 调度器配置文件路径，留空则不自动读取组名
        "log_path": "",          # BetterGI 日志路径，留空则禁用完成判定 C
        "log_done_keyword": "",  # 日志中的完成关键字，留空则禁用完成判定 C
        "game_processes": ["YuanShen.exe", "GenshinImpact.exe"],  # 完成判定 B 监视的游戏进程名
    },
    "execution": {
        "default_timeout_min": 90,    # 任务默认最大执行时长（分钟）
        "default_after_done": "sleep",  # 默认收尾动作：sleep 休眠 / shutdown 关机 / lock 锁屏 / none
        "grace_seconds": 30,          # 完成判定命中后的反悔窗口（秒），期间可 /abort 阻止收尾
        "keep_history": 20,           # 内存中保留的最近任务历史条数
    },
}


@dataclass
class Server:
    """HTTP 服务监听地址。"""
    host: str
    port: int


@dataclass
class Auth:
    """鉴权配置：API 密钥 + 已信任 IP 列表。"""
    api_key: str
    trusted_ips: list[str]


@dataclass
class BetterGI:
    """BetterGI 相关路径与完成判定配置。"""
    exe_path: str
    config_path: str
    log_path: str
    log_done_keyword: str
    game_processes: list[str]


@dataclass
class Execution:
    """任务执行默认参数。"""
    default_timeout_min: int
    default_after_done: str
    grace_seconds: int
    keep_history: int


@dataclass
class ListenerConfig:
    """解析后的监听器配置对象。"""
    server: Server
    auth: Auth
    bettergi: BetterGI
    execution: Execution
    _path: Path = field(default=None, repr=False)  # 配置文件路径，内部使用

    @classmethod
    def load(cls, path: Path | str) -> "ListenerConfig":
        """加载配置文件：读取 → 合并默认值 → 首启生成密钥 → 返回配置对象。

        若 api_key 为空（首次启动），生成随机密钥并立即回写到磁盘，
        保证下次启动读到的是同一个密钥。
        """
        path = Path(path)
        raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        merged = _deep_merge(DEFAULTS, raw)
        if not merged["auth"]["api_key"]:
            # 首次启动：生成 16 字节随机数 → 32 位 hex 字符串作为密钥
            merged["auth"]["api_key"] = secrets.token_hex(16)
            _dump_toml(merged, path)
        return cls(
            server=Server(**merged["server"]),
            auth=Auth(**merged["auth"]),
            bettergi=BetterGI(**merged["bettergi"]),
            execution=Execution(**merged["execution"]),
            _path=path,
        )


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并：以 base 为骨架，override 中存在的字段覆盖 base，缺失字段保留 base 默认值。"""
    out = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = _deep_merge(v, override.get(k, {}))
        else:
            out[k] = override.get(k, v)
    return out


def _dump_toml(data: dict, path: Path) -> None:
    """将配置字典序列化为 TOML 写回磁盘（仅支持扁平分节结构，满足本配置需求）。"""
    lines: list[str] = []
    for section, kv in data.items():
        lines.append(f"[{section}]")
        for k, v in kv.items():
            lines.append(f"{k} = {_toml_value(v)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _toml_value(v) -> str:
    """将单个 Python 值转为 TOML 字面量字符串。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot serialize {type(v)} to TOML")
