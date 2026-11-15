# 纸面交易迁移交接（2026-10-23 夜）

> 前一份交接：`docs/HANDOFF_20261023.md`（旧前端复用 + 兼容层）
> 本份只讲 4.1 纸面交易真迁移。**读完这页 + `docs/paper_trading_security_review.md` 就能接上。**

---

## 1. 一句话状态

**纸面交易已从旧 8800 真迁移到 8810**：core 自持 QMT 订阅、core 纸面撮合/账本、paper 插件接管旧前端全部交易路径；旧账本已导入新目录，读端点与旧 8800 **逐字段全等（9/9）**。**真实下单通道不存在**。

---

## 2. 完成清单

| 子任务 | 结果 |
|---|---|
| 4.1.1 定架构 | `docs/paper_trading_architecture.md`：core 内常驻订阅，否决复用旧 tick_feed 落盘 |
| 4.1.2 vendoring | `app/core/paper/`：broker 53KB / orders 57KB / state 14KB / service 10KB；删实盘类、删 intraday 双写；96 passed / 3 skipped |
| 4.1.3a feed | `app/core/feed.py` + `quotes.order_quote` + `ctx.feed`；26 passed / 1 skipped |
| 4.1.3b 插件 | `app/plugins/paper/`（root 11 路由）+ `ctx.paper`；删 compat_misc 5 个骨架路由；15 passed |
| 4.1.4 旧前端 | `legacy/shell.html` 实测账户 473,596.70 / 今日成交 / 流水 4 条；下单、撤单、条件单 API 实测 |
| 4.1.5 对账 | `tools/paper_parity.py`：`paper_account/order_list/condition_list/ledger/trades×5` **9/9 全等** |
| 4.1.6 安全 | `docs/paper_trading_security_review.md`；`tools/paper_reset.py`、`paper_import_legacy.py`、`feed_probe.py`；工具测试 2 项 |

## 3. 当前数字

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
# 555 passed / 4 skipped
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\compare_legacy.py
# 31/32（唯一缺口 /api/concept.json）
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\cutover_gate.py
# 8 项 PASS
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\paper_parity.py
# 9/9 全等（需 8800+8810 都在跑）
```

---

## 4. 数据与开关

| 项 | 值 |
|---|---|
| 新账本目录 | `panel-v2/data/panel_v2/paper/`（`PANEL_V2_PAPER_DIR` 可覆盖） |
| 旧账本 | `engine/state/paper_account.json` / `paper_trades.json`（**一字未改**） |
| 导入工具 | `tools/paper_import_legacy.py --yes --force --include-conditions`（已执行；21 条旧条件单已导入） |
| 一键回初始 | `tools/paper_reset.py --yes`（归档旧账本到 `backups/reset_*`） |
| feed 开关 | `PANEL_V2_FEED=1`，`run.ps1` 与 `ensure_8810.ps1` 都已设 |
| feed 诊断 | `GET /api/paper/feed/status` |

---

## 5. 周一开盘前唯一必须补的实测

现在是 2026-10-23 21:15，QMT 客户端未开（58610 无监听），feed 为 `ERROR` 属预期。

**周一 09:15 前**：

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\feed_probe.py --seconds 20
```

- 有 tick 返回 + 旧 tick_feed / 旧 8800 同时活着 → 多订阅者成立，架构无需改。
- 0 tick / QMT 拒绝第二订阅者 → 按 `docs/paper_trading_architecture.md §8` 的回退方案：新版作为唯一订阅者，旧 tick_feed 只在新版停机时启动。

---

## 6. 已知缺口（不阻塞纸面使用）

1. **`signal_buy` 条件单未接**：需要 `signals/library.py` 60KB；其余 8 类条件单已可用。
2. **`/api/concept.json`** 仍是唯一未对齐旧端点（与纸面无关）。
3. **旧 8800 若继续跑**：两套纸面账互不影响，但下单入口只能选一边；切换后建议只用 8810。
4. **旧前端两个 404**：`/api/kline-indicator-settings`、`/api/amv_research.json`（兼容层未做，与纸面无关；`themes.css` 已修）。
5. **`paper_account` 无成交时 404**：与旧端一致；只要导入旧账本或发生一次 FILLED 就会有文件。

---

## 7. 关键文件地图

| 用途 | 路径 |
|---|---|
| feed / 下单行情 | `app/core/feed.py`、`app/core/quotes.py::order_quote/order_metrics` |
| 纸面引擎 | `app/core/paper/{broker,orders,state,service}.py` |
| HTTP 插件 | `app/plugins/paper/{backend,views}.py` |
| 账本 | `data/panel_v2/paper/`（gitignored） |
| 工具 | `tools/{paper_reset,paper_import_legacy,paper_parity,feed_probe}.py` |
| 文档 | `docs/paper_trading_{architecture,security_review}.md`、`docs/CONTRACT.md` §4.3/4.4 |

---

## 8. 验证口径（别重复踩）

- `paper_parity.py` 只有在**新旧两端都跑、且新账本已导入旧账本**时才 9/9；新账本发生新成交后，`paper_account`/`ledger` 的现金/成交会自然偏离旧端，这是**预期**，不是 bug。
- `feed_probe.py` 只能在**交易时段 + QMT 已登录**时跑；夜间一定 0 tick。
- `paper_reset.py` 不动 `kill_switch.json`（重置账本不等于解除急停）。
- 新账本任何写入都走 `app/core/paper/state.py`；插件里出现写代码会被 `cutover_gate` 挡。
