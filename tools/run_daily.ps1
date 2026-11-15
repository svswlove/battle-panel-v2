param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "data\panel_v2\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ((Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

# Tee-Object 在 PS5.1 默认写 UTF-16LE，与 Log 的 UTF-8 混在同一文件会乱码；
# 这里改为逐行 UTF-8 追加，日志全程单编码。
function Write-LogTee {
    param([Parameter(ValueFromPipeline = $true)]$Line, [string]$Path)
    process {
        $text = [string]$Line
        Add-Content -LiteralPath $Path -Value $text -Encoding UTF8
        Write-Host $text
    }
}

try {
    Log "ensure 8810"
    & (Join-Path $PSScriptRoot "ensure_8810.ps1") -Once *>&1 | Write-LogTee -Path $log
    if ($LASTEXITCODE -ne 0) {
        Log "ensure_8810 FAILED exit=$LASTEXITCODE"
        exit 1
    }

    Log "cutover gate"
    $py = $env:PANEL_V2_PYTHON
    if (-not $py) { $py = "python" }
    & $py (Join-Path $PSScriptRoot "cutover_gate.py") *>&1 | Write-LogTee -Path $log
    $code = $LASTEXITCODE
    Log "cutover_gate exit=$code"
    Log "log=$log"
    exit $code
}
catch {
    Log ("ERROR: " + $_.Exception.Message)
    exit 1
}
