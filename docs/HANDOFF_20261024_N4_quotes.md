# HANDOFF · N4 行情补口 + amv_research（kline + compat）

> 执行：01 ｜ 2026-10-24 ｜ 任务书 `docs/task_N4_quotes.md` ｜ 并发组 N4/4
> 改动白名单内完成：`app/plugins/kline/`（0.6.0→0.7.0）、`app/plugins/compat/`（0.1.0→0.2.0）、本文件。
> **未动 core、未动其他插件、未 git commit、未重启 8810。**

## 1. 交付结论

| 项 | 状态 | 位置 |
|---|---|---|
| `GET /api/p/kline/volume_ticks/{ts_code}` | 完成 | `app/plugins/kline/backend.py::build_volume_ticks` |
| `GET /api/{name}.json` 通配 | 完成 | `app/plugins/compat/backend.py::register_wildcard` |
| `GET /api/amv_research` + `/api/amv_research.json` | 完成（**v1 全量，未降级**） | `app/plugins/compat/backend.py::build_amv_research` |

三处均按「原生实现、不转发」：没有一处发 HTTP 打旧端，数据全走 `ctx.quotes` / `ctx.db_read` / `ctx.panel_json` / `ctx.lib`。

### 自测（新鲜输出）

```
python -m pytest app/plugins/kline/tests app/plugins/compat/tests -q  →  117 passed
python -m pytest -q                                                   →  977 passed, 4 skipped
python tools\cutover_gate.py                                          →  PASS（7 项全 OK，46.3s）
```

### 对齐抽查（同参 vs 8800，真实运行实例，非 TestClient）

用临时端口 8819 拉起真实 uvicorn（`PANEL_V2_FEED=0`），跑完即杀，不碰 8810：

- **amv_research 5 组参数逐值零差异**：`L2/days=30`、`L3/days=20`、`CONCEPT/days=12`、
  `L2&scope=all`、`L2&code=881121.TI`。比对面：返回键集合、`boards` 全部行的
  `contrib/amv_pct/px_pct/bias/up/rk`、`members` 同字段、`sel_parts` 全字段、
  `total_pct`、`asof/prev/sel/n`、`sp{up,dn,ma}` path 字符串。
- **volume_ticks**：`pre_close/limit_up/limit_down` 与旧端一致（3658.03 / 4023.83 / 3292.23），
  `minute_idx` 轴 0–240 一致，rows 条数 4565 vs 4566。

## 2. volume_ticks 实现要点

- 数据源 `ctx.quotes.today_ticks()`（core 门面，6×1800 分页，20s TTL，与旧端 `_eltdx_volume_profile` 同参）。
- `minute_idx` 对齐 `_intraday_slots()`（09:30=0 … 15:00=240），**13:00 归 13:01(121)**。
- `price_milli = round(price×1000)`；`vol` 单位 = **手**（eltdx ticks 原值，不 ×100）。
- 剔除 `event_kind != "trade"`（竞价快照/开盘撮合）；同 `(分钟, 价位)` 合并相加，按 `(分钟, 价位)` 升序。
- 空态（旧端实测）：`pre_close/limit_up/limit_down = null`、`rows = []`、HTTP 200 —— 已按此复刻，非 404。
- 非法代码 → 400。`code` 返回**归一后**的 ts_code（`000001` → `000001.SZ`，与旧端 `to_ts_code` 同）。

### 已知差异（源侧，非逻辑差异，已实测归因）

`rows` 总成交量与旧端差 1–2 笔，例：`000001.SH` 新 8912696991 / 旧 8911173158（差 1523833 手，恰好是第一页
首条那一笔）；单笔行重叠 4563/4565。

**归因**：eltdx `trades.today(start=N)` 的分页边界在**同一进程连取三次**就给出 4744/4743/4744 笔，
即源侧边界本身不稳（同一个 glb 三次请求，总量差 304 万手）。旧端走同一分页同参数，因此这是**行情源
分页抖动**，不是行合并逻辑差异 —— 行合并/剔除/轴向已用单测锁死。**恢复稳定后重跑对照即可**。

## 3. 通配 {name}.json 实现要点 + 一个必须知道的坑

- 白名单唯一源 = core `paths.READABLE_PANEL`（`backend._PANEL_JSON_NAMES` 直接取该集合，不自建第二份）。
- 名字不在白名单 → 404「未知文档 {name}」（旧端 `api_doc` 同文案）；插件**不做任何路径拼接**，
  `../x`、`C:\...` 这类名字不可能命中 ⇒ 不存在任意文件读。
- 名字在白名单但磁盘无文件 → 404（不装 200 空对象）；manifest 未声明 → 同样 404，detail 说明原因。

### ⚠ 坑：通配会抢走后面插件的具体 `.json` 路由（已修，有回归测试）

