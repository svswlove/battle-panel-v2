# 8810 保活包装（供计划任务每 5 分钟调用）
# 为什么不直接调 ensure_8810.ps1：-Once 是 switch 参数集，scheduled 命令行里显式传更稳，
# 同时把结果追加到保活日志，便于事后统计「当天拉起过几次」。
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "data\panel_v2\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir "_keepalive.log"
$out = & (Join-Path $PSScriptRoot "ensure_8810.ps1") -Once *>&1
$code = $LASTEXITCODE
# 只在「不正常」或「拉起过」时记录，避免每 5 分钟刷一行正常日志
$text = ($out | Out-String)
$interesting = ($code -ne 0) -or ($text -notmatch "已在运行")
if ($interesting) {
    Add-Content -LiteralPath $log -Value ("[{0}] exit={1}`n{2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $code, $text.Trim()) -Encoding UTF8
}
# 2026-11-07 返工（夏天 ②）：顺带心跳 daemon_guard——它守 watchdog/dashboard_builder，
# 自己没人守。跑在本脚本末尾、try 包住，任何异常都不影响 8810 保活主流程。
$ws = Split-Path -Parent (Split-Path -Parent $root)
$guardHeartbeat = Join-Path $ws "engine\tools\ensure_guard.ps1"
if (Test-Path $guardHeartbeat) {
    try { & $guardHeartbeat *>&1 | Out-Null } catch { }
}
exit $code
