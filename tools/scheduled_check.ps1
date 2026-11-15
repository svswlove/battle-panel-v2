# panel-v2 每日自检包装（供计划任务调用，2026-10-23 新增）
# 为什么需要包装：run_daily.ps1 读 PANEL_V2_PYTHON 环境变量，计划任务的会话里没有，
# 直接调会退化成 `python`（PATH 上的可能是 uv 存根，缺 fastapi）。
# 这里把解释器路径钉死，并把输出统一落到 data\panel_v2\logs\ 下。

$ErrorActionPreference = "Continue"

$toolsDir = $PSScriptRoot                       # ...\panel-v2\tools
$root = Split-Path -Parent $toolsDir            # ...\panel-v2
$projDir = Split-Path -Parent $root             # ...\projects
$ws = Split-Path -Parent $projDir               # ...\OH-WorkSpace
$logDir = Join-Path $root "data\panel_v2\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$py = Join-Path $ws "envs\qmt312\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
    Add-Content -LiteralPath (Join-Path $logDir "_scheduled_error.log") `
        -Value ("[{0}] python not found: {1}" -f (Get-Date -Format s), $py) -Encoding UTF8
    exit 2
}
$env:PANEL_V2_PYTHON = $py
# 计划任务会话没有 Hana 会话的 UTF-8 环境；不钉住会让子进程输出编码随会话漂移（10-23 08:50 假红）。
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$out = & (Join-Path $toolsDir "run_daily.ps1") *>&1
$code = $LASTEXITCODE
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
Add-Content -LiteralPath (Join-Path $logDir "_scheduled.log") `
    -Value ("[{0}] run_daily exit={1}" -f $stamp, $code) -Encoding UTF8
if ($code -ne 0) {
    Add-Content -LiteralPath (Join-Path $logDir "_scheduled_fail_$stamp.log") `
        -Value ($out | Out-String) -Encoding UTF8
}
exit $code
