# 设置抽屉「重启服务」→ 真重启面板（8810）

- 时间：2026-10-29 03:1x–03:4x
- 起点：主人截图指出「设置 → 服务 → 重启服务」拉的是旧 8800，点下去只回「旧引擎已退役」
- 结论：**按钮现在重启 8810 本身**，点一次约 6 秒不可用，页面自动刷新；8800 彻底不在链路里

## 1. 改前是什么

| 层 | 改前 |
|---|---|
| 前端按钮 | `app/web/shell.html#btn-restart`「↻ 重启服务」，提示「拉起旧 8800 引擎」 |
| 前端确认框 | `shell.js::doRestart()`：「确认拉起旧 8800 引擎服务？」 |
| 后端 | `POST /api/shell/restart`（2026-10-26 旧端口下线时改）**固定返回** `{ok:false, engine:"retired"}`，不拉任何进程 |

即：按钮存在、文案过期、接口必失败。开发者改完后端也没有任何重启入口。

## 2. 改后链路

```
点按钮 → confirm
   → POST /api/shell/restart
        ├─ core 起分离 helper：powershell -File tools/restart_8810.ps1
        └─ 立即回 {ok:true, engine:"panel_v2", helper_pid}
   → 前端等 1.5s 后每 0.7s 探 /api/health（必须「先断一次再恢复」才 reload，防旧进程上误刷新）
helper（独立进程，2 秒后动作）
   → 按端口找监听 pid + 命令行含 uvicorn 的父进程 → Stop-Process
   → 等端口释放（≤10s）
   → tools/ensure_8810.ps1 -Once（自带 30s 健康轮询）
兜底：PanelV2_KeepAlive 计划任务每 5 分钟 ensure 一次
```

## 3. 两个实测坑（都已修）

1. **`DETACHED_PROCESS` 下 powershell.exe 直接退出。**
   一开始用 `creationflags=0x00000008 | 0x200`（DETACHED），helper 起来一行都跑不到（pid 在、日志空、脚本没执行）。
   对照实验（`_diag/test_restart_helper_20261029.py`）：DETACHED → 进程 5 秒内死、无日志；`CREATE_NO_WINDOW(0x08000000)|0x200` → 正常执行。
   **现用 CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP**，测试里锁了这一条（不许再写 DETACHED）。

2. **无控制台时 `Add-Content` 静默不落盘。**
   helper 用 `Add-Content -LiteralPath` 写日志，在分离进程里文件一直不出现（也不抛错）。
   改 `[System.IO.File]::AppendAllText($LogFile, $line, UTF8)` 后立刻正常。
   另一条：`.ps1` 必须是 **UTF-8 带 BOM**，否则 PowerShell 5.1 按 ANSI 读，中文注释会把语法搞崩。

## 4. 验证

### 4.1 接口（curl/PowerShell）

| 次数 | 结果 |
|---|---|
| 1 | `{ok:true, engine:panel_v2, helper_pid:26076}`；日志：`stopped pid 56348` → `port released` → `ensure 健康通过`；pid 56348 → 62528，停机 6 s |
| 2 | `helper_pid` 换了；日志里父进程报 `pid 66788 already gone`（先查存在再杀，不再报「找不到进程」）；pid 12408 → 64744 |

### 4.2 真实浏览器点按钮（headless Chrome + CDP，`_diag/verify_restart_button_20261029.mjs`）

```
页面就绪，健康={"ok":true,...}
点 #btn-restart …
confirm 弹窗 → 接受
断开过=true  自动刷新=true  导航次数=1  末次提示="重启中（helper 56532），请稍候…"  页面可用=true
RESULT ok:true
```

即：confirm → 接口成功 → 进程确实被杀（健康断一次）→ 页面自动 reload → 恢复可用。

### 4.3 测试

```
python -m pytest app/core/tests app/plugins/compat_watchlist/tests app/plugins/kline/tests -q
→ 231 passed
```

`test_shell_endpoints.py` 三条新断言：
- restart 必须起 `restart_8810.ps1`，flags 必须含 `CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP`，**不得含 DETACHED**；
- 脚本缺失 → 503 + `ok:false`（不谎报成功）；
- Popen 抛错 → 503 + detail 带异常类型。

## 5. 文件

| 文件 | 改动 |
|---|---|
| `app/core/main.py` | `shell_restart` 重写：起 helper + 立即返回；flags 与日志重定向见 §3 |
| `app/core/tests/test_shell_endpoints.py` | restart 三条用例重写（原「必须自报 retired」已删） |
| `app/web/shell.html` | 按钮文字「↻ 重启面板」；提示改为「8810，约 3–6 秒不可用，就绪后自动刷新」 |
| `app/web/shell.js` | `doRestart` 重写：确认框、ok 判定、健康轮询（先断后恢复才 reload，上限 90 s） |
| `tools/restart_8810.ps1` | 新增：分离 helper（延迟 → 杀端口进程 + uvicorn 父进程 → ensure） |

回退：`git checkout`（工作区无该文件版本控制）→ 用 `_backup`？本批未单独备份；原 `main.py`/`shell.html`/`shell.js` 可从源码历史取；`tools/restart_8810.ps1` 删掉即可回到「按钮必失败」状态（前端会提示重启失败，不会卡死）。

## 6. 未验证 / 遗留

- 「重新加载插件」按钮只跑 `boot()`（重拉前端清单），后端改动仍要重启进程——按需可改成同一个重启入口，未动。
- 前端 90 秒未恢复时的提示路径未在真机演练（只走了正常路径）。
- 重启期间正在进行的请求会失败（预期内）；盘中重启请避开连续竞价。
- 连续调试期（19:20–19:27，人工 + 脚本共点 4 次重启）观察到一次非预期退出：19:24:43 起来的进程在 19:27:26 前消失（`server.err` 无 traceback，Windows 事件日志无 python 崩溃记录），保活任务 19:27:33 拉起后至今稳定（采样 60 s 三次 /health 18–41 ms，`_restart.log` 无新增）。单次人工点击后未见复现；若下次点完按钮自频繁重启，先看 `_restart.log` 与 `_keepalive.log` 时间戳是否交叠。
