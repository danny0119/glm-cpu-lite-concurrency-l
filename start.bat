@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo === GLM Coding Helper Lite ===
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

REM ---- 2. OCR model cache check ----------------------------------------
if exist ".paddlex_cache_cpu\official_models" (
    echo [OK] OCR model cache found (no download needed)
) else (
    echo [INFO] OCR model cache not bundled
    echo   first OCR request will download ~100MB model
)
echo.

REM ---- 3. Environment check --------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if %errorlevel% neq 0 ( pause & exit /b )

REM ---- 4. Start backend ------------------------------------------------
echo.
echo Starting backend server...
echo   Running on http://localhost:8888
echo.
echo To stop: close this window or press Ctrl+C
echo.
"%~dp0venv\Scripts\python.exe" -u "%~dp0glm-lite.py"

if %errorlevel% neq 0 (
    echo Backend exited abnormally (code: %errorlevel%)
    pause
)
