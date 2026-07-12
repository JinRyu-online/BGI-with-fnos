"""鉴权模块：共享 API 密钥 + IP 自动学习白名单。

安全模型（详见 docs/开发方案.md §4.3）：
- 密钥是主凭据。NAS 端在请求头 Authorization: Bearer <key> 中携带。
- IP 白名单为「审计/可视化」层，非硬性预授权门禁：第一次收到「携带正确
  密钥」的请求时，把来源 IP 自动加入信任列表，后续该 IP 直接放行。
  监听器 UI 可查看/清除已配对 IP。
- 用户已明确接受「内网环境下密钥即足够」的取舍。

trusted_ips 持久化到磁盘（trusted.json），重启后保留。
"""
from __future__ import annotations

import json
from pathlib import Path


class AuthError(Exception):
    """鉴权失败（密钥错误）。"""


class AuthState:
    """鉴权状态：持有密钥与已信任 IP，并提供校验与持久化能力。"""

    def __init__(self, api_key: str, trusted_ips: list[str], store_path: Path | str | None = None) -> None:
        self._api_key = api_key
        self._trusted: list[str] = list(trusted_ips)
        self._store_path = Path(store_path) if store_path else None
        # 若提供了持久化路径且文件已存在，从磁盘加载已信任 IP（覆盖入参中的列表）。
        if self._store_path and self._store_path.exists():
            self._trusted = self._load_trusted()

    @property
    def trusted_ips(self) -> list[str]:
        """已信任 IP 列表（拷贝，外部修改不影响内部状态）。"""
        return list(self._trusted)

    @property
    def api_key(self) -> str:
        """配对密钥（供 /key 接口暴露给可信内网 NAS）。"""
        return self._api_key

    def verify(self, token: str, client_ip: str) -> None:
        """校验请求：密钥必须正确；来源 IP 若已信任则放行，否则首次自动学习。

        密钥错误 → 抛 AuthError（不会学习 IP）。
        密钥正确 + IP 已信任 → 放行。
        密钥正确 + IP 未信任 → 学习该 IP 并持久化，放行。
        """
        if token != self._api_key:
            raise AuthError("invalid api key")
        if client_ip in self._trusted:
            return
        # 首次以正确密钥到来的新 IP：自动加入信任列表。
        self._trusted.append(client_ip)
        self._persist()

    def clear_trusted_ips(self) -> None:
        """清空已信任 IP 列表（撤销所有已配对设备），并持久化。"""
        self._trusted = []
        self._persist()

    def _load_trusted(self) -> list[str]:
        """从磁盘读取已信任 IP 列表，文件损坏时返回空列表。"""
        try:
            return list(json.loads(self._store_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return []

    def _persist(self) -> None:
        """将已信任 IP 列表写回磁盘（未配置路径时为空操作）。"""
        if self._store_path is None:
            return
        self._store_path.write_text(json.dumps(self._trusted), encoding="utf-8")
