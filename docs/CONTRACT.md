# 面板 v2 插件总线契约（唯一权威）

**版本**：2.0（2026-10-23，纸面交易迁移）｜ **core**：0.3.1
**原则**：manifest 是纯数据；核心只做加载/路由/生命周期/健康；插件按域，不按小卡片拆。

> 1.1 变更：新增 `data.json`（非 `data/panel` 的生产 JSON 只读白名单）与 `ctx.json_read(name)`；
> `data.read` 白名单新增 `buy_signal.db`（买点徽标）、`amv_minute.db`。旧插件不用改：
> 未写 `data.json` 等价于空数组。
>
> 1.2 变更：新增 `data.lib` 与 `ctx.lib(name)`——**口径库白名单**（见 §8.1）。
> 解决「指标口径唯一源在工具目录、插件不许重写也不许 import」的矛盾。
> 旧插件不用改：未写 `data.lib` 等价于空数组。
>
> 2.0 变更（2026-10-23 纸面交易迁移）：新增 `data.feed` / `ctx.feed`（core 唯一 QMT 只读订阅）与
> `data.paper` / `ctx.paper`（core 纸面交易服务）；纸面账本独立在 `data/panel_v2/paper/`，
> 不再双写旧 `engine/state` 或 `data/panel`。插件仍**禁止**出现 `xtquant/xtdata/TdxClient/from eltdx`。

---

## 1. 插件目录

```
app/plugins/<id>/
  plugin.json       # 纯数据 manifest
  backend.py        # def register(ctx) -> APIRouter
  frontend.js       # export default { cards: [...] }
  tests/            # 契约测试 + 业务测试
```

`<id>` 必须与目录名一致；只允许 `[a-z][a-z0-9_]*`。

## 2. manifest 字段

```json
{
  "manifest_version": 1,
  "id": "sector",
  "name": "板块",
  "version": "0.1.0",
  "min_core_version": "0.1.0",
  "enabled": true,
  "kind": "domain",
  "backend": {
    "module": "backend",
    "entry": "register",
    "timeout_ms": 5000,
    "permissions": ["read:amv_board", "read:ths_board"]
  },
  "frontend": {
    "module": "frontend.js",
    "cards": [
      {"id": "sector.board", "title": "板块", "slot": "main", "order": 10}
    ]
  },
  "data": {
    "read": ["amv_board.db", "ths_board.db"],
    "panel": ["amv_regime_latest"],
    "json": ["stock_names"],
    "lib": ["amv_contrib"],
    "quotes": false,
    "write": []
  },
  "config": {},
  "depends": []
}
```

## 3. 生命周期

```
discover → validate → import backend → register(ctx) → mount router → start? → health → stop?
```

- `register(ctx)` 必须返回一个 `fastapi.APIRouter`；核心统一挂到 `/api/p/<id>`。
- 插件不得自己调用 `app.include_router` 或注册全局路由。
- 可选 `start(ctx)` / `stop(ctx)`；v1 先不做后台任务。
- 加载/注册失败：记录到 `/_plugins/health`，只禁用该插件，不影响核心与其他插件。

## 4. 数据访问

- 数据库：只允许通过 `ctx.db_read("amv_board.db")` 获得只读连接：
  - `sqlite3.connect("file:...?mode=ro", uri=True)`
  - `PRAGMA query_only=ON`
  - 白名单（`app/core/paths.py::READABLE_DB`）：`amv_board.db` / `ths_board.db` / `track_pool.db` / `buy_signal.db` / `amv_minute.db`
- 生产 JSON（`data/panel/*.json`）：只允许通过 `ctx.panel_json("amv_regime_latest")` 读取；名字必须同时出现在 `data.panel` 与核心白名单（`amv_live` / `amv_regime_latest` / `position_cap_latest` / `amv_breadth_latest` / `battle_plan` / `real_positions` / `alert_todo` / `watchlist` / `watchlist_quotes`）。
- 其他生产 JSON（不在 `data/panel`）：只允许通过 `ctx.json_read("stock_names")` 读取；名字必须同时出现在 `data.json` 与 `READABLE_JSON`（`hcl_latest` / `stock_names`）。**只登记已有生产脚本在写的文件，不新造产物**。
- v1 插件 `data.write` 必须为空；需要写的数据写 `ctx.data_dir`（`data/panel_v2/<id>/`）。
- 插件代码中禁止出现 `sqlite3.connect` / `pymysql` / `psycopg` 等直连；契约测试静态扫描。
- 公共指标（AMV/板块/指数）只读生产库；不复制计算。

