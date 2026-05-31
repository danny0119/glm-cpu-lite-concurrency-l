@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === GLM Coding Helper Lite ===
echo.

REM ---- 1. Config check ------------------------------------------------
if exist "config.json" (
    echo [OK] 配置文件已存在
) else (
    echo [INFO] 首次运行，启动配置向导...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0config.ps1" -Auto
    if %errorlevel% neq 0 ( pause & exit /b )
)
echo.

REM ---- 2. 优先使用自包含 EXE -----------------------------------------
if exist "%~dp0dist\GLM-Lite\GLM-Lite.exe" (
    echo [MODE] 自包含 EXE 模式
    echo.
    echo 启动后端服务...
    echo   运行于 http://localhost:8888
    echo.
    echo 关闭本窗口或 Ctrl+C 停止
    echo.
    start "GLM-Lite Backend" /B "%~dp0dist\GLM-Lite\GLM-Lite.exe"
    goto :wait_and_done
)

REM ---- 3. 回退：使用 venv Python 直接运行 -----------------------------
if exist "%~dp0venv\Scripts\python.exe" (
    echo [MODE] Python venv 模式
    echo.
    echo 启动后端服务（首次可能需等待模型加载）...
    echo   运行于 http://localhost:8888
    echo.
    echo 关闭本窗口或 Ctrl+C 停止
    echo.
    "%~dp0venv\Scripts\python.exe" "%~dp0backend\server.py"
    pause
    exit /b 0
)

REM ---- 4. 环境不完整 ---------------------------------------------------
echo [FAIL] 未找到 self-contained EXE 也未找到 venv 环境!
echo.
echo 请先运行 setup.bat 安装依赖后再试。
echo.
pause
exit /b 1

:wait_and_done
timeout /t 5 /nobreak >nul
echo.
echo 后端已启动。访问 http://localhost:8888/docs 查看 API 文档
echo.
pause
