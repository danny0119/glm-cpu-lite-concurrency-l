# GLM Coding Helper Lite — 零争用架构版

> 👨‍🚀 轻量级中文验证码自动识别服务  
> 基于 YOLOv8 (目标检测) + PP-OCRv5 (文字识别)  
> **零争用闭环架构 + fetch优先直连，12 窗口并发 CPU 仅 10%~30%**

---

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 🎯 **中文验证码识别** | 自动定位+识别汉字验证码，返回点击坐标 |
| ⚡ **零争用闭环** | 每个 Worker 独享 YOLO+Paddle 完整管线 + 专属队列，彻底消灭锁争用 |
| 🔗 **核族绑定** | Worker 绑定物理核心族，OMP 线程不跨核乱跑，L1/L2 缓存亲和 |
| 🔧 **全自动调优** | 根据 CPU 物理核数自动计算最优 Worker 数 (核数/4) |
| 🖱 **油猴自动点击** | 配合 Tampermonkey 脚本自动识别+点击 |
| 🌐 **fetch 优先直连** | 油猴脚本绕过 `GM_xmlhttpRequest` 排队瓶颈，12 窗口并发零阻塞 |
| 🔌 **懒加载 + 错峰启动** | Worker 逐秒启动，模型预热完成后自动切换就绪状态 |

---

## 🚀 快速开始

### 方式一：源码运行（推荐，免编译）

需要 Python 3.10+：

```batch
# 1. 一键安装依赖（自动创建 venv，纯二进制包，无需 C++ 编译器）
setup.bat

# 2. 启动服务
start.bat
```

> 如果 `start.bat` 报"端口被占用"，说明已有后端实例在运行，关掉旧的再试。

### 方式二：便携版

在开发机上打包：

```batch
build_package.ps1
```

输出 `dist/GLM-Lite/` 文件夹，复制到新电脑双击 `GLM-Lite.exe` 即用，无需任何依赖。

### 方式三：油猴脚本

