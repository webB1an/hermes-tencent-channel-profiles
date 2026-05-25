#!/usr/bin/env python3
"""
Wallpaper壁纸库发帖 · v2 wrapper
强制执行 wallpaper_cold_detect_v2.1.py --force（跳过冷度检测，直接发帖）
"""
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "wallpaper_cold_detect_v2.1.py")

if __name__ == "__main__":
    os.environ.setdefault("HERMES_HOME", "/root/.hermes/profiles/tencent-channel")
    os.execv(sys.executable, [sys.executable, SCRIPT, "--force"])
