# 纸面交易安全评审（4.1.6）

> 2026-10-23｜范围：`app/core/paper/` + `app/plugins/paper/` + `app/core/feed.py` + 账本工具
> 结论：**只服务纸面，无真实下单路径；写账本受控、可备份、可一键回初始态。**

---

## 1. 红线检查（逐条对照）

| # | 红线 | 实现 | 验证 |
|---|---|---|---|
| 1 | 纸面通道绝不 import 真实行情/交易库 | `app/core/paper/` 仅标准库；无 `xtquant` / `xtdata` / `xttrader` / `qmt_trader` | `test_broker.py` 全包静态扫描 + `rg` 复核 0 命中 |
| 2 | 无真实下单调用路径 | 删除 `LiveManualBroker` / `_place_live` / `live_broker()` / `_live_account_view`；`trade_mode()` 恒 `"paper"`，`live_order_enabled()` 恒 `False` | `test_orders.py` 51 项；2 个 live 用例显式 skip 并注明「整类删除」 |
| 3 | 插件不得自己连行情源 | 插件静态扫描 `FORBIDDEN_QUOTE_PATTERNS`（`xtquant`/`xtdata`/`TdxClient`/`from eltdx`） | `cutover_gate.py` 8 项 PASS；`test_compat_misc` 静态扫描 |
| 4 | 核心只有一个 QMT 订阅者 owner | 只有 `app/core/feed.py` 可 import `xtquant`；插件通过 `ctx.feed` 使用 | `test_feed.py` 26 项（假 xtdata）；契约 `data.feed` 白名单 |
| 5 | 不写旧 `engine/state` / `data/panel` | 账本目录独立 `data/panel_v2/paper/`；`state.py` 唯一写入口 | `test_state_dir_isolated`；`test_no_live_channel`；`paper_import_legacy` 只读旧文件 |
| 6 | 账本写入可回溯 | 写前备份 + 原子替换 + `audit.log`；`state.atomic_write_json` 失败抛异常，不静默 | `test_write_is_atomic_and_backs_up`（工具层）、`test_reset_to_initial` |
| 7 | 一键回初始态 | `tools/paper_reset.py --yes`；归档到 `backups/reset_*` | `tools/test_paper_tools.py` 2 项 |
| 8 | 急停仍生效 | `PaperOrderService.place/add_condition` 读 `kill_switch.json`；急停拒单 | 旧测试移植，`test_kill_switch` 等 |
| 9 | 交易时段/新鲜度不造假 | `_resolve_fill` 行情 `fresh=False` 不成交；非交易时段市价单拒绝；条件单 force 仅手动评估可用 | `test_orders.py` `TestSessionGates` |
| 10 | 迁移可回退 | 旧 8800 一行未改；新账本独立；导入工具只读旧账本；`paper_reset` 可重建 | 本文件 §3 |

---

## 2. 变更点（相对旧 8800）

1. **不再双写 `data/panel/intraday.json`**：纸面信号流水不再追加旧生产 panel；K 线买卖点改由 `/api/trades/{code}` 从 `paper_trades.json` 读。
2. **`trade_mode` 固定 paper**：旧版 `engine_state.trade_mode` 可切 live；新版任何 live 路径都不存在，文件里写 `live` 也只按 paper 处理。
3. **状态目录独立**：`panel-v2/data/panel_v2/paper/`；旧 `engine/state` 不被新系统写入。切换时用 `tools/paper_import_legacy.py` 一次性导入。
4. **写账本收口到 core**：插件只做 HTTP 装配；`cutover_gate` 的「插件无写代码」门禁仍然有效。
5. **`signal_buy` 已接信号库**：`app/core/paper/signals/`（从 `engine/signals/library.py` + `macd.py` 逐字 vendoring，只改 import 路径，算法/阈值一行未动）+ `signal_provider.py`，取当日分钟K → `Breakout/Pullback/VwapReclaim/HookTurn/WeakToStrong` 判定；20 秒缓存，任何异常 → False。其余 8 类条件单全部可用。
   - 装配点：`app/core/paper/service.py::build_service()`（`svc.signal_provider = make_signal_provider()`）；`orders.py` 未改。
   - 判定链与旧端 `engine/server.py::_signal_check` 同口径：`quotes.normalize` → `quotes.minute_bars(glb, 241)` → **只保留 `time` 为今天**的 bar（跨日残留丢弃）→ 不足 5 根不判 → 转 `library.Candle` → `triggered`。
   - 可选值仍是 `orders.SIGNAL_NAMES` 那 5 类；`MACD_DIVERGENCE`（只进待机）与 `STOP`（另有专用条件单类型）不注册为买入信号。
   - 纸面边界不变：provider 仅在被调用时懒 import `app.core.quotes`（import 期零连接）；信号库纯 OHLCV，不读不写文件；`app/core/paper/` 的四类行情/交易库关键字全目录静态扫描继续有效。
   - **验证状态：代码路径已存在，未运行验证**（本轮无 shell，不跑 pytest）；新增用例见 `app/core/paper/tests/test_signal_provider.py`。

