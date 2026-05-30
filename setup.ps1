$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonDir = Join-Path $Root "python"
$VenvDir   = Join-Path $Root "venv"
$CfgFile   = Join-Path $VenvDir "pyvenv.cfg"

# ── 0) UTF8 编码（防止中文字符乱码） ──────────────────────────────────────────
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Write-Host "=== GLM Coding Helper Lite 环境检测 ===" -ForegroundColor Cyan

# ── 1) VC++ Redistributable 检测 ────────────────────────────────────────────
Write-Host "[检查] Visual C++ 运行库..." -NoNewline
try {
    # 搜索 vcruntime140.dll — 系统目录下有才算安装
    $sysVc = Get-Item "$env:SystemRoot\System32\vcruntime140.dll" -ErrorAction Stop
    $sysVcVer = $sysVc.VersionInfo.ProductVersion
    Write-Host " 已安装 ($sysVcVer)" -ForegroundColor Green
} catch {
    Write-Host " 未检测到!" -ForegroundColor Red
    Write-Host "  └─ 请安装 VC++ Redistributable (x64):" -ForegroundColor Yellow
    Write-Host "     https://aka.ms/vs/17/release/vc_redist.x64.exe" -ForegroundColor Yellow
    Write-Host "  └─ 或者使用项目自带的 python/vcruntime140.dll (便携模式)" -ForegroundColor Cyan
}

# ── 2) pyvenv.cfg 路径配置 ──────────────────────────────────────────────────
Write-Host "[检查] 虚拟环境路径..." -NoNewline
if (-not (Test-Path $CfgFile)) {
    # 确保 venv 目录存在
    if (-not (Test-Path $VenvDir)) { New-Item -ItemType Directory -Force -Path $VenvDir | Out-Null }
    Write-Host " 创建" -ForegroundColor Yellow
    @"
home = $PythonDir
include-system-site-packages = false
version = 3.13.9
executable = $PythonDir\python.exe
"@ | Set-Content -LiteralPath $CfgFile -Encoding UTF8
} else {
    Write-Host " 正常" -ForegroundColor Green
}

# ── 3) Python 可执行文件检测 ─────────────────────────────────────────────────
Write-Host "[检查] Python 解释器..." -NoNewline
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host " 错误: 找不到 $PythonExe" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}
$ver = & $PythonExe --version 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host " 启动失败" -ForegroundColor Red; Read-Host "按回车退出"; exit 1 }
Write-Host "  $ver - 正常" -ForegroundColor Green

# ── 4) 关键包检测 (用 pip show，不 import，避免加载 ML 库吃内存) ─────────────
Write-Host "[检查] pip 包列表..." -NoNewline
$packages = & $PythonExe -m pip list --format=json 2>$null
if ($LASTEXITCODE -ne 0 -or -not $packages) {
    Write-Host " 无法获取包列表" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}
$pkgNames = ($packages | ConvertFrom-Json).name
$required = @("opencv-python", "numpy", "Pillow", "ultralytics", "paddlepaddle", "paddleocr")
$missing = $required | Where-Object { $_ -notin $pkgNames }
if ($missing.Count -eq 0) {
    Write-Host " 全部正常 ($($required.Count) 个必要包)" -ForegroundColor Green
} else {
    Write-Host " 缺少: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "  └─ 请确保完整解压了 venv 目录" -ForegroundColor Yellow
}

# ── 5) PaddleOCR 模型缓存 ───────────────────────────────────────────────────
Write-Host "[检查] OCR 模型缓存..." -NoNewline
$cacheDir = Join-Path $Root ".paddlex_cache_cpu"
$modelDir = Join-Path $cacheDir "official_models"
if (Test-Path $modelDir) {
    $models = @(Get-ChildItem $modelDir -Directory).Count
    Write-Host " $models 个模型已缓存" -ForegroundColor Green
} else {
    Write-Host " 未找到本地缓存" -ForegroundColor Yellow
    Write-Host "  └─ 首次使用 OCR 时将从网络下载模型，约 100MB" -ForegroundColor Yellow
}

# ── 6) CPU 信息 ─────────────────────────────────────────────────────────────
$cpus = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
Write-Host "  CPU: $cpus 逻辑核心" -ForegroundColor Green

$workers = [math]::Max(2, [math]::Min([int]($cpus / 4), 16))
$tabs = [math]::Max(4, [math]::Min($workers * 2, 32))
Write-Host "  Worker池: $workers  |  推荐标签页: ≤${tabs}" -ForegroundColor Yellow

Write-Host "环境就绪!" -ForegroundColor Cyan
exit 0