1. 浏览器安装 [Tampermonkey](https://www.tampermonkey.net/)
2. 菜单 → 添加新脚本 → 粘贴 `glm-lite.user.js` 内容 → 保存
3. 确认后端已启动
4. 🔥 自动生效！遇到验证码页面自动识别+点击

> **快捷键**: `Ctrl+Shift+S` 手动触发识别

---

## 📋 参数配置

| 参数 | 默认 | 说明 |
|------|------|------|
| `workers` | `CPU核数/4` | 闭环 Worker 数（每个含 YOLO+OCR 完整管线） |
| `port` | 8888 | HTTP 服务端口 |
| `stagger_delay` | 3 | 每个 Worker 启动间隔(秒)，错峰加载模型 |
| `yolo_imgsz` | 448 | YOLO 输入尺寸 |

运行 `config.ps1` 交互式调整，或直接编辑 `config.json`。

---

## 📊 性能实测

| 场景 | 指标 |
|------|------|
| **12 个浏览器窗口同时触发** | 仅 ~1 个窗口空闲，其余 11 个并发处理 |
| **CPU 占用率** | 10%~30%（得益于核族绑定 + 零争用队列） |
| **单次识别延迟** | ~0.8s~1.5s（取决于 CPU 核数） |
| **Worker 满载率** | 接近 100%，无空闲等待 |

> 对比旧架构：之前 12 个窗口会**串行排队**，逐个处理，CPU 空等严重。

---

## 🔌 API 接口

### `POST /captcha_direct`

```json
{
  "text": "请依次点击：口、木、日",
  "image": "data:image/png;base64,..."
}
```

### `POST /captcha_direct_url`

```json
{
  "text": "请依次点击：口、木、日",
  "url": "https://example.com/captcha.png"
}
```

### 响应

```json
{
  "success": true,
  "result": {
    "prompt": ["口", "木", "日"],
    "pred_text": "口木日",
    "confidence": 0.95,
    "click_coords": [
      {"char": "口", "nx": 0.25, "ny": 0.30},
      {"char": "木", "nx": 0.52, "ny": 0.45},
      {"char": "日", "nx": 0.78, "ny": 0.60}
    ],
    "elapsed_ms": 1250
  }
}
```

### `GET /health`

```json
{
  "status": "ok",
  "workers": 4
}
```

---

## 🧠 架构设计

### 设计哲学

> **在 Python 的 multiprocessing 环境中，跨进程传递图像 = 自废武功。**
>
> 因此每个 Worker 内闭环运行 YOLO 检测 + PaddleOCR 识别全过程，绝不将中间结果（裁剪图/特征图）传出进程。

### 架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                        FastAPI 网关 (asyncio)                        │
│                       端口 8888 | Uvicorn                           │
│                                                                      │
│   接收 POST /captcha_direct /captcha_direct_url                      │
│       ↓                                                              │
│   轮询分发器: req_queue[i % N]                                       │
│   put_nowait 非阻塞投放（满队列降级 run_in_executor）                  │
│       ↓                                                              │
│   result_listener_thread  ← res_queue (唯一共享队列)                   │
│       ↓ 通过 call_soon_threadsafe 写回 Future                         │
└───────┬──────────┬──────────┬──────────────────┬──────────────────────┘
        │          │          │                  │
   ┌────┴──┐  ┌────┴──┐  ┌────┴──┐        ┌────┴──┐
   │Queue 0│  │Queue 1│  │Queue 2│  ...    │Queue N│  ← 每个 Worker 专属输入队列
   └───┬───┘  └───┬───┘  └───┬───┘        └───┬───┘
       │          │          │                  │
   ┌───▼──────────▼──────────▼──────────────────▼───┐
   │             零争用闭环 Worker 集群               │
   │   每个 Worker 内部：                              │
   │   ┌─────────────────────────────────┐          │
   │   │ OMP_NUM_THREADS=4              │          │
   │   │                                │          │
   │   │  req_queue.get()  ← 专属无锁   │          │
   │   │       ↓                        │          │
   │   │  YOLOv8 目标检测               │          │
   │   │       ↓                        │          │
   │   │  PP-OCRv5 文字识别 (单进程内)   │          │
   │   │       ↓                        │          │
   │   │  select_fixed3 坐标策略         │          │
   │   │       ↓                        │          │
   │   │  res_queue.put()               │          │
   │   └─────────────────────────────────┘          │
   └────────────────────────────────────────────────┘
```

### 油猴端 fetch 优先策略

```
浏览器端                          FastAPI 网关
   │                                 │
   │  尝试 fetch() 下载图片            │
   │  (无并发限制, 不经过后台页)        │
   │      ↓ 成功                       │
   │  直接 POST /captcha_direct        │
   │  (原生 fetch, 12窗口同步发送)      │
   │      ↓                            │
   │  如果是 captcha_direct_url 路径    │
   │  → fetch() 从 URL 拉取图片         │
   │  → base64 编码后 POST 识别         │
   │                                 │
   │  (仅当 fetch() 失败时回退          │
   │   GM_xmlhttpRequest)             │
   │                                 │
```

### 核族绑定策略

> 16 物理核满血案例

```
Worker 0  → Core 0,1,2,3  → OMP 4 线程 ←→ L1/L2 缓存亲和
Worker 1  → Core 4,5,6,7  → OMP 4 线程
Worker 2  → Core 8,9,10,11 → OMP 4 线程
Worker 3  → Core 12,13,14,15 → OMP 4 线程

16 核全填满，零跨 Worker 争用
```

### 优化历程

| 版本 | 核心改动 | 效果 |
|------|---------|------|
| v1 (原版) | YOLO 进程 + OCR TCP 分离 | 跨进程竞态、队列争用 |
| v2 | 零争用闭环 Worker，专属 mp.Queue | Worker 独立运行，队列无竞争 |
| v3 (当前) | 油猴脚本 **fetch 优先**，直连后端 | 12 窗口并发零阻塞，CPU 10%~30% |

---

## 📂 项目结构

```
GLM-Coding-Helper-Lite/
├── start.bat                ← 启动脚本（含端口冲突检测）
├── setup.bat / setup.py     ← 一键安装依赖（无 C++ 编译）
├── setup.ps1                ← 环境配置（PowerShell 备用）
├── config.ps1               ← 配置向导
├── build_package.ps1        ← 打包为单文件 exe
├── glm-lite.py              ← 统一入口
├── glm-lite.user.js         ← 油猴脚本（fetch 优先策略）
├── glm-coding-helper.user.js← 备用油猴脚本
│
├── backend/
│   ├── server.py            ← 零争用网关 (FastAPI + 轮询分发)
│   ├── worker.py            ← 闭环 Worker (YOLO + PaddleOCR 一体)
│   ├── evaluate.py          ← 坐标选择策略
│   └── ppocr_worker.py      ← (已废弃，保留兼容)
│
├── models/weights/
│   └── yolo-captcha-detector.pt  ← YOLO 模型
│
├── official_models/
│   ├── PP-OCRv5_server_rec_safetensors/  ← OCR 模型（主力）
│   └── PP-OCRv5_mobile_rec_safetensors/  ← OCR 模型（备用）
│
└── config.json              ← 用户配置（自动生成）
```

---

## ⚠️ 常见问题

### 1. 编码格式错误（新电脑上 setup.ps1 报错）

改用 `setup.bat` (它会调用 `setup.py`，纯 Python 无编码问题)：

```batch
setup.bat
```

### 2. C++ 编译错误

已强制使用预编译包，`setup.py` 内部使用 `--only-binary :all:` 标志：

```python
# pip install --only-binary :all: -r requirements.txt
```

**不需要安装 Visual Studio Build Tools。**

### 3. 首次启动慢

首次运行会自动加载 PP-OCR 模型（~100MB），默认 4 个 Worker 错峰 3 秒启动，约 12 秒后所有 Worker 就绪。

模型缓存目录：`.paddlex_cache_cpu/`

### 4. 端口被占用

```
[FAIL] 端口 8888 已被占用！
```

关掉已有的命令行窗口，再重新启动。如果找不到旧窗口，用任务管理器杀掉 `python.exe` 或 `GLM-Lite.exe`。

### 5. 关于 OMP 警告

启动日志中可能出现：

```
WARNING: OMP_NUM_THREADS set to 4, not 1. ...
PLEASE USE OMP_NUM_THREADS WISELY.
```

这是 PaddlePaddle 的已知提示，在零争用架构中 **OMP=4 是正确的设计**——每个 Worker 绑定独立的 4 核族，OMP 线程在核族内运转，不跨 Worker 干扰。无需处理。

### 6. 目标电脑性能建议

| CPU | Workers | 并发上限 | 单次延迟 |
|-----|---------|---------|---------|
| 4 核 | 1 | 2~3 | ~1.5s |
| 6 核 | 1~2 | 3~6 | ~1.2s |
| 8 核 | 2 | 4~8 | ~1.0s |
| 16 核 | 4 | 8~16 | ~0.8s |
| 24 核 | 6 | 12~24 | ~0.7s |

---

## 📄 License

MIT