---

## 3. 切换/回退步骤

### 切换（建议周一开盘前）

1. 确认旧 8800 纸面账本已停止产生新成交（或接受切换时点之后的旧账本不再更新）。
2. 备份旧账本：
   ```powershell
   Copy-Item OH-WorkSpace\engine\state\paper_account.json `
             OH-WorkSpace\engine\state\paper_account.json.bak_20261026
   Copy-Item OH-WorkSpace\engine\state\paper_trades.json `
             OH-WorkSpace\engine\state\paper_trades.json.bak_20261026
   ```
3. 导入新账本（含条件单）：
   ```powershell
   cd OH-WorkSpace\projects\panel-v2
   OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\paper_import_legacy.py --yes --force --include-conditions
   ```
4. 重启 8810，确认：
   ```powershell
   OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\feed_probe.py --seconds 20
   ```
   以及 `http://127.0.0.1:8810/api/paper/feed/status` 为 `CONNECTED`（交易时段）。
5. 旧 8800 的纸面下单入口建议停用/不使用，避免两套账并行；旧 8800 本身保留回退。

### 回退

- 停 8810 → 旧 8800 原样可用（旧账本从始至终未被新版修改）。
- 新版账本在 `data/panel_v2/paper/`，需要时导出/归档；不覆盖旧账本。
- `paper_reset.py --yes` 可把新账本清回 50 万空仓（旧账本归档保留）。

---

## 4. 验证记录（2026-10-23 21:10）

| 项 | 结果 |
|---|---|
| pytest 全套 | **555 passed / 4 skipped**（含 paper core 96、paper 插件 15、feed 26、quote 9、工具 2） |
| `cutover_gate.py` | **8 项 PASS**（含插件无写代码） |
| `compare_legacy.py` | **31/32**（唯一缺口 `/api/concept.json`，与纸面无关） |
| `/api/paper_account` | 与旧 8800 **逐字段相等**（473,596.70 / 0 持仓 / 37 条 equity_curve） |
| `/api/order/list` | 与旧 8800 `orders/conditions/account/mode` **全等** |
| `/api/condition/list` | 21 条条件单 **全等** |
| `/api/ledger?mode=paper` | `account/positions/trades/book` **全等** |
| `/api/trades/{code}` | 3 个样本 **points/cost 全等** |
| 旧前端 | `legacy/shell.html` 渲染纸面账户 473,596.70、今日成交 2 笔、流水 4 条；下单/撤单/条件单 API 实测通过 |

---

## 5. 残余风险（必须知悉）

1. **QMT 第二订阅者尚未活体验证**：当前 QMT 客户端未开（58610 无监听），`/api/paper/feed/status` 为 `ERROR` 属预期。周一 09:15 前跑 `tools/feed_probe.py`；若 QMT 拒绝第二订阅者，按 `docs/paper_trading_architecture.md §8` 回退为「新版唯一订阅者，旧 tick_feed 只在新版停机时启动」。
2. **`signal_buy` 信号库**：已接，但只在**有当日分钟K**时判定（开盘头 5 根内/停牌 → False）；信号源走 `ctx.quotes`（eltdx），不是 QMT 推送。
3. **旧 8800 若继续运行**：它的 `cond-push` 线程会继续撮合旧账本；新旧两套纸面账并行是**两本独立账**，不是同一本。切换后请只使用 8810 的纸面入口。
4. **节假日日历**：`paper` 插件用 eltdx `workdays.is_workday`；取不到时退化为工作日+时段，不猜节假日。
5. **文件锁 fail-open**：`PaperBroker._ledger_lock` 锁超时会放行并审计（沿用旧口径），极端并发下可能丢更新；单进程 8810 下风险低。
