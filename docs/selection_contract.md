# selection 插件契约（v0.1.0，2026-10-24）

> 任务书：`docs/task_R2_selection.md` ｜ 行为基准：旧端 `engine/web/index.html::renderSelection`（本仓只读副本 `app/web/legacy/index.html` 6894 行起）
> 数据实现：**零后端改动**，四端点全部由 `compat_misc`（mount=root）提供。

## 1. 归属与边界

| 项 | 值 |
|---|---|
| 插件 id / 目录 | `selection` / `app/plugins/selection/` |
| 版本 | 0.1.0（新建） |
| 卡片 | `selection.main`，`slot=main`，`order=55`（排在 watchlist 50 之后） |
| backend | `mount=root`，`register(ctx)` 返回**空 router**（不注册任何路径） |
| 写盘 | `data.write=[]`；筛选偏好/选股池的写由 `compat_misc` 的受控写端点承担 |

**为什么 mount=root 却空 router**：本卡消费的四个端点都是根路径 `/api/selection*`，由 `compat_misc`
以 `mount=root` 注册。插件自己的 router 必须存在（core 的 `load_plugin` 要求 `backend.py` 可 import 且
`register(ctx)` 返回 `APIRouter`），但不能注册同名路径 —— 两个 root 插件抢同一路径会让 `main.py`
启动即抛「根路径冲突」。故本插件只保留空 router 作为契约占位。

## 2. 数据端点（只读契约，全部由 compat_misc 实现）

| 端点 | 方法 | 源 | 本卡用途 |
|---|---|---|---|
| `/api/selection` | GET | `data/panel/selection_latest.json`（8.2MB 盘后快照，只读透传 + 拼 `pool`/`freshness`） | 结果表、分组、筛选选项、热门板块 |
| `/api/selection_prefs` | GET / POST | `data/panel/selection_filter.json` | 筛选偏好读 / 写（覆盖写、只落非 None 字段） |
| `/api/selection_pool` | GET（`?date=`） | `data/panel/selection_pool.json` | 选股池当前名单 / 历史收盘日快照 |
| `/api/selection_pool/add` · `/remove` | POST | 同上 | 行尾 ★ 增删（幂等 upsert / 幂等删除） |
| `/api/selection_board_options` | GET | `ths_board.db` + `panel/watchlist.json` | 板块筛选候选（`favorites` 自选池收藏 + `catalog` 全量 711 项） |

**`/api/selection` 载荷键**（缺快照时同构骨架，键齐、`groups=[]`、`error` 有文案）：
`meta / date / asof / filters / filter_options{industries,concepts,ma,defaults} / group_sigs /
group_filters / board_map{code6→{ind,ind3,con,ind_codes,ind3_codes,con_codes}} / hot_concepts{asof,window_days,level,rule,boards,stocks} /
stats{hits,groups,scored} / warnings / score_warn / groups[] / pool / freshness{state,snapshot_date,expected_date,produce_by,today_pending}`。

`groups[]` 每项 `{key,name,count,rows[],excluded_by_gate?}`；`rows[]` 每项
`{code,code6,name,badges[],sector{code,name,regime},pct,vol_ratio,mav_pct,j,mcap_yi,mcap_src,flow_yi,
defense{kind,label,line,price,status,distance_pct,lines[],ntype_low,broken_event},defense_src,
pin{short,long},spark{close[],yellow[],white[]},quote_asof,kline_asof,group,sig,badge,score}`。

**实测**（2026-10-24 18:5x，8810 在跑）：`/api/selection` 8,223,523 B / 0.26s；`stats={hits:2968,groups:10,scored:81}`；
最大分组 `砖形图` 2824 行；`board_map` 2968 条；`/api/selection_board_options` 42,482 B（favorites 9 / catalog 711）。

## 3. 前端契约

