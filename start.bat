@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   GLM Coding Helper Lite - Fast Start
echo ============================================
echo.

REM --- Auto-create minimal config if missing ---
if not exist "config.json" (
    echo [INFO] Creating default config.json...
    >config.json echo {"workers": 4, "port": 8888, "ocr_workers": 4, "yolo_imgsz": 448, "stagger_delay": 0.5, "pipeline_depth": 3}
)

REM --- Prefer venv Python ---
if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [FAIL] Python not found. Install Python 3.10+ first.
        pause
        exit /b 1
    )
    set PYTHON=python
)

REM --- Check that backend/server.py exists ---
if not exist "backend\server.py" (
    echo [FAIL] backend\server.py not found. Are you in the correct directory?
    pause
    exit /b 1
)

REM --- Check port ---
:check_port
netstat -ano | findstr ":8888 " | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port 8888 is already in use!
    echo       1 - Kill all Python processes and retry
    echo       2 - Exit
    set /p "PORT_CHOICE=Choose (1/2): "
    if "!PORT_CHOICE!"=="1" (
        echo [INFO] Killing all Python processes...
        taskkill /F /IM python.exe >nul 2>&1
        timeout /t 3 /nobreak >nul
        goto check_port
    )
    exit /b 1
)

echo [OK] Starting backend service...
echo [OK] Server at http://localhost:8888
echo [OK] Press Ctrl+C to stop
echo.

"%PYTHON%" backend\server.py

echo.
pause
