#!/usr/bin/env python3
"""
GLM Coding Helper Lite — 一键安装脚本
- 创建 venv 虚拟环境
- 用 --only-binary :all: 安装依赖，避免 MSVC 编译
- 预缓存 OCR 模型（避免首次启动下载）
- 自动检测 Python 版本兼容性
作者: AtomCode 生成
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MIN_PYTHON = (3, 9)
MAX_PYTHON = (3, 11)  # paddlepaddle 对 3.12+ 无预编译 wheel
RECOMMENDED = "3.10"


def log(msg: str, ok: bool = True):
    icon = "[OK]" if ok else "[FAIL]"
    print(f"  {icon} {msg}")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """运行命令并返回结果"""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def check_python() -> Path | None:
    """找到兼容的 Python 可执行文件，优先 venv 内"""
    # 1. 检查 venv 内 Python
    venv_py = ROOT / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        ver = run([str(venv_py), "--version"])
        if ver.returncode == 0:
            log(f"venv Python: {ver.stdout.strip()}")
            return venv_py

    # 2. 检查系统 Python
    candidates = ["python", "python3", "py"]
    for cmd in candidates:
        proc = run([cmd, "--version"])
        if proc.returncode == 0:
            ver_str = proc.stdout.strip() or proc.stderr.strip()
            # 解析版本号 "Python 3.10.11"
            parts = ver_str.replace("Python ", "").split(".")
            if len(parts) >= 2:
                major, minor = int(parts[0]), int(parts[1])
                ver_tuple = (major, minor)
                if MIN_PYTHON <= ver_tuple <= MAX_PYTHON:
                    py_path = run(["where", cmd] if sys.platform == "win32" else ["which", cmd])
                    if py_path.returncode == 0:
                        path = py_path.stdout.strip().split("\n")[0].strip()
                        log(f"系统 Python {major}.{minor}: {path}")
                        return Path(path)
                else:
                    log(f"Python {major}.{minor} 不兼容（需要 {MIN_PYTHON[0]}.{MIN_PYTHON[1]}~{MAX_PYTHON[0]}.{MAX_PYTHON[1]}）", ok=False)

    return None


def create_venv(python_path: Path) -> Path:
    """创建虚拟环境"""
    venv_dir = ROOT / "venv"
    if (venv_dir / "Scripts" / "python.exe").exists():
        log("虚拟环境已存在")
        return venv_dir / "Scripts" / "python.exe"

    print("\n[2/5] 创建虚拟环境...")
    proc = run([str(python_path), "-m", "venv", str(venv_dir)])
    if proc.returncode != 0:
        print(f"    创建失败: {proc.stderr}")
        sys.exit(1)

    venv_py = venv_dir / "Scripts" / "python.exe"
    if not venv_py.exists():
        print("    创建失败: 找不到 python.exe")
        sys.exit(1)

    ver = run([str(venv_py), "--version"])
    log(f"venv: {ver.stdout.strip()}  @ {venv_dir}")
    return venv_py


def upgrade_pip(venv_py: Path):
    """升级 pip"""
    print("\n[3/5] 升级 pip...")
    proc = run([str(venv_py), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    if proc.returncode == 0:
        log("pip 已升级")
    else:
        log(f"pip 升级失败（可忽略）: {proc.stderr.strip()[:100]}", ok=False)


def install_deps(venv_py: Path):
    """用 --only-binary :all: 安装依赖"""
    print("\n[4/5] 安装依赖（纯二进制，无需编译环境）...")

    deps = [
        "numpy>=1.21,<2",
        "pillow>=10.0",
        "requests>=2.28",
        "opencv-python",
        "paddlepaddle>=2.6,<3",
        "paddleocr>=2.8",
        "ultralytics>=8.0",
        "fastapi>=0.100",
        "uvicorn>=0.22",
        "psutil>=5.9",
    ]

    for dep in deps:
        name = dep.split(">=")[0].split("<")[0].split("==")[0]
        print(f"  {name}... ", end="", flush=True)
        proc = run(
            [str(venv_py), "-m", "pip", "install", dep, "--only-binary", ":all:"],
        )
        if proc.returncode == 0:
            print("[OK]")
        else:
            # 回退：不用 --only-binary
            print(f"[重试无限制]", end=" ", flush=True)
            proc2 = run([str(venv_py), "-m", "pip", "install", dep, "--no-deps"])
            if proc2.returncode == 0:
                print("[OK]")
            else:
                print(f"[FAIL] {proc.stderr.strip()[-80:]}")


def cache_model(venv_py: Path):
    """预缓存 OCR 模型"""
    print("\n[5/5] 预缓存 OCR 模型（仅首次需要）...")

    cache_dir = ROOT / ".paddlex_cache_cpu" / "official_models"
    if cache_dir.exists() and any(cache_dir.iterdir()):
        log(f"模型已缓存: {cache_dir}")
        return

    # 设置环境变量让 paddleocr 把模型下载到项目目录
    env = os.environ.copy()
    env["PADDLE_PDX_CACHE_HOME"] = str(ROOT)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    script = """
import os
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", r"{root}")
try:
    from paddleocr import TextRecognition
    tr = TextRecognition(model_name="PP-OCRv5_server_rec", device="cpu", engine="paddle_dynamic")
    tr.close()
    print("[OK] 模型下载并缓存完成")
except Exception as e:
    print(f"[WARN] 模型预缓存失败: {{e}}")
    print("[WARN] 服务启动后会自动下载")
""".format(root=ROOT)

    proc = run([str(venv_py), "-c", script], env=env)
    print(f"  {proc.stdout.strip()}")
    if proc.stderr:
        for line in proc.stderr.strip().split("\n"):
            line = line.strip()
            if line:
                print(f"  {line}")


def main():
    print("=" * 50)
    print("  GLM Coding Helper Lite — 一键安装")
    print(f"  项目目录: {ROOT}")
    print("=" * 50)

    # [1/5] 检测 Python
    print("\n[1/5] 检测 Python...")
    py = check_python()
    if py is None:
        print()
        print("  [FAIL] 未找到兼容的 Python!")
        print(f"  需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} ~ {MAX_PYTHON[0]}.{MAX_PYTHON[1]}")
        print(f"  推荐: Python {RECOMMENDED} (32 位不可用，请用 64 位)")
        print()
        print("  下载: https://www.python.org/downloads/release/python-31011/")
        print(f"  或:   winget install Python.Python.{RECOMMENDED.replace('.', '')}")
        sys.exit(1)

    # 2-5
    venv_py = create_venv(py)
    upgrade_pip(venv_py)
    install_deps(venv_py)
    cache_model(venv_py)

    # 完成
    print()
    print("=" * 50)
    print("  安装完成!")
    print()
    print("  启动: 双击 start.bat")
    print("  或:   venv\\Scripts\\python.exe backend\\server.py")
    print("=" * 50)
    print()

    # 验证
    print("  快速验证:")
    for mod, name in [
        ("PIL", "Pillow"),
        ("numpy", "NumPy"),
        ("paddleocr", "PaddleOCR"),
        ("ultralytics", "YOLO"),
        ("fastapi", "FastAPI"),
        ("uvicorn", "uvicorn"),
        ("psutil", "psutil"),
    ]:
        proc = run([str(venv_py), "-c", f"import {mod}; print({mod}.__version__ if hasattr({mod}, '__version__') else 'ok')"])
        if proc.returncode == 0:
            log(f"{name}: {proc.stdout.strip()}")
        else:
            log(f"{name}: 缺失!", ok=False)


if __name__ == "__main__":
    main()
