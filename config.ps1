<#
.SYNOPSIS
  GLM Coding Helper Lite - 配置向导
  检测当前 CPU，推荐最佳参数，支持手动调整后保存配置

.USAGE
  .\config.ps1               # 交互式配置
  .\config.ps1 -Auto         # 自动检测并保存推荐配置（无交互）

效果: 生成 config.json，start.bat 会自动读取
#>

param([switch]$Auto)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $Root "config.json"
$ErrorActionPreference = "Stop"

# ── 颜色辅助 ─────────────────────────────────────────────────────────────
$C     = "Cyan"
$G     = "Green"
$Y     = "Yellow"
$R     = "Red"
$M     = "Magenta"

function Color($clr, $txt) { if ($clr) { Write-Host $txt -ForegroundColor $clr -NoNewline } else { Write-Host $txt -NoNewline } }
function Line($clr, $txt)  { if ($clr) { Write-Host $txt -ForegroundColor $clr } else { Write-Host $txt } }

# ── CPU 检测 & 推荐参数 ──────────────────────────────────────────────────
$cpus = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
if (-not $cpus) { $cpus = 4 }

# 推荐: 每个 YOLO worker 约消耗 0.5~1 核（共享 OCR 池后）
$recWorkers      = [math]::Max(2, [math]::Min([int]($cpus / 2 - 2), 16))
$recTabs         = [math]::Max(4, [math]::Min($recWorkers * 2, 32))
$recOcrWorkers   = [math]::Max(2, [math]::Min([int]($cpus / 8), 6))
$recPort         = 8888
$recStaggerDelay = 3
$recYoloImgsz    = 448

# ── 安全下限（过少会降低并发，过多会撑爆 CPU） ────────────────────────────
$safeMinWorkers    = 2
$safeMaxWorkers    = 16
$safeMinOcrWorkers = 1
$safeMaxOcrWorkers = 8

# ── 默认值（生产环境已验证） ──────────────────────────────────────────────
$defaultWorkers    = [math]::Max(2, [math]::Min([int]($cpus / 4), 16))
$defaultOcrWorkers = 3
$defaultPort       = 8888
$defaultStagger    = 3
$defaultYoloImgsz  = 448

# ── 显示界面 ─────────────────────────────────────────────────────────────
Clear-Host
Line $C  "╔══════════════════════════════════════════════════╗"
Line $C  "║       GLM Coding Helper Lite  配置向导            ║"
Line $C  "╚══════════════════════════════════════════════════╝"
Write-Host ""

Line $M  "  CPU 检测"
Line ""  "  ───────────────────────────────────────"
Line $G  "  逻辑核心数:        $cpus"
Write-Host ""

Line $M  "  参数推荐"
Line ""  "  ───────────────────────────────────────"

# ── 表格 ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "参数", "推荐值", "默认值", "说明")
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "────", "────", "────", "────")
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "YOLO Workers", $recWorkers, $defaultWorkers, "并发识别验证码的进程数")
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "OCR Workers", $recOcrWorkers, $defaultOcrWorkers, "OCR 识别子进程数（共享池）")
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "端口", $recPort, $defaultPort, "后端 HTTP 服务端口")
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "错峰延迟(秒)", $recStaggerDelay, $defaultStagger, "每个 Worker 启动间隔")
Write-Host ("  {0,-20}  {1,-10}  {2,-10}  {3,-10}" -f "YOLO 输入尺寸", $recYoloImgsz, $defaultYoloImgsz, "448=平衡速度与精度")

Write-Host ""
Line $M  "  推荐标签页上限:  $recTabs"

Write-Host ""

