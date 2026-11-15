# 打开作战面板 v2（127.0.0.1:8810）。双击入口：桌面「作战面板.lnk」/ OH-WorkSpace\作战面板.cmd
#
# ── 2026-11-13 重写：为什么又动了这一版 ──────────────────────────────
# 老版（2026-11-08）靠「窗口标题里含『作战面板』且进程名是 chrome」找已有窗口。
# 这个判据会误命中普通 Chrome 浏览器窗口——只要浏览器里开着面板标签页，
# 窗口标题就是「作战面板 v2 - Google Chrome」，于是脚本以为面板已经开着，
# 只把那个浏览器窗口提到前面，永远不再开独立的 App 窗口。
# 用户看到的就是「双击后跳出个带标签栏的浏览器，不是单独的作战面板」，
# 而且只要那个标签页还开着，怎么修都不见效。实测复现：2026-11-13 00:20:30
# 日志「激活已有窗口 hwnd=16386352」，屏幕上是 Chrome 普通窗口，无 app 窗口。
#
# 新判据：只认「本脚本亲手开出来的那个窗口」。
#   1) 成功开出后把 hwnd 记进 data/panel_v2/launcher_state.json
#   2) 下次双击先验这个 hwnd 还活着（窗口在 + 标题对 + 进程是 chrome）→ 直接置前
#   3) 没记录或已失效 → 先给现有 chrome 窗口拍快照，再跑 chrome --app，
#      然后只认「快照里没有的新窗口」。浏览器里的老标签页天然被排除。
#
# 另外两处加固：
#   - 置前用 AttachThreadInput + SetForegroundWindow，失败再 SwitchToThisWindow
#     （老版单靠 SetForegroundWindow，实测会 前台=False，用户以为没反应）
#   - 兜底不用 Start-Process $url：本机默认浏览器是 ego lite，不是 Chrome
#
# 退出码：0 已打开；1 8810 未就绪。日志：data/panel_v2/logs/open_panel.log

$ErrorActionPreference = 'Continue'
$url = 'http://127.0.0.1:8810'
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
$titlePart = '作战面板'

$dataDir = Join-Path $PSScriptRoot '..\data\panel_v2'
$logDir = Join-Path $dataDir 'logs'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$log = Join-Path $logDir 'open_panel.log'
$stateFile = Join-Path $dataDir 'launcher_state.json'

function Write-Log([string]$m) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

Write-Log ('启动器被调用（pid={0}）' -f $PID)

