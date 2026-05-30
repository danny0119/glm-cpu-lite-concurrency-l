"""
GLM Coding Helper Lite — 并发验证码识别服务
- 自动检测 CPU + RAM，计算最佳 Worker 池大小
- 低配机器自动降级，避免内存耗尽
- ThreadingHTTPServer 并发处理 HTTP
- WorkerPool 多子进程轮询分发
- 支持 config.json 自定义参数
"""

import argparse, base64, ctypes, io, json, os, re, socket, subprocess, sys, threading, time, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from datetime import datetime

# ── 路径检测（支持 PyInstaller frozen mode） ──────────────────────────────────
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = ROOT / "config.json"


def _entry_prefix():
    """返回启动本程序的命令前缀（兼容 frozen / dev 模式）"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return [sys.executable]
    return [sys.executable, "-u", str(ROOT / "glm-lite.py")]

# ── 配置加载 ────────────────────────────────────────────────────────────────
def load_config():
    """读取 config.json（如果存在），否则返回 None"""
    paths = [CONFIG_PATH]
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        paths.append(Path(sys._MEIPASS) / "config.json")
    for p in paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return cfg
            except Exception as e:
                print(f"[warn] {p} parse failed: {e}, using auto-detect")
    return None


# ── CPU / RAM 检测 ────────────────────────────────────────────────
def _get_total_ram_mb() -> int:
    """使用 Windows API 获取物理内存总量（MB），零依赖"""
    try:
        kernel32 = ctypes.windll.kernel32
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            return int(mem.ullTotalPhys // (1024 * 1024))
    except Exception:
        pass
    return 0  # 无法获取

def detect_optimal():
    cpus = os.cpu_count() or 4
    total_ram_mb = _get_total_ram_mb()

    if total_ram_mb > 0:
        print(f"[lite] RAM: {total_ram_mb} MB  |  CPU: {cpus} cores")
    else:
        print(f"[lite] CPU: {cpus} cores  (RAM detection unavailable)")

    # YOLO workers：每个 worker 约 200-300MB
    if total_ram_mb > 0 and total_ram_mb < 4096:
        workers = 1
        print(f"[lite] low-memory mode: 1 YOLO worker")
    elif total_ram_mb > 0 and total_ram_mb < 8192:
        workers = max(1, min(cpus // 4, 4))
        print(f"[lite] limited-memory mode: {workers} YOLO workers")
    else:
        workers = max(2, min(cpus // 2 - 2, 16))

    tabs = max(4, min(workers * 2, 32))
    return workers, tabs, cpus, total_ram_mb

def detect_ocr_workers(cpus: int, total_ram_mb: int) -> int:
    """OCR worker 数量：每个 paddle 进程约 500MB-1GB"""
    if total_ram_mb > 0 and total_ram_mb < 4096:
        return 1
    if total_ram_mb > 0 and total_ram_mb < 8192:
        return 1
    return max(1, min(cpus // 8, 6))


# ── Worker 池 ──────────────────────────────────────────────────────────────
class WorkerPool:
    def __init__(self, count, ocr_port=None, stagger_delay=3, yolo_imgsz=448):
        self.procs = []
        self.env = os.environ.copy()
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["PYTHONUTF8"] = "1"
        self.env["YOLO_IMGSZ"] = str(yolo_imgsz)
        if ocr_port is not None:
            self.env["PPOCR_TCP_PORT"] = str(ocr_port)
        for i in range(count):
            p = subprocess.Popen(
                _entry_prefix() + ["--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(ROOT), env=self.env,
            )
            threading.Thread(target=self._drain, args=(p, i), daemon=True).start()
            self.procs.append(p)
            # 等待本 worker 发送 __READY__ 信号（YOLO 加载完毕）
            ready = p.stdout.readline().strip()
            if ready != b"__READY__":
                print(f"[w{i}] unexpected stdout: {ready}")
            else:
                print(f"[w{i}] YOLO model warmed up")
            if i < count - 1:
                time.sleep(stagger_delay)  # 错峰启动，避免 CPU 撑爆
        self.idx = 0
        self.lock = threading.Lock()
        print(f"[lite] WorkerPool: {count} workers  x  {cpus} threads")

    def _drain(self, proc, i):
        for line in proc.stderr:
            sys.stderr.write(f"[w{i}] {line.decode(errors='replace')}")

    def send(self, data: bytes) -> bytes:
        with self.lock:
            p = self.procs[self.idx % len(self.procs)]
            self.idx += 1
        p.stdin.write(data + b"\n")
        p.stdin.flush()
        return p.stdout.readline()


# ── HTTP Handler ────────────────────────────────────────────────────────────
HOST, PORT = "0.0.0.0", 8888
pool = None
workers, max_tabs, cpus = 0, 0, 0

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send(200, {"status": "ok", "workers": workers, "tabs": max_tabs})
        else:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            data = json.loads(raw)
        except Exception:
            self.send(400, {"error": "bad json"}); return

        path = self.path
        if path == "/health":
            self.send(200, {"status": "ok", "workers": workers, "tabs": max_tabs})
        elif path == "/captcha_direct":
            self.handle_direct(data)
        elif path == "/captcha_direct_url":
            self.handle_direct_url(data)
        else:
            self.send(404, {"error": "not found"})

    def handle_direct(self, data):
        text = data.get("text", "").strip()
        img_b64 = data.get("image", "")
        chars = "".join(re.findall(r"[\u4e00-\u9fff]", text or "")[-3:])
        if not chars or not img_b64:
            self.send(400, {"error": "missing text or image"}); return

        img_bytes = base64.b64decode(img_b64.split(",")[-1])
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        debug = ROOT / "logs" / "captcha"
        debug.mkdir(parents=True, exist_ok=True)
        path = debug / f"{chars}_{ts}.png"
        path.write_bytes(img_bytes)

        payload = json.dumps({"image_path": str(path), "chars": list(chars)}, ensure_ascii=False)
        try:
            resp = pool.send(payload.encode())
            result = json.loads(resp)
            self.send(200, {"success": True, "result": result, "ts": int(time.time() * 1000)})
        except Exception as e:
            self.send(500, {"error": str(e)})

    def handle_direct_url(self, data):
        text = data.get("text", "").strip()
        url = data.get("url", "")
        chars = "".join(re.findall(r"[\u4e00-\u9fff]", text or "")[-3:])
        if not chars or not url:
            self.send(400, {"error": "missing text or url"}); return

        try:
            resp = urllib.request.urlopen(url, timeout=5)
            img_bytes = resp.read()
        except Exception as e:
            self.send(400, {"error": f"failed to download image: {e}"}); return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        debug = ROOT / "logs" / "captcha"
        debug.mkdir(parents=True, exist_ok=True)
        path = debug / f"{chars}_url_{ts}.png"
        path.write_bytes(img_bytes)

        payload = json.dumps({"image_path": str(path), "chars": list(chars)}, ensure_ascii=False)
        try:
            resp = pool.send(payload.encode())
            result = json.loads(resp)
            self.send(200, {"success": True, "result": result, "ts": int(time.time() * 1000)})
        except Exception as e:
            self.send(500, {"error": str(e)})

    def send(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode())

    def log_message(self, fmt, *a):
        pass


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ── 启动 ────────────────────────────────────────────────────────────────────
def main():
    global pool, workers, max_tabs, cpus

    # 文件日志（启动阶段捕获所有输出）
    _start_log = Path(sys.executable).resolve().parent / "server.log" if getattr(sys, 'frozen', False) else Path("server.log")
    try:
        _log = open(_start_log, "a", encoding="utf-8")
    except Exception:
        _log = None

    def wlog(msg: str):
        print(msg)
        if _log:
            _log.write(msg + "\n")
            _log.flush()

    wlog(f"\n=== Server started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    wlog(f"frozen={getattr(sys, 'frozen', False)}, cwd={os.getcwd()}")

    parser = argparse.ArgumentParser(description="GLM Coding Helper Lite Backend")
    parser.add_argument("--reconfigure", action="store_true",
                        help="忽略 config.json，重新检测 CPU")
    args = parser.parse_args()

    # 1. 加载配置
    cfg = load_config() if not args.reconfigure else None

    if cfg:
        workers       = cfg.get("workers", 0)
        ocr_workers   = cfg.get("ocr_workers", 1)
        port          = cfg.get("port", 8888)
        stagger_delay = cfg.get("stagger_delay", 3)
        yolo_imgsz    = cfg.get("yolo_imgsz", 448)
        cpus          = os.cpu_count() or 4
        max_tabs      = max(4, min(workers * 2, 32))
        print(f"[lite] config.json loaded: {workers} workers, OCR×{ocr_workers}, port={port}")
    else:
        # 自动检测（考虑 CPU + RAM）
        workers, max_tabs, cpus, total_ram_mb = detect_optimal()
        ocr_workers   = detect_ocr_workers(cpus, total_ram_mb)
        port          = 8888
        stagger_delay = 3
        yolo_imgsz    = 448
        print(f"[lite] auto-detect: {cpus} cores, OCR×{ocr_workers} → {workers} workers")

    print(f"CPU: {cpus} logical  |  Workers: {workers}  |  推荐标签页: ≤{max_tabs}")

    # 2. 启动共享 OCR Worker (TCP 模式)
    ocr_env = os.environ.copy()
    ocr_env["WORKERS"] = str(ocr_workers)
    # 捕获 OCR stderr 到日志
    _ocr_stderr_log = _start_log.with_name("ocr_stderr.log") if getattr(sys, 'frozen', False) else Path("ocr_stderr.log")
    try:
        _ocr_stderr_fh = open(_ocr_stderr_log, "a", encoding="utf-8")
    except Exception:
        _ocr_stderr_fh = None
    ocr_proc = subprocess.Popen(
        _entry_prefix() + ["--ocr-worker", "--tcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(ROOT), env=ocr_env,
    )
    if _ocr_stderr_fh:
        _ocr_stderr_fh.write(f"\n--- OCR subprocess PID={ocr_proc.pid} at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        _ocr_stderr_fh.flush()
    # 清理 OCR 的 stdout（它不写 stdout，偶尔会输出信息）
    def _ocr_drain_stdout():
        for line in ocr_proc.stdout:
            pass  # discard
    threading.Thread(target=_ocr_drain_stdout, daemon=True).start()

    # 从 stderr 读取 TCP 端口
    ocr_port = None
    for line in ocr_proc.stderr:
        text = line.decode(errors="replace").strip()
        sys.stderr.write(text + "\n")  # 转发到我们的 stderr
        if _ocr_stderr_fh:
            _ocr_stderr_fh.write(text + "\n")
            _ocr_stderr_fh.flush()
        if text.startswith("[ppocr-tcp] PORT="):
            ocr_port = int(text.split("=", 1)[1])
            break
    # OCR 剩余 stderr 转发（写入日志 + stderr）
    def _ocr_drain_stderr():
        for line in ocr_proc.stderr:
            text = line.decode(errors='replace')
            sys.stderr.write(f"[ocr] {text}")
            if _ocr_stderr_fh:
                _ocr_stderr_fh.write(text)
                _ocr_stderr_fh.flush()
    threading.Thread(target=_ocr_drain_stderr, daemon=True).start()

    if not ocr_port:
        wlog("[error] Failed to start OCR worker (TCP)")
        ocr_proc.kill()
        return

    print(f"[lite] OCR worker ready on TCP 127.0.0.1:{ocr_port} (x{ocr_workers})")

    global PORT
    PORT = port
    pool = WorkerPool(workers, ocr_port=ocr_port,
                      stagger_delay=stagger_delay, yolo_imgsz=yolo_imgsz)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Server: http://{HOST}:{PORT}")
    print("[ready]")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
