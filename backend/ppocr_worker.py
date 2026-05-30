from __future__ import annotations

import json
import math
import os
import queue
import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path
from threading import Lock

import numpy as np


if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = "PP-OCRv5_server_rec"
ENGINE = "paddle_dynamic"
WORKERS = int(os.environ.get("WORKERS", "3"))
CONSTRAINED_DECODE = True


def configure_env() -> None:
    # 不要乱改 USERPROFILE，避免 Windows 子系统异常
    os.environ.setdefault("HOME", str(ROOT))
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    # paddlex 模型缓存 → 指向打包目录，避免重新下载
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(ROOT))
    # 跳过模型源连通性检测
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def first_cjk(text: str) -> str:
    return next((ch for ch in text if "\u4e00" <= ch <= "\u9fff"), "")


def predict_with_candidate_scores(recognizer, path: str, prompt: list[str]) -> dict:
    predictor = recognizer.paddlex_predictor
    raw_imgs = predictor.pre_tfs["Read"](imgs=[path])
    batch_imgs = predictor.pre_tfs["ReisizeNorm"](imgs=raw_imgs)
    x = predictor.pre_tfs["ToBatch"](imgs=batch_imgs)
    batch_preds = predictor.runner(x=x)
    probs = np.array(batch_preds[0] if isinstance(batch_preds, (list, tuple)) else batch_preds)
    texts, scores = predictor.post_op(batch_preds)

    candidate_scores: dict[str, float] = {}
    for char in prompt:
        idx = predictor.post_op.dict.get(char)
        if idx is None:
            candidate_scores[char] = 0.0
        else:
            candidate_scores[char] = float(probs[0, :, idx].max())

    best_char = max(candidate_scores, key=candidate_scores.get) if candidate_scores else first_cjk(str(texts[0]))
    return {
        "text": str(texts[0]),
        "char": best_char,
        "score": float(candidate_scores.get(best_char, scores[0] if scores else 0.0) or 0.0),
        "ocr_text": str(texts[0]),
        "ocr_score": float(scores[0] if scores else 0.0),
        "candidate_scores": candidate_scores,
    }


def recognizer_worker(req_q: Queue, resp_q: Queue, worker_id: int, model_name: str) -> None:
    try:
        configure_env()
        from paddleocr import TextRecognition

        recognizer = TextRecognition(model_name=model_name, device="cpu", engine=ENGINE)
        resp_q.put({"type": "ready", "worker": worker_id})
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        try:
            resp_q.put({"type": "error", "worker": worker_id, "error": str(exc), "traceback": tb})
        except Exception:
            pass
        sys.stderr.write(f"[worker-{worker_id}] INIT FAILED:\n{tb}\n")
        sys.stderr.flush()
        return
    while True:
        item = req_q.get()
        if item is None:
            break
        req_id, idx, path, prompt = item
        try:
            started = time.perf_counter()
            if CONSTRAINED_DECODE and prompt:
                row = predict_with_candidate_scores(recognizer, str(path), list(prompt))
            else:
                result = recognizer.predict(str(path))
                obj = result[0] if result else {}
                text = str(obj.get("rec_text", "")) if isinstance(obj, dict) else str(obj)
                score = float(obj.get("rec_score", 0.0) or 0.0) if isinstance(obj, dict) else 0.0
                row = {"text": text, "char": first_cjk(text), "score": score}
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            resp_q.put(
                {
                    "type": "result",
                    "req_id": req_id,
                    "idx": idx,
                    **row,
                    "elapsed_ms": elapsed_ms,
                    "worker": worker_id,
                }
            )
        except Exception as exc:
            resp_q.put({"type": "result", "req_id": req_id, "idx": idx, "error": str(exc), "worker": worker_id})
    recognizer.close()


def repair_to_prompt(pred_chars: list[str], prompt_chars: list[str]) -> tuple[list[str], bool]:
    repaired = list(pred_chars)
    in_prompt = [ch for ch in repaired if ch in prompt_chars]
    missing = [ch for ch in prompt_chars if ch not in in_prompt]
    bad_idxs = [idx for idx, ch in enumerate(repaired) if ch not in prompt_chars]
    if len(missing) == len(bad_idxs) == 1:
        repaired[bad_idxs[0]] = missing[0]
    return repaired, repaired != pred_chars


def can_map_prompt(rows: list[dict], prompt: list[str]) -> bool:
    if not prompt:
        return True
    chars = [str(row.get("char", "")) for row in rows]
    repaired, _ = repair_to_prompt(chars, prompt)
    used: set[int] = set()
    for prompt_ch in prompt:
        found = None
        for idx, box_ch in enumerate(repaired):
            if idx not in used and box_ch == prompt_ch:
                found = idx
                break
        if found is None:
            return False
        used.add(found)
    return True


