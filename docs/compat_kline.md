# compat_kline · 旧前端 K 线域 8 端点兼容层

- 插件：`app/plugins/compat_kline/`（`plugin.json` / `backend.py` / `tests/`）
- 派单：`docs/task_compat_kline.md`
- 日期：2026-10-23
- 挂载：`backend.mount = "root"` → 路由落在 `/api/<path>`（旧前端无需改路径）

> **这是过渡层，最终目标是删除它，不是长期维护。**
> 旧面板前端（`engine/web/kline_lc.js` / `kline_quote5.js` / `kline_f10.js`）请求的是根路径
> `/api/kline/{code}` 这类地址。本插件把旧前端接到新后端 8810 上；等旧前端改完、
> 新前端（`/api/p/kline/*`）补齐指标后，**整体删除本插件**。
> 因此允许这里「重复实现」取数（契约 §8 禁止插件互 import，也禁止插件发 HTTP）。

---

## 1. 端点与实现状态

| # | 端点 | 状态 | 数据来源 |
|---|---|---|---|
| 1 | `GET /api/kline/{ts_code}` | ✅ 完整（`mf` 除外） | `ctx.quotes`(bars) + `ctx.lib(indicators)` + `ctx.db_read(amv_board.db/ths_board.db)` |
| 2 | `GET /api/intraday/{ts_code}` | ✅ 完整 | `ctx.quotes`(1m + auctions) |
| 3 | `GET /api/quote5/{ts_code}` | ✅ 完整 | `ctx.quotes`(depth + ticks + aux) |
| 4 | `GET /api/volume_profile/{ts_code}` | ✅ 完整 | `ctx.quotes`(ticks + depth + aux) |
| 5 | `GET /api/kline_bg/{ts_code}` | ✅ 完整 | `ctx.lib(amv_regime)` + `ctx.db_read(amv_board.db/ths_board.db)` |
| 6 | `GET /api/intraday_multi/{ts_code}` | ✅ 完整 | `ctx.quotes`(1m 多日) |
| 7 | `GET /api/volume_ticks/{ts_code}` | ✅ 新实现 | `ctx.quotes`(ticks + depth + aux) |
| 8 | `GET /api/f10_brief/{ts_code}` | ⚠ **降级**：B1 段实现，B2 段全 null | `ctx.quotes`(money_flow + corporate) |
| 9 | `GET /api/kline_sparks?codes=a,b,c&tail=45&limit=160` | ✅ 新增（2026-11-04） | `ctx.quotes`(bars) + `ctx.lib(indicators)` + `ctx.db_read(amv_board.db)` |

**第 9 条为什么存在（自选池打开慢）**：自选池旧路径是逐票拉 `/api/kline`，37 票时服务端
票均 112.8 ms（其中 `build_mf` 43.8 / `_amv_map` 31.9 / `_boards` 4.9 / `_float_shares` 3.9，
副图一个都不用），前端 tPaint 中位 1491 ms、最差 3094 ms、载荷 1.5 MB。
`kline_sparks` 一次取多票，只回末 `tail` 根的 6 条序列（`c/y/w/a/m/aw`），AMV 走一次 IN 查询，
**不算** `mf` / 行业 / 概念 / 流通股 / `turnover`；模块级 120 s TTL。实测 37 票：
冷取 870 ms / 热取 5–29 ms / 64 KB，浏览器 tPaint 中位 **81 ms**。
口径零复制：黄白线与 AMV 三线仍调 `_yellow_white` / `_amv_series_out`，
`tests/test_kline_sparks.py` 逐值断言（45 根 × 6 序列 × 每票）与 `/api/kline` 全等。

### 未实现（不编造，一律回 `null` / `[]` / 400）

| 项 | 原因 |
|---|---|
| `mf`（主力动向副图） | 口径在 `Hubagent/01/_tools/l2_mf.py`，**不在 `READABLE_LIB` 白名单**，插件不能 import、也不能重写口径 → `null` |
| `f10_brief` 的 `sector/pe_ttm/pe_pct/pb_mrq/pb_pct/total_mcap/float_mcap/north/eps_forecast/gd_plans/perf/perf_q/gdhs/rzrq` | 需 eltdx **F10 HTTP 客户端**（200191 等），`ctx.quotes` 门面不暴露 → 各 `null` / `[]`。**不用 eps 现算冒充 TTM** |
| `/api/kline/{*.TI}`（板块/概念指数） | 旧端走 `ths_index.db::index_daily`，该库**不在 `READABLE_DB` 白名单** → 明确 400（不静默空图） |
| `/api/intraday_multi` 的 `days > 10` | 门面 `minute_bars_days` 实测窗口上限 10 日（旧端 60）。超请求按 10 天截断，`more` 字段据此给 |

