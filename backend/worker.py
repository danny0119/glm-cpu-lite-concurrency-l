import json, os, subprocess, sys, threading, time
from pathlib import Path
from PIL import Image
from ultralytics import YOLO

if getattr(sys, 'frozen', False):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DETECTOR = ROOT / "models" / "weights" / "yolo-captcha-detector.pt"
OCR_WORKER = ROOT / "backend" / "ppocr_worker.py"
CROP_DIR = ROOT / "logs" / "crops"
YOLO_IMGSZ = int(os.environ.get("YOLO_IMGSZ", "448"))

from backend.evaluate import select_fixed3

# ── OCR 通信：通过 TCP 连接共享 ppocr_worker ──────────────────────────────
# 如果环境变量 PPOCR_TCP_PORT 已设，则用 TCP（共享模式）；
# 否则回退到旧版子进程模式（向后兼容）

_ocr_conn = None
_ocr_conn_lock = threading.Lock()

def get_ocr_connection(port: int):
    """获取（或创建）到共享 ppocr_worker 的 TCP 连接"""
    import socket
    global _ocr_conn
    with _ocr_conn_lock:
        if _ocr_conn is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(("127.0.0.1", port))
            sock.settimeout(30.0)
            _ocr_conn = sock
        return _ocr_conn

def ask_ocr(crop_paths, prompt_chars, timeout=10.0):
    port_str = os.environ.get("PPOCR_TCP_PORT", "")
    if port_str:
        port = int(port_str)
        conn = get_ocr_connection(port)
        payload = json.dumps({"paths": [str(p) for p in crop_paths], "prompt": list(prompt_chars)}, ensure_ascii=False) + "\n"
        conn.sendall(payload.encode())
        buf = b""
        start = time.perf_counter()
        while True:
            if time.perf_counter() - start > timeout:
                raise TimeoutError("OCR timeout")
            chunk = conn.recv(65536)
            if not chunk:
                raise RuntimeError("OCR worker disconnected")
            buf += chunk
            if b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                resp = json.loads(line.decode())
                if not resp.get("success"):
                    raise RuntimeError(resp.get("error", "OCR failed"))
                return list(resp.get("results", [])), resp
    else:
        # 旧版子进程模式（向后兼容）
        return _ask_ocr_subprocess(crop_paths, prompt_chars, timeout)

_ocr_proc = None

def _drain_stderr(proc):
    for line in proc.stderr:
        sys.stderr.write(line.decode(errors="replace"))

def _ask_ocr_subprocess(crop_paths, prompt_chars, timeout=10.0):
    global _ocr_proc
    if _ocr_proc is None or _ocr_proc.poll() is not None:
        _ocr_proc = subprocess.Popen(
            [sys.executable, "-u", str(OCR_WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(ROOT),
        )
        threading.Thread(target=_drain_stderr, args=(_ocr_proc,), daemon=True).start()
    proc = _ocr_proc
    payload = json.dumps({"paths": [str(p) for p in crop_paths], "prompt": list(prompt_chars)}, ensure_ascii=False) + "\n"
    proc.stdin.write(payload.encode())
    proc.stdin.flush()
    start = time.perf_counter()
    while True:
        if time.perf_counter() - start > timeout:
            raise TimeoutError("OCR timeout")
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("OCR worker exited")
        resp = json.loads(line.decode())
        if not resp.get("success"):
            raise RuntimeError(resp.get("error", "OCR failed"))
        return list(resp.get("results", [])), resp

def process(image_path, chars):
    total_t0 = time.perf_counter()
    image = Image.open(image_path).convert("RGB")

    t0 = time.perf_counter()
    result = detector.predict(source=image, imgsz=YOLO_IMGSZ, conf=0.15, iou=0.5, max_det=10, verbose=False)[0]
    yolo_ms = (time.perf_counter() - t0) * 1000

    raw_boxes, raw_confs = [], []
    if result.boxes is not None:
        for b in result.boxes:
            raw_boxes.append(tuple(float(x) for x in b.xyxy[0].tolist()))
            raw_confs.append(float(b.conf[0].item()))

    boxes, confs, reason = select_fixed3(raw_boxes, raw_confs, image.size)
    selected = sorted(zip(boxes, confs), key=lambda x: x[0][0])
    boxes = [x[0] for x in selected]

    CROP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = str(int(time.time() * 1000))
    crop_paths = []
    for idx, box in enumerate(boxes):
        x1, y1, x2, y2 = [int(round(v)) for v in box]
        crop = image.crop((max(0, x1), max(0, y1), min(image.width, x2), min(image.height, y2)))
        path = CROP_DIR / f"{Path(image_path).stem}_{stamp}_box{idx+1}.png"
        crop.save(path)
        crop_paths.append(path)

    t0 = time.perf_counter()
    ocr_rows, _ = ask_ocr(crop_paths, chars)
    ocr_ms = (time.perf_counter() - t0) * 1000

    raw_box_chars = [str(row.get("char", "")) for row in ocr_rows]
    box_chars = list(raw_box_chars)
    if len(box_chars) == len(chars):
        used, mapping = set(), []
        for ch in chars:
            for idx, bc in enumerate(box_chars):
                if idx not in used and bc == ch:
                    mapping.append(idx)
                    used.add(idx)
                    break
            else:
                mapping.append(-1)
        if -1 not in mapping:
            prompt_to_box = mapping
        else:
            prompt_to_box = list(range(len(box_chars)))
    else:
        prompt_to_box = list(range(len(box_chars)))

    img_w, img_h = image.size
    click_coords = []
    for pi, bi in enumerate(prompt_to_box):
        if bi >= len(boxes):
            continue
        b = boxes[bi]
        click_coords.append({
            "char": chars[pi] if pi < len(chars) else "",
            "nx": round(((b[0] + b[2]) / 2) / img_w, 4),
            "ny": round(((b[1] + b[3]) / 2) / img_h, 4),
        })

    total_ms = (time.perf_counter() - total_t0) * 1000
    scores = [float(row.get("score", 0.0) or 0.0) for row in ocr_rows]
    return {
        "success": True,
        "prompt": chars,
        "pred_text": "".join(box_chars),
        "confidence": round(sum(scores) / max(len(scores), 1), 3),
        "elapsed_ms": round(total_ms, 1),
        "yolo_ms": round(yolo_ms, 1),
        "ocr_ms": round(ocr_ms, 1),
        "click_coords": click_coords,
        "reason": reason,
    }

def run():
    global detector
    detector = YOLO(str(DETECTOR))
    # 预热：触发 YOLO 首次 JIT 编译（避免首次请求等待 ~1.5s）
    _ = detector.predict(source=Image.new("RGB", (YOLO_IMGSZ, YOLO_IMGSZ)), imgsz=YOLO_IMGSZ, conf=0.15, iou=0.5, max_det=1, verbose=False)
    # ── 就绪信号：告诉 server 本 worker 可以开始接收请求 ──
    sys.stdout.write("__READY__\n")
    sys.stdout.flush()

    for line in sys.stdin.buffer:
        req = json.loads(line.decode())
        try:
            result = process(req["image_path"], req["chars"])
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            import traceback; traceback.print_exc(file=sys.stderr)
            sys.stdout.write(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False) + "\n")
            sys.stdout.flush()

if __name__ == '__main__':
    run()
