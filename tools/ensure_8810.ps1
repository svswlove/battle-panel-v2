<#
.SYNOPSIS
    作战面板 v2（8810）启动/健康保障。健康则不动，不健康则用项目内 run.ps1 拉起。

.DESCRIPTION
    只处理 8810：不探活、不启动、不停止旧 8800 / 8002 / 8801。
    不修改项目内除本文件以外的任何文件；不写项目外文件；不注册计划任务。
    启动方式为隐藏窗口调用项目内 run.ps1，脚本退出后该进程继续运行。

.PARAMETER Once
    单次模式（默认）。健康 exit 0；不健康则启动并轮询最多 30 秒，成功 exit 0，失败 exit 1。

.PARAMETER Loop
    常驻循环模式。按 -IntervalSeconds 重复检查/拉起，Ctrl-C 停止。

.PARAMETER IntervalSeconds
    循环间隔秒，默认 300，最小 30；小于 30 按 30 执行。

.EXAMPLE
    .\tools\ensure_8810.ps1

.EXAMPLE
    .\tools\ensure_8810.ps1 -Loop -IntervalSeconds 300
#>
[CmdletBinding(DefaultParameterSetName = 'Once')]
param(
    [Parameter(ParameterSetName = 'Once')]
    [switch]$Once,

    [Parameter(ParameterSetName = 'Loop')]
    [switch]$Loop,

    [Parameter(ParameterSetName = 'Loop')]
    [ValidateRange(1, 86400)]
    [int]$IntervalSeconds = 300
)

$ErrorActionPreference = 'Stop'

# 8810 是常驻行情端：保活任务拉起时必须把 feed 开关传下去。
# run.ps1 自己也会设；这里双保险，防止计划任务环境里变量丢失导致 feed 永久 DISABLED。
$env:PANEL_V2_FEED = '1'

$script:Port = 8810
$script:HealthUrl = "http://127.0.0.1:$($script:Port)/api/health"
$script:StartupWaitSeconds = 30
$script:MinIntervalSeconds = 30

function Write-Log {
    param([string]$Message)
    Write-Host ("[{0}] [ensure_8810] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message)
}

# 健康判定：HTTP 200 且响应体 ok=true。连接失败、超时、非 200、解析失败一律视为不健康。
function Test-PanelHealth {
    try {
        $res = Invoke-WebRequest -Uri $script:HealthUrl -UseBasicParsing -TimeoutSec 3
        if ($res.StatusCode -ne 200) { return $false }
        $body = $res.Content | ConvertFrom-Json
        return ($body.ok -eq $true)
    } catch {
        return $false
    }
}

# 端口是否已在监听。用于区分"进程还没起来"和"进程在启动中"，避免重复拉起。
function Test-PanelPortOpen {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $script:Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        if ($client) { $client.Close() }
    }
}

function Start-PanelProcess {
    $root = Split-Path -Parent $PSScriptRoot
    $runPs1 = Join-Path $root 'run.ps1'
    if (-not (Test-Path -LiteralPath $runPs1)) {
        Write-Log "找不到启动脚本：$runPs1"
        return $null
    }

    $shellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $shellExe)) {
        $cmd = Get-Command powershell.exe -ErrorAction SilentlyContinue
        if ($cmd) { $shellExe = $cmd.Source } else { $shellExe = (Get-Process -Id $PID).Path }
    }

    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $runPs1))

    # 启动日志留档（2026-10-23 新增）：以前用隐藏窗口拉起，uvicorn 的 stdout/stderr 直接丢弃，
    # 崩溃后被保活任务拉起来，**没有任何 traceback 可查**。现在把两个流各写一个文件：
    # Start-Process 的两个重定向不能用同一个文件，且每次启动会截断。
    # 所以启动前先把上一轮的存成 server.prev.*，保证崩溃时至少能看上一轮的尾巴。
    $logDir = Join-Path $root 'data\panel_v2\logs'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $outLog = Join-Path $logDir 'server.out.log'
    $errLog = Join-Path $logDir 'server.err.log'
    foreach ($f in @($outLog, $errLog)) {
        if (Test-Path -LiteralPath $f) {
            try {
                Move-Item -LiteralPath $f -Destination ($f -replace '\.log$', '.prev.log') -Force
            } catch { }
        }
    }
    $spArgs = @{
        FilePath         = $shellExe
        ArgumentList     = $argList
        WorkingDirectory = $root
        WindowStyle      = 'Hidden'
        RedirectStandardOutput = $outLog
        RedirectStandardError  = $errLog
        PassThru         = $true
    }
    try {
        return Start-Process @spArgs
    } catch {
        Write-Log ("创建启动进程失败：{0}" -f $_.Exception.Message)
        return $null
    }
}

# 返回 0 = 8810 可用（本来就在跑，或本次拉起成功）；1 = 不可用。
function Ensure-PanelRunning {
    if (Test-PanelHealth) {
        Write-Log "健康检查通过，8810 已在运行，不重复启动"
        return 0
    }

    if (Test-PanelPortOpen) {
        Write-Log "端口 8810 已监听但健康检查未通过，按正在启动处理，等待而不重复拉起"
    } else {
        $child = Start-PanelProcess
        if (-not $child) {
            Write-Log "拉起失败：未能启动 run.ps1"
            return 1
        }
        Write-Log ("已用隐藏窗口启动 run.ps1（pid {0}），轮询健康检查，最长 {1} 秒" -f $child.Id, $script:StartupWaitSeconds)
    }

    $deadline = (Get-Date).AddSeconds($script:StartupWaitSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        if (Test-PanelHealth) {
            Write-Log "健康检查通过，8810 已就绪"
            return 0
        }
    }

    Write-Log ("启动超时：{0} 秒内未通过健康检查。排查请在前台运行 .\run.ps1 看报错" -f $script:StartupWaitSeconds)
    return 1
}

if ($PSCmdlet.ParameterSetName -eq 'Once') {
    exit (Ensure-PanelRunning)
}

if ($IntervalSeconds -lt $script:MinIntervalSeconds) {
    Write-Log ("间隔 {0} 秒低于下限，按 {1} 秒执行" -f $IntervalSeconds, $script:MinIntervalSeconds)
    $IntervalSeconds = $script:MinIntervalSeconds
}

Write-Log ("循环模式启动，间隔 {0} 秒，Ctrl-C 停止" -f $IntervalSeconds)
try {
    while ($true) {
        $code = Ensure-PanelRunning
        if ($code -eq 0) {
            Write-Log "本轮正常"
        } else {
            Write-Log "本轮异常，等待下一轮重试"
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    Write-Log "循环监控已停止"
}