### 4.1 行情源白名单（v1.3 新增，2026-10-23）

**问题**：分时图、五档盘口、逐笔筹码这三项没有现成生产文件可读，只能实时向行情源要。
如果每个插件各自 `TdxClient()`，就是 **N 个连接、N 套重连逻辑、N 套降级分支**；
行情源换地址/换协议时要改 N 处。这与“一个指标一个 owner”同一条理由。

**规则**：

1. 行情源只能通过 core 的 `ctx.quotes` 用（`app/core/quotes.py`），插件在
   `manifest.data.quotes: true` 里显式声明。未声明 → `PermissionError`。
2. 插件源码禁止出现 `from eltdx` / `import eltdx` / `TdxClient` / `xtquant` / `xtdata`；
   契约测试用 `find_forbidden_quote_usage()` 静态扫描。
3. 门面只做三件事：**单例连接**（全进程一个，懒建、断线重建一次）、
   **短 TTL 缓存**（分时/五档 3s、逐笔 20s、aux 60s）、**代码归一**（`600519.SH` ⇄ `sh600519`）。
   它不算任何指标——均价线、换手、量比、筹码分桶全部在插件里算（口径可见、可测）。
4. **不可用一律返回 None / 空列表，不抛**。调用方据此降级（`ok=false` + `reason`），**不补零、不编造**。
5. **这不是“新版接 QMT”**：§8 禁的是写 `data/panel`、下单、接 QMT 的写链路。
   eltdx 是**只读行情源**，与 `ctx.db_read` 同级；旧 8800 的分时/五档/筹码也全部走它，
   同源才谈得上双跑比对。

**当前使用**：`kline`（`/intraday` 分时 · `/quote5` 五档 · `/volume_profile` 筹码）。

**分时口径的关键细节（三个实测坑，都已收进门面）**：

1. **`count` 是跨日的“最近 N 根”**，不是“当日全量”。2026-10-23 实测 `count=241` 取回的是
   「昨日 11:30 → 今日 13:00」跨两天 241 根；直接当当日分时用会得到错位时间轴
   （缺 09:30–11:29、多出昨天下午）。`minute_bars()` 已统一处理：先多取（2 倍），
   再按**末根所在日期**过滤、升序。
2. **单次 `count` 上限约 800**：实测 `count=800` 正常、`count=900` 直接返回 `None`（无异常）。
3. **翻页必须用 `start` 偏移**，不能只改 `count`——后者每次返回同一段。
   实测 `start=0/241/482` 分别返回相邻三段。`_bars_page()` 负责按 `start` 翻页拼。
   （早期版本用“多次 count”拼多日，只拿到 4 天且首日半天，就是这个原因。）

另：`minutes.history(code, date)`（协议 0x0FB4）虽然能取更深历史，但**无 `amount`、无 09:30、无 `open`**，
均价线只能价×量推，与当日分时（走 1m bars，有真 amount）口径不一致 —— 所以多日分时也统一走 `bars.get` + start 翻页。

### 4.2 受控写（v1.4 新增，2026-10-23）

**背景**：迁移旧后端全部端点时，自选池增删改、签收、急停、下单这些**写操作**必须能落盘。
旧的「插件零写入」在这个前提下不可能保留。于是从“禁止写”改成“**受控写**”。

**规则**：

1. 只能写 `paths.WRITABLE_PANEL` 里登记的文件（当前：`watchlist` / `selection_filter` / `selection_pool`），且插件必须在
   `manifest.data.write` 里显式声明；两边对不上 → 拒绝加载。
