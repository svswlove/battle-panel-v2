# HANDOFF · S2：板块/概念指数 K 线（`*.TI`）迁移到 8810

- 日期：2026-10-24
- 任务：把旧 8800 的板块/概念指数 K 线（`881xxx.TI` / `884xxx.TI` / `885xxx.TI` / `886xxx.TI`）
  完整迁移到 panel-v2，消除全局扫描后最后一块「骨架降级」残留（原先 `.TI` 分支直接 400）。
- 语义唯一源（只读参考，逐段照搬）：`engine/server.py:8946-9260`
  （`INDEX_KLINE_DB` / `is_index_kline_code` / `_ths_api_key` / `_THS_WEEK_MEMO` /
  `_ths_index_bars_mcp` / `_index_week_bars` / `_index_kline_rows` / `_index_amv_map` /
  `_index_kline_name` / `_index_expected_latest_date8` / `build_index_kline_payload`）。

## 1. 改了什么（白名单内，逐条）

| 文件 | 改动 |
|---|---|
| `app/core/paths.py` | `READABLE_DB` 加一行 `"ths_index.db": DATA_DIR / "ths_index.db"`（含 3 行注释） |
| `app/plugins/compat_kline/backend.py` | `.TI` 分支从 400 改为真实现：新增 `is_index_kline_code` / `_index_kline_rows` / `_index_amv_map` / `_index_kline_name` / `_is_trading_day` / `_index_expected_latest_date8` / `_ths_week_bars` / `build_index_kline`；`_kline_payload` 增 4 个可注入参数（`boards`/`amv_map`/`float_sh`/`mf`） |
| `app/plugins/compat_kline/plugin.json` | `data.read` 加 `ths_index.db`、`permissions` 加 `read:ths_index`、`version` 0.1.0 → 0.2.0 |
| `app/plugins/compat_kline/tests/test_compat_kline.py` | `_mk_ctx` 支持 `index_rows`/`sector_rows`/`index_meta_rows`/`sector_name`/`index_code`；`.TI` 400 断言改成「不校验 before」+ 分钟 400 两条 |
| `app/plugins/compat_kline/tests/test_index_kline.py` | **新建**，30 个用例（日线/周线聚合/before/空库/分钟 400/周线降级/stale/AMV/契约） |

旧文件、其它插件、`app/core/`（除 `paths.py` 一行）、`engine/` 全部未动。
未 commit、未重启 8810。

## 2. 实现要点（逐段对齐，不是重新设计）

1. **日线**：`SELECT date,o,h,l,c,volume,turnover FROM index_daily WHERE code=? AND o IS NOT NULL
   [AND date < ?] ORDER BY date DESC LIMIT ?`，周线取 `limit*7` 根（旧端同式），反序成升序；
   日期长度 ≠ 8 的行跳过。
2. **量纲**：`volume` = 股 → `v`（**不 ×100**，与个股「手→股」那条路不同，这里是原值）；
   `turnover` = 元 → `amount`。换手率无流通股本 → 恒 `None`。
3. **周线**：只认原生 1w 源。**不得用日线聚合冒充**（旧端 2026-10-19 主人裁定）。
   原生不可用 → `bars=[] stub stale=True source="ths_mcp_index:unavailable"` +
   `period_unavailable=true` + `unavailable_reason`。带 60s 同码记忆（`_THS_WEEK_MEMO`）。
   注意：`_index_kline_rows(period="w")` 里的 ISO 周聚合逻辑仍照搬保留（旧端有，且被
   `build_index_kline_payload` 的 `p=="w"` 分支之外的路径复用），但它**不参与周线响应**。
