# 条件单独立评估进程（cond_watch_v2）探活与拉起（2026-10-28）
#
# 口径：与 ensure_8810_once.ps1 同形——健康则不动，不健康则隐藏窗口拉起。
#   "健康" = 心跳文件新鲜（ok=true 且 ts 在 120s 内）且 pid 进程存在。
#   cond_watch_v2 盘外每 60s 跑一轮（心跳照样更新），盘中 5s；120s 阈值给两轮余量。
# 边界：只处理 cond_watch_v2；不碰 8810、不碰旧 engine 进程、不注册/修改其它计划任务。

$ErrorActionPreference = 'Stop'

$Project = Split-Path -Parent $PSScriptRoot          # tools 的父目录 = panel-v2
$Python = Join-Path (Split-Path -Parent (Split-Path -Parent $Project)) 'envs\qmt312\Scripts\python.exe'
$Heartbeat = Join-Path $Project 'data\panel_v2\cond_watch_heartbeat.json'
$Script = Join-Path $Project 'app\cond_watch_v2.py'

function Test-CondWatchAlive {
    try {
        if (-not (Test-Path -LiteralPath $Heartbeat)) { return $false }
        $h = Get-Content -LiteralPath $Heartbeat -Raw -Encoding UTF8 | ConvertFrom-Json
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $age = $now - [double]$h.ts
        if ($h.ok -ne $true -or $age -gt 120 -or $age -lt -60) { return $false }
        if (-not $h.pid) { return $false }
        return [bool](Get-Process -Id ([int]$h.pid) -ErrorAction SilentlyContinue)
    } catch {
        return $false
    }
}

if (Test-CondWatchAlive) { exit 0 }

if (-not (Test-Path -LiteralPath $Python)) { exit 2 }
if (-not (Test-Path -LiteralPath $Script)) { exit 3 }

Start-Process -WindowStyle Hidden -WorkingDirectory $Project -FilePath $Python `
    -ArgumentList (Join-Path 'app' 'cond_watch_v2.py')

Start-Sleep -Seconds 4
if (Test-CondWatchAlive) { exit 0 }
exit 1
