# 任务书：compat_kline 插件（旧前端 K 线域端点）

- 派单：01 → DSHana
- 日期：2026-10-23
- **与另一个 DSH 任务并行执行**：另有一个任务在写 `app/plugins/compat/`，
  你**只写 `app/plugins/compat_kline/`**，不要碰 `compat/`、不要碰 core、不要碰其他插件。

## 1. 目标

旧面板前端（复用旧界面，见 `docs/legacy_endpoints_inventory.md`）需要一批 `/api/...` 端点。
本插件负责 **K 线域 8 个**，响应格式必须与旧 8800 逐字段一致。

| 旧路径 | 新后端已有的对应能力 |
|---|---|
| `GET /api/kline/{ts_code}?period=d&limit=120&before=&refresh=&mf_lookback=` | `/api/p/kline/bars/{ts_code}`（**字段名不同**：旧 `t/o/h/l/c/v`，新 `date/open/high/low/close/volume`；旧还多 `amv/amvMa/amvWhite/chg/amp/turnover`）|
| `GET /api/kline_bg/{ts_code}?mode=stock` | `/api/p/kline/bg/{ts_code}`（**结构不同**：旧 `items{日期:状态}`，新 `dates[]+states[]`）|
| `GET /api/intraday/{ts_code}` | `/api/p/kline/intraday/{ts_code}`（几乎同构，需核对字段名）|
| `GET /api/intraday_multi/{ts_code}?days=5` | `/api/p/kline/intraday_multi/{ts_code}`（几乎同构）|
| `GET /api/quote5/{ts_code}` | `/api/p/kline/quote5/{ts_code}`（几乎同构）|
| `GET /api/volume_profile/{ts_code}?until=` | `/api/p/kline/volume_profile/{ts_code}`（同构）|
| `GET /api/volume_ticks/{ts_code}` | **无**，需新实现 |
| `GET /api/f10_brief/{ts_code}` | **无**，需新实现 |

## 2. 关键做法

**不要重新取数**。前 6 个端点的数据新后端已经有了，你只做**格式转换**：
内部用 `httpx`/`requests` 调本机 8810 的新端点？**不行**——插件内不允许发 HTTP 请求（契约 §8）。

正确做法：插件里 `from app.plugins.kline import backend as kb` 直接调用它的纯函数？
**也不行**——CONTRACT §8 禁止插件互相 import。

所以：**在本插件内重新实现取数**（只读、同源）：
- K 线/分时/五档/筹码 → `ctx.quotes`（eltdx 行情门面，用法见 `app/core/quotes.py`）
- 四态背景带 → `ctx.lib("amv_regime").stock_state_series` + `ctx.db_read("amv_board.db")` 的 `stock_amv`
- 黄白线 → `ctx.lib("indicators").yellow_white_lines`
- 逐笔/筹码分桶 → `ctx.quotes.today_ticks`

**允许这种"重复实现"**：这是过渡期的兼容层，目标是把旧前端接上；等旧前端改完、新前端补齐后，
本插件整体删除。在 `docs/compat_kline.md` 里写明这一点。

### 2.1 `/api/kline/{ts_code}` 的字段要求（最重要）

旧端 bar 字段：`{t,o,h,l,c,v,yellow,white,amv,amvMa,amvWhite,chg,amp,turnover,amount}`

- `t` = `YYYYMMDD`
- `o/h/l/c` = 前复权价（`ctx.quotes.daily_bars(glb, n, adjust="qfq")`）
- `v` = **股**（eltdx 的 `volume_lots` × 100）
- `yellow/white` = 黄白线（口径库 `indicators`）
- `amv/amvMa/amvWhite` = 日线 AMV 副图三线（**可以先返回 null**，在报告里标注未实现）
- `chg` = 本根收/前根收 − 1（%）；`amp` = (高−低)/前根收（%）；`turnover` = 量/流通股本（%）
  - 首根无前收 → `chg/amp` 为 `null`（不编造）
  - 流通股本缺失 → `turnover` 为 `null`
- `amount` = 成交额（元）

顶层字段（旧端实测）：`{version,code,period,adj,asof,stale,source,vol_unit,prev_close,boards,bars,has_more,next_before}`。
**先 `GET http://127.0.0.1:8800/api/kline/600519.SH?period=d&limit=5` 把真实响应存下来当契约**，逐键对齐。

### 2.2 `/api/volume_ticks/{ts_code}` 新实现