4. **before 游标**：`_index_kline_rows` 用 `date < ?` 严格早于；`fetch_n = lim + 1` 判
   `has_more`；`next_before = bars[0]["t"][:8]`。`has_more`/`next_before` **只在 before 分支出现**。
   ⚠ **旧端板块分支对 `before` 不做任何校验/归一**（个股分支才有剥非数字）：
   `before=2026-06-01` 原值进 SQL，字典序 `'-'(0x2D) < '0'(0x30)` 只截到上一年度；
   `before=bad` 不报错而是「全部早于 bad」。这是旧端实际行为，双跑对拍要求逐字段一致 →
   **照搬，不在这里「修好」**（已写进代码注释 + 两个回归测试）。
5. **stale**：`latest < _index_expected_latest_date8(today8)`。期望日 = 交易日且 ≥15:05 → 当日，
   否则回退到上一交易日（最多退 10 天）。交易日判定走 `common.trading_calendar.is_trading_day`，
   按路径懒加载（插件禁 `from common`，做法与 `app/core/panic_calc._is_trading_day` 同）+
   用完还原 `sys.path`；日历不可用 → 工作日兜底（宁可少报 stale）。
6. **AMV 副图**：`amv_board.db::sector_daily`（同库同表，`/1e8` 亿口径在 `_amv_series_out` 装配层）。
   坑位照旧：`date` 是 `YYYY-MM-DD`、bar 的 `t` 是 `YYYYMMDD`，入参必须转格式、返回键去横线。
   **周线档的 AMV 实现细节**：旧端 `build_index_kline_payload` 虽然对周线也传了 `amv_map`，
   但 `_kline_payload_out` 只在 `period == "d"` 时调 `_amv_series_out` ⇒ 周线三列恒 `None`。
   本实现周线传 `amv_map={}`（不白跑一次库查询），输出逐字段不变（活体对拍验过
   `/api/kline/881121.TI?period=w&limit=120` 两侧 JSON 长度与内容全等）。
7. **板块名**：`ths_board.db::board` → `amv_board.db::sector_meta` → 空串。
8. **分钟/分时**：`period not in (d,w)` → 400
   「板块指数暂无 {p} 周期数据（仅日线；分钟与分时需个股逐笔源）」。
   `/api/intraday*`、`/api/quote5`、`/api/volume_profile`、`/api/volume_ticks`、`/api/f10_brief`
   对 `.TI` 仍走原有「非法 ts_code」400（旧端同：门面 `normalize` 认不出 `.TI`）。

## 3. 旧端缺陷（本实现不复制）

**旧 8800 `GET /api/kline/999001.TI?period=d&limit=800` 回 500。**

- 根因：`_kline_payload_out` 里 `float(b["v"])`，而 `index_daily` 有 1 行 `volume`/`turnover`
  为 NULL（`999001.TI` `20260105`，自建「物理AI」指数首根，`source='custom'`）→ `TypeError`。
  该行在 `limit=120` 时落在窗口外所以不触发，`limit=800` 取全量就炸。
- 本实现：`v=None` 保留、下游 `float(b.get("v") or 0)` 按 0 处理（与旧端设计意图一致），
  `amount=None`。实测 8899 回 200、`count=193`、该根 `v=0.0`。
- 闭环建议：修旧端 `_kline_payload_out`（改 `float(b.get("v") or 0)`），或让
  `ths_index_fetch.py` 给自建指数首根写 0 而非 NULL。**属旧仓/数据生产侧，本次未动**
  （任务书：旧文件只读）。

## 4. 周线上游：接缝**已落地**（2026-10-24 追补）

**状态**：接缝已落到 core 行情门面，`*.TI` 周线自动从「不可用」切到「原生 1w」。
当前线上仍返回 `source=ths_mcp_index:unavailable` + `period_unavailable=true` + 空 bars——
原因是**上游本身不可用**（见下），不是接线缺陷。

### 4.1 上游当前状态（今日实测，属正确行为，不要“修”）