compat 按**目录名排序**先于 `compat_sector` / `compat_watchlist` / `concept` / `ops` / `review` 加载，
它们都是 `mount="root"` 的 `/api/<名>.json` 字面路由。FastAPI 按注册顺序匹配 ⇒ 通配先注册就会把
`/api/sector_card.json`、`/api/concept.json`、`/api/ops.json`、`/api/watchlist.json` 全部抢走。
**实测确认过**（修复前 `GET /api/sector_card.json` 返回通配体的 404）。

修法：不依赖注册顺序，通配路由类在匹配阶段查「同应用内是否存在同字面路径的具体路由」，
有就让路（返回 `Match.NONE`），让 starlette 继续往后找。等价于「具体路由在前」，且以后加插件不用改 compat。
字面路径集合按 app 缓存一次（存 app 引用防 `id()` 复用）。

⚠ **FastAPI 0.141 的 `include_router` 已改成 `_IncludedRouter` 嵌套包装**（不再把子路由拍平进
`app.routes`），所以收集路径时要递归进 `original_router` 并叠 `include_context.prefix`。
按老写法（遍历 `app.routes` 取 `.path`）会一条都拿不到，让路逻辑会静默失效 —— 这个坑踩过，
`test_wildcard_does_not_shadow_specific_routes` 就是锁它的。

## 4. amv_research 实现要点

**未走 v1 降级**：`_rs_base` 虽在旧端是 ~70 行，但依赖的三个 helper 都很小且全都已在白名单内，
所以全量实现了参数校验 + 数据组装 + 贡献分项 + 缩略线，无 `v2-partial`。

- 参数：`level` ∈ L2/L3/CONCEPT（非法 → L2）、`days` 12–60（默认 30）、`scope` board/all、`live` int。
  注意 `live=abc` **与旧端一样返回 422**（FastAPI int 注解），不是静默当 0。
- 数据源：`ths_board.db`（board/board_member/concept/concept_member）+
  `amv_board.db`（stock_amv / stock_state / daily_total），全走 `ctx.db_read`。
- 口径库：分项 = `ctx.lib("amv_contrib").parts`；MA10 = `ctx.lib("amv_regime").sma`。**无第二份实现。**
- 缓存：`_RS_CACHE` TTL = **120s**（旧端 `_RS_TTL`），最多 24 条，key 与旧端同构
  `(level, code, days, live, scope)`；另有 `("_base", level, days)` 重活缓存。
- 上限：成分股表 300 行（`RS_MEMBER_CAP`）、全A榜 300 行（`RS_ALL_CAP`），与旧端同值。
- 收盘后 EOD 重建窗口期：`_rs_base` 失败 → 回退同层级最近一次成功结果并置 `stale=true`（旧端同款兜底）。

### 与旧端的三处差异（能力边界，均不编造）

1. **`live=1` 实时口径未接**：旧端调 `executor.amv_live.stocks_snapshot()`（重模块 + 不在任何只读白名单）。
   v2 恒回收盘口径，回 `live_mode=false` + `live_err` 说明原因。前端本来就接受 `live_mode=false`
   （旧端注释明写「自动回退收盘口径」），故不影响上屏，只影响盘中实时性。
   **要补这一档，需要 core 开一条 `amv_live` 只读通道并把它登记进口径库/白名单 —— 属 core 范围。**
2. **个股名称来源**：旧端 `common.names.name_map()`（多源聚合，含 QMT/TDX 全量名表）；
   v2 用 `ctx.json_read("stock_names")`（东财 6 位码表）。实测 183 只成分股里 8 个名称不同，
   例 `688801` 旧「燧原科技」/新「燧原科技-U」、`688008` 旧「XD澜起科」/新「澜起科技」。
   差异是**名表新旧与来源不同**，不是逻辑错。要完全一致需把 `common.names` 登记为口径库（core 范围）。
3. **`ts` 后缀**：旧端用 `{"6":"SH","0":"SZ","3":"SZ"}` 硬表 → `920207` 被判成 **`920207.SZ`（旧端 bug）**；
   v2 用本插件既有的 `_suffix_of()` → `920207.BJ`（正确）。这是**修了旧端错**，已在此登记，
   不打算为了逐字一致去复刻 bug。若要对齐请先决定「以谁为准」。

## 5. 需要 01 决定 / 合并的事项（**未自行改动**）

### 5.1 core 白名单登记（按纪律只登记不直接改）

`app/plugins/compat/plugin.json` 已声明以下名字，全部**已在** `paths.READABLE_PANEL`，
所以**本次不需要新增 core 白名单条目**。但下一批若要覆盖旧端 `WHITELIST` 的全部名字，仍需登记：

