"""BetterGI Trigger Listener — Windows 端监听器包。

本包按职责分层:
  - api (app.py)         FastAPI 路由（HTTP /key /trigger /status 等接口）
  - service (config/auth/settings)   配置加载 / 鉴权
  - core  (state/launcher/execution/tasks)   状态机 / 启动器 / 完成判定 / 任务清单
"""
__version__ = "1.0.0"