`fuyao.aicubes.cn/mcp/a-share-index` 的 `get_a_share_index_prices_historical` 对
`interval=1w` 当前对所有代码返回 `{"code":5003,"message":"Data source unavailable"}`
（2026-10-24 22:0x 实测）。core 按「`code != 0` → None」处理 → 插件报周线不可用。
上游恢复后无需改代码，自动生效（与旧 8800 同一行为）。

### 4.2 已落地的 core 接缝（`app/core/quotes.py`）

| 新增 | 说明 |
|---|---|
| `THS_INDEX_MCP_URL` / `THS_INDEX_MCP_HOST` / `THS_CFG_FILE` | 上游地址、代理旁路主机、凭据配置文件（`夏天-activity/工具/hithink_config.json`） |
| `_no_proxy_hosts(hosts)` | 由 `_f10_no_proxy()` 泛化而来的通用代理旁路（同一把锁、同一套环境变量读写） |
| `_f10_no_proxy()` | 保留为 `_no_proxy_hosts(_F10_HOSTS)` 薄包装，行为与泛化前完全一致 |
| `_ths_api_key()` | 读凭据 → str \| None；凭据不落盘、不进日志、不进异常文案 |
| `_ths_parse_body(raw)` | 响应体解析：先按 JSON，失败再逐行找 `data:`（SSE） |
| `ths_index_weekly(code, start8, end8=None)` | 原生周线 → `[{t,o,h,l,c,v,amount}]` 升序，或 None |

插件侧调用未变（`backend.py::_ths_week_bars` 的 `getattr(_qs(ctx), "ths_index_weekly", None)`）。
`compat_kline/backend.py`、`plugin.json` 本次**未改**。

### 4.3 单测

`app/core/tests/test_quotes_ths_weekly.py`（新建，17 例，**零网络**：`urllib.request.urlopen`
全程 monkeypatch）。覆盖：凭据缺失/文件损坏/key 空 → None 且不发请求；`urlopen` 异常 →
None；上游 `code=5003` → None；空 items / 缺 `result.content` / 非 JSON 体 → None；
非法 `start8` → None 且不发请求；纯 JSON 与 SSE 两种体 → 同一份升序 bars；
缺 `date_ms` 脏行跳过；`end8` 缺省=今天；请求侧 URL/头/参数（`interval=1w`、
`thscode` 大写、`end` 含当日 +1 天）逐项钉死；代理旁路写入并还原；凭据不进
stdout/stderr/日志。

### 4.4 语义边界（沿用 §2 的口径，未变）

门面没有该方法 / 抛异常 / 返回空 → 一律按「周线不可用」处理（不编造，不用日线聚合冒充）。

<details>
<summary>历史记录：本节原文（「接缝已留，实现未落」+ 待裁定项 + 待应用补丁）</summary>

**当时现状**：周线响应与旧 8800 今日实测**逐字段一致**——两边都是
`source=ths_mcp_index:unavailable` + `period_unavailable=true` + 空 bars。
原因同上（上游 5003）。

**当时为什么不直接把旧端 `_ths_index_bars_mcp` 抄进插件**：插件侧禁一切出站调用
（`docs/CONTRACT.md` §8 + `tools/cutover_gate.py` 的 banned tokens 含 `urllib.request.urlopen`
+ `test_plugin_has_no_http_no_forbidden_imports` 直扫 `urllib` 字面）。

**当时的两条待裁定路线**：A（授权改 `app/core/quotes.py`，推荐）／B（维持现状，等上游恢复再改代码）。
**已采用 A**：任务方已授权改 `app/core/quotes.py`，补丁已按本节落地（逐段对齐，另加
`_ths_parse_body` 拆分与 `THS_HTTP_TIMEOUT` 常量）。

</details>


## 5. 验证

### 5.1 单测（零网络、零生产库）

```
python -m pytest app/plugins/compat_kline/tests -q
→ 68 passed（含新建 test_index_kline.py 的 30 例）
```

core 接缝单测（§4.3 的 17 例）与插件测试合并跑：

