# 标注 N20261027-101349-33 · 盘中研究区用昨日数据（修复记录）

日期：2026-10-27 ｜ 执行：01（架构 + 实现）｜ 验收/重启：主人
范围：`engine/executor/amv_live.py`（快照取数范围）+ `intraday` 插件研究卡（前端刷新 + 降级提示）

## 1. 根因（证据来自主人实测 + 本轮复算）

`stocks_snapshot()` / `sector_snapshot()` 在 `ticks=None` 时，直接拿 `stock_state` 全库码
（6164 只，含退市/长期停牌旧票）去 `fetch_ticks`：

| 环节 | 旧行为（state 码 6164） | 新行为（现役清单 5580） |
|---|---|---|
| fetch_live_list | 未调用 | 1.25–2.94s（5580 只） |
| fetch_ticks | 86.14s，有效 1603 | 1.69–2.88s，有效 5570 |
| 完整 stocks_snapshot | 85.42s | 冷启 8.9s / 暖 2.77–3.09s |

core 门面 `app/core/amvlive.py` 的 join 上限 6s 必超时 → `live_mode=false` /
`asof=2026-10-26` / `live_err=timeout` → 研究卡回退收盘口径，盘中看到昨日数据。

## 2. 生产修复（最小 2 处调用点 + 1 个 helper）

`engine/executor/amv_live.py`

- 新增 `snapshot_tick_scope(state_codes)`：先 `fetch_live_list()`，非空 → 取数范围 = 现役清单；
  空/异常 → 回退原行为（state 码）并返回 `src="state_fallback"`。
- `stocks_snapshot` / `sector_snapshot`：`ticks=None` 分支改用该 helper；已算出的 `live` 透传给
  `fetch_new_listings`，不再重复取清单（调用方给了 ticks 时（tick_feed / core 门面）行为不变，
  仍在原位置取一次清单）。
- 返回值新增 `quote_src`（`live_list` / `state_fallback` / `given`），降级可如实标注。
- **口径零改动**：`build_rows` 第一参数仍是 `state["codes"]`（退市票无 tick → tr=NaN 冻结，
  历史票 missing 的冻结语义保留）；新上市票的 tick 现在也在取数范围内，由 `build_rows`
  的 `sh_fallback` 分支照旧补成首日票；v9 公式（`step_live` → `amv_store.step_one_day`）、
  基线、写入路径全部未动。

## 3. 前端刷新（`app/plugins/intraday/frontend/research.js`）

- 看门狗条件从 `if (st.rs) return;` 改为 `watchdogDue(st, now)`：
  无数据 → 立刻重试（原语义）；TTL 内不打扰；TTL 到期 + 盘中窗口（工作日 08:45–15:35）
  + `st.live` → 重试。盘外/周末 TTL 到期也不刷历史帧（保持原 TTL 语义）。
  盘中窗口内拿到收盘帧本身就是降级结果，必须继续试实时，否则永远回不到 live。
- 降级提示：口径行新增 `⚠ 实时不可用，当前为收盘（原因）`，来源两个——
  服务端自报 `rs.live_err`，或本轮前端实时请求失败（新增 `st.liveErr`，随 `live=1` 请求的
  成败更新，回落收盘时保留）。真实时帧不出现该提示（不误报）。
- 签名（`signature`）加入 `live_err`，保证提示变化时重绘。
- 8s 超时 / 15s 悬挂释放 / pending 排队 / 实时→收盘回落一次：全部未动。

## 4. 测试

| 命令 | 结果 |
|---|---|
| `engine: python -m pytest tests/test_amv_live.py -q` | 19 passed（含新增 3 例） |
| `panel-v2: pytest app/plugins/intraday/tests/test_research.py -q`（除 legacy 对齐例） | 16 passed（含新增 2 例） |
| `panel-v2: pytest app/plugins/intraday/tests -q`（除 legacy 对齐例） | 89 passed / 11 failed（见 §7，全部为 legacy 对齐例，改动前即红） |

新增断言：

- `TestTickScope::test_live_list_is_quote_scope`：取数范围 = 现役清单，退市旧票不入、新上市票入。
- `TestTickScope::test_fallback_to_state_codes`：清单不可得 → state 码 + `state_fallback`。
- `TestTickScope::test_snapshot_uses_live_scope_and_keeps_state_inputs`：
  `stocks_snapshot` / `sector_snapshot` 都用 live 码取数，`build_rows` 第一参数仍是 state codes，
  `date` = tick 归属日，`quote_src=live_list`。
- `test_watchdog_retries_stale_data_in_session`：截获 setInterval 回调 + 假时钟，
  已有旧 `rs` 时 TTL 到期必须重试、重试带 `live=1`；盘后/周末不刷；`watchdogDue` 九种情形逐个对。
- `test_live_err_visible_when_fallback_to_close`：`rs.live_err` 与 `st.liveErr` 都渲染出
  「实时不可用，当前为收盘」，真实时帧不误报。

## 5. 实测（本机，2026-10-27 11:30–11:45，直接调旧仓函数，未重启常驻）
```
fetch_live_list            2.47s  5580
stocks_snapshot #0(冷)      8.65s  date=2026-10-27 quote_src=live_list stocks=6164 keys
stocks_snapshot #1(暖)      2.77s
sector_snapshot #0(冷)      9.71s  date=2026-10-27 boards=90
sector_snapshot #1(暖)      4.25s
stocks 有贡献值只数           5560（>5000）
all_amv 13,867,290,924,070 元 / base_all 13,866,960,347,361 元
```

分段（暖）：`fetch_live_list 1.25s` + `fetch_ticks 1.69s` + `resolve_fresh_shares 0.00s`
（冷启首轮 `resolve_fresh_shares 3.99s`：eltdx `daily_share_capital` 首次调用的客户端预热成本）。

**未验证**：core 门面 `app/core/amvlive.py` 6s join 下的端到端结果（需重启 8810 后由主人验收）；
冷启首轮 8.9s 仍会撞 6s 上限，随后 25s 失败缓存到期、暖路径 2.8s 可成功。

## 6. 部署注意

- 后端改动要重启 8810 才生效（`ensure_8810.ps1` 由主人执行；本轮未启停任何常驻）。
- `intraday` 插件 `plugin.json` 版本 0.1.0 → 0.1.1；该插件的相对 import 未带 `?v=`
  （与 kline 的版本化 import 不同），已打开的页面不会自动换新模块，需手动刷新一次。

## 7. 遗留 / 不在本轮范围

- `test_render_is_byte_identical_to_legacy`、`test_audit*`、`test_evo*`、`test_quotes*` 的 legacy
  逐字节对齐例：`engine/web/index.html` 已被改动（git 显示 +1016/−341 行），对齐脚本取的
  行号区间（4306-4531 等）切在表达式中间 → 与本次改动无关，改前即红（HEAD 版本同样解析失败）。
  需单独开工单重切行号。
- 冷启首轮 8.9s 未压到 6s 以内（eltdx 客户端预热成本，改它要动 `collectors/eltdx_adapter.py`，
  超出本轮白名单）。
