# 作战面板托盘图标（2026-11-08 主人要求）
# 作用：常驻托盘，显示面板服务是否在跑；左键点一下打开面板，右键有菜单。
# 设计取舍：不装 Python 第三方包、不引新框架 —— 只用 .NET 自带的 NotifyIcon，代价就是这一个 PS 进程。
# 启动方式：开机自启（启动文件夹里的 作战面板托盘.lnk），或用 run_tray.ps1 手动拉起。

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$url    = 'http://127.0.0.1:8810'
$icoP   = 'C:\ProgramData\PanelV2\panel-v2.ico'
$openPs = Join-Path $PSScriptRoot 'open_panel.ps1'
$logDir = Join-Path $PSScriptRoot '..\data\panel_v2\logs'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$log = Join-Path $logDir 'tray.log'

function Write-Log([string]$m) {
    try { Add-Content -LiteralPath $log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 } catch {}
}

# 单实例：开机自启 + 手动双击可能同时触发，多实例会出多个图标。
# 用系统互斥量（不用命令行匹配 —— 那样会把任何引用本脚本路径的 shell 都当成实例）。
$script:mutex = New-Object System.Threading.Mutex($false, 'Local\PanelV2Tray')
$script:gotMutex = $false
try { $script:gotMutex = $script:mutex.WaitOne(0) } catch { $script:gotMutex = $false }
if (-not $script:gotMutex) { Write-Log ('已有托盘实例在跑，本实例退出（pid=' + $PID + '）'); exit 0 }

function Test-Panel {
    try {
        $r = Invoke-WebRequest -Uri "$url/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

$ni = New-Object System.Windows.Forms.NotifyIcon
if (Test-Path -LiteralPath $icoP) {
    try { $ni.Icon = New-Object System.Drawing.Icon($icoP) } catch { $ni.Icon = [System.Drawing.SystemIcons]::Application }
} else {
    $ni.Icon = [System.Drawing.SystemIcons]::Application
}
$ni.Text = '作战面板'
$ni.Visible = $true

$script:online = $false

$context = New-Object System.Windows.Forms.ApplicationContext

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miOpen = $menu.Items.Add('打开面板')
$miOpen.add_Click({
    try { Start-Process powershell.exe -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-File', ('"' + $openPs + '"')) } catch { Write-Log ('打开失败: ' + $_) }
})
$miState = $menu.Items.Add('状态：检测中…')
$miState.Enabled = $false
[void]$menu.Items.Add('-')
$miRestart = $menu.Items.Add('重启面板服务')
$miRestart.add_Click({
    Write-Log '菜单：重启面板服务'
    try { schtasks /run /tn PanelV2_KeepAlive | Out-Null } catch { Write-Log ('重启失败: ' + $_) }
    Start-Sleep -Seconds 3
    Update-State
})
$miLog = $menu.Items.Add('打开日志目录')
$miLog.add_Click({ try { Start-Process explorer.exe $logDir } catch {} })
[void]$menu.Items.Add('-')
$miExit = $menu.Items.Add('退出托盘')
$miExit.add_Click({
    Write-Log '菜单：退出托盘'
    try { $ni.Visible = $false; $ni.Dispose() } catch {}
    try { $context.ExitThread() } catch {}
})

function Open-Panel {
    try { Start-Process powershell.exe -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-File', ('"' + $openPs + '"')) } catch { Write-Log ('打开失败: ' + $_) }
}

function Update-State {
    $script:online = Test-Panel
    if ($script:online) {
        $ni.Text = '作战面板 · 运行中'
        $miState.Text = '状态：运行中'
    } else {
        $ni.Text = '作战面板 · 未运行（左键点击拉起）'
        $miState.Text = '状态：未运行'
    }
}

$ni.ContextMenuStrip = $menu
$ni.add_MouseClick({ if ($_.Button -eq [System.Windows.Forms.MouseButtons]::Left) { Open-Panel } })
$ni.add_DoubleClick({ Open-Panel })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 20000
$timer.add_Tick({ try { Update-State } catch {} })
$timer.Start()

Update-State
Write-Log ('托盘已启动（pid={0}，状态={1}）' -f $PID, $(if ($script:online) { '运行中' } else { '未运行' }))

try { [System.Windows.Forms.Application]::Run($context) } catch { Write-Log ('主循环异常: ' + $_) }
Write-Log '托盘已退出'
try { if ($script:gotMutex) { $script:mutex.ReleaseMutex() } } catch {}
try { $script:mutex.Dispose() } catch {}