```
..\..\envs\qmt312\Scripts\python.exe -m pytest app/core/tests app/plugins/compat_kline/tests -q
→ 148 passed, 9 warnings in 8.08s  EXIT=0
```

覆盖：日线取数/量纲/顶层键、周线 ISO 聚合（含跨年 W53、NULL 量）、周线原生源优先、
周线不可用标记、60s 记忆、上游异常降级、before 严格早于/has_more/next_before/带横线不归一、
空库/未知码/`o IS NULL` 过滤、分钟 400、分时等 6 端点对 `.TI` 400、stale 四态、
AMV 副图（含 date 格式坑）、白名单/manifest/版本。

### 5.2 全量 pytest（任务书要求，贴退出码）

```
python -m pytest app/tests app/plugins/{sector,kline,amv,battle_plan,battle_plan_rw,
  watchlist,compat,compat_sector,compat_misc,compat_watchlist,dashboard,ops,paper,
  concept,review,compat_kline}/tests app/core/tests tools/test_shadow_comparators.py -q
→ 1081 passed, 1 skipped  EXIT=0
```

（改动前基线：1049 passed, 1 skipped。新增 32 例 = 30 个主用例 + 2 个 before 改写。）

### 5.3 切流门禁

```
python tools/cutover_gate.py
→ PASS 46.6s  8810/api/health=OK  全部插件健康=OK  shadow双跑=OK  AMV原始JSON比对=OK
             battle_plan写域接管=OK  插件无写代码=OK  kline边界声明=OK  pytest全套=OK
EXIT=0
```

（「插件无写代码」里含 `urllib.request.urlopen` 等 token 扫描 → 本插件未引入出站调用。）

⚠ **门禁盲区（如实登记，未修）**：`tools/cutover_gate.py::_check_pytest` 的目录清单里
**没有 `app/plugins/compat_kline/tests`**（有 `compat`/`compat_sector`/`compat_misc`/
`compat_watchlist`，漏了 `compat_kline`）——所以门禁打印的「pytest 全套=OK」（1013 passed）
**不包含本插件这 68 例**。上面 §5.2 的手工全量命令是含它的（1081 passed）。
修法：往 `_check_pytest` 的目录数组里加一项即可；`tools/cutover_gate.py` 不在本次白名单，
**未动**，留给任务方决定。

另：跑门禁会重写 `docs/cutover_gate_latest.md`（门禁自己指定的输出文件）。该文件不在
本次白名单，跑完已 `git checkout --` 恢复到入任务前状态（所以文档里的 PASS 证据以
本节复述为准，不以那份落盘文件为准）。

### 5.4 活体对拍（临时端口实例 8899 vs 旧 8800）

临时起 `python -m uvicorn app.core.main:app --port 8899`（`PANEL_V2_FEED=0`），
逐字段 `json` 深比对（键集合/类型/数值全等，不是抽样）：

| 用例 | 结果 |
|---|---|
| `881121.TI?period=d&limit=120 / 240 / 800` | 一致（120/240/278 bars，asof=20261023，stale=False，source=ths_index.db） |
| `881121.TI?period=w&limit=120` | 一致（0 bars，stale=True，source=ths_mcp_index:unavailable，含 period_unavailable/unavailable_reason） |
| `881121.TI?period=w&limit=120&before=20260601` | 一致（同上 + has_more=False/next_before=None） |
| `881121.TI?period=d&limit=120&before=20260601` | 一致（120 bars，asof=20260529，has_more=True，next_before=20251127） |
| `881121.TI?period=d&before=2026-06-01` | 一致（85 bars，asof=20251231，旧端不归一 before 的怪癖也一致） |
| `881121.TI?period=d&before=bad` | 一致（240 bars，不报 400） |
| `881101.TI / 885311.TI / 886111.TI / 884123.TI / 999999.TI / 999001.TI` 日线 | 一致 |
| `881121.TI?period=m5 / x1` | 一致（HTTP 400 + 同一 detail 文案） |

