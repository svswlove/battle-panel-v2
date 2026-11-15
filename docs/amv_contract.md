# amv 插件契约（v0.1）

**插件**：`app/plugins/amv/`（id=`amv`，v0.1.0）
**挂载前缀**：`/api/p/amv`
**数据源**：只读 `data/panel/` JSON；`/daily` `/history` 只读 `data/amv_board.db::daily_total`。不写文件。
- `amv_live.json` → 盘中 AMV / 覆盖 / 预测 / 涨跌家数 / 指数 / 板块
- `amv_regime_latest.json` → `state / ma10 / pct / amv / last_switch`
- `position_cap_latest.json` → `final_pct / cap_amount / degraded`

| 端点 | 说明 |
|---|---|
| `GET /summary` | 汇总卡片：live + regime + position_cap + 日期一致性 |
| `GET /live` | 原始 `amv_live.json` |
| `GET /regime` | 原始 `amv_regime_latest.json` |
| `GET /position_cap` | 原始 `position_cap_latest.json` |
| `GET /indices` | 9 个指数 AMV 快照 |
| `GET /sectors` | 板块领涨/领跌 |
| `GET /daily?days=250` | `daily_total` 的 `date/total/total_yi/ma10`；MA10 头部分支按实际长度 |
| `GET /history?code=ALL&days=60` | v0.1 仅支持 `code=ALL`，返回同上；其他 code 400 |

**日期口径**：`live.trade_date` 是盘中交易日；`live.state_date` 是 regime 对应的上一收盘日。`/summary.consistency.stale.live_vs_regime` 用 `state_date` 与 `regime.date` 比较，不用 `trade_date`，避免把正常的“盘中 live / 昨收 regime”误报为陈旧。

**v0.1 不覆盖**：板块 AMV 趋势图、个股贡献、盘中逐笔；`/history` 仅支持 `code=ALL`。写入/下单/交易链接口一律不接，旧 8800 对应卡片保留。