# ---------------- Win32 小工具 ----------------
if (-not ('PanelWin' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Diagnostics;
using System.Collections;
using System.Runtime.InteropServices;

public class PanelWin
{
    private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr h);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] private static extern void SwitchToThisWindow(IntPtr h, bool fAltTab);

    // Chromium 系（Chrome / Edge）的顶层窗口类名都是 Chrome_WidgetWin_1。
    // 光看类名不够，再用进程名确认是 chrome.exe，避免把 Edge 窗口算进来。
    private static bool IsChromiumTopLevel(IntPtr h, string exeName)
    {
        StringBuilder cn = new StringBuilder(128);
        GetClassName(h, cn, 128);
        if (cn.ToString() != "Chrome_WidgetWin_1") return false;
        uint pid;
        GetWindowThreadProcessId(h, out pid);
        try
        {
            return string.Equals(Process.GetProcessById((int)pid).ProcessName, exeName, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    // 当前所有 chrome 顶层窗口：key = hwnd(十进制字符串)，value = 标题
    public static Hashtable Snap(string exeName)
    {
        Hashtable t = new Hashtable();
        EnumWindows(delegate(IntPtr h, IntPtr l)
        {
            if (!IsChromiumTopLevel(h, exeName)) return true;
            StringBuilder sb = new StringBuilder(512);
            GetWindowText(h, sb, 512);
            string title = sb.ToString();
            if (title.Length == 0) return true;
            t[h.ToInt64().ToString()] = title;
            return true;
        }, IntPtr.Zero);
        return t;
    }

    public static string TitleOf(IntPtr h)
    {
        StringBuilder sb = new StringBuilder(512);
        GetWindowText(h, sb, 512);
        return sb.ToString();
    }

    public static bool IsForeground(IntPtr h)
    {
        return h != IntPtr.Zero && GetForegroundWindow() == h;
    }

    // 记住的窗口还活着吗：句柄有效 + 还是 chrome 的窗口 + 标题里还有关键字
    public static bool Alive(IntPtr h, string titlePart, string exeName)
    {
        if (h == IntPtr.Zero || !IsWindow(h)) return false;
        if (!IsChromiumTopLevel(h, exeName)) return false;
        return TitleOf(h).IndexOf(titlePart, StringComparison.OrdinalIgnoreCase) >= 0;
    }

    // 硬置前：先还原（若最小化），再借前台线程的输入队列抢焦点。
    // 单独 SetForegroundWindow 在「调用方不是前台进程」时会被系统忽略（老版踩过）。
    public static bool Activate(IntPtr h)
    {
        if (h == IntPtr.Zero || !IsWindow(h)) return false;
        ShowWindow(h, IsIconic(h) ? 9 : 5); // 9=SW_RESTORE, 5=SW_SHOW

        IntPtr fg = GetForegroundWindow();
        uint fgPid;
        uint fgThread = (fg == IntPtr.Zero) ? 0 : GetWindowThreadProcessId(fg, out fgPid);
        uint me = GetCurrentThreadId();
        bool attached = false;
        if (fgThread != 0 && fgThread != me)
        {
            attached = AttachThreadInput(me, fgThread, true);
        }
        bool ok;
        try
        {
            SetForegroundWindow(h);
            BringWindowToTop(h);
            ok = (GetForegroundWindow() == h);
            if (!ok) { SwitchToThisWindow(h, true); ok = (GetForegroundWindow() == h); }
        }
        finally
        {
            if (attached) AttachThreadInput(me, fgThread, false);
        }
        return ok;
    }
}
'@
}

# 激活并确认还留在前台。
# 只调一次 SetForegroundWindow 不够：QQ / 微信这类会弹窗抢焦点的程序，
# 抢回来不到半秒就能把面板顶下去，用户看到的就是「双击了，没反应」。
function Invoke-Activate([IntPtr]$h, [string]$tag) {
    for ($i = 1; $i -le 3; $i++) {
        $ok = [PanelWin]::Activate($h)
        Start-Sleep -Milliseconds 260
        if ([PanelWin]::IsForeground($h)) {
            Write-Log ("{0} 已置前 hwnd={1}（第 {2} 次确认）" -f $tag, $h, $i)
            return $true
        }
        $ok = $false
    }
    Write-Log ("{0} 置前失败 hwnd={1}" -f $tag, $h)
    return $false
}

# ---------------- 1) 服务探活 ----------------
function Test-Panel {
    try {
        $r = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (-not (Test-Panel)) {
    Write-Log '8810 不通 -> 触发 PanelV2_KeepAlive'
    schtasks /run /tn PanelV2_KeepAlive 2>&1 | Out-Null
    $ready = $false
    for ($i = 0; $i -lt 8; $i++) {
        Start-Sleep -Seconds 2
        if (Test-Panel) { $ready = $true; break }
    }
    if ($ready) { Write-Log '8810 已就绪' }
    else { Write-Log '拉起失败：16 秒内 8810 未就绪（继续开窗口，页面会显示连不上）' }
} else {
    Write-Log '8810 探活 OK'
}

# ---------------- 2) 上次亲手开出来的窗口还在吗 ----------------
$state = $null
if (Test-Path -LiteralPath $stateFile) {
    try { $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $state = $null }
}
if ($state -and $state.hwnd) {
    $h = [IntPtr][int64]$state.hwnd
    if ([PanelWin]::Alive($h, $titlePart, 'chrome')) {
        [void](Invoke-Activate $h '上次记录的面板窗口')
        exit 0
    }
    Write-Log ("记录的面板窗口已失效 hwnd={0} -> 重新开" -f $state.hwnd)
}

# ---------------- 3) 给现有 chrome 窗口拍快照 ----------------
$before = [PanelWin]::Snap('chrome')
$beforeKeys = @($before.Keys)
Write-Log ("现有 chrome 窗口 {0} 个{1}" -f $beforeKeys.Count, $(if ($beforeKeys.Count -gt 0) { '：' + (($beforeKeys | ForEach-Object { $before[$_] }) -join ' | ') } else { '' }))

# ---------------- 4) 开 App 窗口 ----------------
if (-not (Test-Path -LiteralPath $chrome)) {
    Write-Log "找不到 Chrome：$chrome"
    Write-Log '失败：无浏览器可用'
    exit 1
}
Start-Process -FilePath $chrome -ArgumentList "--app=$url", '--window-size=1500,1000'
Write-Log '已请求 chrome --app 新窗口'

# ---------------- 5) 只认「快照里没有的新窗口」 ----------------
$deadline = (Get-Date).AddSeconds(15)
$target = [IntPtr]::Zero
$targetTitle = ''
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
    $now = [PanelWin]::Snap('chrome')
    foreach ($k in @($now.Keys)) {
        if ($before.ContainsKey($k)) { continue }
        $t = [string]$now[$k]
        if ($t.IndexOf($titlePart, [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
        $target = [IntPtr][int64]$k
        $targetTitle = $t
        break
    }
    if ($target -ne [IntPtr]::Zero) { break }
}

if ($target -ne [IntPtr]::Zero) {
    [void](Invoke-Activate $target '新面板窗口')
    Write-Log ("新面板窗口 hwnd={0} 标题={1}" -f $target, $targetTitle)
    $saved = @{ hwnd = $target.ToInt64(); title = $targetTitle; saved_at = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') }
    try {
        $saved | ConvertTo-Json -Compress | Set-Content -LiteralPath $stateFile -Encoding UTF8
    } catch {
        Write-Log ("状态文件写入失败：{0}" -f $_.Exception.Message)
    }
    exit 0
}

# ---------------- 6) 没等到新窗口 ----------------
# 两种可能：a) Chrome 复用了已有的 App 窗口（同一 URL 的 app 窗口会复用，不产生新句柄）
#          b) Chrome 异常，--app 被吞
# 只在「复用」时这里才有意义：把标题对得上的旧窗口挨个试置前。
$reused = [IntPtr]::Zero
$reusedTitle = ''
foreach ($k in $beforeKeys) {
    $t = [string]$before[$k]
    if ($t.IndexOf($titlePart, [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
    $h = [IntPtr][int64]$k
    if ([PanelWin]::Alive($h, $titlePart, 'chrome') -and [PanelWin]::Activate($h)) { $reused = $h; $reusedTitle = $t }
}
if ($reused -ne [IntPtr]::Zero) {
    Write-Log ("15 秒无新窗口；已置前既有同名窗口 hwnd={0} 标题={1}（可能是 Chrome 复用了旧 App 窗口）" -f $reused, $reusedTitle)
    $saved = @{ hwnd = $reused.ToInt64(); title = $reusedTitle; saved_at = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') }
    try { $saved | ConvertTo-Json -Compress | Set-Content -LiteralPath $stateFile -Encoding UTF8 } catch {}
    exit 0
}

# 真正兜底：明确用 Chrome 开普通标签页（不用 Start-Process $url，本机默认浏览器是 ego lite）
Write-Log '15 秒内既无新窗口也无同名窗口 -> 兜底用 Chrome 开普通标签页'
Start-Process -FilePath $chrome -ArgumentList '--new-tab', $url
Write-Log '兜底已执行'
exit 1