**全库全码扫描**：对 `index_daily` 里全部 `.TI` 码（711 个）逐个 `?period=d&limit=800` 做
payload 指纹比对（除 `mf`），**711/711 一致**；唯一例外 `999001.TI` 是旧端 500（见 §3），
脚本显式跳过并单独验证（旧 500 / 新 200+193 bars）。

**个股回归**：`600519.SH` 日/周/before、`000001.SZ` 日线、`kline_bg` 两种 mode
（含 `.TI` 的 l2）逐字段一致——`_kline_payload` 加可注入参数后个股分支行为未变。

（对拍脚本为一次性工具，收尾已删。）

## 6. 已知差异 / 未做

1. **周线上游已接通**（§4）——接缝已落到 `app/core/quotes.py`；上游 `interval=1w` 当前
   返回 5003，故线上仍报「不可用」，上游恢复后自动生效（无需再改代码）。
2. **旧端 500 缺陷不复刻**（§3 `999001.TI`）——本实现 200 且如实给 `v=0.0`。
3. **`_index_kline_rows(period="w")` 的 ISO 聚合逻辑**照搬保留但不参与周线响应
   （旧端亦然：周线走 `_index_week_bars`）。留着是为了「口径逐段照搬」的可对读性。
4. 未 commit、未重启 8810（任务书明确）。
5. **未登记 `OH-Works/文件修改日志/2026-10-24.md`**：任务书白名单只给 5 个路径
   （`compat_kline/{backend.py,plugin.json,tests/}` + `paths.py` 一行 + 本 HANDOFF），
   日志文件不在白名单内。需要补登的话由任务方另行授权。

## 7. 回退

删 `app/plugins/compat_kline/tests/test_index_kline.py`、把 `backend.py` 的
`build_index_kline` 分流改回 400、`plugin.json` 回 0.1.0、`paths.py` 删那行
`"ths_index.db"`，重启 8810 即回到改动前（旧 8800 全程未动）。

## 8. 盘点更正：/api/sector_list 定性（2026-10-24 实测）

**结论：`/api/sector_list` 不是真缺口，是死端点 —— 不迁。与 `module_switch`（单条）
`/api/ticket_policy*` 同列为死端点。**

此前差集清单把它记为「真缺口：sector_list 待补」，依据是一句注释。实测依据：

| 检查项 | 实测结果 |
|---|---|
| `夏天-activity/工具/dashboard/web/app.js:201` | 在块注释里（`/* 作战面板桥（2026-09-21） ... */`），只是文案，不是调用 |
| 全仓 .js/.html 搜 `fetch('...sector_list')` | 零命中。`夏天-activity/工具/dashboard/` 下唯一命中就是那条注释；`_export/板块轮动_20261003.html:1375` 是同一段注释的导出副本 |
| panel-v2 全仓搜 `sector_list` | 零命中（无插件、无前端、无门禁引用） |
| 8002 manifest 的指数码来源 | `夏天-activity/工具/dashboard/server.py:531 _index_code_map()` 自己算（口径源 `tdx_industry.load_index_table`），不请求 8800；调用点在 `server.py:600/604/606`（L2/L3/CONCEPT 的 `index_code`）；注释明写「2026-09-22：供 manifest 下发，**免去前端跨源 fetch 8800**」 |
| 8800 上的现存实现 | `engine/server.py:8767 @app.get("/api/sector_list")` → `md.sector_list()`，只读 128 行二级行业清单（含指数码），无任何前端调用方 |

即：8002 的前端拿指数码走自家 manifest，8800 那个接口从来没有被面板调用过，迁移它等于
把无人调用的只读清单复制一份。**本单只做定性落盘，不实现端点。**

若日后 8002 需要跨源取指数码（当前不需要，manifest 已自带），应在 8002 侧补，不在 8810 补。
