@echo off
chcp 65001 >nul
title GLM Coding Helper Lite - Setup

echo ============================================
echo   GLM Coding Helper Lite — Yi Jian An Zhuang
echo ============================================
echo.

:: 找 Python（兼容各种安装方式）
python --version >nul 2>&1
if errorlevel 1 (
    py --version >nul 2>&1
    if errorlevel 1 (
        echo [FAIL] Mei you Python!
        echo       Qing an zhuang Python 3.10 (64-wei):
        echo       https://www.python.org/downloads/release/python-31011/
        pause
        exit /b 1
    )
    set PYTHON=py -3
    goto :run
)
set PYTHON=python
:run

echo [OK] Python found
echo.
echo Installing... zhe ge guo cheng xu yao wang luo, yue 2-5 fen zhong.
echo.
%PYTHON% setup.py
if errorlevel 1 (
    echo.
    echo [FAIL] An zhuang shi bai, jian cha shang mian de cuo wu xin xi.
    pause
    exit /b 1
)
pause
