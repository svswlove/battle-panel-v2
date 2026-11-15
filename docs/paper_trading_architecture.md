# 纸面交易真迁移 · 架构决策（4.1.1）

> 定稿：2026-10-23 20:40｜决策人：主会话｜状态：**已定，后续按此执行**
> 一句话：**行情订阅放进 8810 进程的 core（`app/core/feed.py`），纸面撮合做成 core 服务（`app/core/paper/`），HTTP 由 `paper` 插件薄壳挂根路径；账本用独立目录，绝不与旧 8800 双写。**

---

## 1. 为什么选「core 内常驻订阅」

### 1.1 三条代码证据

1. **旧系统本来就是多订阅者，不是单例独占**
   - `engine/executor/tick_feed.py:797`：`subscribe_whole_quote(codes, self.on_quote)`，独立进程。
   - `engine/executor/auto_buy.py:5`：同接口，另一个独立进程。
   - `engine/server.py:11135`：`tick_feed / auto_buy 都是 subscribe_whole_quote 回调驱动（生产已验证多订阅者）`，server 进程内还有 `cond-push` 线程做第三份订阅。
   - 结论：QMT 58610 允许多个本地进程各自 `xtdata.connect()` + `subscribe_whole_quote`。新版不需要抢旧端的订阅，也不需要旧端常驻。

2. **新版运行环境已有 xtquant**
   - `envs/qmt312/Scripts/python.exe -c "import xtquant"` 通过（Python 3.12.12）。
   - 8810 当前就是该解释器启动（`run.ps1` 默认找 `envs/qmt312`）。
   - 结论：不需要新装依赖，也没有跨解释器问题。

3. **撮合是事件驱动的，轮询文件会改变语义**
   - 旧链：每个 tick → `svc.evaluate(quotes)` + `svc.match_pending(quotes)`（`server.py:11135` 段）。
   - `paper_broker` 的补成交走 `reconcile_fill`，依赖 `quote_time` 和盘口；用旧 `watchlist_quotes.json`（60 秒落盘、只覆盖自选/持仓）会漏掉挂单标的，且延迟不可控。
   - 结论：必须在新版进程内拿到**实时 tick 回调**，不能复用旧端落盘文件。

### 1.2 被否决的方案

| 方案 | 为什么不用 |
|---|---|
| 复用旧端 `tick_feed` 落盘（watchlist_quotes.json / intraday.json） | ① 旧端必须常驻，不是真迁移；② 60s 落盘 + 只覆盖自选池，挂单标的可能不在；③ 撮合延迟从 tick 级变成分钟级，和旧端不可比。 |
| 新版单独起一个 `feed` 进程，插件读 IPC/文件 | 多一个常驻进程、多一套守护与 IPC；纸面交易只需要 8810 活着时撮合，进程内线程足够。将来若要把 feed 给其他消费端，再把 `app/core/feed.py` 抽成独立进程，接口不变。 |
| 每个纸面插件自己连 QMT | 违反「一个行情源一个 owner」；N 个插件 = N 个连接 + N 套重连；契约明确禁止插件出现 `xtquant`。 |

---

## 2. 组件与职责

```
app/core/feed.py                 ← 唯一允许 import xtquant 的模块
  QuoteFeed
    ├─ connect() / reconnect()   QMT 58610，启动 3 次×30s 退避，盘中失流 60s 自愈
    ├─ subscribe(codes)          增量订阅（持仓 + 活跃条件单 + 未成交挂单）
    ├─ quote(code) -> dict|None  最近盘口（last/pre_close/bids/asks/quote_time/ts/fresh）
    ├─ snapshot(codes) -> dict   批量读
    ├─ add_listener(fn)          撮合回调（队列消费线程，不在 QMT 回调里做重活）
    └─ status() -> dict          connected / subscribed / last_tick_at / reconnect_count

app/core/paper/                  ← 纸面交易 core 服务（业务核心，不在插件里）
  state.py                       路径/文件锁/原子写/备份/审计（唯一写账本的地方）
  broker.py                      vendored PaperBroker（只保留下单/撮合/记账，去掉 intraday 双写）
  orders.py                      vendored ManualOrderService 的 paper-only 版（删掉 LiveManualBroker 与 qmt_trader）
  service.py                     单例装配：PaperBroker + PaperOrderService + feed 撮合循环

app/plugins/paper/               ← HTTP 薄壳，root mount 到 /api
  backend.py                     参数校验 + 调 ctx.paper；不含业务规则
  plugin.json                    data.paper=true, mount=root
```

### 2.1 为什么放 core 不放插件

