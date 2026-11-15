# 面板产物一致性审计报告

**结论：FAIL**（PASS 7 / FAIL 3 / UNPARSED 1）

- 运行时间：2026-10-22 15:56:43
- 数据目录（只读）：`OH-WorkSpace\data\panel`
- 原始结果：`tools/consistency_result.json`
- 退出码：1

## 冲突项（必须处理）

- **A2 文本 MA10**：`文本 MA10` = 139824.51 ／ `amv_regime_latest.ma10` = 143013.53
- **A3 文本 ratio**：`文本 ratio` = 0.9867 ／ `round(amv/ma10, 4)` = 0.9647
- **A4 文本 当日涨跌%**：`文本当日涨跌%（AMV）` = 0.18 ／ `amv_regime_latest.pct` = -0.58

## 未解析项（需人工确认，不判冲突）

- **A5 文本 空档第 N 日**：文本 40 日与 last_switch 起算的 19（工作日）/ 27（自然日）都不符；起点可能不是 last_switch（如首次跌破 MA10 日），JSON 无该字段，不判冲突

## 逐项结果

| ID | 检查项 | 结果 | 左值 | 右值 | 说明 |
|---|---|---|---|---|---|
| A1 | 文本 AMV 值 | **PASS** | 文本 AMV（亿，AMV语境·亿） = 137967.48 | amv_regime_latest.amv = 137967.48 |  |
| A2 | 文本 MA10 | **FAIL** | 文本 MA10 = 139824.51 | amv_regime_latest.ma10 = 143013.53 | 文本 139824.51 ／ JSON 143013.53，差 3189.02 |
| A3 | 文本 ratio | **FAIL** | 文本 ratio = 0.9867 | round(amv/ma10, 4) = 0.9647 | 原文写作 0.9867，口径算得 0.9647 |
| A4 | 文本 当日涨跌% | **FAIL** | 文本当日涨跌%（AMV） = 0.18 | amv_regime_latest.pct = -0.58 | 文本涨跌与 regime.pct（当日 AMV 环比）方向/量级不符 |
| A5 | 文本 空档第 N 日 | **UNPARSED** | 文本 N = 40 | (last_switch.date=2026-09-24, regime.date=2026-10-21] = {'natural_days': 27, 'weekdays': 19} | 文本 40 日与 last_switch 起算的 19（工作日）/ 27（自然日）都不符；起点可能不是 last_switch（如首次跌破 MA10 日），JSON 无该字段，不判冲突 |
| A6 | 文本「距 MA10 差 X%」（证据项，不判定） | **INFO** | 文本 gap % = 1.35 | 现算 gap % = -3.53 | 差 4.88 个百分点 |
| B1 | position_cap.regime ↔ regime.state | **PASS** | position_cap_latest.regime = 空 | amv_regime_latest.state = 空 |  |
| B2 | position_cap.cap_amount ↔ battle_plan.position_cap | **PASS** | position_cap_latest.cap_amount = 142308.42 | battle_plan.position_cap = 142308.42 |  |
| C1 | amv_live.state_date ↔ regime.date | **PASS** | amv_live.state_date = 2026-10-21 | amv_regime_latest.date = 2026-10-21 | 口径：live.state_date 是 regime 对应的上一收盘日，不与 trade_date 比 |
| C2 | position_cap.trade_date ↔ amv_live.trade_date | **PASS** | position_cap_latest.trade_date = 2026-10-22 | amv_live.trade_date = 2026-10-22 |  |
| D1 | battle_plan.plan_date ↔ position_cap.trade_date | **PASS** | battle_plan.plan_date = 2026-10-22 | position_cap_latest.trade_date = 2026-10-22 |  |
| D2 | battle_plan.meta.trade_date ↔ position_cap.trade_date | **PASS** | battle_plan.meta.trade_date = 2026-10-22 | position_cap_latest.trade_date = 2026-10-22 |  |

## 证据：源字段原值

```json
{
  "amv_regime_latest": {
    "date": "2026-10-21",
    "state": "空",
    "ma10": 143013.53,
    "pct": -0.58,
    "amv": 137967.48,
    "resonance": false,
    "last_switch": {
      "i": 8213,
      "date": "2026-09-24",
      "dir": "空",
      "mark": "共振",
      "trigger": "P+M",
      "delay_any": 0,
      "delay_track": 0
    },
    "generated_at": "2026-10-22 11:00:29"
  },
  "position_cap_latest": {
    "trade_date": "2026-10-22",
    "regime": "空",
    "final_pct": 30,
    "cap_amount": 142308.42,
    "degraded_mode": "carry_over",
    "computed_at": "2026-10-22 01:12:46"
  },
  "amv_live": {
    "trade_date": "2026-10-22",
    "state_date": "2026-10-21",
    "asof": "2026-10-22 14:10:45"
  },
  "battle_plan": {
    "plan_date": "2026-10-22",
    "meta_trade_date": "2026-10-22",
    "position_cap": 142308.42,
    "judgment_head": "10/21 判断：**空档内的换仓，不是二次杀跌**。① 大盘：仓位闸门 30%（142,308.42 元），AMV 空档第 40 日、137,967.48 亿 / MA10 139,824.51 / ratio 0.9867，距 MA10 差 1.35%、单轨未共振，仍为空档；但 AMV +0.18% 而全A −0.21%、上证 −0.60%，是 40 个空档日里第一次「钱涨指数跌」。② 情绪：judge_stage 判「分歧」（涨停 50 / 跌停 5 / 高度 4 板 / 2板+ 16 / 涨跌比 0.59）；昨日两个开关一红一绿——二板晋级 1/8（要求 ≥3，红灯）、L2 多头家数 40/90=44.44%（绿灯）→ 按纪律不动仓。③ 板块：宁德时代三季报增收不增利（−12.03%、成交 324 亿居首）是全场唯一的因；锂电链价崩但钱没撤（锂电池 −3.78%/钱 +1.91%、"
  },
  "derived": {
    "amv_div_ma10": 0.964716,
    "gap_pct": -3.53
  }
}
```

## 边界

- 只读 `data/panel/*.json`；不连库、不发 HTTP、不写生产文件、不碰旧 8800/8002。
- 数值容差 1e-6（ratio 按文本 4 位小数放宽到 1e-4，涨跌% 放宽到 1e-2）。
- A5「空档第 N 日」：JSON 里没有权威起点字段（last_switch 是双轨确认日），工具只报 last_switch 推算区间，不判冲突。
- A6 是证据项，不进入退出码判定；它与 A2/A3 同根，用于说明文本 gap 与现算 gap 的差。
- 本工具不改 `battle_plan.json`；发现冲突只报告。
