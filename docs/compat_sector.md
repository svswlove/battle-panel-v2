# compat_sector 插件报告（旧前端板块域 9 端点）

- 日期：2026-10-23
- 任务书：`docs/task_compat_sector.md`
- 交付：`app/plugins/compat_sector/{plugin.json, backend.py, tests/test_compat_sector.py}`

> **先看 §0**：任务要求「开工前 curl 8002 / 8800 逐路径确认端口」，本执行环境**没有 shell 与网络执行能力**（工具集只有文件读写/搜索），因此**无法真跑 curl、pytest、cutover_gate，也无法重启 8810**。本报告用「旧端源码级核实」替代 curl，并把所有未实测项在 §7 明确标出。请派单方按 §7 清单补跑。

---

## 0. 端口归属结论（源码级，替代 curl）

任务书表格 9 行全写「8002?」，实测**全部在 8800**：

| 路径 | 注册位置（唯一实现） | 8002 侧 |
|---|---|---|
| `/api/sector_card.json` | `engine/server.py:2050` `api_sector_card` | 无 |
| `/api/sector_intraday.json` | `engine/server.py:6792` `api_sector_intraday` | 无 |
| `/api/sector_spark.json` | `engine/server.py:2158` `api_sector_spark` | 无 |
| `/api/sector_track.json` | `engine/server.py:2798` `api_sector_track` | 无 |
| `/api/sector_track_backfill_status` | `engine/server.py:2024` | 无 |
| `/api/sector/health` | `engine/server.py:1859`（**反向 HTTP 探活 8002**） | 8002 自身只有 `/health` |
| `/api/etf_sector_map.json` | `engine/server.py:2845` | 无 |
| `/api/breadth_880.json` | `engine/server.py:978` | 无 |
| `/api/temperature_latest.json` | `engine/server.py:857` | 无 |

依据：`夏天-activity/工具/dashboard/server.py:1861-1929` 的 `handle()` 路由表逐条核对（含 `/api/card_live`、`/api/board_flow`、`/api/minute_*` 等 20 余条），**上述 9 条一个都不在**；其 `/health` 自述 `endpoints` 列表也无一条。

**结论**：无一条属于「旧端不可用」→ 9 个端点**全部实现**。只有 §5、§6 三处语义/数据源必须降级，逐条说明。

---

## 1. 实现方式总表

| 端点 | 类型 | 数据源（新） | 关键点 |
|---|---|---|---|
| `/sector_card.json` | 组装 | `ctx.db_read("amv_board.db")` + `ctx.lib("amv_regime").sma` + `ctx.lib("indicators")` | SVG path 逐字照旧（x=**下标**，100x40 / 100x20） |
| `/sector_spark.json` | 组装 | 同上 | 100x24；len<5 或有 None → 跳过 |
| `/sector_intraday.json` | 静态转发 | `ctx.panel_json("amv_live_sector_track")` **← 白名单缺，见 §4** | 只回请求码；无轨迹→`ok:false,count:0` |
| `/sector_track.json` | 组装 | panel 同上 + `amv_board.db` 权重基线 | 日期键去横杠比较；`stale` 仅连续竞价判 |
| `/sector_track_backfill_status` | 降级骨架 | **无**（不读 engine/state，红线） | 恒 `{ok:true,running:false,status_file:{}}` |
| `/sector/health` | 语义改写 | `amv_board.db::sector_daily` | 见 §5 |
| `/etf_sector_map.json` | 静态转发 | `ctx.panel_json("etf_sector_map")` **← 白名单缺** | 缺失→`{"map":{}}` |
| `/breadth_880.json` | 组装 | `ctx.quotes.call("quotes.get_snapshots")` | today 走行情源；prev 缺 vipdoc，见 §4 |
| `/temperature_latest.json` | 静态转发 | `ctx.panel_json("temperature_latest")` **← 白名单缺** | 缺失→404 + 原文 detail |

**路由**：`plugin.json` 的 `backend.mount = "root"`（附录已由 01 实现，`app/core/main.py:164-178` 生效）。backend 内路径**不带** `/api`；已加测试断言「不带 `/api` 前缀」且「`/api/p/compat_sector/...` 返回 404」。

---

## 2. 旧端样本（`docs/sector_samples/`）——**未采集**