旧端返回 `{code, pre_close, limit_up, limit_down, rows:[[minute_idx, price_milli, vol], ...], asof, source}`：
- `minute_idx` 对齐 241 位时间轴（09:30=0 … 11:30=120，13:01=121 … 15:00=240）
- `price_milli` = 价格 × 1000
- 只计真实成交（`event_kind == "trade"`），剔除竞价快照
- 同 (分钟, 价位) 合并求和

### 2.3 `/api/f10_brief/{ts_code}` 新实现

旧端返回 F10 基础信息（估值/股本/股东/资金流等）。**这个最复杂**，如果 eltdx 的 F10 取数
（`ctx.quotes.call("helpers...")` 或 HTTP 200191）拿不到，就**返回全部 null 的骨架 + 在报告里标注未实现**，
不要编造。允许本端点降级。

## 3. 边界（硬约束）

**允许**
- 只写 `app/plugins/compat_kline/` 目录下文件
- 只读：`ctx.quotes` / `ctx.lib` / `ctx.db_read` / `ctx.panel_json`
- 用到的库/数据库已在白名单，**不要改 `app/core/paths.py`**

**禁止**
- 改 `app/core/*`、`app/plugins/{sector,kline,amv,battle_plan,watchlist,compat}/`
- 改 `engine/`（旧仓）
- 写任何生产文件
- 插件间互相 import
- 发 HTTP 请求（`requests`/`urllib`/`httpx` 一律禁止，契约会静态扫描拦截）
- 新依赖

## 4. 环境事实（直接用）

- 8810 在跑；插件端点在 `/api/p/<插件id>/...`
- 旧端 8800 在跑，是格式权威
- **重启 8810**（改完必须重启）：
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*port 8810*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  schtasks /Run /TN "PanelV2_KeepAlive"
  ```
  等 10 秒。
- manifest 参考 `app/plugins/kline/plugin.json`（注意 `data.quotes: true`、`data.lib`）
- `ctx.quotes` 用法见 `app/core/quotes.py` 的 docstring：
  `normalize(code)` / `daily_bars(glb, count, adjust="qfq")` / `minute_bars` /
  `month_days` / `today_ticks` / `depth` / `stock_aux`
- **eltdx 坑（已实测）**：`bars.get` 单次 `count>800` 返回 None；翻页必须用 `start` 偏移；
  1m 的 `count` 是跨日「最近 N 根」，不是当日全量

## 5. 自测（逐条做，写进回执）

1. `python -m pytest -q` → 全绿，且**总数 ≥196**
2. 8 个端点在 8810 上 `curl` 全 200
3. 逐端点与 8800 递归比对 JSON：
   - 允许数值差异（数据更新/精度）
   - **不允许键名缺失或结构变化**
   - 把每个端点的「键集合是否一致 / 值差异条数+原因」列进报告
4. `python tools/cutover_gate.py` → 8 项 PASS（含「插件无写代码」静态扫描）

## 6. 交付

- 代码：`app/plugins/compat_kline/{plugin.json, backend.py}`
- 测试：`app/plugins/compat_kline/tests/test_compat_kline.py`
- 报告：`docs/compat_kline.md`
- 回执：**≤300 字 + 路径**（做了几个端点、pytest 数字、比对结论、未实现项）

## 7. 优先级（做不完时按此顺序）

1. `/api/kline/{ts_code}`（K线弹窗是最高频）
2. `/api/intraday/{ts_code}`、`/api/quote5/{ts_code}`、`/api/volume_profile/{ts_code}`
3. `/api/kline_bg/{ts_code}`、`/api/intraday_multi/{ts_code}`
4. `/api/volume_ticks/{ts_code}`
5. `/api/f10_brief/{ts_code}`（允许降级为 null 骨架）

## 附录：根路径挂载（2026-10-23 已由 01 实现，直接用）

旧前端请求 `/api/<名>`（没有 `/api/p/<插件>` 前缀）。core 现已支持：

- `plugin.json` 的 `backend.mount` 设为 `"root"` → router 挂到 `/api`
- 默认 `"plugin"` → 挂到 `/api/p/<id>`
- 两个 root 插件注册同一路径会**启动即报错**（不静默覆盖）

所以你的 `plugin.json` 写：

```json
"backend": {
  "module": "backend",
  "entry": "register",
  "mount": "root"
}
```

插件里 `@router.get("/kline/{ts_code}")` 最终就是 `/api/kline/{ts_code}`。
`path` 里**不要**再写 `/api` 前缀。
