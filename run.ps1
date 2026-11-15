# 启动作战面板 v2（8810）。不碰旧 8800 / 8002 进程。
# 用法：.\run.ps1
# 可选：$env:PANEL_V2_PYTHON 指定解释器；否则先找项目内相对路径，再找 PATH 里的 python。
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = $env:PANEL_V2_PYTHON

if (-not $python) {
    $candidates = @(
        (Join-Path $root '..\..\envs\qmt312\Scripts\python.exe'),
        (Join-Path $root '..\envs\qmt312\Scripts\python.exe'),
        (Join-Path $root '.venv\Scripts\python.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { $python = (Resolve-Path -LiteralPath $c).Path; break }
    }
}
if (-not $python) { $python = 'python' }

if (-not (Get-Command $python -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $python)) {
    Write-Error "找不到 Python，请设置 PANEL_V2_PYTHON 或安装 python"
    exit 1
}

# 作战面板 v2 = 常驻行情端：打开 QMT 整推订阅（app/core/feed.py）。
# 测试/离线环境不要设这个变量，feed 会保持 DISABLED，不产生任何 QMT 连接。
$env:PANEL_V2_FEED = '1'

Set-Location -LiteralPath $root
Write-Host "panel_v2 core -> http://127.0.0.1:8810"
& $python -m uvicorn app.core.main:app --host 127.0.0.1 --port 8810
