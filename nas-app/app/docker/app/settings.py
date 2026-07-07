"""NAS 应用配置持久化模块。

配置以 JSON 文件存储于 fnOS 应用的 etc 目录（环境变量 TRIM_PKGETC 指向）。
本模块负责加载（与默认值合并）与保存。M3/M4 的设备扫描、触发回报均依赖此配置。

配置结构：
  default_target  默认目标监听器 {ip, port, hostname}，扫描配对后写入；未配对时为 null
  api_key         与 Windows 监听器一致的 API 密钥
  scan            扫描相关：subnet(覆盖自动探测的子网,null=自动)、listener_port、diag_ports
  poll            状态轮询：interval_sec
"""
from __future__ import annotations

import json
from pathlib import Path

# 默认配置：未配置文件或字段缺失时用此兜底。
DEFAULT_CONFIG: dict = {
    "default_target": None,  # {"ip":..., "port":..., "hostname":...} 或 null
    "api_key": "",
    "scan": {
        "subnet": None,            # null=自动从网卡探测子网；填则覆盖
        "listener_port": 8765,     # 监听器默认端口，扫描时探测此端口
        "diag_ports": [22, 3389, 445, 8765, "8000-8100"],  # 端口诊断扫描列表
    },
    "poll": {
        "interval_sec": 10,        # 触发后轮询 /status 的间隔
    },
}


class Settings:
    """配置加载/保存器。构造时指定配置文件路径。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def load(self) -> dict:
        """加载配置：文件不存在或部分缺失时，与 DEFAULT_CONFIG 深度合并兜底。"""
        if not self._path.exists():
            return _deep_copy(DEFAULT_CONFIG)
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _deep_copy(DEFAULT_CONFIG)
        return _deep_merge(DEFAULT_CONFIG, raw)

    def save(self, config: dict) -> None:
        """将配置写回磁盘（覆盖）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并：base 为骨架，override 覆盖，缺失字段保留 base 默认。"""
    out = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = _deep_merge(v, override.get(k, {}))
        else:
            out[k] = override.get(k, _deep_copy(v))
    # 保留 override 中存在但 base 没有的键（向前兼容）。
    for k, v in override.items():
        if k not in out:
            out[k] = v
    return out


def _deep_copy(d):
    """简易深拷贝（配置仅含 dict/list/标量）。"""
    import copy
    return copy.deepcopy(d)
