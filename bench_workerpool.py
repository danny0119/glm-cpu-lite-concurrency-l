"""
WorkerPool throughput benchmark for GLM Coding Helper Lite.
Spawns the server, sends concurrent /captcha_direct requests,
measures throughput at various concurrency levels.
Hard timeout: 180s (3 min).
"""
import base64, io, json, subprocess, sys, time, threading, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent
SERVER_SCRIPT = ROOT / "glm-lite.py"
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
PORT = 8888
BASE = f"http://127.0.0.1:{PORT}"
TIMEOUT = 178  # slightly under 3 min

# Create a tiny valid PNG (100x100 white) as synthetic test image
def _make_test_png(size=100):
    """Return base64 of a solid-color PNG."""
    import struct, zlib
    def _png_chunk(ctype, data):
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b""
    for y in range(size):
        raw += b"\x00" + b"\xff\xff\xff" * size
    idat = zlib.compress(raw)
    iend = b""
    buf = sig
    buf += _png_chunk(b"IHDR", ihdr)
    buf += _png_chunk(b"IDAT", idat)
    buf += _png_chunk(b"IEND", iend)
    return base64.b64encode(buf).decode()

TEST_IMAGE_B64 = _make_test_png()

def start_server():
    log_out = ROOT / "logs" / "bench-server-out.log"
    log_err = ROOT / "logs" / "bench-server-err.log"
    proc = subprocess.Popen(
        [str(VENV_PYTHON), "-u", str(SERVER_SCRIPT)],
        stdout=open(log_out, "w"), stderr=open(log_err, "w"),
        cwd=str(ROOT)
    )
    return proc

def wait_ready(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = urlopen(f"{BASE}/health", timeout=3)
            if r.status == 200:
                return True
        except (URLError, ConnectionResetError, OSError):
            pass
        time.sleep(1)
    return False

def send_request(uid):
    body = json.dumps({"text": "验证码", "image": TEST_IMAGE_B64}).encode()
    req = Request(f"{BASE}/captcha_direct", data=body,
                  headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        resp = urlopen(req, timeout=30)
        data = resp.read()
        elapsed = time.perf_counter() - t0
        result = json.loads(data)
        return {"uid": uid, "ok": True, "elapsed": elapsed, "result": result}
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {"uid": uid, "ok": False, "elapsed": elapsed, "error": str(e)}

def bench_concurrency(concurrency, n_requests):
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        fut_to_uid = {pool.submit(send_request, i): i for i in range(n_requests)}
        for fut in as_completed(fut_to_uid):
            results.append(fut.result())
    return results

def main():
    print("=== WorkerPool 吞吐量测试 ===")
    print(f"  平台: {sys.platform}")
    print(f"  CPU: {os.cpu_count()} 逻辑核心")

    # start server
    print("\n[1] 启动 lite 服务器...")
    proc = start_server()
    print(f"  PID: {proc.pid}")
    if not wait_ready(90):
        print("  FAIL: 服务器未就绪")
        proc.kill(); proc.wait()
        sys.exit(1)
    print("  OK: 服务器就绪")

    # warmup
    print("\n[2] 预热 (1 请求)...")
    r = send_request(-1)
    print(f"  {r['elapsed']:.3f}s  {'OK' if r['ok'] else 'FAIL: '+r.get('error','')}")

    # benchmarks at concurrency levels 1, 2, 4, 8
    levels = [1, 2, 4, 8]
    results = {}
    total_deadline = time.time() + TIMEOUT
    for lvl in levels:
        if time.time() >= total_deadline:
            print(f"\n  超时，跳过 concurrency={lvl}")
            break
        n = max(lvl * 2, 8)  # enough samples
        print(f"\n[3] 并发度={lvl}, 请求数={n} ...")
        t0 = time.perf_counter()
        rr = bench_concurrency(lvl, n)
        wall = time.perf_counter() - t0
        ok = [r for r in rr if r["ok"]]
        fail = [r for r in rr if not r["ok"]]
        times = [r["elapsed"] for r in ok]
        avg_t = sum(times) / len(times) if times else 0
        throughput = len(ok) / wall if wall > 0 else 0
        results[lvl] = {
            "ok": len(ok), "fail": len(fail), "wall": wall,
            "avg": avg_t, "min": min(times) if times else 0,
            "max": max(times) if times else 0, "throughput": throughput
        }
        print(f"  完成: {len(ok)} OK / {len(fail)} FAIL  wall={wall:.2f}s")
        print(f"  延迟: avg={avg_t:.3f}s  min={min(times) if times else 0:.3f}s  max={max(times) if times else 0:.3f}s")
        print(f"  吞吐: {throughput:.2f} req/s")

    # cleanup
    print("\n[4] 关闭服务器...")
    proc.kill(); proc.wait(timeout=10)
    print("  OK")

    # summary
    print("\n" + "=" * 50)
    print("测试汇总")
    print("=" * 50)
    for lvl, d in results.items():
        print(f"  并发 {lvl:2d}:  {d['ok']:3d} OK / {d['fail']:2d} FAIL  |  "
              f"延迟 avg={d['avg']:.3f}s  p50≈{d['avg']:.3f}s  |  "
              f"吞吐 {d['throughput']:.2f} req/s")

if __name__ == "__main__":
    main()
