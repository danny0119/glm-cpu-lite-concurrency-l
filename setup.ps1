<#
.SYNOPSIS
  GLM Coding Helper Lite - Auto Setup
#>

$ErrorActionPreference="Stop"
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir=Join-Path $Root "venv"
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)

Write-Host "=== GLM Coding Helper Lite ===" -ForegroundColor Cyan
Write-Host "[Check] Python interpreter..." -NoNewline

$candidates = @(
  (Join-Path $Root "python\python.exe"),
  (Get-Command "python" -ErrorAction SilentlyContinue).Source,
  "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
  "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
  "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
  "$env:ProgramFiles\Python313\python.exe",
  "$env:ProgramFiles\Python312\python.exe",
  "$env:ProgramFiles\Python311\python.exe"
)
$pythonExe = $null
foreach ($c in $candidates) {
  if ($c -and (Test-Path $c)) { $pythonExe = $c; break }
}
if (-not $pythonExe) {
  Write-Host "NOT FOUND - install Python 3.11-3.13" -ForegroundColor Red
  Read-Host; exit 1
}
$ver = & $pythonExe --version 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "FAILED to start" -ForegroundColor Red
  Read-Host; exit 1
}
Write-Host " $ver" -ForegroundColor Green

Write-Host "[Check] venv..." -NoNewline
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
  Write-Host "creating..." -ForegroundColor Yellow
  & $pythonExe -m venv $VenvDir
  if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED" -ForegroundColor Red
    Read-Host; exit 1
  }
  Write-Host "done" -ForegroundColor Green
} else {
  Write-Host "ok" -ForegroundColor Green
}

Write-Host "[Check] pip packages..." -NoNewline
$installed = @(& $venvPy -m pip list --format=json 2>$null | ConvertFrom-Json).name
$required = @("opencv-python","numpy","Pillow","ultralytics","paddlepaddle","paddleocr")
$missing = $required | Where-Object { $_ -notin $installed }
if ($missing.Count -eq 0) {
  Write-Host "all $($required.Count) packages ok" -ForegroundColor Green
} else {
  Write-Host "installing $($missing.Count) missing..." -ForegroundColor Yellow
  foreach ($p in $missing) {
    Write-Host "  $p"
    & $venvPy -m pip install $p 2>&1 | Out-Null
  }
}

Write-Host "[Check] CPU..." -NoNewline
$cpus = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
if (-not $cpus) { $cpus = 4 }
$workers = [math]::Max(2, [math]::Min([int]($cpus / 4), 16))
Write-Host " $cpus cores, recommend $workers workers" -ForegroundColor Green

Write-Host "Ready!" -ForegroundColor Cyan
exit 0
