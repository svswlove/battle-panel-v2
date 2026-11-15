# M4 切流前 shadow 双跑方案

**工具**：`tools/shadow_check.py`
**目的**：M4 切流前，同一逻辑请求同时打旧端与新 8810，按域比对响应，给切流提供可复核的证据，而不是靠人工点页面。
**边界**：只发 HTTP GET。不连库、不写生产数据、不改旧端口、不建常驻服务/计划任务。结果只落项目内 `data/panel_v2/shadow/`。

## 1. 运行方式

```powershell
cd <panel-v2>

# 跑一轮（默认行为，--once 可省）
python tools\shadow_check.py --once

# 前台循环，5 分钟一轮；Ctrl-C 结束
python tools\shadow_check.py --loop --interval 300

# 只跑某几个域
python tools\shadow_check.py --once --domains sector,kline

# 严格模式：float32 精度差异也判 fail（见 3.3）
python tools\shadow_check.py --once --strict
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--once` | 是 | 跑一轮退出 |
| `--loop` | 否 | 循环跑，前台进程 |
| `--interval` | `300` | 循环间隔秒，最小 30 |
| `--strict` | 否 | float32 精度差异计入 fail |
| `--domains` | 全部 | 逗号分隔：`sector,amv,battle_plan,kline` |
| `--out-dir` | `data/panel_v2/shadow` | 仅用于自测，正常不传 |

端点可用环境变量覆盖（默认值即下表，正常不用设）：`PANEL_SHADOW_OLD_8800`、`PANEL_SHADOW_OLD_8002`、`PANEL_SHADOW_NEW_8810`。

**旧端端口不是统一的**：sector 域的旧端在 **8002**（板块工作台），amv / battle_plan / kline 在 **8800**。8800 上 `/api/sector_tree`、`/api/sector_level`、`/api/series` 全部 404，不要把 sector 指向 8800。这与既有 `tools/diff_sector.py`（`OLD_BASE=8002`）一致。

## 2. 比对清单
| 域 | 名称 | 旧端 | 新端 8810 |
|---|---|---|---|
| sector | tree | `8002 /api/sector_tree` | `/api/p/sector/tree` |
| sector | rank | `8002 /api/sector_level?level=L2&period=1`（date 省略=最新） | `/api/p/sector/rank?level=L2&period=1` |
| sector | series | `8002 /api/series?code=881101.TI&days=60&period=1` | `/api/p/sector/series?code=881101.TI&days=60&period=1` |
| sector | members_full | `8002 /api/members?code=881101.TI&period=1` | `/api/p/sector/members/881101.TI?full=1&period=1&limit=0` |
| sector | minute_rank | `8002 /api/minute_rank?level=L2&n=20` | `/api/p/sector/minute_rank?level=L2&n=20` |
| sector | minute_series | `8002 /api/minute_series?code=881101.TI&level=L2&period=1` | `/api/p/sector/minute_series?code=881101.TI&level=L2&period=1` |
| sector | minute_dates | `8002 /api/minute_dates` | `/api/p/sector/minute_dates` |
| sector | rank_top | `8002 /api/rank?level=L2&n=1` | `/api/p/sector/rank_top?level=L2&n=1` |
| sector | trend | `8002 /api/trend?code=881101.TI&days=120` | `/api/p/sector/trend?code=881101.TI&days=120` |
| sector | board_top | `8002 /api/board_top?sector=881101.TI&top=8` | `/api/p/sector/board_top?sector=881101.TI&top=8` |
| sector | board_flow | `8002 /api/board_flow?period=1&level=L2` | `/api/p/sector/board_flow?period=1&level=L2` |
| sector | contrib | `8002 /api/contrib?code=881101.TI&period=1` | `/api/p/sector/contrib?code=881101.TI&period=1` |
| sector | contrib_p5 | `8002 /api/contrib?code=881101.TI&period=5` | `/api/p/sector/contrib?code=881101.TI&period=5` |
| sector | members_full_index | `8002 /api/members?code=000001.SH&period=1` | `/api/p/sector/members/000001.SH?full=1&period=1&limit=0` |
| amv | live | `8800 /api/amv_live.json` | `/api/p/amv/live` |
| amv | regime | `8800 /api/amv_regime_latest.json` | `/api/p/amv/regime` |
| amv | position_cap | `8800 /api/position_cap_latest.json` | `/api/p/amv/position_cap` |
| amv | hcl | `8002 /api/hcl` | `/api/p/amv/hcl` |
| battle_plan | plan | `8800 /api/battle_plan.json` | `/api/p/battle_plan/plan` |
| battle_plan | alerts | `8800 /api/alert_todo.json` | `/api/p/battle_plan/alerts` |
| battle_plan | positions | `8800 /api/real_positions` | `/api/p/battle_plan/positions` |
| kline | bars | `8800 /api/kline/000001.SH?period=d&limit=120` | `/api/p/kline/bars/000001.SH?limit=120&adjust=qfq` |
| watchlist | items | `8800 /api/watchlist.json` | `/api/p/watchlist/items?limit=1000` |
| kline | bg_600000.SH | `8800 /api/kline_bg/600000.SH?mode=stock` | `/api/p/kline/bg/600000.SH?limit=60` |
| kline | bg_000001.SZ | 同上（代码替换） | 同上 |
| kline | bars_000001.SZ | `8800 /api/kline/000001.SZ?period=d&limit=120` | `/api/p/kline/bars/000001.SZ?limit=120&adjust=qfq` |
| kline | bars_600000.SH | 同上（代码替换） | 同上 |
| kline | bars_300750.SZ | 同上 | 同上 |
| kline | bars_399006.SZ | 同上 | 同上 |
| kline | bars_399001.SZ | 同上（**源精度容差 0.031**） | 同上 |