def assign_prompt_globally(rows: list[dict], prompt: list[str]) -> list[dict]:
    if len(rows) != len(prompt):
        return rows

    best_perm: tuple[str, ...] | None = None
    best_score = -float("inf")

    def permutations(items: list[str]):
        if len(items) <= 1:
            yield tuple(items)
            return
        for idx, item in enumerate(items):
            rest = items[:idx] + items[idx + 1 :]
            for suffix in permutations(rest):
                yield (item,) + suffix

    for perm in permutations(list(prompt)):
        score = 0.0
        for row, char in zip(rows, perm):
            candidate_scores = row.get("candidate_scores") or {}
            prob = float(candidate_scores.get(char, 0.0) or 0.0)
            score += math.log(max(prob, 1e-12))
        if score > best_score:
            best_score = score
            best_perm = perm

    if best_perm is None:
        return rows

    assigned = []
    for row, char in zip(rows, best_perm):
        updated = dict(row)
        updated["raw_char"] = updated.get("char", "")
        updated["char"] = char
        updated["score"] = float((updated.get("candidate_scores") or {}).get(char, updated.get("score", 0.0)) or 0.0)
        assigned.append(updated)
    return assigned


# ── 单进程 OCR 池（PyInstaller 兼容，避免 multiprocessing 加载 DLL 失败） ──
class DirectOcrPool:
    """直接在当前进程加载 OCR 模型，适用于 workers=1 场景"""
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._lock = threading.Lock()
        configure_env()
        from paddleocr import TextRecognition
        self.recognizer = TextRecognition(model_name=model_name, device="cpu", engine=ENGINE)

    def predict(self, req_id: int, paths: list[str], prompt: list[str], timeout: float = 30.0) -> list[dict]:
        with self._lock:
            return self._predict_unlocked(req_id, paths, prompt, timeout)

    def _predict_unlocked(self, req_id: int, paths: list[str], prompt: list[str], timeout: float = 30.0) -> list[dict]:
        results: list[dict] = []
        for idx, path in enumerate(paths):
            try:
                started = time.perf_counter()
                if CONSTRAINED_DECODE and prompt:
                    row = predict_with_candidate_scores(self.recognizer, str(path), list(prompt))
                else:
                    result = self.recognizer.predict(str(path))
                    obj = result[0] if result else {}
                    text = str(obj.get("rec_text", "")) if isinstance(obj, dict) else str(obj)
                    score = float(obj.get("rec_score", 0.0) or 0.0) if isinstance(obj, dict) else 0.0
                    row = {"text": text, "char": first_cjk(text), "score": score}
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                row.update({"req_id": req_id, "idx": idx, "elapsed_ms": elapsed_ms, "worker": 0})
                results.append(row)
            except Exception as exc:
                results.append({"req_id": req_id, "idx": idx, "error": str(exc), "worker": 0})
        return results

    def close(self) -> None:
        if hasattr(self, "recognizer"):
            try:
                self.recognizer.close()
            except Exception:
                pass


class CpuOcrPool:
    def __init__(self, workers: int, model_name: str) -> None:
        self.model_name = model_name
        self.req_q: Queue = Queue()
        self.resp_q: Queue = Queue()
        self.procs = [
            Process(target=recognizer_worker, args=(self.req_q, self.resp_q, idx, model_name))
            for idx in range(workers)
        ]
        for i, proc in enumerate(self.procs):
            proc.start()
            if i < len(self.procs) - 1:
                time.sleep(5.0)  # 错峰启动，避免内存撑爆
        ready = 0
        deadline = time.perf_counter() + 120.0
        while ready < workers:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                alive = sum(1 for p in self.procs if p.is_alive())
                raise RuntimeError(
                    f"Worker init timeout (workers={workers}, alive={alive}). "
                    f"Workers may have crashed silently. "
                    f"Check the worker's stderr for details."
                )
            try:
                msg = self.resp_q.get(timeout=min(remaining, 5.0))
            except queue.Empty:
                continue
            if msg.get("type") == "ready":
                ready += 1
            elif msg.get("type") == "error":
                raise RuntimeError(
                    f"Worker {msg.get('worker')} init failed:\n"
                    f"error: {msg.get('error')}\n"
                    f"traceback:\n{msg.get('traceback')}"
                )

    def predict(self, req_id: int, paths: list[str], prompt: list[str], timeout: float = 30.0) -> list[dict]:
        for idx, path in enumerate(paths):
            self.req_q.put((req_id, idx, path, prompt))

        deadline = time.perf_counter() + timeout
        results: list[dict] = []
        while len(results) < len(paths):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("PP-OCR CPU pool timeout")
            try:
                msg = self.resp_q.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("PP-OCR CPU pool timeout") from exc
            if msg.get("type") == "result" and msg.get("req_id") == req_id:
                results.append(msg)

        results.sort(key=lambda item: int(item["idx"]))
        for item in results:
            if item.get("error"):
                raise RuntimeError(str(item["error"]))
        return [
            {
                "text": str(item.get("text", "")),
                "char": str(item.get("char", "")),
                "score": float(item.get("score", 0.0) or 0.0),
                "ocr_text": str(item.get("ocr_text", item.get("text", ""))),
                "ocr_score": float(item.get("ocr_score", item.get("score", 0.0)) or 0.0),
                "candidate_scores": item.get("candidate_scores", {}),
                "elapsed_ms": round(float(item.get("elapsed_ms", 0.0) or 0.0), 1),
                "worker": int(item.get("worker", -1)),
            }
            for item in results
        ]

    def close(self) -> None:
        for _ in self.procs:
            self.req_q.put(None)
        for proc in self.procs:
            proc.join(timeout=5)


