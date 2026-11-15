# 桌面快捷方式打不开独立面板窗口（2026-11-13 修）

## 症状

双击桌面「作战面板.lnk」，跳出来的不是独立的 App 窗口，而是一个带标签栏、地址栏的普通 Chrome 窗口；或者干脆没反应。

## 根因（已复现）

老版 `tools/open_panel.ps1` 靠 **窗口标题含「作战面板」+ 进程名 chrome** 判断"面板窗口已经开着"。

只要 Chrome 里开着面板的标签页，那个浏览器窗口的标题就是 `作战面板 v2 - Google Chrome`，判据成立 → 脚本只把浏览器窗口提到前面 → 永远不再开独立的 App 窗口。

复现证据（2026-11-13 00:20:30 日志）：

```
00:20:30 启动器被调用（pid=84172）
00:20:31 8810 探活 OK
00:20:31 激活已有窗口 hwnd=16386352 前台=True    ← 这个 hwnd 是浏览器窗口
```

当时屏幕上是一个普通 Chrome 窗口，没有任何 `--app` 窗口。

该场景不是偶发：`Chrome\User Data\Default\Sessions\Tabs_13438788052417882` 里就有 `127.0.0.1:8810`，说明浏览器里确实长期开着面板标签页。只要那个标签页在，老逻辑就必然走错分支，修多少次冷启动路径都没用。

第二处坑：老版置前只调 `SetForegroundWindow`，日志出现过 `前台=False`，用户感知就是"双击了没反应"。

## 改法（只改 `tools/open_panel.ps1`）

判据从「标题匹配」换成「只认本脚本亲手开出来的窗口」：

1. 成功开出窗口后，把 hwnd 记进 `data/panel_v2/launcher_state.json`
2. 下次双击先验这个 hwnd：窗口还在 + 标题还含「作战面板」+ 进程还是 chrome → 直接置前（0.3 秒内返回）
3. 记录失效 → 先给现有 chrome 顶层窗口拍快照，再跑 `chrome --app=...`，**只认快照里没有的新窗口**。浏览器里的老标签页天然被排除
4. Chrome 复用了旧的 App 窗口（不产生新句柄）时，才回退到按标题找已有窗口

顺带的加固：

- 置前改为 `AttachThreadInput` + `SetForegroundWindow`，失败再 `SwitchToThisWindow`；再连做 3 次、每次 260ms 复核前台，防 QQ/微信这类弹窗把窗口顶下去
- `GetCurrentThreadId` 从 `user32.dll` 改到 `kernel32.dll`（写错会导致置前整段抛异常、被 `Continue` 吞掉，日志只显示「前台=」空值）
- 兜底不再用 `Start-Process $url`：本机默认浏览器是 **ego lite**，会打开浏览器而不是 Chrome 窗口；改为明确 `chrome --new-tab`

## 验证（2026-11-13 00:17–00:25，全部实机跑）

| 场景 | 条件 | 结果 |
|---|---|---|
| A 冷启动 | Chrome 全退，无 state | 开新窗口 hwnd=33229340，1.6 秒出现并置前 |
| B 浏览器里有同名标签页 | Chrome 普通窗口标题 `作战面板 v2 - Google Chrome`，无 App 窗口，无 state | 开新 App 窗口 hwnd=9634300，2 秒出现；浏览器窗口未被误认 |
| C 已有 App 窗口 + state | 连按两次 | 第 2 次 0.3 秒内置前，不重复开窗口 |
| D state 丢失但窗口在 | 删 state 后双击 | Chrome 不复用，开出新窗口（多一个，见下） |

截图确认：前台窗口标题 `作战面板 v2`，无标签栏、无地址栏。

## 回滚

改动只在 `projects/panel-v2/tools/open_panel.ps1`（git 已跟踪，`git checkout` 即回滚）。`data/panel_v2/launcher_state.json` 是运行时产物，删掉不影响启动（只会退化成"新开窗口"）。

## 已知边界

1. **state 文件丢失时会多开一个窗口**：Chrome 不复用同 URL 的 App 窗口（实测场景 D），所以记录丢了就是新开。只有手动删/首次运行才会遇到。
2. **窗口被别的程序抢焦点**：已做 3 次重试，但 Windows 前台规则限制，持续被抢（如 QQ 连续弹新消息）时无法保证一直留在最前。
3. **换 Chrome 安装路径**：脚本里写死 `C:\Program Files\Google\Chrome\Application\chrome.exe`，与老版一致。
4. 若将来要彻底摆脱"依赖 Chrome 实例"的行为差异，可做 WebView2 壳（`docs/desktop_launcher.md` 里有方向），代价是面板的 localStorage 界面偏好会重置一次，本次没走这条路。