if ($Auto) {
    Write-Host "  [Auto模式] 使用推荐参数" -ForegroundColor $Y
    $workers       = $recWorkers
    $ocrWorkers    = $recOcrWorkers
    $port          = $recPort
    $staggerDelay  = $recStaggerDelay
    $yoloImgsz     = $recYoloImgsz
} else {
    # ── 交互 ────────────────────────────────────────────────────────────
    $ans = Read-Host "  是否自定义参数? (Y/n, 默认 n=使用推荐值)"
    if ($ans -eq "" -or $ans -match "^[nN]") {
        $workers       = $recWorkers
        $ocrWorkers    = $recOcrWorkers
        $port          = $recPort
        $staggerDelay  = $recStaggerDelay
        $yoloImgsz     = $recYoloImgsz
    } else {
        Write-Host ""
        Line $Y  "  直接回车 = 使用推荐值"
        Write-Host ""

        $workers = Read-Host "  YOLO Workers [推荐 $recWorkers]"
        if ($workers -eq "") { $workers = $recWorkers } else { $workers = [int]$workers }
        $workers = [math]::Max($safeMinWorkers, [math]::Min($workers, $safeMaxWorkers))

        $ocrWorkers = Read-Host "  OCR Workers (共享池) [推荐 $recOcrWorkers]"
        if ($ocrWorkers -eq "") { $ocrWorkers = $recOcrWorkers } else { $ocrWorkers = [int]$ocrWorkers }
        $ocrWorkers = [math]::Max($safeMinOcrWorkers, [math]::Min($ocrWorkers, $safeMaxOcrWorkers))

        $port = Read-Host "  服务端口 [推荐 $recPort]"
        if ($port -eq "") { $port = $recPort } else { $port = [int]$port }
        if ($port -lt 1024 -or $port -gt 65535) { $port = $recPort }

        $staggerDelay = Read-Host "  错峰延迟(秒) [推荐 $recStaggerDelay]"
        if ($staggerDelay -eq "") { $staggerDelay = $recStaggerDelay } else { $staggerDelay = [int]$staggerDelay }
        $staggerDelay = [math]::Max(0, [math]::Min($staggerDelay, 30))

        $yoloImgsz = Read-Host "  YOLO 输入尺寸 (320/448/640) [推荐 $recYoloImgsz]"
        if ($yoloImgsz -eq "") { $yoloImgsz = $recYoloImgsz } else { $yoloImgsz = [int]$yoloImgsz }
        if (@(320, 448, 640) -notcontains $yoloImgsz) { $yoloImgsz = $recYoloImgsz }
    }
}

# ── 保存配置 ─────────────────────────────────────────────────────────────
$config = @{
    "workers"       = $workers
    "ocr_workers"   = $ocrWorkers
    "port"          = $port
    "stagger_delay" = $staggerDelay
    "yolo_imgsz"    = $yoloImgsz
}

$config | ConvertTo-Json | Set-Content -LiteralPath $ConfigPath -Encoding UTF8

Write-Host ""
Line $G  "  ✓ 配置已保存: $ConfigPath"
Write-Host ""
Line $Y  "  ┌─────────── 最终参数 ───────────"
Line $Y  "  │ CPU 核心数:         $cpus"
Line $Y  "  │ YOLO Workers:       $workers"
Line $Y  "  │ OCR Workers (共享): $ocrWorkers"
Line $Y  "  │ 端口:               $port"
Line $Y  "  │ 错峰延迟:           ${staggerDelay}s"
Line $Y  "  │ YOLO 输入尺寸:      $yoloImgsz"
Line $Y  "  │ 推荐标签页上限:     $($workers * 2)"
Line $Y  "  └───────────────────────────────"
Write-Host ""
Line $C  "  现在运行 start.bat 启动服务"
Write-Host ""

# ── 安全校验提醒 ─────────────────────────────────────────────────────────
if ($workers -gt ($cpus / 2)) {
    Line $R  "  ⚠ 注意: YOLO Workers ($workers) > 核心数一半 ($([int]($cpus/2)))"
    Line $R  "     可能会导致系统响应变慢，建议减小 Workers 数量"
    Write-Host ""
}

exit 0
