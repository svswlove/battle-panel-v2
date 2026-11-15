# 预测实时源 · AMV 盘中按需刷新（修复记录）

日期：2026-10-27 ｜ 执行：01 ｜ 范围：`engine/executor/amv_live.py` + `intraday` 插件前端

## 1. 结论

盘中卡不再只 GET 静态 `amv_live.json`。交易日连续竞价内，卡片按 60s 上限调用既有
`POST /api/amv_refresh`（core `ops_run` → `amv_live.run_once(publish=True)`）后重取文件；
预测浮层因此拿到当天快照，不再是「历史预测」。生产实测：`run_once` 由 86s 超时降到 8.56s。

## 2. 根因（两层）

| 层 | 缺陷 | 证据 |
|---|---|---|
| 计算 | `run_once()` 仍按 `stock_state` 全库 6164 码 `fetch_ticks` —— 上批只修了 `stocks_snapshot` / `sector_snapshot` 的取数范围 | 走 `snapshot_tick_scope` 前，同一逻辑 86.14s |
| 前端 | 卡片只 `GET /api/amv_live.json`，没有任何触发刷新的调用，文件停在 2026-10-23 12:24 | `grep api.post` 在 intraday 前端零命中 |

## 3. 改动

### 3.1 `engine/executor/amv_live.py`（仅 `run_once` 取数范围）

```python
qcodes, live_list, quote_src = snapshot_tick_scope(state["codes"])
qcodes = list(dict.fromkeys(qcodes + _holdings_codes()))   # 叠加持仓码（含 ETF/标准券）
```

- 取数范围 = QMT 现役清单 ∪ 持仓码；清单不可得 → helper 内部回退 state 码并标 `state_fallback`，
  此处额外写一行日志（不静默降级）。
- **未动**：v9 公式（`build_payload` / `step_live` / `amv_store.step_one_day`）、覆盖率闸门、
  tick 归属日时间闸、`_publish_stale` 语义、写文件归属（`run_once` 仍是 `amv_live.json` 唯一生产者）、
  core 的 20s join。

### 3.2 `intraday` 前端（0.1.2 → 0.1.3）

新增 `app/plugins/intraday/frontend/amvrefresh.js`（零 IO、零 import，纯判定 + 编排）：

- `planRefresh()` 判定顺序：卸载 → `S.loading` → 页面不可见 → 非交易日/非连续竞价 → 距上次 POST <60s →
  其余才刷。`why` 取值 `unmounted/loading/hidden/off-session/throttled/tick/stale-file`。
- 连续竞价 = 工作日 09:30–11:30、13:00–15:00（午休、收盘后、周末零请求）；节假日表在 Python，
  前端不复制 —— 工作日节假日会各发一次，服务端返 `ok:false` → 卡片显式标「刷新失败」。
- `createRefresher()`：POST 前先占用节流时间戳（失败也算一次，防每 20s 重试打爆）；
  成功才 GET `amv_live.json` 并回调，失败只出文案。
- `frontend.js`：`setInterval(20s)` 驱动 `tick()`（`unref` 防 jsdom 挂进程），
  mount 末尾立即 `tick()` 一次（首轮文件非今日 → `stale-file` 触发，不等下一拍）；
  `unmount` 里 `clearInterval`。刷新只改 `S.amv`，不碰 `S.loading`、不调用 `S.load`，
  故不会形成「刷新 → 整卡取数 → 再刷新」递归。
- 失败文案落 `secHold` 顶部一行 `.it-src`：`刷新失败 HH:MM（原因）`；成功清空。**保留原值**，
  不把旧值写成实时。版本号与 `evo.js?v=` 同步到 0.1.3。

## 4. 测试

| 项 | 命令 | 结果 |
|---|---|---|
| A 单元 | `python -m pytest engine/tests/test_amv_live.py -q` | 21 passed（含新增 `TestRunOnceScope` 2 条） |
| C 前端频次/时段/失败 | `python -m pytest app/plugins/intraday/tests/test_amv_refresh.py -q` | 8 passed |
| 插件回归 | `python -m pytest app/plugins/intraday/tests -q` | 108 passed / 12 failed（12 条为既有旧页行号对齐红灯，本批之前即红，未修） |
| ops 端点回归 | `python -m pytest app/plugins/ops/tests/test_ops_actions.py app/plugins/ops/tests/test_ops_m1_endpoints.py -q` | 57 passed / 1 failed（既有 `test_panic_store_mode_shape`，本轮之前即红） |
| 整卡挂载 | `node app/plugins/intraday/tests/audit_dom.mjs` | 绿（order / unmount 后不再响应） |

新增断言要点：取数范围 = 现役清单 + 持仓码且不含退市码、`build_payload` 仍接 state codes、
`publish=False` 不写文件、5 拍 20s 只 2 次 POST、6 个非交易场景零 POST、失败 `刷新失败` 前缀、
失败不替换 AMV、`loading/hidden/unmounted` 零 POST。

## 5. 生产只读验收（01 执行）

```
POST http://127.0.0.1:8810/api/amv_refresh   → 7.03s
{"ok":true,"asof":"2026-10-27 12:19:35","coverage":{"pct":0.9971,"denom_kind":"tradable","ok":true},"stale":false,"indices":9}
```

- `data/panel/amv_live.json`：`asof=2026-10-27 12:19:35`、`trade_date=2026-10-27`、
  `tick_date=2026-10-27`、`forecast.ok=true`、`stage=中午模型`、`anchor_label=11:30`（原为 10-23 12:24）。
- `GET /api/amv_live.json` 读到同一份（12:19:35）。
- `run_once(publish=False)` 单跑 8.56s、覆盖率 99.71%、`forecast.ok=true`，未写文件（单测亦锁死）。

## 6. 生效与遗留

- **需重启 8810**：`/_plugins` 仍报 `intraday 0.1.2`（manifest 在进程 import 期读）；但
  `GET /plugins/intraday/frontend.js` 已含 `amvrefresh`（文件按请求读盘），前端逻辑已可运行。
  本轮未重启任何常驻服务。
- 节假日（工作日）会各触发一次 POST 并显式显示「刷新失败」；若要去掉，需在 core 暴露交易日接口，
  属新接口，未做。
- 已存在的 12 条旧页行号对齐红灯与 `test_panic_store_mode_shape` 未修（本批半径外）。
