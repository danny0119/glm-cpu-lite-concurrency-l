#!/usr/bin/env python3
"""
GLM Coding Helper Lite — 统一入口
- 无参数 → 启动 HTTP 服务 (backend.server.main)
- --worker → 启动 YOLO Worker (backend.worker.run)
- --ocr-worker → 启动 OCR Worker (backend.ppocr_worker.main)
- --reconfigure 等其它参数透传给 server
"""

import sys, os, ctypes
from pathlib import Path

# ── Windows 控制台 UTF-8 编码（防止中文乱码） ──────────────────────────
if sys.platform == "win32":
    # 通过 Win32 API 直接设置控制台代码页为 UTF-8（影响本进程及其子进程）
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCP(65001)
    kernel32.SetConsoleOutputCP(65001)
    # Python 内部编码也锁定为 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    # PyInstaller 子进程可能传入 multiprocessing 内部参数，过滤掉
    args = [a for a in sys.argv[1:] if not a.startswith("--multiprocessing-fork")]
    # multiprocessing 也可能传 bare args: parent_pid=... pipe_handle=...
    args = [a for a in args if not a.startswith("parent_pid=")]
    args = [a for a in args if not a.startswith("pipe_handle=")]

    if "--worker" in args:
        from backend.worker import run
        raise SystemExit(run())
    elif "--ocr-worker" in args:
        from backend.ppocr_worker import main
        raise SystemExit(main())
    else:
        from backend.server import main
        # server 端会将剩余参数透传给 argparse
        # 手动替换 sys.argv 以确保 server 的 parser 不崩溃
        sys.argv = [sys.argv[0]] + args
        raise SystemExit(main())