- 账本写入需要跨请求串行、文件锁、原子替换、备份与审计——这是**基础设施**，不是插件业务。
- `cutover_gate.py` 的「插件无写代码」门禁必须保持有效；把写账本代码放进插件就得给门禁开例外，风险大。
- 行情订阅只能有一个 owner（契约 §4.1）。core 持有 feed，插件通过 `ctx.feed` / `ctx.paper` 使用。

### 2.2 契约扩展（v2.0，待落文档）

- `manifest.data.paper: true` 才能拿到 `ctx.paper`；未声明 → `PermissionError`。
- `ctx.feed` 只读行情，不存在写方法；插件源码仍禁止出现 `xtquant` / `xtdata`。
- 新增 `paths.PAPER_STATE_DIR`（默认 `panel-v2/data/panel_v2/paper/`），不登记进 `WRITABLE_PANEL`（旧 `data/panel` 一个字节都不写）。
- CONTRACT §8 的「新版禁止接 QMT/下单」改为：**仅 core feed 可接 QMT 只读行情；纸面下单只允许走 core paper 服务；真实交易通道永久禁止**。

---

## 3. 状态与账本

### 3.1 独立目录，绝不双写

```
panel-v2/data/panel_v2/paper/
  paper_account.json          # 纸面账户（初始 50 万）
  paper_trades.json           # 委托/成交流水
  conditional_orders.json     # 条件单
  manual_orders_state.json    # 冻结资金/冻结持仓
  manual_lock.json            # 手工优先锁
  engine_state.json           # trade_mode 固定 paper（只读，不可切 live）
  kill_switch.json            # 纸面急停
  backups/                    # 每次写前快照（保留最近 N 份）
  audit.log                   # 写审计（谁/何时/文件/字节）
```

- **不复用 `engine/state/paper_account.json`**：旧 8800 的 `cond-push` 线程还在跑，双写同一文件必炸（两个撮合器各写各的账）。
- **迁移工具**：`tools/paper_import_legacy.py` 一次性把旧 `engine/state/{paper_account,paper_trades}.json` 复制进新目录（先备份、再校验、打印摘要）。是否导入由人显式执行，不自动。
- **回退**：旧账本从始至终未被新版修改；停 8810 即回到旧账本。新版产生的纸面流水只在新目录，需要时用工具导出。

### 3.2 写安全

- 所有账本写入走 `app/core/paper/state.py`：`exclusive_lock`（msvcrt）+ tmp 原子替换 + `os.replace` 重试 + 写前快照 + `audit.log`。
- 每次启动做一次 `paper_account.json` / `paper_trades.json` 快照到 `backups/startup_<ts>/`。
- 提供 `tools/paper_reset.py --to-initial`：归档当前账本 → 重置为 50 万空仓。这是 4.1.6 的「一键回到纸面初始态」。

---

## 4. feed 线程模型（关键细节）

```
QMT 回调线程 (xtquant)
   └─ on_quote(datas) 只做：解析 → 写 _QUOTES（加锁）→ put 到 queue（不阻塞）
         │
         ▼
   单消费者线程 (paper-match)
     每轮：drain queue，按 code 保留最新一条 → quotes
           svc.evaluate(quotes, now_hm)
           svc.match_pending(quotes)
           （异常吞掉并记 audit/progress，回调线程绝不抛）
```

- **为什么不在回调里直接撮合**：旧 server 在回调里直接 `evaluate` + `match_pending`，有文件锁和磁盘写；tick 密集时会把 QMT 回调线程拖慢甚至断流。新架构用队列解耦，语义仍是 tick 驱动，但回调只做内存操作。
- **合并策略**：同一轮里同一 code 只取最后一条，避免一个 tick 风暴把队列打爆。
- **订阅清单**：`PaperOrderService.wanted_codes()` = 持仓 + ACTIVE 条件单 + REPORTED/PARTIAL 挂单；feed 每 15 秒重读一次，新增补订阅（xtquant 支持增量 `subscribe_whole_quote(new, cb)`）。
- **断流自愈**：盘中 60 秒无 tick → `unsubscribe_quote` → `disconnect` → `connect` → 全量重订阅；重连节流 60 秒；非交易时段不判流（同 tick_feed 口径）。
- **启动失败**：3 次 × 30 秒退避；耗尽不退出进程（8810 仍可看盘），feed status=`ERROR`；之后 supervisor 每 60 秒继续重连（QMT 可能晚于 8810 启动）。paper 下单仍可走 eltdx 快照（与旧 `_order_quote` 同口径），只是没有 QMT 盘口覆盖。

---