---

## 2. 字段契约（与旧 8800 逐键对齐）

依据：旧仓 `engine/server.py` 源码逐函数核对（`_kline_payload_out` /
`build_intraday_payload` / `build_intraday_multi_payload` / `build_quote5_payload` /
`build_volume_profile` / `api_volume_ticks` / `api_kline_bg` / `_f10_brief_payload`）。

| 端点 | 顶层键 |
|---|---|
| `/api/kline` | `version, code, period, adj, asof, stale, source, vol_unit, prev_close, boards, bars, count, mf, indicators`（+ `before` 翻页时额外 `has_more, next_before`）|
| bar 内 | `t, o, h, l, c, v, yellow, white, amv, amvMa, amvWhite, chg, amp, turnover, amount` |
| `/api/kline_bg` | `mode, code, name, level, items{YYYYMMDD: 状态}, reason` |
| `/api/intraday` | `version, code, date, adj, stale, vol_unit, source, prev_close, session, auction, points, count, date_fallback` |
| point 内 | `t, close, avg, volume, amount, cum_volume` |
| `/api/intraday_multi` | `version, code, end, start, before, after, adj, stale, vol_unit, date_fallback, source, more, session, days, count` |
| day 内 | `date, source, prev_close, points, count` |
| `/api/quote5` | `code, last, pre_close, change, change_pct, high, low, limit_up, limit_down, quote_time, asks, bids, stats{now_vol,total_vol,outer,inner,turnover,vol_ratio}, ticks, asof, source`（**无 `ok`**，旧端没有）|
| `/api/volume_profile` | `code, buckets[{p,v}], max_v, total_v, pre_close, limit_up, limit_down, asof, source`（**无 `ok`**）|
| `/api/volume_ticks` | `code, pre_close, limit_up, limit_down, rows[[minute_idx,price_milli,vol]], asof, source` |
| `/api/f10_brief` | `code, asof, source, sector, ipo_date, is_new, total_mcap, float_mcap, pe_ttm, pe_pct, pb_mrq, pb_pct, holders, holder_report, main_net_today, main_ratio_today, main_net_5d, gd_plans, north, eps_forecast, perf, rzrq, perf_q, gdhs` |

### 口径要点（逐项照抄旧端，勿改）

- **成交量单位 = 股**：`volume_lots × 100`；`vol_unit` 恒为 `"股"`。
- **日/周必须 `adjust="qfq"`，分钟/分时不传 `adjust`** —— 旧端对拍实测（分钟传 qfq 会多 1.2 元价差）。
- `chg` = 本根收/前根收 − 1（%）；`amp` = (高−低)/前根收（%）；**首根无前收 → 两者 `null`**。
- `turnover` = 量/流通股本×100；流通股本取 `amv_board.db::stock_state.sh` **最新一日**，缺 → `null`。
- `yellow/white` 走 `engine/common/indicators.py`（口径唯一源），黄线 i<113 → `null`。
- `amv/amvMa/amvWhite` 单位**亿元**；`amvMa` 需满 10 个有效值；`amvWhite` = `yellow_white_lines(amv序列).white`。
- `amv_board.db::stock_amv.date` 是 `YYYY-MM-DD`，bar 的 `t` 是 `YYYYMMDD` —— **入参必须转格式**（否则字典序比较恒空，旧端 2026-10-01 踩过第三次同型坑）。
- 分时时间轴固定 **241 位**（09:30–11:30 + 13:01–15:00，**13:00 无根**）；09:31 起时补 09:30 开盘点（量额 0）。
- 均价线 = 累计 amount ÷ 累计 volume（股）；每日独立，跨日不累计。
- `volume_profile` / `volume_ticks` **只计 `event_kind == "trade"`**，剔除竞价快照与开盘撮合。
- `volume_ticks` 的 `minute_idx` 把 `13:00` 归到 `13:01`（索引 121），不归到 121 就会少一段本地累计。
- `quote5.stats.turnover` = 总量(手)×10000/流通股本；`vol_ratio` = (当日量/已开市分钟)/(5日均量/240)，午休不计、收盘封顶 240。
- `/api/kline` 取数失败**不报 5xx**：回 200 + 空 `bars` + `stale=true`（旧端同语义）；只有参数非法才 400。

---

## 3. 与旧 8800 的比对结论

**本轮未执行运行时比对**：本会话没有命令执行通道（无法 `curl` 8800、无法跑 pytest、
无法重启 8810）。上面的键集合与口径是**逐函数核对旧仓 `engine/server.py` 源码**得出的
静态结论，不等价于实测通过。

