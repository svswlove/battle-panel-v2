# 8810 启动/健康保障（`tools/ensure_8810.ps1`）

**用途**：补 M4 评审 §1.1 G4 指出的缺口——8810 不在 `run_daemons.DAEMONS`、不在 `watchdog` 探活范围内，挂了没人拉起。
**范围**：只管 8810。不探活、不启动、不停止旧 8800 / 8002 / 8801。
**状态**：2026-10-22 落地，单次模式与拉起分支已实测（见文末「实测记录」）。

## 1. 用法

脚本在 `tools\` 下，路径一律相对项目根。

### 1.1 单次检查（默认）

```powershell
cd <panel-v2>
.\tools\ensure_8810.ps1          # 等价于 -Once
```

行为：

| 情况 | 动作 | 退出码 |
|---|---|---|
| `GET http://127.0.0.1:8810/api/health` 返回 200 且 `ok=true` | 不动，打印「已在运行，不重复启动」 | `0` |
| 不健康、端口未监听 | 用隐藏窗口执行项目内 `run.ps1`，每 1 秒轮询，最长 30 秒 | 成功 `0` / 超时 `1` |
| 不健康、端口已在监听 | 判为「正在启动」，只等待不重复拉起（避免两个进程抢 8810） | 就绪 `0` / 超时 `1` |
| 找不到 `run.ps1` 或无法创建进程 | 直接失败，不重试 | `1` |

适合手工执行、或挂到任意外部调度器上做一次性探活。

### 1.2 循环模式

```powershell
.\tools\ensure_8810.ps1 -Loop -IntervalSeconds 300
```

- `-IntervalSeconds` 默认 300，**下限 30**；传小于 30 的值按 30 执行并打印一行提示。
- 每轮独立判定，单轮失败不退出循环，下一轮重试。
- `Ctrl-C` 停止（`finally` 里打印一行「循环监控已停止」）。
- 循环模式不写状态文件、不写日志文件，输出只到控制台。要留痕就在启动时重定向：`... *> ensure_8810.log`。

### 1.3 参数组合

`-Once` 与 `-Loop` 属于不同参数集，不能同时传。不传任何参数时按 `-Once`。

## 2. 与旧 watchdog 的边界

| 项 | 旧 watchdog / run_daemons | 本脚本 |
|---|---|---|
| 覆盖端口 | 8800 `/api/health`、8002 `/health`、8801 `/api/shell/health` | **只有 8810** `/api/health` |
| 8810 是否在内 | 否。`watchdog.py` / `run_daemons.py` 对 `8810` 命中 **0 次** | 是，唯一目标 |
| 启动方式 | `run_daemons` 注册常驻 + watchdog 自愈拉起（窗口 600 秒内单目标最多 2 次） | 直接调项目内 `run.ps1`，不经 `run_daemons` |
| 轮询间隔 | 300 秒一轮（`DAEMON_INTERVAL_S`） | `-Once` 一次；`-Loop` 默认 300 秒，最小 30 秒 |
| 告警 | 有告警链（预警/推送） | **无告警**，只写控制台与退出码 |
| 改动范围 | 旧仓 `engine/executor/*.py` | 项目内 `tools/ensure_8810.ps1` + 本文件 |

**互不干扰的三条保证**：

1. 只探 8810，只对 8810 调 `run.ps1`。不 import、不调用旧仓任何模块。
2. 不 kill 任何进程。发现 8810 已监听但未健康时选择等待，而不是先杀再拉。
3. 不注册计划任务、不改旧仓文件、不写项目外文件。

## 3. 为什么观察期先不改旧仓

M4 §4.3 的结论：观察期内不改 `run_daemons.py` / `watchdog.py`，用独立入口拉起 8810。理由：

1. **「旧一行不动」是切流评审的前置铁律**。在 `run_daemons.DAEMONS` 加一条 8810 注册、在 `watchdog` 加一路探活，表面是两行，实际改的是旧仓常驻链——一旦旧链因此出问题，回退路径本身就不成立了，而回退路径正是这周唯一的安全网。
2. **旧仓自愈有节流，不适合当 8810 的唯一保障**。`SELFHEAL_WINDOW_S = max(下限, 300 × 2)`，单目标窗口内最多自愈 2 次；8810 处在「可能被反复重启调试」的阶段，撞上限流后反而静默。
3. **回滚成本**。本脚本是新增文件，删掉即回到「8810 手工起」的原状，不需要恢复旧仓。改动旧仓则要留 diff、跑 `engine/tests/test_run_daemons.py` 与 `test_watchdog*.py`，回滚成本高一个量级。
4. **失败隔离**。本脚本出问题最多是「8810 没被拉起」，不影响 8800 / 8002 / 8801 的现有保障链。