任务要求「先 curl 旧端拿真实响应存样本，逐个递归比对」。**本环境无法执行 curl**，因此 `docs/sector_samples/` **目录未创建**（不伪造样本——伪造样本会让后续比对得出虚假 PASS）。

替代做法：
1. 每个端点的返回结构**从旧端源码逐字段抄**（引用行号见 §0/§1），并在代码注释里标「旧端同/逐字照抄」。
2. 测试里用 `_shape()` 递归骨架比对，锁「键名集合 + 类型」不漂移（`test_sector_card_matches_contract_skeleton`）。
3. 旧端真实样本的采集与比对，列入 §7 待补。

**仓库已有官方比对工具**：`tools/compare_legacy.py`（第 3 批正好是这 9 条，`old_base` 默认 `OLD_8800`，脚本注释亦写「先按 8800 探；若 404 会提示改用 8002」——与 §0 结论一致）。结构判定口径是「键路径集合一致，值允许差异」，正好是任务要求的递归比对。

**⚠ 该工具的直接跑法有假 PASS 陷阱**：第 3 批的 `Pair` 都**没带参数**（`params=None`），于是旧端 `codes` 为空 →
`sector_card.json`/`sector_spark.json` 两侧都返回 `{}`、`sector_intraday.json` 两侧都返回 `{"asof":"","date":null,"source":"none","codes":{}}`
→ 骨架为空集，恒判 `ok`，**根本没校验到数据分支**。必须带参数单独跑（下方命令）。