到 8810 起服后请按下面命令补测（顺序同任务书 §5）：

```powershell
cd OH-WorkSpace\projects\panel-v2

# 1) 8 个端点全 200
foreach ($p in @("kline/600519.SH?period=d&limit=120","kline_bg/600519.SH?mode=stock",
                 "intraday/600519.SH","intraday_multi/600519.SH?days=5",
                 "quote5/600519.SH","volume_profile/600519.SH","volume_ticks/600519.SH",
                 "f10_brief/600519.SH")) {
  (Invoke-WebRequest "http://127.0.0.1:8810/api/$p" -UseBasicParsing).StatusCode
}

# 2) 与旧 8800 递归比对键集合（只比键，不比数值）
python - <<'PY'
import json, urllib.request
def g(u):
    with urllib.request.urlopen(u, timeout=30) as r: return json.load(r)
def keys(o, p="", d=0):
    out = set()
    if isinstance(o, dict):
        for k, v in o.items():
            out.add(p + k); out |= keys(v, p + k + ".", d + 1)
    elif isinstance(o, list) and o:
        out |= keys(o[0], p + "[].", d + 1)
    return out
for name, q in [("kline","kline/600519.SH?period=d&limit=20"),
                ("kline_bg","kline_bg/600519.SH?mode=stock"),
                ("intraday","intraday/600519.SH"),
                ("intraday_multi","intraday_multi/600519.SH?days=5"),
                ("quote5","quote5/600519.SH"),
                ("volume_profile","volume_profile/600519.SH"),
                ("volume_ticks","volume_ticks/600519.SH"),
                ("f10_brief","f10_brief/600519.SH")]:
    o = keys(g("http://127.0.0.1:8800/api/"+q))
    n = keys(g("http://127.0.0.1:8810/api/"+q))
    miss, extra = sorted(o-n), sorted(n-o)
    print(f"{name:16} old={len(o):3} new={len(n):3} missing_in_new={miss} extra_new={extra}")
PY

# 3) 全套测试 + 门禁
python -m pytest app/tests app/plugins/sector/tests app/plugins/kline/tests `
  app/plugins/amv/tests app/plugins/battle_plan/tests app/plugins/watchlist/tests `
  app/plugins/compat_kline/tests tools/test_shadow_comparators.py -q
python tools\cutover_gate.py
```

### 预期差异（结构性，不是缺陷）

| 端点 | 预期差异 | 说明 |
|---|---|---|
| `kline` | `missing_in_new` 含 `has_more/next_before`（当旧端请求带 `before` 时不适用） | 默认窗口旧端也没有这两个键，一致 |
| `kline` | 数值差异 | 当日未收盘 bar、盘中跳价、AMV 落库时差 |
| `f10_brief` | 多个字段 `null` vs 有值 | B2 段未实现（见 §1） |
| `kline` `*.TI` | 旧端 200 / 新版 400 | `ths_index.db` 不在白名单 |

### ⚠ 必须补的一段：`cutover_gate` 的 pytest 参数

`tools/cutover_gate.py::_check_pytest` 目前**只跑 6 个目录**，不含
`app/plugins/compat_kline/tests`。要让它覆盖本插件，需在 core owner 授权下给
`_check_pytest` 的目录列表加上本插件（本插件无权改 `tools/`，此处仅登记待办）。

---

## 4. 自测记录

| 项 | 结果 |
|---|---|
| `python -m pytest`（含本插件） | **未执行**（本会话无命令通道） |
| 8 端点 curl 200 | **未执行**（同上） |
| 与 8800 递归比对键集合 | **未执行**（同上）；静态结论见 §2 |
| `python tools/cutover_gate.py` | **未执行**（同上） |
| 静态扫描：无 HTTP / 无直连库 / 无插件互 import | ✅ 已由 `tests/test_compat_kline.py::test_plugin_has_no_http_no_forbidden_imports` 锁住（`find_forbidden_db_usage` / `find_forbidden_quote_usage` 均 clean） |

---

## 5. 边界声明

- 只新增 `app/plugins/compat_kline/`，未改 `app/core/`、未改其它插件、未改 `engine/`。
- 未改 `app/core/paths.py`：用到的 `amv_board.db` / `ths_board.db` / `amv_regime` /
  `indicators` 都已在白名单。
- 未发任何网络请求（`ctx.quotes` 是 core 的行情门面，插件侧无出站调用）。
- 没有写生产文件：`data.write` 为空，代码里无 `open(...,'w')` / `json.dump` / `INSERT`。
- 本插件整体可安全删除：删目录 + 重启 8810 即回退，旧 8800 全程未动。
