#!/usr/bin/env python3
"""
GLM Coding Helper Lite — 统一入口 (DEBUG VERSION)
"""
import sys, os, ctypes
from pathlib import Path

# ── Windows 控制台 UTF-8 编码 ──────────────────────────
if sys.platform == "win32":
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCP(65001)
    kernel32.SetConsoleOutputCP(65001)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

# ════════════════════════════════════════════════════════
# DEBUG: 记录进程启动信息到文件
# ════════════════════════════════════════════════════════
debug_log = Path(os.environ.get("TEMP", ".")) / "glm_debug.txt"
frozen = getattr(sys, "frozen", False)
with open(debug_log, "a", encoding="utf-8") as f:
    f.write(f"PID={os.getpid()} frozen={frozen} __name__={__name__}\n")
    f.write(f"  argv={sys.argv}\n")
    f.write(f"  executable={sys.executable}\n")

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    # ── 多进程子进程处理 ──
    if any(a == '--multiprocessing-fork' for a in sys.argv[1:]):
        from multiprocessing.spawn import spawn_main, is_forking
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"  CHILD: detected --multiprocessing-fork, is_forking={is_forking(sys.argv)}\n")
        pipe_handle = None
        parent_pid = None
        for a in sys.argv[1:]:
            if a.startswith('pipe_handle='):
                pipe_handle = int(a.split('=', 1)[1])
            elif a.startswith('parent_pid='):
                parent_pid = int(a.split('=', 1)[1])
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"  CHILD: pipe_handle={pipe_handle} parent_pid={parent_pid}\n")
        if pipe_handle is not None:
            try:
                spawn_main(pipe_handle, parent_pid)
            except Exception as e:
                with open(debug_log, "a", encoding="utf-8") as f:
                    f.write(f"  CHILD ERROR: {e}\n")
                import traceback
                with open(debug_log, "a", encoding="utf-8") as f:
                    traceback.print_exc(file=f)
        sys.exit(0)

    # 过滤 multiprocessing 内部参数
    args = [a for a in sys.argv[1:] if not a.startswith("--multiprocessing-fork")]
    args = [a for a in args if not a.startswith("parent_pid=")]
    args = [a for a in args if not a.startswith("pipe_handle=")]

    with open(debug_log, "a", encoding="utf-8") as f:
        f.write(f"  MAIN: args={args}\n")

    if "--worker" in args:
        from backend.worker import run
        raise SystemExit(run())
    elif "--ocr-worker" in args:
        from backend.ppocr_worker import main
        raise SystemExit(main())
    else:
        from backend.server import main
        sys.argv = [sys.argv[0]] + args
        raise SystemExit(main())