def serve_stdin(pool: CpuOcrPool) -> int:
    """标准 stdin/stdout 模式（向后兼容）"""
    req_id = 0
    for raw_line in sys.stdin.buffer:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        req_id = _process_one(pool, req_id, line)
    return 0


def _process_one(pool: CpuOcrPool, req_id: int, line: str) -> int:
    """处理一条 OCR 请求并写入 stdout（或 socket）"""
    try:
        req = json.loads(line)
        req_id += 1
        paths = [str(path) for path in (req.get("paths") or [])]
        prompt = list(req.get("prompt") or req.get("chars") or [])
        rows = pool.predict(req_id, paths, prompt)
        if CONSTRAINED_DECODE and prompt and len(rows) == len(prompt):
            rows = assign_prompt_globally(rows, prompt)
        resp = json.dumps({"success": True, "results": rows, "model": MODEL_NAME}, ensure_ascii=False) + "\n"
        sys.stdout.write(resp)
        sys.stdout.flush()
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        resp = json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False) + "\n"
        sys.stdout.write(resp)
        sys.stdout.flush()
    return req_id


def serve_tcp(pool: CpuOcrPool) -> int:
    """TCP 服务器模式 — 所有 YOLO Worker 共享一个 OCR 进程"""
    import socket
    import threading

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    port = server.getsockname()[1]

    # 把端口号写入 stderr，server.py 读取
    sys.stderr.write(f"[ppocr-tcp] PORT={port}\n")
    sys.stderr.flush()

    stop_event = threading.Event()

    # 每个线程的请求计数器（线程安全）
    _req_counter = 0
    _counter_lock = threading.Lock()

    def handle(conn: socket.socket):
        nonlocal _req_counter
        f = conn.makefile("rw", buffering=1, encoding="utf-8", errors="replace")
        local_req_id = 0
        try:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                with _counter_lock:
                    _req_counter += 1
                    local_req_id = _req_counter
                try:
                    req = json.loads(line)
                    paths = [str(p) for p in (req.get("paths") or [])]
                    prompt = list(req.get("prompt") or req.get("chars") or [])
                    rows = pool.predict(local_req_id, paths, prompt)
                    if CONSTRAINED_DECODE and prompt and len(rows) == len(prompt):
                        rows = assign_prompt_globally(rows, prompt)
                    f.write(json.dumps({"success": True, "results": rows, "model": MODEL_NAME}, ensure_ascii=False) + "\n")
                    f.flush()
                except Exception as exc:
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                    f.write(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False) + "\n")
                    f.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            f.close()
            conn.close()

    def accept_loop():
        server.settimeout(1.0)
        while not stop_event.is_set():
            try:
                conn, addr = server.accept()
                t = threading.Thread(target=handle, args=(conn,), daemon=True)
                t.start()
            except socket.timeout:
                continue
        server.close()

    acceptor = threading.Thread(target=accept_loop, daemon=True)
    acceptor.start()

    # 主线程等待停止信号
    try:
        while True:
            stop_event.wait(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        acceptor.join(timeout=3)
        pool.close()
    return 0


def main() -> int:
    configure_env()
    # 文件日志：捕获所有子进程输出
    log_file = Path(ROOT).parent / "ocr_worker.log"
    try:
        _log = open(log_file, "a", encoding="utf-8")
    except Exception:
        _log = None
    if _log:
        _log.write(f"\n--- OCR Worker started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        _log.write(f"frozen={getattr(sys, 'frozen', False)}, args={sys.argv}\n")
        _log.flush()
    try:
        if getattr(sys, "frozen", False):
            pool = DirectOcrPool(MODEL_NAME)
        else:
            pool = CpuOcrPool(WORKERS, MODEL_NAME)
        if "--tcp" in sys.argv[1:]:
            ret = serve_tcp(pool)
        else:
            ret = serve_stdin(pool)
        pool.close()
        if _log:
            _log.write(f"OCR Worker exited with code {ret}\n")
        return ret
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        if _log:
            _log.write(f"OCR Worker CRASHED:\n{tb}\n")
            _log.flush()
        return 1
    finally:
        if _log:
            _log.close()


if __name__ == "__main__":
    raise SystemExit(main())