## 5. 交易口径（必须与旧端逐字一致）

直接 vendoring 旧 `PaperBroker` / `ManualOrderService` 的 paper 分支，不重写规则：

- 费用：佣金万 1（最低 0）、印花税卖出万 5、过户费双向万 0.1。
- 限价单：只有对手盘真的打到才成交（买→卖一 ≤ 限价；卖→买一 ≥ 限价）；非交易时段/行情不新鲜一律只挂单。
- 市价单：非交易时段拒绝；成交按最新价。
- 部分成交：对手档量不足时按档量部分成交。
- 买入 100 股整数倍；卖出允许零股但不超过可卖；T+1 近似。
- 条件单 9 种类型、OCO、过期、急停、手工优先锁全部保留。
- **删除**：`LiveManualBroker`、`qmt_trader`、`trade_mode=live` 分支、`intraday.json` 双写、`push_queue` 推送（改成 audit/progress 文件）。
- **保留红线**：纸面通道绝不 import `xtquant/xttrader`，绝不出现任何真实下单调用路径。

---

## 6. API 约定（旧前端零改）

`paper` 插件 root mount，接管以下旧路径：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/order/place` | 下手工单（纸面） |
| POST | `/api/order/cancel` | 撤单 |
| GET | `/api/order/list` | 当日委托 + 条件单 + 账户摘要 + metrics |
| POST | `/api/condition/add` | 加条件单 |
| POST | `/api/condition/cancel` | 撤条件单 |
| GET | `/api/condition/list` | 条件单列表 |
| POST | `/api/condition/evaluate` | 手动评估一轮 |
| GET | `/api/paper_account` | 纸面账户 + 持仓估值 |
| GET | `/api/ledger` | 账本（paper 模式） |
| GET | `/api/trades/{ts_code}` | K 线买卖点（纸面流水） |
| GET | `/api/paper/feed/status` | feed 健康（新增，排查用） |

**冲突处理**：`compat_misc` 目前注册了 `/order/list`、`/condition/list`、`/paper_account`、`/ledger`、`/trades/{ts_code}` 五个**骨架**路由（`ok:false`）。`paper` 插件挂 root 会启动即冲突——必须从 `compat_misc` 删除这五个路由（保留 `ops.json` 等其余只读端点），并同步它的测试与 README。

---

## 7. 验证计划

| 阶段 | 验证 | 通过标准 |
|---|---|---|
| 4.1.1（本文件） | xtquant 可导入；多订阅者有代码证据 | 已完成；QMT 客户端当前未开（58610 无监听），**周一 09:15 前做一次实连双订阅验证** |
| 4.1.2 | vendored broker/orders 单测 | 旧 `test_paper_broker.py` + `test_manual_orders.py` 全量移植后全绿 |
| 4.1.3 | feed 单测（stub xtquant）+ 插件契约 | 无 QMT 也能测：注入假 tick，验证队列合并/重连/订阅增量 |
| 4.1.4 | 旧前端实点 | `/legacy/shell.html` 下单、撤单、条件单、账户、账本五处可用 |
| 4.1.5 | 历史回放 | 用旧 `paper_trades.json` 某日记录，逐笔对比成交价/数量；差异必须能解释 |
| 4.1.6 | 安全评审 | 无 xtquant 交易路径；`paper_reset` 可一键回初始；kill switch 生效 |

---

## 8. 周一开盘前必须补的一条实测

现在是 2026-10-23 20:40，QMT 客户端未运行（58610 无监听），无法做**活体**双订阅验证。周一 09:15 前执行：

```powershell
# 终端 A：旧 tick_feed 正常启动（或旧 server 的 cond-push）
# 终端 B：
OH-WorkSpace\envs\qmt312\Scripts\python.exe -c "
import sys, time
sys.path.insert(0, r'OH-WorkSpace\envs\qmt312')
from xtquant import xtdata
xtdata.connect(ip='127.0.0.1', port=58610)
n = {'c':0}
def cb(d):
    n['c'] += len(d or {})
    print('tick', n['c'], list((d or {}).keys())[:3])
seq = xtdata.subscribe_whole_quote(['000001.SH','600519.SH'], cb)
print('seq', seq)
time.sleep(20)
print('ticks', n['c'])
"
```

通过标准：旧 tick_feed 的 `intraday.json` 仍在更新，同时上面脚本收到 tick（`ticks > 0`）。若失败（QMT 拒绝第二订阅者），退回方案：新版 feed 作为**唯一订阅者**，旧 tick_feed 仅在新版停机时启动；切换窗口内保证只有一个订阅者。这一条在周一验证后回写本文件。