2. 写一律走 `ctx.panel_write(name, payload)`，它内部做三件事：
   - **备份**：把当前文件拷到 `data/panel_v2/backups/<name>/<时间戳>.json`
   - **原子替换**：先写同目录 `.tmp` 再 `os.replace`，不会产生半个文件
   - **审计**：`backups/<name>/audit.log` 追加一行（时间 / 插件 / 文件 / 字节数）
3. 插件仍**不得**自己 `open(...,"w")` / `.write_text()` / `os.replace()` —— 静态扫描拦。
4. 新增一个可写文件要改两处（`paths.WRITABLE_PANEL` + 插件 manifest），并在这里登记原因。

**当前登记**：`watchlist`（自选池条目/分组增删改）、`selection_filter`（选股页筛选偏好）、`selection_pool`（选股池 add/remove，v2 快照结构）。

**对回退的影响**：旧版「停 8810 即回到原样」的前提是**新版零写入**。现在有了写通道，
回退时要多一步：确认 `data/panel/watchlist.json` / `selection_filter.json` / `selection_pool.json` 是否被新面板改过，改过则从备份恢复。
具体步骤见 `docs/CUTOVER_RUNBOOK.md` §3.3。

### 4.3 QMT 整推行情（v2.0 新增，2026-10-23）

**背景**：纸面交易是 tick 事件驱动，旧 8800 的 `subscribe_whole_quote` 回调在每个 tick 上调
`evaluate()` + `match_pending()`；没有 tick 就没有撮合。新版必须在 8810 进程里有常驻订阅。

**规则**：

1. **只有 `app/core/feed.py` 可以 import `xtquant/xtdata`**。插件通过 `ctx.feed` 使用；
   `ctx.feed` 需要 `manifest.data.feed: true`，未声明 → `PermissionError`。
2. `QuoteFeed` 是进程单例：单订阅 owner + 内存快照 + `queue.Queue` 派发 + 60s 断流自愈。
   QMT 未启动时 `status.state` 为 `ERROR`，supervisor 每 60s 重试；**不阻塞 8810 启动**。
3. 订阅清单由 `ctx.feed.set_interest_provider(fn)` 每 15s 拉取（持仓 + 活跃条件单 + 挂单）；
   `ensure()` 只增不减，增量订阅不重复订阅旧代码。
4. 开关：`PANEL_V2_FEED=1`（`run.ps1` / `ensure_8810.ps1` 设）；测试默认 0，不得连 58610。
5. 插件仍禁止 `TdxClient` / `from eltdx` / `xtquant` / `xtdata`；`cutover_gate.py` 静态扫描。

### 4.4 纸面交易服务（v2.0 新增，2026-10-23）

**规则**：

1. 纸面交易业务唯一源 = `app/core/paper/`（broker / orders / state / service）；
   `app/plugins/paper/` 只做 HTTP 参数校验与 JSON 装配，**不含业务规则、不写文件**。
2. 插件通过 `ctx.paper` 拿 `PaperOrderService`；需 `manifest.data.paper: true`。
3. `trade_mode()` 恒 `"paper"`、`live_order_enabled()` 恒 `False`；纸面包内**零** `xtquant/xttrader/qmt_trader`
   字符串，静态扫描 + 单测双重守门。
4. 账本目录 = `paths.PAPER_STATE_DIR`（默认 `panel-v2/data/panel_v2/paper/`，`PANEL_V2_PAPER_DIR` 覆盖）；
   **不登记进 `WRITABLE_PANEL`**，旧 `data/panel` 一字节不写。
5. 写账本只能走 `app/core/paper/state.py`：文件锁 + 原子替换 + 写前备份（60s 节流）+ `audit.log`。
6. 一键回初始态：`tools/paper_reset.py --yes`；旧账本迁移：`tools/paper_import_legacy.py`。
7. `signal_buy` 条件单依赖 `signals/library.py`，本版**未接**（`signal_provider=None`，不触发）；
   其余 8 类条件单全部可用。

### 4.5 文本目录白名单（v2.1 新增，2026-10-23）

**背景**：`/api/concept.json` 的数据源是 `夏天-activity/概念追踪/` 的 Markdown 知识库
（总表 + 每个概念的 `概况.md`），不是 JSON 文件。

**规则**：

