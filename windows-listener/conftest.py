"""Pytest fixtures / path setup.

使 ``windows-listener`` 根目录与 ``bgi_trigger`` 子包同时 importable,
测试既可直接 ``from bgi_trigger.core import state``,也兼容旧的扁平模块名。
"""
import os
import sys
from pathlib import Path

# 把 windows-listener 根目录加到 sys.path,确保 ``import bgi_trigger`` 能找到包。
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 同时让 bgi_trigger 包的子模块可独立 import(保持向后兼容)。
os.chdir(ROOT)
