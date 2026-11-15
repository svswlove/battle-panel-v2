# 上线操作手册（M4 切流 · 2026-10-23 版）

> 一句话：**旧面板不动，新面板 8810 已在跑，每天只用看它。出问题停 8810 就回到原样。**
> 本文件面向"明天开始怎么用"，不解释架构。架构看 `ARCHITECTURE.md`，验收证据看 `M4_CUTOVER.md`。

---

## 1. 现在是什么状态

| 项 | 状态 |
|---|---|
| 新面板地址 | `http://127.0.0.1:8810`（左侧导航切六张卡） |
| 旧面板 | `8800` / `8002` / `8801` 全部照常运行，一行代码没改 |
| 六张卡 | 板块（含评分/板块内名次）· K线（含 AMV 四态背景带）· 活跃市值 · HCL 离散度 · 作战表 · 自选池 |
| 写操作 | **新版一个都没有**。签收、改单、急停、下单——全部仍在 8800 |
| 自动保活 | 每 5 分钟探活，挂了自动拉起（实测 2 秒恢复） |
| 每日自检 | 工作日 **08:50**（开盘前）与 **19:20**（18:00 灌库后）各跑一轮全套检查 |

## 2. 每天怎么用

### 2.1 开盘前（08:50 之后）

自检脚本已经自动跑过，看一行结果就够：

```powershell
Get-Content OH-WorkSpace\projects\panel-v2\data\panel_v2\logs\_scheduled.log -Tail 1
```

`exit=0` = 全绿（8810 健康 + 31 项新旧比对一致 + 8 项门禁 + 151 项测试）。
非 0 就去 `_scheduled_fail_<时间>.log` 看哪一项挂了。

### 2.2 盘中

直接开 `http://127.0.0.1:8810` 看盘。需要**写**的时候回 8800。

### 2.3 收盘后

19:20 自检会自动跑完，同样看 `_scheduled.log` 最后一行。
它验证的是"跨过 16:10 主链 + 18:00 灌库之后，新面板读到的是新产物，不是昨天的旧文件"。

## 3. 出问题怎么办

### 3.1 一张卡坏了

打开该插件 `app/plugins/<id>/plugin.json`，把 `"enabled": true` 改 `false`，然后：

```powershell
# 只重启 8810（不要动 8800/8002/8801）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*port 8810*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

保活任务会在 5 分钟内把它拉起来，只少了那张卡，其它五张不受影响。

### 3.2 整个 8810 不可用

**什么都不用做**——保活任务每 5 分钟拉一次。想立刻拉：

```powershell
schtasks /Run /TN "PanelV2_KeepAlive"
```

### 3.3 要彻底回退（回到切流前）

```powershell
schtasks /End /TN "PanelV2_KeepAlive"
schtasks /Change /TN "PanelV2_KeepAlive" /DISABLE
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*port 8810*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

停掉 8810 之后，8800/8002/8801 立刻就是唯一入口。

**但 2026-10-23 起多一步**：新版有了**受控写**（自选池增删改会改 `data/panel/watchlist.json`），
所以“停 8810 就恢复原样”只对**只读部分**成立。回退时还要把被改过的文件恢复：

```powershell
# 1) 看审计：哪一天被谁改过、改了几次
Get-Content "projects\panel-v2\data\panel_v2\backups\watchlist\audit.log" -Tail 20
# 2) 恢复最后一次写入之前的那份（备份文件按时间戳命名，取最近一个早于首次写入的）
Copy-Item "projects\panel-v2\data\panel_v2\backups\watchlist\<时间戳>.json" `
          "data\panel\watchlist.json" -Force
```

其它文件（dashboard / battle_plan / positions / …）仍为**只读**，不需要恢复。
将来每加一个可写文件，这里就多一行恢复动作；`docs/CONTRACT.md §4.2` 是唯一清单。

要恢复新版：`schtasks /Change /TN "PanelV2_KeepAlive" /ENABLE` 后 `schtasks /Run /TN "PanelV2_KeepAlive"`。

## 4. 哪些东西**不要**在 8810 上找

| 想做什么 | 去哪 |
|---|---|
| 签收作战表 / 改单 / 急停 | 8800（新版永久只读） |
| 下单、撤单 | 8800 |
| 触发 AMV 重算（`/api/amv_refresh`） | 8800 上点按钮；新版不提供 |
| 看持仓市值（`positions_list`） | 8800；新版不接实时行情 |
| K 线的当日未收盘 bar、黄白线/AMV 指标列 | 8800；新版只保证**已完成日线** |
| 15 年历史指数收盘（`/api/index_closes`、`/api/hcl_idx`） | 8002 |
| 选股页、复盘页 | 8801 / 8800 |

## 5. 自检都查什么（31 + 8 项）

| 层 | 内容 |
|---|---|
| 8810 健康 | `/api/health` 与六个插件的 `/_plugins/health` |
| 新旧双跑（31 项） | 同一问题问新旧两套：板块树/榜/序列/成分/语义榜/趋势/板块TOP/资金流/贡献分项/评分轨迹/分钟四端点 · AMV 三份原始 JSON · 作战表与告警 · K线六标的 · 个股四态背景带 · 自选池条目 |
| 门禁（8 项） | 8810 健康、插件健康、双跑、AMV 原始 JSON 精确相等、作战表无写接口（POST→405）、**插件代码零写**（扫全部 py/js）、K线边界声明、pytest 全套 |
| 判 fail 的规则 | 旧端有、新版没的字段 → fail；K线共同日期为 0 → fail；并列名次互换 → 放行（按名次集合判定）；北交所 920xxx 的已知旧端缺陷 → 单列不计 fail |

## 6. 今天还需要人工确认的一件事

板块卡的「评分」「板块内名次」两列是昨晚新加的，结构已实测（11 列、29 行名次全显示、无溢出），
但**视觉上顺不顺眼**只能你看。不喜欢就说，改宽度或换位置都是小改。

---

## 附：关键路径

| 用途 | 路径 |
|---|---|
| 每日自检日志 | `projects/panel-v2/data/panel_v2/logs/_scheduled.log` |
| 8810 启动/请求日志 | `data/panel_v2/logs/server.out.log`（请求）；`server.err.log`（启动与异常）；上一轮存 `server.prev.log` |
| 保活日志（只在拉起/失败时写） | `projects/panel-v2/data/panel_v2/logs/_keepalive.log` |
| 双跑明细 | `projects/panel-v2/data/panel_v2/shadow/` |
| 门禁结果 | `projects/panel-v2/docs/cutover_gate_latest.md` |
| 切流评审与观察记录 | `projects/panel-v2/docs/M4_CUTOVER.md` |
| 今晚这轮的验收细节 | `projects/panel-v2/docs/m4_score_trail_notes.md` |
| 口径唯一源（评分/名次） | `夏天-activity/工具/panel_scores.py` |