1. 目录登记在 `paths.READABLE_TEXT_DIR`（当前 `concept_tracking`），插件在 `manifest.data.text` 声明；
   未声明/未登记 → `ctx.text_dir(name)` 抛 `PermissionError`。
2. `ctx.text_dir(name)` 只返回目录 Path，**只读**；插件自行读文件，但不得写。
3. 仍然禁止 `open(...,"w")` / `write_text` / `os.replace` 等写操作（`cutover_gate` 静态扫描）。
4. 当前使用：`concept` 插件（`/api/concept.json`）。

### 4.6 实时活跃市值快照（`data.amv_live`，2026-10-24 新增）

**背景**：旧 8800 `/api/amv_research.json?live=1` 的分子是「现在这一刻的逐股活跃市值」，
数据源 `engine/executor/amv_live.py::stocks_snapshot()`（内部是 `amv_store.step_one_day`
v9 递推，与 tick_feed 同一条算路）。这是**重模块 + 读 QMT** 的组合，插件两样都拿不到：
`amv_live` 顶层 `import numpy` / `amv_store`，且要连 QMT `get_full_tick`。
若让插件自己 import，就是 N 套连接与降级分支（理由同 §4.1 `ctx.quotes` / §4.3 `ctx.feed`）。

**规则**：

1. 门面是 `app/core/amvlive.py`，插件经 `ctx.amv_live` 用，**必须**在 `manifest.data.amv_live`
   声明 `true`；未声明 → `ctx.amv_live` 抛 `PermissionError`。非布尔值 → `ContractError`。
2. 接口只有两个：
   - `ctx.amv_live.snapshot() -> dict | None`，形状同旧仓
     `{date, asof, state_date, all_amv, base_all, stocks:{code6:[amv,alive,sh,chg,amt]}}`；
   - `ctx.amv_live.last_error() -> str | None`，失败原因（`timeout` / `<ExcType>: <msg>` /
     `empty` / `bad_snapshot`），供上层填 `live_err`。
3. 缓存：盘中 25s / 盘外 300s（旧端 `_RS_LIVE_TTL_ON/OFF` 同值）；**失败也写缓存**，防连点打穿 QMT。
   并发：非阻塞锁 + 线程 `join(6s)`，上一轮还在算就返回旧帧，超时不 kill。
4. core 启动期**不 import** 本模块（`main.py` 不引用），只在首次调用时动态 import；
   旧仓 import 根 `<WS>/engine` 临时插进 `sys.path`，**只删本次新增条目**，不留残留。
5. **口径零复制**：本门面不算任何指标，只说「何时调、多久缓存、失败怎么报」；
   算路唯一源仍在 `engine/executor/amv_live.py`。
6. 当前使用：`compat` 插件（`/api/amv_research.json?live=1` 的分子）。

### 4.7 盘后落盘缓存（`ctx.cache_read` / `ctx.cache_write`，2026-11-07 新增）

**背景**：收盘数据是算好的，面板重启/重开后不该再重算。内存 TTL 只在进程内有效，
所以给插件一个「算一次、存下来」的口子：路径由 core 管（`data/panel_v2/_cache/<plugin>/<name>.json`），
插件不自己 open/建目录。

**规则**：

1. 名字只允许 `[A-Za-z0-9_.-]`（≤64），防路径穿越；路径由 `paths.plugin_cache_path` 拼。
2. `cache_read(name)`：不存在 → `FileNotFoundError`；损坏/非对象 → `ValueError`。
3. `cache_write(name, payload)`：原子写（tmp → replace），**无备份/审计**（缓存可重算）。
4. **三条铁律（调用方守，core 不做业务判断）**：
   - 空数据不写：榜单/序列 0 条、`ok=false` 不得写入；
   - 失败不写：取数失败/超时/降级回上一交易日的结果不得写入；
   - 日期对齐：读时 payload 数据日必须等于当前应有交易日，不等当 miss。
5. 当前使用：`compat_watchlist`（日更富化）、`compat`（amv_research）、`compat_kline`/`kline`（当日分时）。

### 4.8 交易日时钟（`ctx.trade_clock`，2026-11-07 新增）

