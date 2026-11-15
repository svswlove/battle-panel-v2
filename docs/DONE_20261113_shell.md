# 作战面板独立壳（WebView2，2026-11-13）

## 为什么做

桌面快捷方式走 Chrome `--app` 这条路反复出问题：浏览器实例在跑时 `--app` 请求被已有实例接管，窗口可能不产生；按窗口标题找已有窗口又会把浏览器标签页当成面板。2026-11-13 现场实测确认了后者（浏览器窗口标题 `作战面板 v2 - Google Chrome` 被误认为面板窗口，日志「激活已有窗口 hwnd=16386352」）。

主人决定不再修启动器，直接做独立外壳。本机 WebView2 Runtime 已装（157.0.4341.46），不依赖任何浏览器实例。

## 是什么

`projects/panel-v2/shell/PanelShell/`：.NET 6 WinForms + Microsoft.Web.WebView2 的窗口壳。双击 → 一个属于自己的窗口 → 加载 `http://127.0.0.1:8810`。

| 项 | 值 |
|---|---|
| 源码 | `projects/panel-v2/shell/PanelShell/`（csproj + 5 个 .cs） |
| 构建 | `dotnet publish -c Release -r win-x64 --self-contained false -o ../publish` |
| 部署 | `C:\ProgramData\PanelV2\shell\`（publish 内容，不含 userdata） |
| 入口 | 桌面「作战面板.lnk」→ `PanelShell.exe` |
| 用户数据 | `%LOCALAPPDATA%\PanelV2Shell\WebView2Data`（写不进去时依次退到 exe 同目录 `userdata`、`%TEMP%`） |
| 日志 | `%LOCALAPPDATA%\PanelV2Shell\shell.log`（失败时退到 `%TEMP%\PanelV2Shell\shell.log`） |
| 窗口状态 | `%LOCALAPPDATA%\PanelV2Shell\window.json`（位置/大小/最大化） |

## 行为

- **单实例**：`Local\PanelV2Shell` 互斥体；第二次双击广播一条自定义消息，已有窗口被叫到前台，不新开进程
- **服务自愈**：启动先探 `/api/health`；不通就调 `tools/ensure_8810_once.ps1` 拉起，每秒重试最多 45 秒，就绪后自动进面板
- **快捷键**：F5 刷新，F12 开发者工具
- **大小记忆**：关窗时存，下次还原；位置落在所有屏幕之外时拉回主屏
- **安静退出**：任何初始化失败都会弹一次明确提示后退出进程，不留占着单实例锁的空壳

## 验证（2026-11-13 实机）

| 项 | 结果 |
|---|---|
| 双击桌面图标 | 窗口出现，标题「作战面板 v2」，有标准标题栏，无地址栏/标签栏 |
| 页面内容 | 仪表盘正常渲染（总资产 473,596.70 / 持仓 / 今日成交 等卡片与数据） |
| 单实例 | 再双击一次：进程数仍为 1，日志 `Mutex createdNew=False -> 广播唤醒后退出` |
| 服务拉起 | 日志 `探活 8810… → 8810 已就绪 → 导航` |
| 窗口显示 | Win32 枚举：`vis=True rect=168,12,1200x800` |

## 回滚

1. 桌面「作战面板.lnk」Target 改回 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`，参数 `-NoProfile -WindowStyle Hidden -File "<项目>\tools\open_panel.ps1"`
2. 旧的 `tools/open_panel.ps1` 原样保留，未删

## 已知边界

1. **依赖 .NET 6 桌面运行时**（本机有 6.0.18）。运行时被卸载则壳起不来，需重装或改成 self-contained 发布（体积约 +150MB）。
2. **localStorage 换了一份**：WebView2 的存储独立于 Chrome / ego，面板里的界面偏好（K 线周期、展开状态、最近列表等）从默认值重新开始，需要重新点一次。
3. **窗口默认 1200×800**（按屏幕工作区上限收的），主人的屏幕是 1536×864；手动调整后会记住。
4. **不做开机自启**，也没有固定到任务栏；要的话在 exe 上右键固定即可。
5. 壳只负责窗口与服务拉起，8810 进程本身仍由 `PanelV2_KeepAlive` 计划任务保活，两条线不互相依赖。