1. 原生 ES module，零 import、零构建、零裸 `fetch`；只操作自己的 root。
2. 读走 `ctx.api.get`、写一律 `ctx.api.post`；**绝对 `/api/…` 路径**（root 挂载端点，无 `/api/p/selection` 前缀）。
3. 颜色一律 `themes.css` 语义 token，零裸 hex；不改写 shell 的 `.card`。
4. **大载荷渲染纪律**（8.2MB / 2968 行）：
   - 固定行高 `ROW_H=30` + 滚动窗口（可视区 ± 6 行缓冲）渲染，DOM 里只留 ~40 行；
   - 排序/过滤在内存数组上做，结果集变化才重置滚动位；
   - 走势列 SVG 只为可见行生成；
   - 首屏渲染完成后在 root 上写 `data-paint-ms`（自身 DOM 属性，供性能实测取值，不写全局变量）。
5. **写操作纪律**：
   - 筛选偏好：改一项 → 300ms 防抖 → `POST /api/selection_prefs`（全量字段，含读到的 `inds/cons` 原样回传，避免覆盖丢字段）→ 回读 `/api/selection_prefs` 核对 → 状态条显示「已保存 hh:mm:ss」或原样失败文案；
   - 选股池：行尾 ★ 与池面板 × 都是**行内二次点击**（第一次点亮「确认…」，4s 内第二次才 POST，同 paper 卡 `data-arm` 模式）；写成功后以服务端回执为准更新 `poolSet` 并重绘，不乐观更新；
   - 写失败原样渲染 `e.message`（含 HTTP 状态与 detail），不吞、不重试。
6. 不声明 `refreshEvery`：数据是 T-1 盘后快照，只有「刷新」按钮和写后重拉。
7. `unmount()` 释放定时器、关闭浮层、清空状态引用。

## 4. 与旧 Tab 的差异（如实记录）

| 项 | 旧 Tab | 本卡 | 原因 |
|---|---|---|---|
| 结果表渲染 | 一次 `innerHTML` 全部行 | 固定行高窗口化，DOM 只留可见行 | 2824 行一次性插 DOM 会卡死；任务书要求分页/虚拟滚动 |
| 卡片视图（表格/卡片切换） | 有 | **无** | 任务书 §1 只点名结果表；卡片视图另开任务 |
| 股票名点开 K 线 | `KLineApp.open`（旧页全局） | 纯文本 + 悬停显示 ts_code | 跨插件导航不属本卡契约（CONTRACT §5 禁依赖其它插件）；需 core 提供统一导航能力 |
| 筛选浮层 | `position:fixed` 浮层挂 body | 卡内联展开面板（筛选条下方） | iframe 内浮层定位与裁切不可靠，功能等价 |
| 市值 min/max | localStorage（`sel_filter_v1` 的 `selMcap`，不落服务端） | localStorage（`sel_mcap_v2`） | 后端 `SelectionPrefsBody` 无市值字段；沿用旧口径 |
| 筛选偏好快速层 | localStorage 双写 + 服务端为准 | **只以服务端为准** | 双写会出现两处不一致；任务书要求 POST 后回读一致 |
| 距防线/走势/徽标口径 | 后端下发，前端零计算 | 同（逐字搬渲染，不重算） | 一个指标一个 owner |

## 5. 验收

