"""
验证码极速网关 - 双端流水线架构
- 8 YOLO + 8 OCR 完美填满 16 物理核，彻底消灭串行等待
- 共享内存零拷贝传递切片，消灭序列化开销
"""
import os
import sys
import io
import base64
import time
import asyncio
import urllib.request
import multiprocessing as mp
import threading
from pathlib import Path
from contextlib import asynccontextmanager

import psutil
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

if getattr(sys, 'frozen', False):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).resolve().parent.parent
# 确保 backend 包在 sys.path 中
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

N_YOLO = 4
N_OCR = 8

# 队列：网关 -> YOLO (传原图 bytes，几十KB，Queue 足矣)
yolo_req_queues = [mp.Queue(maxsize=10) for _ in range(N_YOLO)]
# 队列：YOLO -> OCR (无界队列，YOLO永不阻塞)
ocr_req_queue = mp.Queue()
# 队列：OCR -> 网关
res_queue = mp.Queue()

pending_requests = {}
request_lock = threading.Lock()
request_counter = 0
round_robin_idx = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global workers_list
    workers_list = []

    from backend.worker import run_yolo_worker
    from backend.ppocr_worker import run_ocr_worker_direct

    print(f"[architect] 启动 {N_YOLO} YOLO 流水线 (Core 0-7)...")
    for i in range(N_YOLO):
        p = mp.Process(target=run_yolo_worker, args=(i, yolo_req_queues[i], ocr_req_queue), daemon=True)
        p.start()
        workers_list.append(p)
        time.sleep(0.5)

    print(f"[architect] 启动 {N_OCR} OCR 流水线 (Core 8-15, 错峰加载)...")
    for i in range(N_OCR):
        p = mp.Process(target=run_ocr_worker_direct, args=(8 + i, ocr_req_queue, res_queue), daemon=True)
        p.start()
        workers_list.append(p)
        time.sleep(3)  # 错峰3秒，避免8个OCR同时加载OOM

    threading.Thread(target=result_listener_thread, daemon=True).start()
    yield
    for p in workers_list:
        p.terminate()


def result_listener_thread():
    while True:
        res = res_queue.get()
        if not res:
            continue
        req_id = res.get("req_id")
        with request_lock:
            future = pending_requests.pop(req_id, None)
        if future and not future.done():
            future.get_loop().call_soon_threadsafe(future.set_result, res)


class CaptchaRequest(BaseModel):
    text: str
    image: str


class CaptchaUrlRequest(BaseModel):
    text: str
    url: str


app = FastAPI(lifespan=lifespan)

# CORS：允许油猴脚本跨域 fetch（GM_xmlhttpRequest 有连接数瓶颈，fetch 无此限制）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "workers": N_YOLO + N_OCR}


@app.post("/direct")
@app.post("/captcha_direct")
async def handle_direct(data: CaptchaRequest):
    global request_counter, round_robin_idx
    chars = "".join(ch for ch in data.text if "\u4e00" <= ch <= "\u9fff")[-3:]
    if not chars or not data.image:
        raise HTTPException(status_code=400, detail="missing text or image")

    img_bytes = base64.b64decode(data.image.split(",")[-1])
    if not img_bytes:
        raise HTTPException(status_code=400, detail="empty image")

    with request_lock:
        request_counter += 1
        req_id = request_counter
    future = asyncio.get_event_loop().create_future()
    with request_lock:
        pending_requests[req_id] = future

    payload = {"req_id": req_id, "img_bytes": img_bytes, "chars": list(chars)}

    try:
        target_q = yolo_req_queues[round_robin_idx % N_YOLO]
        round_robin_idx += 1
        target_q.put_nowait(payload)
    except Exception:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, target_q.put, payload)

    try:
        result = await asyncio.wait_for(future, timeout=15.0)
        return {"success": True, "result": result}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Processing timeout")


