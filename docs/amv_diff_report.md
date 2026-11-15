# amv 双跑比对报告（旧 8800 vs 新 8810）

**结论：PASS**。三份原始 JSON 在旧 8800 与新 8810 之间**逐对象精确相等**（Python `==`，无容差）。

- 生成时间：2026-10-22
- 旧：`http://127.0.0.1:8800`
- 新：`http://127.0.0.1:8810`
- 端点映射：

| 旧 8800 | 新 8810 | 顶层 keys | 结果 |
|---|---|---:|---|
| `/api/amv_live.json` | `/api/p/amv/live` | 15 | **equal=True** |
| `/api/amv_regime_latest.json` | `/api/p/amv/regime` | 11 | **equal=True** |
| `/api/position_cap_latest.json` | `/api/p/amv/position_cap` | 14 | **equal=True** |

## 派生摘要自检

`/api/p/amv/summary` 的字段直接来自上述三份 JSON：

- `amv = amv_live.all.amv`
- `amv_pct = amv_live.all.amv_pct`
- `regime.ma10 / pct / state = amv_regime_latest.*`
- `position_cap.final_pct / cap_amount = position_cap_latest.*`
- `consistency.live_vs_regime` 用 `amv_live.state_date` 与 `amv_regime_latest.date` 比较（盘中 live 的 `trade_date` 会比 regime 新一天，属正常口径，不报 stale）。

## 覆盖边界

- 只比较三份 AMV 原始 JSON；未比较 AMV 历史序列、个股贡献、板块 AMV 趋势图。
- 旧 8800 的 `/api/amv_refresh` 是写接口（触发 AMV 重算），新版**不迁移、不调用**。
- 新版 AMV 插件不连数据库、不写 `data/panel`，只读 panel JSON。

## 切流建议

三份原始 JSON 已可 shadow；`/summary` 是只读派生，可先挂卡片观察。旧 8800 AMV 卡继续保留，直到只读域整体切流评审。