```powershell
python -m pytest app/plugins/selection/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

- 静态：`tests/test_selection_frontend.py`（零 import / 零裸 hex / 零写盘 / 端点集合 / 窗口化标记 / 二次点击 / unmount）
- 后端：`tests/test_selection_backend.py`（空 router、root 挂载、无路由冲突、manifest 与卡片一致）
- 浏览器实测：`/v2/?card=selection.main` 首屏 `data-paint-ms`、筛选 POST 回读、池增删按钮态（见 HANDOFF）

## 6. ETF 模式（2026-11-11 加，v0.2.0）

> 背景（主人）：“入市不满两年只能买主板，很多个股买不了，只能买 ETF”。
> 本卡顶部加「个股 / ETF」切换，**个股路径一行未改**（`S.mode` 在两个入口处分派：
> `renderAll` 与 `rowHtml`），ETF 是独立的一套列与独立数据源。

| 项 | 值 |
|---|---|
| 端点 | `GET /api/selection_etf`（仍然由 `compat_misc` 以 root 挂载提供，本插件仍为零路由） |
| 数据源 | `data/panel/etf_snapshot_latest.json`（盘后链 `etf_snapshot` 步，`engine/collectors/etf_snapshot.py`） |
| 载荷 | `{ok, date, generated_at, source, meta, cats[], stats{rows,by_cat}, warnings[], rows[]}`，约 420KB / 1,535 行 |
| 行字段 | `code, code6, name, cat, sub, pct, pct5, pct20, above_ma20, dist_hi60, ratio, amount_yi, tail30, split_suspect` |
| 默认排序 | 成交额降序（ETF 先看流动性）；列头可点，复用个股的排序机制 |
| 筛选 | 分类 tabs（全部 / 宽基 / 行业主题 / 跨境，键与计数由后端给）+ 成交额下限（默认 0.5 亿） |
| 不做的 | 选股池、盘中现算、走势图、买点徽标——ETF 无这些口径，切到 ETF 时对应控件隐藏 |

**口径边界（不掩匩）**：ETF 模式第一版只是「量价 + 位置」，买点徽标已于同日补齐（见 §6.1）；
异动判级（`etf_minute_flow`）是市场级护盘判据，不进本表；以后若要逐只分钟级异动，
需盘后链额外落一份 `etf_minute_latest.json`，另开工单。

### 6.1 买点徽标（2026-11-11 晚补，主人：要的就是买点信号）

ETF 行现在带**与个股同一套**的买点徽标（B1 四形态 / B2/B3/B4 / 单针 / 砖形图三档），
信号日与个股一致（当日收盘）。

| 项 | 值 |
|---|---|
| 生产者 | `夏天-activity/工具/buy_signal/run_etf.py`（盘后链 `etfsignal` 步，紧随 etf_minute） |
| 产物 | `data/buy_signal.db::etf_signal(etf, daily_date, daily_sigs)`（整体替换 daily 组） |
| 公式 | **逐字复用** `buy_signal/core.py`（唯一口径源，不重写、不改阈值） |
| 取数 | eltdx `bars.get(period="day", count=500)`（ETF 不进 track_pool，无前复权库） |
| 能算 | B1_W/B1_Y/B1_Q/TOP_RES、B2/B3/B4、A_GOLD/B_WHITE、ZHU_P/ZHU_O/ZHU_W、**MOM（动能）、周（周B1）** |
| 市值门槛 | 主人 2026-11-11 裁定「**不看流通市值**，反正选股页自带市值筛选」→ `run_etf.MKT_GATE_ALWAYS=1e6` 使 core 的两道门（动能>40亿、周B1≥200亿）恒通过。**公式与阈值仍全在 core**，只是输入表示「不设限」；个股（run_daily）的 PIT 市值口径一行未动 |
| 边界 | 窗口内单日跳空 >50%（份额折算）→ 只取折算后段，不足 250 根则跳过该只；周线要 114 周（取数取 700 根日线 ≈140 周） |
| 端点 | `/api/selection_etf` 额外返回 `sig_n`（命中只数）、`sig_meta`（code→label/cls/desc，取自 `selection_latest.json::group_sigs`）、行内 `badges[]`（有才带） |
| 前端 | 名称后徽标（复用 `.sel-sg`）+ 两个筛选 chip：「只看买点」「只看 B1」（再点同一个 = 取消） |
| 自选页 | `compat_watchlist._stock_badges` 查不到个股时回落 `etf_signal`（ETF 也能在自选卡看到徽标） |

**实测（2026-11-11 18:3x，11-11 收盘数据）**：全量 1,707 只 → 取回 1,665（36s）→ 有效计算 1,210 只（112s，
跳过：无日线 42 / 根数不足 402 / 折算后不足 53 / 异常 0）→ 命中 355 只。
形态分布：ZHU_W 275、A_GOLD 65、B_WHITE 11、B1_W 8、B1_Q 3、B1_Y 1、TOP_RES 1、ZHU_P 1。
**已知特性**：砖形图在 ETF 上命中率偏高（275/1210≈23%，个股 627/5390≈12%）——
ETF 波动小、形态平滑，公式口径不变；B1 才稀缺（全市场只有 12 只，全集中在红利/价值/现金流风格）。

**实测（2026-11-11 16:4x，8810 在跑，headless Chrome）**：切到 ETF → 10 列 / 首行“中韩半导体ETF华 107.05 亿”；
分类“宽基”→ 116 只；按涨幅降序生效；成交额下限 20 亿 → 12 只；切回个股 12 列/图例/池按钮全部恢复，无报错。