**背景**：盘后落盘、预热、页面「数据日」三处都要判「今天是不是交易日、该看哪一天」。
此前三个插件各写一份 16:10 时钟，只看周末不判节假日。

**接口**（`app/core/trade_clock.py`，唯一日历源 = `engine/common/trading_calendar`）：

| 方法 | 语义 |
|---|---|
| `is_trading_day(d)` | 交易日；日历不可用 → 工作日兜底并标 `degraded()` |
| `expected_data_date8(now)` | 该看的数据日：交易日 15:00 后=当天，之前/非交易日=最近交易日 |
| `postclose(now)` | 数据固定窗口：非交易日，或交易日 ≥ **16:40**（链 READY_DEADLINE；16:10 只是启动） |
| `prev_trading_date(d)` / `latest_trading_date(d)` / `status(now)` | 辅助 |

插件**不得**再自建时段常量；需要注入时间时传 `now=`。

### 4.9 跨插件产物只读（`ctx.output_read`，2026-11-07 新增）

一个指标一个 owner：产物归生产插件，消费方只读、不重算。
`ctx.output_read(plugin_id, name, default=None)` 读另一个插件输出目录的 JSON（`data/panel_v2/<plugin>/<name>.json`），
缺失/损坏 → `default`。当前使用：`intraday` 读 `thermometer/index_880008_series.json`。

### 4.10 盘后预热（`app/core/prewarm.py`，2026-11-07 新增，非插件接口）

后台线程每 60s 读后台看板（`data/panel/ops_status.json`）：
看板 `meta.trade_date` == `trade_clock.expected_data_date8()` 且清单 `ready_sources` 全部「新鲜」→
按 `data/panel_v2/prewarm_plan.json` 并行 GET 首屏端点，让落盘缓存先热。
未就绪不预热；单任务失败只记录，同一数据日最多重试 3 次。
清单条目支持 `{codes}` + `codes_from`（从 panel JSON 按字段/过滤条件取代码，core 不含业务常量）。
**只用于「知不知道数据好了」，绝不参与链自己判断跑不跑。**

## 5. 前端卡片

`frontend.js` 是原生 ES module，无构建：

```js
export default {
  cards: [{
    id: "sector.board",
    title: "板块",
    slot: "main",
    order: 10,
    refreshEvery: 0,          // 可选。>0 时 shell 每隔这么多毫秒调一次 refresh()
    async mount(el, ctx) { /* 渲染 */ },
    async unmount() { /* 清理定时器/监听 */ },
    async refresh() { /* 可选；仅当 refreshEvery>0 才会被调 */ }
  }]
};
```

- 核心提供 `ctx.api.get(path)`，自动加 `/api/p/<id>` 前缀。
- 卡片只能操作自己的 `el`；不得写全局变量、不得依赖其他插件。
- 核心生成 `GET /_plugins`（清单）和 `GET /_plugins/health`（状态）。

### 5.1 自动刷新（v0.2.1 新增，2026-10-23）

**背景**：在 2026-10-23 之前，shell **没有任何定时器**，各插件的 `refresh()` 全是空壳——
自选池卡的盘中现价只在打开页面那一刻抓一次，之后不再变。旧面板是自刷的，这是真实功能回退。

**机制**：卡片自报 `refreshEvery`（毫秒），shell 在挂载后启动定时器，卸载时清除；
页面不可见（`document.visibilityState !== 'visible'`）时跳过 tick；上一轮未完成不重入。

**为什么由卡片声明、而不是 shell 统一刷**：各卡数据的新鲜度差异极大——自选池现价 20 秒一变，
板块榜是 T-1 日线且一次重算几千行。统一刷会造成真实的无效负载（板块卡每 30 秒重算 3,876 行成分股）。
默认 `0` = 不刷新，需要实时性的卡片自己声明。

**当前声明**：`watchlist.main` → `refreshEvery: 20000`（tick_feed 的行情文件 60 秒落一次盘，20 秒足够跟上）。
其余五张卡不声明（数据源本身是日频或按需刷新）。

## 6. 路由与错误