观察期满、8810 稳定后是否并入 `run_daemons` 自愈，按 M4 §4.2 另行决定；届时本脚本可保留为手工应急入口。

## 4. 如何手工停止

### 4.1 停循环监控

在跑 `-Loop` 的那个窗口按 `Ctrl-C`。窗口已关但进程还在时：

```powershell
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
  Where-Object { $_.CommandLine -like '*ensure_8810*' } |
  Select-Object ProcessId, CommandLine
# 确认后
Stop-Process -Id <pid>
```

只停监控，不影响已经跑起来的 8810。

### 4.2 停 8810 本身

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*port 8810*' } |
  Select-Object ProcessId, ParentProcessId, CommandLine
```

正常情况会看到两个：父进程（`envs\qmt312\python.exe -m uvicorn`）和它派生的子进程。**停父进程即可**，子进程随之退出：

```powershell
Stop-Process -Id <父进程 pid>
```

若只停了子进程，父进程可能留下空壳，届时一并停掉父进程。

### 4.3 先停监控再停 8810

顺序很重要：`-Loop` 还在跑时停 8810，下一轮（≤ 间隔秒数）就会把它拉回来。要彻底停，先 `Ctrl-C` 停监控，再停 8810。

### 4.4 确认已停

```powershell
try { (Invoke-WebRequest 'http://127.0.0.1:8810/api/health' -UseBasicParsing -TimeoutSec 3).StatusCode } catch { '已停' }
```

## 5. 实测记录（2026-10-22）

| 用例 | 条件 | 结果 |
|---|---|---|
| A `-Once`（默认参数同） | 8810 在跑（pid 19536 父 / 66632 子） | `exit 0`，打印「已在运行，不重复启动」，前后 8810 进程数均为 2，**未重复启动** |
| B 不健康 → 拉起成功 | 临时目录副本，端口改 18810，`run.ps1` 指向一个返回 `{"ok":true}` 的测试服务 | `exit 0`，约 1 秒内就绪，端口 18810 返回 `ok:true` |
| C 不健康 → 拉起失败 | 同上副本，端口 18811，`run.ps1` 为空壳 | `exit 1`，等待 4 秒后打印「启动超时」 |
| D `-Loop -IntervalSeconds 5` | 端口 18810 | 打印「间隔 5 秒低于下限，按 30 秒执行」，10 秒内只跑 1 轮（未按 5 秒空转） |
| E Ctrl-C | 向 D 的控制台进程发送 Ctrl-C | 进程退出，`finally` 打出「循环监控已停止」并落盘，无残留 |
| F 端口已监听但健康失败 | 端口 18813 挂一个只监听、不回合法 JSON 的服务，等待窗口 3 秒 | `exit 1`，打印「按正在启动处理，等待而不重复拉起」，**未重复拉起、未杀该进程** |
| G 同会话调用 | `& ensure_8810.ps1 -Once` 后继续执行调用方语句 | 调用方存活，`$LASTEXITCODE=0`，`exit` 未吞掉会话 |

B / C / D / E / F 在系统临时目录（`%TEMP%\ensure8810_test`、`%TEMP%\ensure8810_ctrlc`、`%TEMP%\ensure8810_dumb`）中执行，用改端口与改 `run.ps1` 指向的方式验证，**未触碰 8810，未杀任何 8810 进程**；三个测试目录已清理，18810 / 18812 / 18813 无残留进程。

**未验证**：`run.ps1` 真实启动失败（如 Python 缺失、端口被占）时的超时路径——F 用替身服务覆盖了「等不到健康」的逻辑，但没有真的让 `run.ps1` 失败一次。观察期内首次真实拉起时留意一下。

## 5.1 git 边界

`projects/panel-v2` 不是独立仓库，它位于 `OH-WorkSpace` 这个更大的仓库内。所以 `git status` 会显示大量无关改动。本次只新增两个文件：

```
?? projects/panel-v2/tools/ensure_8810.ps1
?? projects/panel-v2/docs/launcher.md
```

未提交，未暂存，未改 `run.ps1` 及其他项目文件。

## 6. 已知限制

1. **无告警**。脚本只给退出码和控制台输出。要告警需外部调度器读退出码，或另接通知链。
2. **卡死不重启**。8810 进程活着但健康检查持续失败时，脚本按「正在启动」等待，不杀不重启——避免在观察期制造双进程抢端口。需要重启时按 §4.2 手工停掉，下一轮自然拉起。
3. **不保证单实例**。若在脚本之外又手工跑一次 `run.ps1`，会出现端口占用冲突，由 uvicorn 自身报错，脚本不管。
4. **只认 `ok=true`**。`/api/health` 返回 200 但 `ok` 不为 `true` 时按不健康处理。
5. **不探插件健康**。`/_plugins/health` 的插件级检查不在本脚本范围；插件红了但核心健康时，脚本视为正常。
