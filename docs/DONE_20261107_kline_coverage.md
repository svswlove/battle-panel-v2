# K 线副图数据覆盖：活跃市值 1000 天 / 主力资金翻页 1400 根（2026-11-07）

## 起因

标注 N20261107-143622-87：K 线弹窗往前拖图时，活跃市值与主力资金到某个节点就停。排查结论是**数据边界**，不是渲染 bug：

| 指标 | 改前覆盖 | 来源 |
|---|---|---|
| 活跃市值 | 500 个交易日（约 2 年） | `amv_board.db::stock_amv`，滚动窗口 `STOCK_AMV_DAYS=500` |
| 主力资金 | 700 个交易日（约 3 年） | 7709 直连 `.cah`，`l2_mf.fetch_cah_live(records=700)` |

成交额、砖型图能一直画，是因为它们在日线库里是全历史。

## 改动

### 1. 活跃市值 500 → 1000 天

- `夏天-activity/工具/amv_store.py`：`STOCK_AMV_DAYS = 1000`
- `夏天-activity/工具/amv_board_init.py`：`keep_from = nT - max(STOCK_TOP_DAYS, STOCK_AMV_DAYS)`
  —— 递推保留必须取两者较大值，否则更早 500 天的 `contrib` 会被丢掉，`stock_amv` 补不进去。
- 重跑：`python amv_board_init.py --incr-pack`（**必须带 `--incr-pack`**：底座 pkl 停在 2026-09-18，库里已有之后的数据，不带包会触发「库内 N 个交易日不在会话日历内」红灯）。

### 2. 主力资金翻页取 1400 根

- `Hubagent/01/_tools/l2_mf.py`：`load_cah` / `build_mf` 增加 `records`；`.cah` 缓存**按深度分文件**（`sh600000.cah` / `sh600000.1400.cah`），否则翻页会命中首页那份 700 条缓存。
- `compat_kline/backend.py`：`MF_RECORDS_DEFAULT=700`、`MF_RECORDS_PAGED=1400`；**翻页分支（带 `before`）传 1400**，首页仍 700（首屏速度不变）。
- 7709 按 700 一批拼接，1400 条实测 0.3 秒、覆盖到 2021-01。

## 验证

- `stock_amv`：500 → **1000 天**（2022-09-19 ~ 2026-11-06，6.15M 行）；`amv_board.db` 2.05GB，WAL 已 checkpoint。
- 写后校验：交易日历连续 ✓ / 新日 AMV 与 daily_total 一致 ✓。
- 接口逐页（000400.SZ）：

| before | 区间 | 活跃市值非空 | 主力资金非空 |
|---|---|---|---|
| 最新 | 2025-11~2026-11 | 240 | 240 |
| 20251111 | 2024-11~2025-11 | 240 | 240 |
| 20241114 | 2023-11~2024-11 | 240（改前 20） | 240 |
| 20221121 | 2021-11~2022-11 | 40（改前 0，1000 天边界内） | 240（改前 0） |
| 20211124 | 2020-11~2021-11 | 0（超 1000 天） | 200（改前 0） |

- 测试：`pytest app` → 18 failed / 1907 passed（18 项均改前既有）；`compat_kline/tests` 89 passed；`a2_sector/test_amv_stock_amv.py` 5 passed。

## 回退

- 活跃市值窗口：`STOCK_AMV_DAYS` 改回 500，重跑 `amv_board_init.py --incr-pack`（库回到 500 天）。
- 主力资金：`MF_RECORDS_PAGED` 改回 700，或直接删 `_tmp/l2_mf_cache/*.1400.cah`（只会让翻页重新取一次）。
- K 线弹窗状态栏的「仅最近 N 根」提示在 `engine/web/kline_lc.js::dataBoundNote`，不需要可整段删除。

## 已知边界

- 活跃市值再往前（2022-09 之前）仍无数据：`stock_amv` 是 v9 递推产物，底座有 8210 天，但再放宽窗口会让库线性变大（每 500 天约 +1.1GB）。
- 主力资金再往前（2021-01 之前）需把 `records` 提到 2100（3 批），本次未做。
