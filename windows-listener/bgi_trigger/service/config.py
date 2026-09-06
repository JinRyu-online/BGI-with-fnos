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

import logging
import re
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("bgi_trigger.config")

# 默认配置：当 config.toml 中某字段缺失时，用此处的值兜底。
# 修改默认值需同步更新 config.toml.example，保持一致。
DEFAULTS: dict = {
    "server": {"host": "0.0.0.0", "port": 8765},
    "auth": {"api_key": "", "trusted_ips": []},
    "bettergi": {
        "dir": "",               # BetterGI 安装根目录（空=使用下方显式字段）。
                                    # 非空时 exe_path/config_path/log_path 自动推导：
                                    #   exe_path  = {dir}/BetterGI.exe
                                    #   config_path = {dir}/config
                                    #   log_path  = {dir}/log/better-genshin-impact*.log （glob）
                                    # 对应字段用户显式配置了，则优先用显式的。
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
        "abort_kills_game": True,     # /abort 时是否同时终止游戏进程
        "pre_launch_script": "",      # 拉起 BetterGI 前执行的 cmd 脚本（空=禁用），如关闭聊天自启
    },
    "tasks": {
        # 任务清单来源目录（相对路径相对于监听器目录）。该目录下所有 *.json 热加载。
        
        "dir": "tasks",
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
    dir: str          # ★ 新增：安装根目录（空=使用显式字段）
    exe_path: str
    config_path: str
    log_path: str
    log_done_keyword: str
    game_processes: list[str]
    # ★ "count"=命中组数才触发(默认,多组任务用);"first"=首次命中即触发(单组任务用)。
    #   放末尾:game_processes 无默认值,dataclass 要求有默认的字段排在后面。
    log_done_mode: str = "count"


@dataclass
class Execution:
    """任务执行默认参数。"""
    default_timeout_min: int
    default_after_done: str
    grace_seconds: int
    keep_history: int
    # ★ /abort 时是否同时终止游戏进程（True=abort 连游戏一起杀，默认）
    abort_kills_game: bool = True
    # ★ 拉起 BetterGI 前执行的 cmd 脚本（空=禁用）。带默认值排尾部（dataclass 约定）。
    #   仅真正 Popen 分支执行（handoff 接管不执行）；失败仅 WARNING 不阻断。
    pre_launch_script: str = ""


@dataclass
class Tasks:
    """任务清单来源配置。"""
    dir: str


@dataclass
class ListenerConfig:
    """解析后的监听器配置对象。"""
    server: Server
    auth: Auth
    bettergi: BetterGI
    execution: Execution
    tasks: Tasks
    _path: Path = field(default=None, repr=False)  # 配置文件路径，内部使用

    @classmethod
    def load(cls, path: Path | str) -> "ListenerConfig":
        """加载配置文件：读取 → 合并默认值 → 首启生成密钥 → 返回配置对象。

        若 api_key 为空（首次启动），生成随机密钥并立即回写到磁盘，
        保证下次启动读到的是同一个密钥。

        bettergi.dir 非空且对应字段未显式配置时，自动推导 exe_path /
        config_path / log_path（log_path 使用 glob 模式按天自动发现）。
        """
        path = Path(path)
        raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        merged = _deep_merge(DEFAULTS, raw)
        if not merged["auth"]["api_key"]:
            # 首次启动：生成 16 字节随机数 → 32 位 hex 字符串作为密钥
            merged["auth"]["api_key"] = secrets.token_hex(16)
            _persist_api_key(path, merged)

        # ★ 配置简化：dir 非空时，未显式配置的字段自动推导
        _derive_bettergi_paths(merged, raw)

        return cls(
            server=Server(**merged["server"]),
            auth=Auth(**merged["auth"]),
            bettergi=BetterGI(**merged["bettergi"]),
            execution=Execution(**merged["execution"]),
            tasks=Tasks(**merged["tasks"]),
            _path=path,
        )


def _derive_bettergi_paths(merged: dict, raw: dict) -> None:
    """当 bettergi.dir 非空时，自动推导未显式配置的路径字段。

    推导规则（仅当对应字段用户未显式配置时生效）：
      - exe_path      = {dir}/BetterGI.exe
      - config_path   = {dir}/config
      - log_path      = {dir}/log/better-genshin-impact*.log （glob，按天自动发现）
      - log_done_keyword 不推导（业务特定，必须显式配置）

    显式字段始终优先：用户在 config.toml 写了某个字段 → 用用户的。
    dir 缺省（空字符串）→ 完全不参与，回退到旧行为。

    路径处理：
      - 用 Path 拼接（自动处理 / 与 \、尾部分隔符）
      - 相对路径按 TOML 惯例，相对路径相对于监听器目录，推导前先 resolve 成绝对路径
      - dir 非空但目录实际不存在：打 WARNING 日志，不阻塞启动
    """
    bg = merged.get("bettergi", {})
    dir_str = bg.get("dir", "")
    if not dir_str:
        return  # 旧行为：dir 空，完全不参与

    # 用户显式配置了哪些字段（基于合并前的 raw 判断）
    raw_bg = raw.get("bettergi", {}) if isinstance(raw, dict) else {}
    # 默认值集合（用于判断"用户未显式配置"——字段值 == 默认值空字符串）
    defaults_bg = DEFAULTS.get("bettergi", {})

    try:
        dir_path = Path(dir_str).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        log.warning("bettergi.dir=%r 无法解析为绝对路径: %s", dir_str, e)
        return

    def _is_unset(field: str) -> bool:
        """字段用户未显式配置：raw 里没有该键，且默认值也是空字符串。"""
        if field in raw_bg:
            return False
        default_val = defaults_bg.get(field, "")
        return default_val == "" or default_val is None

    if _is_unset("exe_path"):
        bg["exe_path"] = str(dir_path / "BetterGI.exe")
        log.info("bettergi.exe_path derived from dir: %s", bg["exe_path"])
    if _is_unset("config_path"):
        bg["config_path"] = str(dir_path / "config")
        log.info("bettergi.config_path derived from dir: %s", bg["config_path"])
    if _is_unset("log_path"):
        bg["log_path"] = str(dir_path / "log" / "better-genshin-impact*.log")
        log.info("bettergi.log_path derived from dir: %s", bg["log_path"])

    if not dir_path.is_dir():
        log.warning("bettergi.dir=%s does not exist; task launch will fail until it does",
                    dir_path)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并：以 base 为骨架，override 中存在的字段覆盖 base，缺失字段保留 base 默认值。"""
    out = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = _deep_merge(v, override.get(k, {}))
        else:
            out[k] = override.get(k, v)
    return out


def _persist_api_key(path: Path, data: dict) -> None:
    """把生成的 api_key 写回配置文件，尽量保留原有注释。

    若文件已存在且含 api_key 行：只替换该行的值（注释与结构原样保留）。
    否则（文件不存在或无 api_key 行）：整体重写为裸配置（无注释，但功能完整）。
    """
    key = data["auth"]["api_key"]
    if path.exists():
        text = path.read_text(encoding="utf-8")
        updated = _replace_api_key_line(text, key)
        if updated is not None:
            path.write_text(updated, encoding="utf-8")
            return
    _dump_toml(data, path)


# 匹配独立的 api_key = "..." 行（含行尾注释），用于就地替换值、保留注释。
_API_KEY_LINE = re.compile(r'(^api_key\s*=\s*)"[^"]*"(\s*(?:#.*)?)$', re.MULTILINE)


def _replace_api_key_line(text: str, key: str) -> str | None:
    """把文本中 api_key 行的值替换为 key；未找到该行返回 None。"""
    if not _API_KEY_LINE.search(text):
        return None
    return _API_KEY_LINE.sub(lambda m: f'{m.group(1)}"{key}"{m.group(2)}', text)


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
