"""
GLM Coding Helper Lite — 并发验证码识别服务
- 自动检测 CPU + RAM，计算最佳 Worker 池大小
- 低配机器自动降级，避免内存耗尽
- ThreadingHTTPServer 并发处理 HTTP
- WorkerPool 空闲分发（只向空闲 worker 发请求）
- 支持 config.json 自定义参数
"""

import argparse, base64, ctypes, io, json, os, queue, re, socket, subprocess, sys, threading, time, urllib.request
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


def load_config():
    """读取 config.json，返回 dict 或 None"""
    if not CONFIG_PATH.exists():
        return None
    try:
        raw = CONFIG_PATH.read_bytes()
        if raw[:3] == b'\xef\xbb\xbf':
            raw = raw[3:]
        return json.loads(raw)
    except Exception as e:
        print(f"[lite] config.json parse error: {e}")
        return None


def _get_total_ram_mb() -> int:
    """Windows: 通过 Kernel32.GlobalMemoryStatusEx 获取物理内存总量（MB）"""
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

        state = MEMORYSTATUSEX()
        state.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return state.ullTotalPhys // (1024 * 1024)
    except Exception:
        pass
    return 0


def detect_optimal():
    cpus = os.cpu_count() or 4
    total_ram_mb = _get_total_ram_mb()

    if total_ram_mb > 0:
        print(f"[lite] RAM: {total_ram_mb} MB  |  CPU: {cpus} cores")
    else:
        print(f"[lite] CPU: {cpus} cores  (RAM detection unavailable)")

    # YOLO workers：每个 worker 约 200-300MB；空闲分发不再需要过度预分配
    if total_ram_mb > 0 and total_ram_mb < 4096:
        workers = 2
        print(f"[lite] low-memory mode: 2 YOLO workers")
    elif total_ram_mb > 0 and total_ram_mb < 8192:
        workers = max(1, min(cpus // 4, 6))
        print(f"[lite] limited-memory mode: {workers} YOLO workers")
    else:
        workers = max(2, min(cpus // 2, 10))
        if total_ram_mb > 0 and total_ram_mb >= 65536:
            workers = max(2, min(cpus // 2, 12))  # 大内存机器可以更多 YOLO

    tabs = max(4, min(workers * 2, 32))
    return workers, tabs, cpus, total_ram_mb


def detect_ocr_workers(cpus: int, total_ram_mb: int) -> int:
    """OCR worker 数量：每个 paddle 进程约 500MB-1GB"""
    if total_ram_mb > 0 and total_ram_mb < 4096:
        return 1
    if total_ram_mb > 0 and total_ram_mb < 8192:
        return 2
    if total_ram_mb > 0 and total_ram_mb < 16384:
        return 4
    # ≥16GB RAM：可以开多一些
    return max(2, min(cpus // 4, 8))


# ── Worker 池（空闲分发） ────────────────────────────────────────────────────
class WorkerPool:
    """空闲分发 Worker 池：只向空闲 worker 发送请求，避免轮询排队"""
    def __init__(self, count, ocr_port=None, stagger_delay=3, yolo_imgsz=448):
        self.procs = []
        self.env = os.environ.copy()
        self.env["PYTHONIOENCODING"] = "utf-8"
        self.env["PYTHONUTF8"] = "1"
        self.env["YOLO_IMGSZ"] = str(yolo_imgsz)
        if ocr_port is not None:
            self.env["PPOCR_TCP_PORT"] = str(ocr_port)

        # 空闲 worker 索引队列
        self._idle_queue = queue.Queue()
        self._pending = {}                # idx → [threading.Event, response_bytes]
        self._pending_lock = threading.Lock()

        for i in range(count):
            p = subprocess.Popen(
                _entry_prefix() + ["--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(ROOT), env=self.env,
            )
            # 后台：转发 stderr
            threading.Thread(target=self._drain, args=(p, i), daemon=True).start()
            # 先等待本 worker 发送 __READY__ 信号（YOLO 加载完毕），
            # 再启动 _reader 线程，避免与主线程争抢 stdout
            ready = p.stdout.readline().strip()
            if ready != b"__READY__":
                print(f"[w{i}] unexpected stdout: {ready}")
            else:
                print(f"[w{i}] YOLO model warmed up")
            # 后台：持续读取 stdout，填充 _pending（此时 __READY__ 已消费）
            threading.Thread(target=self._reader, args=(p, i), daemon=True).start()
            self.procs.append(p)
            self._idle_queue.put(i)  # 初始都空闲
            if i < count - 1:
                time.sleep(stagger_delay)  # 错峰启动，避免 CPU 撑爆
        print(f"[lite] WorkerPool: {count} workers (idle-dispatch)")

    def _drain(self, proc, i):
        for line in proc.stderr:
            sys.stderr.write(f"[w{i}] {line.decode(errors='replace')}")

    def _reader(self, proc, i):
        """持续读取 worker stdout，完成对应的 _pending 请求"""
        for line in proc.stdout:
            ev = None
            with self._pending_lock:
                entry = self._pending.get(i)
                if entry is not None:
                    entry[1] = line  # store response under lock
                    ev = entry[0]
            if ev is not None:
                ev.set()  # notify send() — outside lock to avoid deadlock
            self._idle_queue.put(i)  # worker 回归空闲池

    def send(self, data: bytes, timeout: float = 30.0) -> bytes:
        """取一个空闲 worker 发送请求，阻塞等待结果"""
        idx = self._idle_queue.get()
        p = self.procs[idx]
        ev = threading.Event()
        with self._pending_lock:
            self._pending[idx] = [ev, None]
        p.stdin.write(data + b"\n")
        p.stdin.flush()
        if not ev.wait(timeout=timeout):
            with self._pending_lock:
                self._pending.pop(idx, None)
            self._idle_queue.put(idx)  # 超时也归还，避免永久丢失
            raise TimeoutError(f"Worker {idx} timeout")
        with self._pending_lock:
            resp = self._pending.pop(idx, [None, None])[1]
        if resp is None:
            raise RuntimeError(f"Worker {idx} returned no data")
        return resp


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

        # 优先从请求体 action 字段获取，否则从 URL 路径推断（兼容用户脚本旧格式）
        action = data.get("action", "")
        if not action:
            if "direct_url" in self.path:
                action = "direct_url"
            elif "direct" in self.path:
                action = "direct"

        if action == "direct":
            self.handle_direct(data)
        elif action == "direct_url":
            self.handle_direct_url(data)
        else:
            self.send(400, {"error": f"unknown action: {action}"})

    def handle_direct(self, data):
        text = data.get("text", "").strip()
        img_b64 = data.get("image", "")
        chars = "".join(re.findall(r"[\u4e00-\u9fff]", text or "")[-3:])
        if not chars or not img_b64:
            self.send(400, {"error": "missing text or image"}); return

        img_bytes = base64.b64decode(img_b64.split(",")[-1])
        if not img_bytes:
            self.send(400, {"error": "empty image"}); return

        # 用 PIL 验证图片有效性
        try:
            from PIL import Image
            Image.open(io.BytesIO(img_bytes)).verify()
        except Exception:
            self.send(400, {"error": "invalid image data"}); return

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

        if not img_bytes:
            self.send(400, {"error": "empty image from server"}); return

        # 用 PIL 验证图片有效性（支持 PNG/JPEG 等常见格式）
        try:
            from PIL import Image
            Image.open(io.BytesIO(img_bytes)).verify()
        except Exception:
            self.send(400, {"error": "invalid image data from server"}); return

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
        pass  # 禁用默认日志输出


# ── HTTP 服务器 ─────────────────────────────────────────────────────────────
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    request_queue_size = 20  # 限制排队请求数，避免积压
    daemon_threads = True


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

    print(f"CPU: {cpus} logical  |  Workers: {workers}  |  OCR: ×{ocr_workers}  |  推荐标签页: ≤{max_tabs}")

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
