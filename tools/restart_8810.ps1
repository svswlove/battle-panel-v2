<#
.SYNOPSIS
    作战面板 v2（8810）自重启 helper —— 由 `POST /api/shell/restart` 以分离进程拉起。

.DESCRIPTION
    为什么需要 helper 而不是在请求里直接杀：被杀的进程就是处理该请求的进程，
    自己停自己会让 HTTP 响应发不回去。所以分三步：
      1. 先睡 $WaitSeconds（默认 2 秒），让 8810 把 {ok:true} 响应写完、浏览器收到；
      2. 按端口找监听进程（+ 命令行含 uvicorn 的父进程）逐个停止；
      3. 调 `ensure_8810.ps1 -Once` 拉起——它自己会轮询健康检查最长 30 秒。

    兜底：即使本脚本在杀完之后崩了，`PanelV2_KeepAlive` 计划任务（每 5 分钟）
    仍会调 ensure 把 8810 拉回来。本脚本不注册/修改任何计划任务。

    日志：`data/panel_v2/logs/_restart.log`（每次重启追加）。
    用 [IO.File]::AppendAllText 而不是 Add-Content：后者在无控制台的分离进程里
    实测静默不落盘（2026-10-29 排查）。

.PARAMETER Port
    面板端口，默认 8810。

.PARAMETER WaitSeconds
    杀进程前的等待秒数，默认 2；小于 0 按 0。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\restart_8810.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8810,
    [int]$WaitSeconds = 2
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root 'data\panel_v2\logs'
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$LogFile = Join-Path $logDir '_restart.log'
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Log {
    param([string]$Message)
    $line = "[{0}] [restart_8810] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $Utf8)
}

Write-Log ("helper start port={0} wait={1}s" -f $Port, $WaitSeconds)
if ($WaitSeconds -lt 0) { $WaitSeconds = 0 }
Start-Sleep -Seconds $WaitSeconds

# ---- 1. 找目标：监听该端口的进程 + 它的 uvicorn 父进程（qmt312 python 启动器）
$targets = New-Object System.Collections.Generic.List[int]
$conns = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
foreach ($c in $conns) {
    $opid = 0
    [void][int]::TryParse([string]$c.OwningProcess, [ref]$opid)
    if ($opid -le 0) { continue }
    if (-not $targets.Contains($opid)) { $targets.Add($opid) }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$opid" -ErrorAction SilentlyContinue
    if ($proc -and $proc.ParentProcessId -and $proc.ParentProcessId -gt 0) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
        # 只连带命令行确实在跑本面板的父进程，避免误伤 shell / 计划任务宿主
        if ($parent -and $parent.CommandLine -and ($parent.CommandLine -match 'uvicorn' -or $parent.CommandLine -match 'run\.ps1')) {
            $ppid = [int]$parent.ProcessId
            if (-not $targets.Contains($ppid)) { $targets.Add($ppid) }
        }
    }
}

# ---- 2. 停进程
if ($targets.Count -eq 0) {
    Write-Log "port $Port has no listener, skip kill"
} else {
    foreach ($t in $targets) {
        # 杀子进程后其父可能已自行退出（常发生），先看存在再杀，避免日志里堆无意义的失败
        if (-not (Get-Process -Id $t -ErrorAction SilentlyContinue)) {
            Write-Log "pid $t already gone"
            continue
        }
        try {
            Stop-Process -Id $t -Force -ErrorAction Stop
            Write-Log "stopped pid $t"
        } catch {
            Write-Log ("stop pid {0} failed: {1}" -f $t, $_.Exception.Message)
        }
    }
}

# 等端口真正释放，避免 ensure 判成「端口已监听但健康未过」而干等 30 秒
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
    $still = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($still.Count -eq 0) { break }
    Start-Sleep -Milliseconds 300
}
Write-Log "port $Port released"

# ---- 3. 拉起（ensure 自己轮询健康，最长 30 秒）
$code = 1
try {
    $ensureOut = & (Join-Path $PSScriptRoot 'ensure_8810.ps1') -Once *>&1
    foreach ($ln in $ensureOut) { Write-Log ("ensure: {0}" -f $ln) }
    $code = $LASTEXITCODE
} catch {
    Write-Log ("ensure_8810.ps1 threw: {0}" -f $_.Exception.Message)
    $code = 1
}
Write-Log "helper end ensure_exit=$code"
exit $code
