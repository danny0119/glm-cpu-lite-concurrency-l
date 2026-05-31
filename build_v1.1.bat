@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title GLM Coding Helper Lite v1.1 Pack

echo ====== GLM Coding Helper Lite v1.1 Pack ======
echo.

set ROOT=%~dp0
set ROOT=%ROOT:~0,-1%
set DIST_DIR=D:\glm-coding-helper-lite-v1.1
set BUILD_DIR=%ROOT%\build
set LOG_FILE=%ROOT%\pyinstaller-v1.1.log

echo [INFO] Root: %ROOT%
echo [INFO] Output: %DIST_DIR%
echo [INFO] Log: %LOG_FILE%
echo.

:: ---- check venv -------------------------------------------------------
if not exist "%ROOT%\venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run setup.bat first.
    pause
    exit /b 1
)
echo [OK] venv found

:: ---- check PyInstaller -----------------------------------------------
"%ROOT%\venv\Scripts\python.exe" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    "%ROOT%\venv\Scripts\python.exe" -m pip install pyinstaller -q
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed
        pause
        exit /b 1
    )
)
echo [OK] PyInstaller is available

:: ---- check model file ------------------------------------------------
if not exist "%ROOT%\models\weights\yolo-captcha-detector.pt" (
    echo [ERROR] Model not found: %ROOT%\models\weights\yolo-captcha-detector.pt
    pause
    exit /b 1
)
echo [OK] Model file found

:: ---- check VC++ runtime ----------------------------------------------
if not exist "%WINDIR%\System32\vcruntime140.dll" (
    echo [WARN] vcruntime140.dll not found. Old Windows may need VC++ redist.
)
if not exist "%WINDIR%\System32\msvcp140.dll" (
    echo [WARN] msvcp140.dll not found.
)

:: ---- clean old builds ------------------------------------------------
if exist "%DIST_DIR%" (
    echo [CLEAN] Removing old output dir...
    rmdir /s /q "%DIST_DIR%"
)
if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
)
if exist "%ROOT%\GLM-Lite.spec" (
    del /f /q "%ROOT%\GLM-Lite.spec" 2>nul
)

echo.
echo ====== Starting PyInstaller build ======
echo   This may take 10-30 minutes...
echo   DO NOT close this window.
echo.

set TMPDIR=%ROOT%\tmp_build
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"

"%ROOT%\venv\Scripts\python.exe" -m PyInstaller ^
    --clean ^
    --log-level INFO ^
    --specpath "%ROOT%" ^
    --distpath "%TMPDIR%" ^
    --workpath "%BUILD_DIR%" ^
    -y ^
    --onefile ^
    --name "GLM-Coding-Helper-Lite" ^
    --add-data "%ROOT%\backend;backend" ^
    --add-data "%ROOT%\models;models" ^
    --hidden-import ultralytics ^
    --hidden-import ultralytics.nn.tasks ^
    --hidden-import paddleocr ^
    --hidden-import paddle ^
    --hidden-import paddle.nn ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageDraw ^
    --hidden-import PIL.ImageFont ^
    --hidden-import numpy ^
    --hidden-import cv2 ^
    --hidden-import safetensors ^
    --hidden-import pypdfium2 ^
    --hidden-import pypdfium2_raw ^
    --hidden-import pypdfium2_cfg ^
    --hidden-import backend.server ^
    --hidden-import backend.worker ^
    --hidden-import backend.ppocr_worker ^
    --hidden-import backend.evaluate ^
    --exclude-module tkinter ^
    --exclude-module scipy ^
    --exclude-module IPython ^
    "%ROOT%\glm-lite.py" >"%LOG_FILE%" 2>&1

set EXIT_CODE=%ERRORLEVEL%

echo.
echo ====== PyInstaller finished (exit code %EXIT_CODE%) ======
echo.

if "%EXIT_CODE%"=="0" goto :BUILD_OK

    echo [ERROR] PyInstaller build failed (exit code %EXIT_CODE%)
    echo   Check full log: "%LOG_FILE%"
    echo.
    echo   Last 30 lines of log:
    powershell -Command "Get-Content '%LOG_FILE%' -Tail 30 2>$null"
    echo.
    if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
    pause
    exit /b %EXIT_CODE%

:BUILD_OK

set EXE_PATH=%TMPDIR%\GLM-Coding-Helper-Lite.exe
if not exist "%EXE_PATH%" (
    echo [ERROR] Output file not found after build!
    pause
    exit /b 1
)

echo [OK] Build success!

echo [INFO] Copying to output directory...
mkdir "%DIST_DIR%" 2>nul
copy "%EXE_PATH%" "%DIST_DIR%\GLM-Coding-Helper-Lite.exe" /y >nul

echo [INFO] Copying auxiliary files...
copy "%ROOT%\glm-lite.user.js" "%DIST_DIR%\" /y >nul 2>&1
copy "%ROOT%\glm-coding-helper.user.js" "%DIST_DIR%\" /y >nul 2>&1
copy "%ROOT%\README.md" "%DIST_DIR%\" /y >nul 2>&1
copy "%ROOT%\requirements.txt" "%DIST_DIR%\" /y >nul 2>&1

echo [CLEAN] Removing build cache...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
if exist "%ROOT%\GLM-Lite.spec" del /f /q "%ROOT%\GLM-Lite.spec" 2>nul
if exist "%ROOT%\__pycache__" rmdir /s /q "%ROOT%\__pycache__" 2>nul

echo.
echo ====== Pack complete ======
echo   Output: %DIST_DIR%
echo   Main exe: GLM-Coding-Helper-Lite.exe
echo.
echo   Open D:\glm-coding-helper-lite-v1.1 to check.
echo.

start "" "%DIST_DIR%"
pause
exit /b 0