共 **30 项**。`512480.SH` 因 `factor` 表缺 2026-07-03 份额折算事件（新版 qfq 实际未生效，接口已 `warnings` 明示）暂不入日常 shadow，待 factor 补数后再加。`members_full` 只跑 `period=1`：period=10/20 有 93 处已知差异，全落在北交所 920xxx（旧端 vipdoc 路径缺陷导致 `real_pct=None`），属旧端问题，详见 `docs/sector_contract.md` §6。

比对口径按域区分：

### 2.1 字段缺失的两种处理（2026-10-23 收紧）

**背景**：旧端很多字段是“当场富化”拼上去的（行情、多空、评分轨迹…），新版按新架构拆成了独立端点。
如果一律不搬，新架构就变成了旧面板的复刻品。所以缺失必须分两类处理，**且都要显式声明**：

| 类别 | 声明字段 | 判定 |
|---|---|---|
| **有意不搬** | `Case.allowed_missing_old_keys`（正则列表） | 记 `ignored_old_keys` + `ignored_old_keys_total`，不判 fail |
| **漏了** | 未在 `allowed_missing_old_keys` 里的任何缺失 | 记 `missing_in_new` → **判 fail** |

这条规则的来历：2026-10-23 审核发现 `compare_exact` 把所有缺失都记进 `ignored_old_keys`，
导致「旧端有 200 个字段、新版一个没有」也能判 pass。收紧后实测立刻抓出 4 个用例的真实缺失（已补声明）。

**当前声明的“有意不搬”**：

| 用例 | 不搬什么 | 为什么 |
|---|---|---|
| `sector.members_full` | `sector`、`rows[].code/name/score/mkt_rank/sec_rank/trail/sec_total` | 行身份字段新版用 `stock_code/stock_name`；评分与名次轨迹的口径嵌在旧 `dashboard/server.py` 内部，要搬得先抽成独立模块 |
| `sector.members_full_index` | 同上 | 同上 |
| `battle_plan.positions` | `positions_list` | 需实时行情源（现价/PnL/权重），新版不接实时 |
| `watchlist.items` | `meta/mode/groups/stats/quote_asof/updated_at`、`items[].quote*/quote_source/regime/sector/div/flow/badges/level/pct/quote_date` | 旧端把“条目 + 行情 + 板块涨跌”全拼在一个文档；新版拆成 `/items` `/quotes` `/levels` 三个端点，板块涨跌归 sector 的 `/rank`（不在这里算第二份） |

新增“不搬”必须同时改 `CASES` 与本节表格，两边对不上说明口径漂了。

比对口径按域区分：

- **sector / amv / battle_plan**：递归比对**共同原始键**。数值型：两端均为整数 → 必须完全相等；含 float → 绝对差 ≤ 1e-6。`null` 与键不存在严格区分。旧端独有键（如 `positions` 的 `positions_list`、`market_value_total`）记为 `ignored_old_keys`，不判 fail；新端多出的键记 `extra_new_keys`，也不判 fail。
- **kline**：只比**共同日期**的 `open/high/low/close/volume/amount`，容差 1e-6。旧端 bar 内多出的 `yellow/white/amv/amvMa/amvWhite/chg/amp/turnover` 记为 `uncovered_old_fields`（未覆盖，不判 fail）。旧端顶层 `version/adj/asof/source/vol_unit/prev_close/boards/count/mf/indicators` 记为 `ignored_old_keys`。

