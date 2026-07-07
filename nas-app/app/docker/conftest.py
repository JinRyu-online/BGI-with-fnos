"""pytest 配置：把 app/ 子目录加入 sys.path，使测试可扁平 import（与容器内
WORKDIR /app 的扁平布局一致），如 `from settings import ...`。"""
import os
import sys

_APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
