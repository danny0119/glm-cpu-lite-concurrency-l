# GLM Coding Helper Lite

> 👨‍🚀 轻量级中文验证码自动识别服务  
> 基于 YOLOv8 (目标检测) + PP-OCRv5 (文字识别)  
> **支持多进程并发，开箱即用**

---

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 🎯 **中文验证码识别** | 自动定位+识别汉字验证码，返回点击坐标 |
| ⚡ **多 Worker 并发** | 根据 CPU 自动调整并发数，支持 8+ 标签页同时操作 |
| 🔧 **自动配置** | 首次启动自动检测 CPU，推荐最佳参数 |
| 📦 **单文件部署** | 可打包为 exe，目标电脑无需 Python |
| 🖱 **油猴自动点击** | 配合 Tampermonkey 脚本自动识别+点击 |

---

## 🚀 快速开始

### 方式一：便携版（推荐）

1. 下载 `GLM-Lite-Portable.zip`
2. 解压到任意目录
3. 双击 **`GLM-Lite.exe`**
4. 浏览器打开 `http://localhost:8888/health` 检查状态
5. 🎉 完成！

> 目标电脑不需要安装 Python、C++ 编译器或任何依赖

### 方式二：源码运行

需要 Python 3.10+ 和 C++ 编译器（MSVC Build Tools）：

```batch
# 1. 配置环境
setup.ps1          # 自动创建 venv + 安装依赖

# 2. （可选）自定义参数
config.ps1         # 交互式配置
config.ps1 -Auto   # 自动检测并保存

# 3. 启动
start.bat
```

### 方式三：油猴脚本

1. 浏览器安装 [Tampermonkey](https://www.tampermonkey.net/)
2. 菜单 → 添加新脚本 → 粘贴 `glm-lite.user.js` 内容 → 保存
3. 确认后端已启动（方式一或二）
4. 🔥 自动生效！遇到验证码页面自动识别+点击

> **快捷键**: `Ctrl+Shift+S` 手动触发识别

---

## 📋 参数配置

| 参数 | 默认 | 推荐 | 说明 |
|------|------|------|------|
| `workers` | 自动 | `cpu/2 - 2` | YOLO 识别进程数 |
| `ocr_workers` | 3 | `cpu/8` | OCR 识别进程数（共享池） |
| `port` | 8888 | 8888 | HTTP 服务端口 |
| `stagger_delay` | 3 | 3 | 每个 Worker 启动间隔(秒) |
| `yolo_imgsz` | 448 | 448 | YOLO 输入尺寸 |

运行 `config.ps1` 交互式调整：

```text
  CPU 逻辑核心数: 12 (6C/12T)

  参数                推荐值   默认值   说明
  ─────────────────────────────────────────────
  YOLO Workers       4        3        并发识别验证码的进程数
  OCR Workers        2        3        OCR 识别子进程数
  端口               8888     8888     后端 HTTP 服务端口
  ...

  是否自定义参数? (Y/n)
```

---

## 🏗 打包为单文件 exe

在**开发机**上（已装好所有依赖）运行：

```batch
build_package.ps1
```

输出：`dist/GLM-Lite/` 文件夹（约 3~5GB）

> ⚠️ 大是因为内置了 paddlepaddle + torch 完整运行时
>
> 将 `dist/GLM-Lite/` 整个目录压缩后发给别人，双击 `GLM-Lite.exe` 即用

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

## 📂 项目结构

```
GLM-Coding-Helper-Lite/
├── glm-lite.py              ← 统一入口
├── start.bat                ← 启动脚本
├── config.ps1               ← 配置向导
├── build_package.ps1        ← 打包工具
├── setup.ps1                ← 环境配置
├── glm-lite.user.js         ← 油猴脚本
│
├── backend/
│   ├── server.py            ← HTTP 服务 + Worker 管理
│   ├── worker.py            ← YOLO 识别 Worker
│   ├── ppocr_worker.py      ← OCR 识别 Worker（共享池）
│   └── evaluate.py          ← 坐标选择策略
│
├── models/weights/
│   └── yolo-captcha-detector.pt  ← YOLO 模型
│
└── config.json              ← 用户配置（自动生成）
```

---

## 🧠 架构

```
┌──────────────────────────────────────────────────┐
│                  浏览器 (油猴脚本)                  │
│          HTTP POST /captcha_direct                │
└──────────┬───────────────────────────┬───────────┘
           │                           │
           ▼                           ▼
┌──────────────────────┐   ┌──────────────────────┐
│   server.py          │   │  ThreadingHTTPServer  │
│   WorkerPool 轮询    │   │   并发处理 HTTP       │
├──────────┬───────────┤   └──────────────────────┘
           │
    ┌──────┴──────┐
    │  TCP 共享    │  ← YOLO Worker x N
    │  OCR 池     │
    └──────┬──────┘
           │
    ┌──────┴──────┐
    │ ppocr_worker│  ← OCR 子进程 x N
    │ (TCP 服务)  │
    └─────────────┘
```

---

## ⚠️ 常见问题

### 1. `setup.ps1` 报编码错误

```powershell
# 手动指定 UTF-8 运行
powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
```

### 2. 首次 OCR 慢

首次运行会自动下载 PP-OCR 模型（~100MB），之后会缓存在 `.paddlex_cache_cpu/`。

### 3. C++ 编译错误

PaddleOCR 需要 MSVC Build Tools：

```batch
# 方式一：安装 Visual Studio Build Tools
choco install visualstudio2022buildtools

# 方式二：使用预编译包
# 直接下载便携版（打包好的 exe，无需编译）
```

### 4. 端口被占用

修改 `config.json` 中的 `port` 值，或运行 `config.ps1` 重新配置。

### 5. 目标电脑性能建议

| CPU | 推荐 Workers | 标签页上限 | 延迟 |
|-----|-------------|-----------|------|
| 4 核 (i3) | 2 | 4~6 | ~2s |
| 6 核 (i5) | 4 | 8~12 | ~1.5s |
| 8 核 (i7) | 6 | 12~16 | ~1s |
| 12+ 核 | 8~12 | 16~24 | ~0.8s |

---

## 📄 License

MIT