| 名字 | 旧端来源 | 磁盘现状 | 建议 |
|---|---|---|---|
| `patrol_0600/0800/1000/1200/1600` | `_panel_path` 默认分支 | 5 个文件都在（1.0–4.9KB） | 建议登记；现状核对：旧 8800 `GET /api/patrol_0800.json` = 200（trade_date 2026-09-29），新 8810 = 404 |
| `review` / `review_ext` | 旧端 `WHITELIST` | `review.json` 15.9KB、`review_ext.json` 16.3KB 都在 | 建议登记；旧 8800 `GET /api/review.json` = 200（trade_date 2026-08-24）。与 `review` 插件的 `/api/review_chart.json` 不冲突 |
| `fupan_decision_YYYYMMDD` | 旧端带日期名 | 30+ 个按日文件 | **不建议**登记（日期名会无限膨胀），由复盘域插件另开端点 |
| `sector_temperature` / `panic_breadth` / `sector_anomaly_*` | 各域 | 在 `data/panel/` | 对应域插件自己声明，不进本通配 |

**结论**：通配当前能服务的名字 = `READABLE_PANEL` 现有 21 个键（含 `ops`、`selection_*`、
`amv_live_sector_track`、`etf_sector_map` 等）。`patrol_*` 与 `review/review_ext` 若要走通配，
需 01 在 `paths.READABLE_PANEL` 增 7 个条目（另一并发任务在动 `paths.py`，我不碰）。

### 5.2 建议登记进 `paths.READABLE_LIB`（口径库白名单）

| 库 | 用途 | 现状 | 为什么值得登记 |
|---|---|---|---|
| `bj_scope` | 北交号段唯一源（`BJ_CODE_PREFIXES`/`is_bj`） | 不在白名单 | 本任务按号段字面量实现了 `RS_BJ_PREFIXES`。**实测两套判定在 `board_member ∪ concept_member ∪ stock_amv` 全量 6165 个代码上零差异（603 个北交票完全重合）**，所以当前无口径漂移；但号段若变，这里就是第二份定义。登记后 exports 给 `("is_bj", "BJ_CODE_PREFIXES", "is_hs_a")` 即可（该模块 import 期会 `sys.path.insert`，core 加载器已处理这种情况）。 |
| `amv_regime` 补 `is_bj` 相关 | — | 不必 | `amv_regime` 已登记，`sma` 已在 exports 里。 |

### 5.3 `mount` 顺序脆弱性（提醒，不属本任务范围）

`app/core/main.py::create_app` 按 `discover_plugin_dirs`（目录名字母序）挂载，root 插件之间
**字面路径冲突才会报错，参数化路径冲突不报**。本次通配靠运行期让路解决了自己这一例；
若以后再加 `/{something}` 这类通配插件，仍需要各自的让路或 core 层面提供「低优先级路由」注册位。
（FastAPI 0.141 内部有 `_low_priority_routes` 机制，但当前只给 frontend routes 用，没开放给插件。）

## 6. 回滚

三个文件独立回退即可，无数据迁移、无持久化结构变更：

```
git checkout -- app/plugins/kline/backend.py app/plugins/kline/plugin.json app/plugins/kline/tests/
git checkout -- app/plugins/compat/backend.py app/plugins/compat/plugin.json app/plugins/compat/tests/
```

`plugin.json` 版本号回落即代表功能下线；旧代码不认 0.7.0/0.2.0 的新路由，无兼容负担。
`_RS_CACHE` 是进程内内存，重启即清，不落盘。

## 6.5 文件编码约定（踩坑登记）

这四个 `.py` 文件在 git HEAD 里存的是 **UTF-16LE + BOM**，但工作区里其他未被改动的同类文件
（`compat_kline/backend.py`、`app/tests/test_contract.py`、`concept/plugin.json` 等）实际都是
**UTF-8 无 BOM**。`panel-v2` 目录没有 `.gitattributes`，git 不做转换。

后果：用工具以 UTF-8 写入后，`git diff` 会把整个文件当“重写”（+519/−439 这种假信号）。
**处理方式**：统一按工作区实际约定写 **UTF-8 无 BOM**；看 diff 用
`git diff --ignore-cr-at-eol --stat` 才是真实改动量（本次真实改动：kline backend +86/−1、
compat backend +610/−1、两个测试文件 +80/0 与 +297/0、两个 plugin.json 仅版本与清单行）。
不要为了“diff 好看”把文件转回 UTF-16 —— 那会让 pytest 导入时报
`SyntaxError: source code string cannot contain null bytes`。

## 7. 未验证 / 遗留

- **8810 常驻进程仍在跑旧代码**（按纪律未重启）：`/api/p/kline/volume_ticks/*` 与
  `/api/amv_research.json` 在 8810 上现为 404，**需下次重启 8810 才生效**。临时实例 8819 已验证可用。
- `live=1` 实时口径：未实现（见 §4 差异 1）。
- amv_research 的 `days` 只影响缩略线窗口，我按 12–60 钳位；旧端 `days` 非法非数字时同样走 422
  （FastAPI int 注解），未额外做兜底 —— 与旧端一致。
- volume_ticks 的源侧分页抖动（§2）不是本次能修的，属行情门面 `app/core/quotes.py::today_ticks`
  的分页策略，若需彻底消掉要动 core（本任务范围外）。