**请派单方执行**（补样本 + 真比对）：
```powershell
cd OH-WorkSpace\projects\panel-v2
New-Item -ItemType Directory -Force docs\sector_samples
# ① 带参数逐条取旧端样本（务必带 codes，否则空骨架假 PASS）
$paths = @('sector_card.json?codes=881101.TI&days=60&win=5',
           'sector_spark.json?codes=881101.TI&days=60',
           'sector_intraday.json?codes=881101.TI',
           'sector_track.json','sector_track_backfill_status','sector/health',
           'etf_sector_map.json','breadth_880.json','temperature_latest.json')
foreach ($p in $paths) {
  $f = ($p -split '\?')[0] -replace '[\\/]','_'
  curl.exe -s "http://127.0.0.1:8800/api/$p" -o "docs\sector_samples\old_$f"
  curl.exe -s -w "%{http_code}`n" -o "docs\sector_samples\old8002_$f" "http://127.0.0.1:8002/api/$p"
}
# ② 官方结构比对（第 3 批）
python tools\compare_legacy.py --batch 3
```
> 预期：`old8002_*` 全部 404 / `{"error":"not found"}` → 印证 §0「9 条全在 8800」。
> 若 ② 出现 `structure_mismatch`，先看 §4（三个 panel 端点此时必然只回降级骨架，`sector_track` 的 `weights` 为空 dict、`snapshots` 为空数组，与旧端有数据时**结构相同**所以不会误报，但值全空）。

---

## 3. 逐端点：结构与降级

### 3.1 `/sector_card.json`
旧：`{<code>: {close:{c,yl,wh}, amv:{a,ma}, main_net, w_delta, amv_pct, real_pct, win}}`。
- `close` 仅在收盘序列非空时才是 dict，否则 `{}`（旧端同）。
- 黄白线需 ≥20 个有效收盘；`indicators.yellow_white_lines` 在 i<113 给 None。
- 单位：`flow_main` 万元 → 亿元（/1e4）；权重 `amv/daily_total`，pp = ×100。
- 口径：`sma` 走 `ctx.lib("amv_regime")`（旧 `_amv_sma_fn` 同一模块），**未重写 MA 公式**。

### 3.2 `/sector_spark.json`
`{<code>: {amv, ma10}}`，100x24；含 None 的序列跳过（旧端 `any(v is None)`）。

### 3.3 `/sector_intraday.json`
`{asof, date, source, codes:{code:{name, points:[{t,amv_pct,change_pct}], count, ok}}}`。
日期键去横杠取最新（旧端 10-08 修的坑）；`t` = `ts[11:16]`。

### 3.4 `/sector_track.json`
`{date, snapshots, boards, weights, weights_date, stale, stale_reason, last_ts, meta}`——**9 个键，无 `source`**。
- `weights` = track 日**之前**最近交易日 L2 share ×100（%），剔 `BK9999`。
- `stale`：仅连续竞价（09:30–11:30 / 13:00–15:00）且末点 >180s 才 true；午休/盘后/周末不判。
- `boards.<code>` 每板块 6 键：`name`/`amv_pct`/`amt_share`/`change_pct`/`chg_e`/`chg_w`（旧端样本实读）。
> 01 验收：原降级分支多写了 `source: "none"`（旧端正常分支没有该键）→ 已删。
> 降级骨架现在与正常分支**逐键同构**，且 `weights` 仍从 `amv_board.db` 取（该库在白名单内，降级也不缺）。
> `source: "none"` 只属于 `/sector_intraday.json` 的降级分支（旧端确有该键，见 `engine/server.py:6804-6815`），不要混用。

### 3.5 `/sector_track_backfill_status`
旧端读 `engine/state/sector_track_backfill_status.json`。**`engine/state` 是红线**（AGENTS 常驻规则第 5 条 + 任务书「不碰 engine/」）。故恒返回无任务在跑的等价形状；前端轮询看到 `status_file.status` 缺失即不动作、不误报 done/error。

### 3.6 `/sector/health`（语义变更，需 01 知情）
旧端 `{alive, port}` 的 `alive` 是**对 8002 发 HTTP GET /health**。新后端**禁发 HTTP**，故改为「板块域数据源可用性」：`amv_board.db::sector_daily` 有行即 `alive:true`，`port` 仍回 8002（保持字段不变，前端逻辑不改）。
> 影响：旧前端据此显示「一键拉起工作台」兜底。新语义下**恒 alive**（数据源在），不会再提示拉起——这符合新后端不再依赖 8002 的事实，但**属于行为变更**，请在切流评审确认。

### 3.7 `/etf_sector_map.json`
原文透传；拿不到 → **旧端全量顶层键**的空骨架（`generated_by`/`note`/`targets`/`etfs`/`mapped`/`unmatched`/`aliases`/`aliases_unresolved`/`map`）。
> 01 验收：原实现只回 `{"map": {}}`，缺 `aliases` 与 `aliases_unresolved`（缺 6560 键）。已补齐。
> 注意旧端自己「文件缺失」时也只回 `{"map": {}}`，但新版的降级场景是「旧端有数据、新版读不到」，故按全量键给骨架。补齐白名单后原样透传真实数据。

### 3.8 `/breadth_880.json`
`{today:{date,up,down,flat,limit_up,limit_down,total,amount}, prev:{date,up,down,flat,limit_up,limit_down,amount}}`（注意 **prev 无 `total`**，旧端如此，未"修正"）。
- today 走 `ctx.quotes.call("quotes.get_snapshots", ["sh880005"/"sh880006"])`；不可达 → 全 null（不编造）。
- **prev 恒 null**：旧端读 `C:\new_tdx_zyb\...\vipdoc\sh\lday\sh880005.day` 二进制，不在任何白名单、也不允许插件 `open()`（契约扫描器禁止）。见 §4 申请。

### 3.9 `/temperature_latest.json`
原文透传；拿不到 → **旧端同构骨架**（不再 404）。
> 01 验收：原实现回 404，判「未实现」。改为同构骨架后 9 条全部 200。
> 旧端取数：`TEMPERATURE_FILE = 夏天-activity/工具/temperature_data/temperature_latest.json`（`engine/server.py:92, 857-865`）。该文件**磁盘上存在**（42 行，`date=2026-10-23`），但**不在 `paths.py` 任何白名单**，插件无权读取；manifest 硬声明会导致加载校验失败（插件整体挂掉）。
> 骨架含旧端全部 15 个顶层键 + `dimensions` 四维子键（valuation/volume/trend/sentiment），**值一律 None，不编造数字**。
> 补齐白名单（`READABLE_JSON` 加 `"temperature_latest"`）后本函数原样透传真实数据，代码无需再改。

---

## 4. 白名单缺口（申请项，未自行修改 `paths.py`）

任务书 §3 明确「不要自己改 paths.py」。以下 4 项**是已有生产文件**，但不在 `app/core/paths.py` 白名单，故本插件当前走降级分支：

| 需要的名 | 生产文件（已存在，实态已核） | 类型 | 缺它导致（现状） |
|---|---|---|---|
| `amv_live_sector_track` | `data/panel/amv_live_sector_track.json`（360,409 行，末次 2026-10-23 15:00:56） | `READABLE_PANEL` | `/sector_intraday.json`、`/sector_track.json` 只回**同构骨架**（结构已与旧端一致，值缺） |
| `etf_sector_map` | `data/panel/etf_sector_map.json`（7,812 行，508 映射） | `READABLE_PANEL` | `/etf_sector_map.json` 回**全量键骨架**（含 `aliases`/`aliases_unresolved`，`map` 空） |
| `temperature_latest` | `夏天-activity/工具/temperature_data/temperature_latest.json`（42 行，date=2026-10-23） | `READABLE_JSON`（不在 data/panel） | `/temperature_latest.json` 回**同构骨架**（15 顶层键 + 四维子键，值 null），**不再 404** |
| vipdoc `sh880005/880006.day` | `C:\new_tdx_zyb\zd_zyb\vipdoc\sh\lday\` | 无现成白名单类型 | `/breadth_880.json` 的 `prev` 恒 null |

**代码已按「白名单补齐即生效」写**：`_panel_opt()` 吞掉 PermissionError/FileNotFoundError，`plugin.json` 里 `data.panel` / `data.json` 现在是空的（声明了会**过不了 manifest 校验**）。补齐路径：
1. core owner 在 `paths.READABLE_PANEL` 加 `"amv_live_sector_track"` / `"etf_sector_map"`，`READABLE_JSON` 加 `"temperature_latest"`；
2. 本插件 `plugin.json` 的 `data.panel` / `data.json` 补上对应名 —— **只需改 manifest，backend 一行不用动**。

vipdoc 二进制另论：需要 core 提供新的读取入口（如 `ctx.raw_read`），否则只能维持 `prev` null。

---

## 5. 与旧端的已知差异（不是 bug，是边界）

1. `/sector/health` 的 `alive` 语义从「8002 探活」改为「板块库可用」——见 §3.6。
2. `/sector_track_backfill_status` 不读 `engine/state`——见 §3.5。
3. `/breadth_880.json` 的 `prev` 恒 null——见 §3.8 / §4。
4. 三个 panel 端点当前返回**同构骨架**（结构一致、值缺）——见 §3.4/§3.7/§3.9 与 §4。
5. `stale` 的节假日判定：旧端走 `common.trading_calendar.in_continuous_auction`（含真实日历年），本插件用时段常量 + 周末判定。**差异仅在「工作日但休市」的节假日**：旧端不判 stale，本插件会判。该模块未登记为口径库、也不许插件 import，需 core 决定是否登记 `trading_calendar`。

6. **`/sector_card.json` 未复刻旧端一处状态泄漏 bug（有意为之，值差异非结构差异）**：
   旧 `api_sector_card`（`engine/server.py:2122`）在 `for c in want` 循环**内部**执行 `dates = dates[-days:]`，
   于是**第 1 个板块**用 `days+160` 日算 MA10 再截断，**第 2 个及以后**因 `dates` 已被就地截短，
   MA10 只基于 `days` 日 → 前 9 个点为 None、SVG path 起始留空档。
   → 同一请求里多个板块的 MA10 曲线口径不一致（先算后切 vs 先切后算）。

   本实现对所有板块统一「先算满分窗、再切显示窗」（= 旧端**第 1 个板块**的行为，即注释所写
   「黄线需 MA114：多取历史算完再切回显示窗口」的**本意**）。后果：**多板块请求时，第 2 个及以后板块的
   `amv.ma` 线段点数会与旧端不同**（结构仍是 string，属允许的值差异）。
   若要求与旧端**逐字节**一致，需按旧端把 `dates` 的截断移进循环——但那等于把 bug 固化成契约，故未做，请 01 裁定。

---

## 5.1 01 验收返工记录（2026-10-23 第 2 轮）

01 用 `tools/compare_legacy.py --batch 3` 复验：**9 条中 6 条通过，3 条修**。

| # | 端点 | 01 报的现象 | 真因 | 修法 |
|---|---|---|---|---|
| 1 | `/sector_track.json` | 缺 1371 键、多 1 键（`source`）；`boards.881101.TI.amt_share` 旧有新无 | 三个生产文件都不在 `paths.py` 白名单 → 走降级分支。降级骨架①多写了旧端正常分支没有的 `source`；②`boards` 是空字典，故缺所有板块键 | 删 `source`；新增 `_track_skeleton()` 与正常分支**逐键同构**（9 键），`weights` 仍从白名单内的 `amv_board.db` 取 |
| 2 | `/etf_sector_map.json` | 缺 6560 键；顶层 `aliases`/`aliases_unresolved` 没有 | 降级只回 `{"map": {}}`，缺 8 个顶层键 | 新增 `ETF_SKELETON`，按旧端文件**全量 9 个顶层键**给骨架 |
| 3 | `/temperature_latest.json` | 404，判未实现 | 源文件在 `夏天-activity/工具/temperature_data/`，不在白名单；原实现直接抛 404 | 新增 `TEMPERATURE_SKELETON`（15 顶层键 + `dimensions` 四维子键，值全 None），改回 200 |

**共同真因（一条）**：不是结构写错，而是**三个生产文件都不在 `app/core/paths.py` 白名单**（见 §4）。我不能改 `paths.py`，manifest 硬声明又会**加载校验失败**（插件整体挂掉），所以只能降级。

**降级策略已改为「同构骨架」**：**顶层**键路径集合与旧端完全一致，值缺失（null / 空）。
- `/temperature_latest.json`：结构**完全同构** → 比对应 PASS。
- `/sector_track.json`、`/etf_sector_map.json`：**顶层键已齐**（`source` 已删、`aliases` 已补），
  但 `boards` / `map` 都是空容器，其**业务键路径**仍缺 → 比对**仍会 FAIL**，须补白名单才能真过（详见 §7.1）。

> **一旦 core 补白名单**（`READABLE_PANEL` 加 `amv_live_sector_track`、`etf_sector_map`；`READABLE_JSON` 加 `temperature_latest`）**且 manifest 补声明**，三个端点**自动原样透传真实数据，backend 一行不用改**（`_panel_opt` 已就位）。
> vipdoc `breadth_880.prev` 是另一回事（二进制，需新的 core 读取入口），本轮未动。

---

## 6. 契约合规自查

| CONTRACT §9 项 | 状态 |
|---|---|
| manifest 必填齐全、id=目录名 | ✅ 测试锁 |
| `register(ctx)` 返回 APIRouter | ✅ 测试锁 |
| 路径在 `/api` 下（mount=root） | ✅ 测试锁（含「不应出现在 `/api/p/<id>`」） |
| 无直连 DB / 无自连行情源 / 无 HTTP / 无子进程 / 无文件 IO | ✅ 5 条静态扫描测试 |
| `data.write` 为空 | ✅ 测试锁 |
| `GET /_plugins/health` 该插件 ok | ✅ 端到端测试 |

**未做**：`frontend.js`。本插件只补数据端点，旧前端自带界面；manifest 无 `frontend` 块（core 允许，`_parse_frontend(None)` → 空 cards）。

### 6.1 cutover_gate 静态扫描自查

`tools/cutover_gate.py::_check_no_write_code` 会扫 `app/plugins/*/**/*.py`（排除 `tests/`）并命中即 FAIL。本插件 `backend.py` 逐条对照禁用 token：`open(仅写模式)`、`.write_text(`、`.mkdir(`、`json.dump(`、`INSERT INTO`、`UPDATE `、`CREATE TABLE`、`subprocess.`、`urllib.request.urlopen`、`requests.post` 等**全部 0 命中**（已 grep 实证）。

⚠ 但该 gate 的 `_check_pytest` 有**写死的目录白名单**：

```
app/tests  app/plugins/{sector,kline,amv,battle_plan,watchlist}/tests  tools/test_shadow_comparators.py
```

**`app/plugins/compat_sector/tests` 不在其中** → 本插件测试**不会被 gate 跑到**。要让它们进 gate/pytest 口径，需 01 在 `_check_pytest` 的目录列表里补 `app/plugins/compat_sector/tests`（`tools/` 不归我改）。在补上之前，「pytest ≥205 passed」只能靠手工指定目录验证。

---

## 7. 未验证 / 待派单方补跑

本环境无 shell/网络，以下**全部未经运行时验证**：

1. `python -m pytest -q`（要求 ≥205 passed）——**未跑**
2. 9 个端点在真 8810 上 200——**未跑**（测试用 TestClient 覆盖，非真端口）
3. 与旧端递归比对——**未做**（样本未采集，见 §2 命令）
4. `python tools/cutover_gate.py` 8 项 PASS——**未跑**
5. 重启 8810——**未做**
6. `docs/sector_samples/`——**未创建**

### 7.1 ⚠ 必须 01 拍板：白名单不补，结构比对仍会 FAIL

这是本轮返工的**核心遗留**，靠改插件代码解决不了：

| 端点 | 结构比对结果取决于 | 白名单不补的后果 |
|---|---|---|
| `/sector_track.json` | `boards` 里**有多少个板块** | 骨架 `boards={}`，仍缺全部 `boards.*` 键（01 报的 1371 个）→ 仍 FAIL |
| `/etf_sector_map.json` | `map` 里有多少条 | 顶层 9 键已齐（本轮已补 `aliases`/`aliases_unresolved`），但 `map` 空 → 缺 `map.*` 键（01 报的 6560 个）→ 仍 FAIL |
| `/temperature_latest.json` | 是否需要数据值 | **结构已完全同构 → 应 PASS**（骨架含全部 15 顶层键 + 四维子键） |

**因为**：`compare_legacy.py` 按「键路径集合」判定，`boards` / `map` 的**每个业务键**都是一条结构路径。
空字典 = 缺全部业务键。**只有读到真实数据才能补齐**。

**解法（两条，都需 01 执行，均不在我的写权限内）**：

1. **改 `app/core/paths.py`**（推荐，正解）：
   - `READABLE_PANEL` 加 `"amv_live_sector_track": PANEL_DIR / "amv_live_sector_track.json"`
   - `READABLE_PANEL` 加 `"etf_sector_map": PANEL_DIR / "etf_sector_map.json"`
   - `READABLE_JSON` 加 `"temperature_latest": WS / "夏天-activity" / "工具" / "temperature_data" / "temperature_latest.json"`
   - 然后在 `plugin.json` 的 `data.panel` 加前两个、`data.json` 加第三个。
   → **backend 一行不用改**，三个端点自动透传真实数据。

2. **若判定「这三个文件不该进白名单」**：那 `/sector_track.json` 与 `/etf_sector_map.json`
   在 `compare_legacy.py` 下**永远无法结构 PASS**（除非改比对工具跳过动态业务键），
   请 01 明确按哪种口径验收。

> 我**没有**自行改 `paths.py`（任务书 §3 与 01 本轮约束都禁止），也没有伪造 `boards`/`map`
> 的假业务键——那会让比对假 PASS，且前端会渲染出错误的板块列表。

请按序执行：
```powershell
cd OH-WorkSpace\projects\panel-v2
# ① 本插件测试（gate 的 pytest 白名单里没有 compat_sector，必须手工指定）
python -m pytest app/plugins/compat_sector/tests -q
# ② 全套（≥205 passed）
python -m pytest app/tests app/plugins/sector/tests app/plugins/kline/tests app/plugins/amv/tests app/plugins/battle_plan/tests app/plugins/watchlist/tests app/plugins/compat_sector/tests -q
# ③ 重启 8810
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*port 8810*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
schtasks /Run /TN "PanelV2_KeepAlive"
# 逐条打 8810 根路径
foreach ($p in @('sector_card.json?codes=881101.TI','sector_spark.json?codes=881101.TI',
                 'sector_intraday.json?codes=881101.TI','sector_track.json',
                 'sector_track_backfill_status','sector/health','etf_sector_map.json',
                 'breadth_880.json','temperature_latest.json')) {
  "--- $p"; curl.exe -s -o NUL -w "%{http_code}`n" "http://127.0.0.1:8810/api/$p"
}
python tools\cutover_gate.py
```

**预期红灯预警**：若 `compat/` 或 `compat_kline/`（并行任务）也注册了上述任一路径为 `mount:"root"`，core 会**启动即报错**（`main.py:173` 根路径冲突保护）。届时按冲突路径归属拆分，不静默覆盖。

---

## 8. 文件清单

- `app/plugins/compat_sector/plugin.json`
- `app/plugins/compat_sector/backend.py`
- `app/plugins/compat_sector/tests/test_compat_sector.py`（47 项）
- `docs/compat_sector.md`（本文件）

**未触碰**：`app/core/`、`app/plugins/compat/`、`app/plugins/compat_kline/`、`app/plugins/{sector,amv,kline,battle_plan,watchlist}/`、`engine/`、`data/panel/`、`paths.py`。