@app.post("/captcha_direct_url")
async def handle_direct_url(data: CaptchaUrlRequest):
    """接收图片 URL，下载后识别"""
    global request_counter, round_robin_idx
    chars = "".join(ch for ch in data.text if "\u4e00" <= ch <= "\u9fff")[-3:]
    if not chars or not data.text:
        print(f"[400] text='{data.text[:80]}' → no Chinese chars", flush=True)
        raise HTTPException(status_code=400, detail="missing text or url")
    if not data.url:
        print(f"[400] url='{data.url[:120]}' → empty url", flush=True)
        raise HTTPException(status_code=400, detail="missing text or url")

    loop = asyncio.get_event_loop()
    try:
        resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(data.url, timeout=15))
        img_bytes = resp.read()
    except Exception as e:
        print(f"[400] download failed: url='{data.url[:120]}' error={e}", flush=True)
        raise HTTPException(status_code=400, detail=f"failed to download image: {e}")

    if not img_bytes:
        raise HTTPException(status_code=400, detail="empty image from url")

    with request_lock:
        request_counter += 1
        req_id = request_counter
    future = asyncio.get_event_loop().create_future()
    with request_lock:
        pending_requests[req_id] = future

    try:
        await loop.run_in_executor(None, _dispatch_one, req_id, img_bytes, chars)
    except Exception:
        # _dispatch_one 内部已经处理异常并回调 future，
        # 这里无需额外操作
        pass

    try:
        result = await asyncio.wait_for(future, timeout=15.0)
        return {"success": True, "result": result}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Processing timeout")


class BatchCaptchaRequest(BaseModel):
    requests: list[CaptchaRequest]


def _dispatch_one(req_id: int, img_bytes: bytes, chars: list[str]) -> int:
    """同步dispatch单个请求到YOLO队列（在run_in_executor中执行）"""
    target_q = yolo_req_queues[req_id % N_YOLO]
    payload = {"req_id": req_id, "img_bytes": img_bytes, "chars": chars}
    target_q.put(payload)
    return req_id


@app.post("/batch_direct")
async def handle_batch_direct(data: BatchCaptchaRequest):
    """批量处理多窗口验证码：一次接收所有窗口的截图，并行识别后一起返回"""
    global request_counter

    n = len(data.requests)
    if n == 0:
        raise HTTPException(status_code=400, detail="empty batch")
    if n > 30:
        raise HTTPException(status_code=400, detail="batch too large, max 30")

    futures = {}
    loop = asyncio.get_event_loop()

    for item in data.requests:
        chars = "".join(ch for ch in item.text if "\u4e00" <= ch <= "\u9fff")[-3:]
        if not chars or not item.image:
            continue
        img_bytes = base64.b64decode(item.image.split(",")[-1])
        if not img_bytes:
            continue

        with request_lock:
            request_counter += 1
            req_id = request_counter
        future = loop.create_future()
        with request_lock:
            pending_requests[req_id] = future
        futures[req_id] = future

        # 异步dispatch（避免阻塞event loop）
        loop.run_in_executor(None, _dispatch_one, req_id, img_bytes, list(chars))

    if not futures:
        raise HTTPException(status_code=400, detail="no valid requests")

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*futures.values(), return_exceptions=True),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        # 清理超时的future
        with request_lock:
            for req_id in futures:
                pending_requests.pop(req_id, None)
        raise HTTPException(status_code=504, detail="Batch processing timeout")

    # 收集结果，保持原始顺序
    final_results = []
    for req_id, fut in futures.items():
        result = results[list(futures.keys()).index(req_id)]
        if isinstance(result, Exception):
            final_results.append({"req_id": req_id, "success": False, "error": str(result)})
        else:
            final_results.append({"req_id": req_id, "success": True, "result": result})

    return {"success": True, "count": len(final_results), "results": final_results}


def main():
    mp.freeze_support()
    uvicorn.run("backend.server:app", host="0.0.0.0", port=8888, log_level="info")


if __name__ == "__main__":
    main()
