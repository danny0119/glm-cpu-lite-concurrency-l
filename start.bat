@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === GLM Coding Helper Lite (全环境打包版) ===
echo.

REM ---- 1. Config check -------------------------------------------------
if exist "config.json" (
    echo [OK] Config file found
) else (
    echo [INFO] First run, launching config wizard...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0config.ps1" -Auto
    if %errorlevel% neq 0 ( pause & exit /b )
)
echo.

REM ---- 2. Check bundled EXE --------------------------------------------
if not exist "%~dp0dist\GLM-Lite\GLM-Lite.exe" (
    echo [FAIL] GLM-Lite.exe not found in dist\GLM-Lite\
    echo   Please re-extract the full package.
    pause
    exit /b 1
)
echo [OK] Self-contained EXE found (no Python/env needed)
echo.

REM ---- 3. Start backend (self-contained EXE) ---------------------------
echo.
echo Starting backend server...
echo   Running on http://localhost:8888
echo.
echo To stop: close this window or press Ctrl+C
echo.
start "GLM-Lite Backend" /B "%~dp0dist\GLM-Lite\GLM-Lite.exe"

REM Wait briefly then show status
timeout /t 5 /nobreak >nul
echo.
echo Backend started. Check http://localhost:8888/health
echo.
pause