**版本键单列**：`asof`、`generated_at`、`updated_at`、`plan_date` 等时间戳键不参与内容比对，只在两端取值不同时记入 `version_drift`（相同值不记）。作用是区分「真差异」和「两次请求之间数据更新了」。

## 3. 判定标准

### 3.1 单例状态

| status | 含义 | 退出码影响 |
|---|---|---|
| `pass` | 共同键全部一致（或在容差内） | 无 |
| `fail` | 存在无法解释的差异 | **1** |
| `skew` | 有差异，但版本键也不同 → 判定为两次请求间的数据更新，需复跑确认 | 无 |
| `live_skew` | 差异**全部**落在声明的盘中实时字段上，且相对差 ≤ 阈值 → 两份盘口差一个跳价 | 无 |
| `error` | 任一端请求失败（非 2xx / 超时 / JSON 解析失败） | **1** |

**`live_skew` 的存在理由**（2026-10-23 10:07 实测）：`battle_plan/positions` 的 `market_value_total`
旧 8800 取 eltdx 实时快照现算（78.3），新版读 `real_positions.json`（78.4）——同一时刻的两份盘口，
差 0.13%。判 fail 会让**盘中每一轮都红**，人就学会无视红，那这套检查就废了。
但也不能一味放宽：只有 `Case.live_fields` 显式列出的字段、且**所有**差异都落在里面、且相对差 ≤ `live_tol_pct`（默认 0.5%），
才降为 `live_skew`；混入任何一个其它字段、或差得超过阈值，照常判 fail。判定逻辑抽成 `_live_skew_only()`，
由 `tools/test_shadow_comparators.py` 的 5 个用例锁住（含「混入其它字段」「相对差过大」「非数值」三条反例）。

当前声明的实时字段只有 `positions.market_value_total`。收盘后、开盘前跑到这一例时应为 `pass`（差值为 0）。

### 3.2 域状态与退出码

域内任一例 `fail` → 域 `fail`；无 fail 但有 `error` → 域 `error`；否则有 `skew` → 域 `skew`；否则有 `live_skew` → 域 `live_skew`。
**任一域 fail 或 error → 进程退出码 1**；全 pass → 0；参数错误 → 2。

`skew` 与 `live_skew` 不计入退出码，都是并发/盘口的正常现象，不是契约不一致。
但连续出现 skew 必须人工复跑确认；`live_skew` 应只在连续竞价时段出现。

### 3.3 已确认的旧端缺陷（默认不判 fail，必须单列可见）

1. **kline `volume` 的 float32 精度**：旧 8800 的成交量按 float32 落盘。`000001.SH` 的 119 个共同日期里有 112 个 `volume` 超出 1e-6 绝对容差，但 `float32(新值) == 旧值` 逐位成立，相对误差在 1e-8 量级。工具默认把这类差异计入 `precision_fp32` 而不判 fail，报告中仍显示 `diff_count`（含 fp32 数），`unexplained` 才是判 fail 的依据。需要严格口径时加 `--strict`。

   另：OHLC 价格允许 **1 分钱**的舍入差（`precision_price`，阈值 0.0101）；超过 1 分钱仍判 fail。
2. **kline 窗口错位**：旧端会补当日未收盘 bar，新版按 v0.1 契约只读已完成日线（`intraday=false`）。结果旧端最后一条是今天（`20261022`），新端最后一条是昨天（`20261021`），120 根窗口整体前移，共同日期 119 个，`only_new` 多出最早一根（`20260423`）。工具在 `bars.window` 里显式记录 `old_tail_intraday` / `new_head_backfill`，不当作内容差异。

这两项是**已确认的旧端/契约边界，不是新端 bug**。切流判据不因此放宽，但也不重复报警。

3. **vipdoc 北交所缺一年 + 旧端 `n_total` 矛盾**（2026-10-22 定案）：`vipdoc/bj/lday/bj920087.day` 695 根，`20250930` 后直接跳 `20261009`（断 374 天）。旧 8002 只读 vipdoc 尾部 80 根 → 920xxx 的 `real_pct` / `amv_avg` / `bull` 恒为 `None`；新版读 `track_pool.db` 有值。另 INDEX 层 `n_total` 旧端报 2230（与它自己返回的 2181 行矛盾），新版取实际成分数。

   工具支持按用例声明 `known_old_defect_patterns`（正则列表）：命中的差异从 `diffs` 移出、记入 `known_old_defects` + `known_old_defect_count`，**不判 fail 但仍出现在报告里**。已用于 `members_full`（`rows[].bull/real_pct/amv_pct/d_contrib/amt_avg`）与 `members_full_index`（`n_total`）。

