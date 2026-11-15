# battle_plan 双跑比对报告（旧 8800 vs 新 8810）

**结论：只读影子可用**。作战表与告警/待办原始文档精确相等；实盘持仓原始文件精确相等，旧端点的实时估值附加字段未迁移。

- 生成时间：2026-10-22
- 旧：`http://127.0.0.1:8800`
- 新：`http://127.0.0.1:8810`

| 文档 | 旧端点 | 新端点 | 结果 |
|---|---|---|---|
| `battle_plan.json` | `/api/battle_plan.json` | `/api/p/battle_plan/plan` | **equal=True**（19 keys） |
| `alert_todo.json` | `/api/alert_todo.json` | `/api/p/battle_plan/alerts` | **equal=True**（7 keys） |
| `real_positions.json` | `/api/real_positions` | `/api/p/battle_plan/positions` | 原始文件 7 keys 相等；旧端点额外有 `positions_list` / `market_value_total`（实时行情估值），新版未迁移 |

## 写接口

- `POST /api/p/battle_plan/plan` → **405**（新版只有 GET）。
- 旧 8800 的 `/api/real_positions/manual`、签收、改单等写接口全部保留，新版不接。

## 已知内容 stale（不是插件 bug）

`battle_plan.json` 的 `direction_forecast.judgment` 仍写「AMV 空档第 40 日 / MA10 139,824.51 / ratio 0.9867」，与 2026-10-22 修复后的 regime（空档第 14 日 / MA10 143,013.53 / ratio 0.9647）不一致。这是上游文档内容问题，插件只读展示，不做改写；需要时由主会话单独修 `battle_plan.json` 的 AMV 段。

## 切流建议

- 作战表/告警/待办可进入 shadow。
- 实盘持仓先展示原始字段；等独立估值插件就绪后，再补 `positions_list` 视图。
- 交易链不接。
