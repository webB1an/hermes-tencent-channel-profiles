#!/usr/bin/env python3
"""
Wallhaven壁纸库发帖 · v2.2 wrapper
强制执行 wallhaven_cold_detect_v2.2.py --force（跳过冷度检测，直接发帖）
"""
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "wallhaven_cold_detect_v2.2.py")

if __name__ == "__main__":
    os.environ.setdefault("HERMES_HOME", "/root/.hermes/profiles/tencent-channel")
    os.execv(sys.executable, [sys.executable, SCRIPT, "--force"])