## 4. 观察频率

- **切流前 1–2 个交易日**：每 30 分钟一轮（`--interval 1800`），覆盖盘中数据更新时段。盘中 9:30–11:30、13:00–15:00 至少各跑到 2 轮。
- **收盘后**（15:30 之后）与**次日开盘前**（9:00 之前）各跑一轮 `--once`，这两轮是切流判定的主依据，因为盘后数据是静态的，能排除并发更新干扰。
- **切流当天**：开盘前一轮，收盘后再一轮；发现 fail 立即停切。
- 不做常驻服务。需要定时就跑前台 `--loop`，关掉窗口即停。

## 5. 失败处理

按 status 分三路，不混在一起处理：

**`error`（请求失败）** —— 先看是哪一端。报告里 `error.old` / `error.new` 分别记录，`old_status` / `new_status` 给出 HTTP 码，`old_body` / `new_body` 保留响应片段。
- 旧端 error：旧面板可能没起或重启中。确认端口后复跑，不改新端。
- 新端 error：8810 进程问题，先查 `/api/health`，再看该插件日志。**这是切流阻塞项**。
- 两端都 error：网络/端口层问题，与本工具无关。

**`fail`（真差异）** —— 报告 `samples` 前 20 条给出 `path / old / new`，按路径定位到插件。
- 差异在数值末尾若干位、且量级像成交量/成交额 → 先查是否 float32 精度，用 `--strict` 复核，并比对 `float32(新值)` 是否等于旧值。
- 差异是键缺失 → 看是否新端漏字段，属契约破坏，**阻塞切流**。
- 差异是列表长度或行数 → 多半是数据版本不同，检查 `version_drift` 是否为空；为空则确认是真不一致。
- 处理原则：差异归因到具体插件后，按「一个指标一个 owner」修对应插件，不改旧端、不改口径去迁就。

**`skew`（版本漂移）** —— 说明比对期间数据被更新了。等 1–2 分钟复跑；连续 3 轮同例 skew，按 `fail` 处理。

**切流门禁**：连续 2 个交易日、所有轮次 `fail=0` 且 `error=0`，且收盘后与开盘前两轮均为全 pass，才可进入切流评审。`skew` 不阻塞，但需在评审记录里列出次数。

## 6. 输出

`data/panel_v2/shadow/YYYYMMDD_HHMMSS.json`，同秒重跑自动加 `_1`、`_2` 后缀，不覆盖历史。

结构：

```jsonc
{
  "schema": "shadow_check/1",
  "generated_at": "2026-10-22 14:29:41",
  "strict": false,
  "tolerance": 1e-06,
  "endpoints": { "old_8800": "...", "old_8002": "...", "new_8810": "..." },
  "elapsed_ms": 1109.3,
  "summary": { "total": 15, "pass": 15, "fail": 0, "skew": 0, "error": 0, "exit_code": 0 },
  "domains": { "sector": { "status": "pass", "cases": ["tree:pass", "..."], "fail": 0, "skew": 0, "error": 0 } },
  "cases": [
    {
      "domain": "kline", "name": "bars",
      "old_url": "...", "new_url": "...",
      "old_status": 200, "new_status": 200,
      "old_ms": 150.0, "new_ms": 7.8, "elapsed_ms": 157.8,
      "status": "pass",
      "diff_count": 112, "unexplained": 0, "precision_fp32": 112,
      "version_drift": [], "ignored_old_keys": ["..."], "extra_new_keys": [],
      "uncovered_old_fields": ["yellow", "..."],
      "samples": [],
      "bars": { "old": 120, "new": 120, "common": 119, "window": { "...": "..." } }
    }
  ]
}
```

`diff_count` = 全部差异数（含 fp32）；`unexplained` = 判 fail 的依据。看报告先看 `summary` 与各例 `status`，再看 `unexplained > 0` 的 `samples`。

## 7. 不做的事

- 不建计划任务、不建常驻服务、不装后台进程。
- 不写项目外文件；不写生产数据目录；不连数据库。
- 不碰 `app/core`、不改旧端口、不提交 git。
- 不把 `skew` 或 `precision_fp32` 静默吞掉 —— 两者都在报告里显式计数。