- 所有插件路由统一 `/api/p/<id>/...`；核心负责前缀，插件内部只写相对路径。
- 核心统一异常包装：插件抛错 → 500 + `{"error":"plugin:<id>","detail":"..."}`；健康检查标红。
- 超时：`timeout_ms` 由核心在调用层实现（v1 可用同步超时/线程池；先保证不挂死）。

## 7. 版本与兼容

- `manifest_version` 当前为 1；核心拒绝未知版本。
- `min_core_version`：低于核心版本则拒绝加载，记录原因。
- 破坏性接口变更：核心升 minor，插件同步抬 `min_core_version`；旧插件不静默运行。

## 8. 禁止

- 微服务/多进程插件、微前端、构建链。
- 每个小卡片一个插件（按域拆分）。
- 插件互相 import、共享全局状态、直连数据库。
- 运行时热替换代码；只支持启用/禁用（重启生效）。
- 新版接 QMT/下单/写 `data/panel`。
  **v2.0 修订**：仅 `app/core/feed.py` 可接 QMT 只读行情；纸面下单只允许走 `app/core/paper/`；
  **真实交易通道永久禁止**；`data/panel` 仍禁止写。

### 8.1 口径库白名单（v1.2 新增，2026-10-22）

**问题**：部分指标的口径唯一源在 `夏天-activity/工具/` 下的独立纯函数模块（如 `amv_contrib.py`）。
若插件重写一份就是口径双源（违反“一个指标一个 owner”）；若一律禁 import，这些指标就永远搬不过来。

**规则**：

1. 口径库必须登记在 `app/core/paths.py::READABLE_LIB`（名 → 模块文件路径），且插件在
   `manifest.data.lib` 里显式声明。未登记/未声明 → 拒绝加载。
2. 口径库必须是**纯函数库**：import 期无副作用（不连库、不开文件、不发网络、不起线程）。
   **实测补充（2026-10-23）**：`amv_contrib.py` 在 import 期会 `sys.path.insert(0, 工具目录)` 且不清理，等于把整个工具目录塞进 `sys.path[0]`——任何插件两行 `import amv_regime` 就能绕过 facade。core 的 `ctx.lib` 现在**导入前后快照并恢复 `sys.path`**，并有回归测试锁住。
   核心在加载时做静态扫描，命中 `sqlite3.connect` / `open(` / `urllib` / `socket` / `threading` / `subprocess`
   即拒绝。
3. 口径库只算数学/口径，**不碰数据源**；数据仍由插件通过 `ctx.db_read` 取好后传进去。
4. 插件不得写口径库、不得改其返回值语义；口径变更只能改正本并同步。
5. 每个口径库在 CONTRACT 里列一行“谁用、算的是什么”，新增需说明为何不能重写。

**当前登记（`READABLE_LIB`）**：

| 名 | 模块 | 算的是什么 | 用它的插件 |
|---|---|---|---|
| `amv_contrib` | `夏天-activity/工具/amv_contrib.py` | AMV 变化五档分解（涨跌/流通股/活跃度/新股/停牌）+ 大盘基准，逐股恒等于 ΔAMV | `sector`（`/contrib`） |
| `amv_regime` | `夏天-activity/工具/amv_regime.py` | AMV 双轨多空状态机（MA10 + pct±4%/−2%；共振当天/单轨第二个） | `sector`（`/board_flow` 的 `bull`） |
| `panel_scores` | `夏天-activity/工具/panel_scores.py` | 个股评分的五窗口横截面百分位加权（0–100）与板块内名次轨迹；**2026-10-23 从旧 `dashboard/server.py` 抽出**（`stock_score_map` / `sector_rank_trail` 已改为调它，口径单源） | `sector`（`/score_trail`、`/members?full=1` 的 `score/mkt_rank/sec_rank/trail`） |

## 9. 验收（每个插件）

1. `manifest` 必填字段齐全，`id` 与目录名一致。
2. `register(ctx)` 返回 APIRouter；所有路径在 `/api/p/<id>` 下。
3. 插件代码无直连数据库；`data.write` 为空。
4. `GET /_plugins/health` 中该插件 `ok`。
5. 前端 `frontend.js` 可被 shell 动态 import，卡片 id 唯一，`mount/unmount` 不报错。
6. 改一个字段，diff 只落在一个插件目录。
