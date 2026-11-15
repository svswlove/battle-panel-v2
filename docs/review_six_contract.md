# 复盘六格 · 落点与契约（v0.3.0，2026-11-03）

**版式正本**：`OH-Works/临时文件/六格卡片式版式规格_20261103.md`（v2 卡片式）。
早前那份暖纸样张作废，只作内容参考。

## 1. 落点（变更单 2026-11-03 18:0x：只留复盘第四视图）

| 落点 | 形态 | 入口 |
|---|---|---|
| 复盘卡第四视图「六格」 | 两列卡片，每格四段（标题 / chips / 依据 / 动作） | `review.main` 视图条 `图表｜纸面｜文档｜六格` |

- 侧栏**不新增入口**（六格是复盘卡的视图，不是独立卡）。
- **板块页不挂**（顶部两块 + 涨停集中徽标已撤：板块页已有排名与资金维度，六格搬过去没有增量判断）。
- 选股页、仪表盘不挂。
- 复盘视图只读 `GET /api/review_six.json`，不在页面层重算口径。
- 日期：默认跟复盘卡当前归属日；`?sixdate=YYYY-MM-DD`（或 `args.date`）可覆盖，用于回看/验收。

**判据与字段与复盘 skill 对齐**：`<HOME>\.hanako\skills\fupan_ri\SKILL.md` 的「附录 A」为准，生成器 `CRITERIA` 常量与之一一对应，两边不各写一套。

## 2. 数据链路

```
盘后产物（只读）
  ├ data/panel/sector_temp_latest.json          L2 板块池 + pct + state
  ├ data/amv_board.db::sector_daily             份额 share / d1 / d5
  ├ data/panel/sector_ladder_{date}.json        当日涨停家数（交叉校验）
  ├ 夏天-activity/工具/xiaoshi_cache/hithink_{date}_limitup.json
  │                                             全市场逐股涨停池（ladder 的原始源）
  ├ data/ths_board.db::board(sw_level=2)        涨停股 → L2 互斥归属
  ├ data/panel/amv_regime_latest.json           AMV / MA10 / state / 触发类型 / 第几天
  ├ data/panel/position_cap_latest.json         闸门 % 与金额
  ├ data/panel/amv_breadth_latest.json          多头家数占比
  ├ data/track_pool.db::sentiment               涨停跌停 / 首板+2板+ / 溢价（当前空）
  └ data/ths_index.db::index_daily              两市成交额（000001.SH + 399001.SZ）
        │
        ▼  tools/review_six_build.py（生产者，唯一口径源）
  data/panel/review_six_cells_{YYYYMMDD}.json
  data/panel/review_six_cells_latest.json
        │
        ▼  app/core/review_six.py（读取器：日期解析 + 源日期一致性）
        ▼  review 插件 GET /api/review_six.json（只透传）
        ▼  复盘第四视图 / 板块页顶部（只展示）
```

## 3. 口径（唯一源 = `tools/review_six_build.py`）

| 字段 | 定义 | 来源 |
|---|---|---|
| share | 板块AMV / 大盘AMV（L2 互斥完备，Σshare=1） | `sector_daily.share` |
| d1 | 今日份额 − 上一交易日份额（pp，= share 差 ×100） | `sector_daily.d1`（生成器用 share 差重算校验，误差 <1e-9） |
| d5 | 今日份额 − 5 个交易日前份额（pp） | `sector_daily.d5`（同上） |
| pct | THS 板块指数当日涨跌幅（%），价维度 | `sector_temp.boards[].pct` |
| limitup | 该 L2 板块当日涨停家数（一股只归一个 L2） | 涨停池 ticker → `ths_board` sw_level=2 |
| HHI | Σ(n_i / 当日涨停家数)²；分母写死 = 当日涨停家数 | 同上 |
| hhi_ma5 | 含目标日在内的近 5 个交易日 HHI 均值（缺池日期跳过） | 历史涨停池 |

**六格判据（初值 v1，跑出来再校）**

| # | 卡片标题 | 判据 | 排序 | chips 括号 |
|---|---|---|---|---|
| 01 | 重点配置 · 持续吸金 | d1>0 且 d5>0 且 state=多 | d5 降序前 5 | d5 |
| 02 | 潜伏观察 · 冷门流入 | d5>0 且 d1>0 且 pct<1% 且 涨停=0，**排除已进 01** | d5 降序前 5 | d5 |
| 03 | 趋势确认 · 强共振 | d1>0 且 pct>0 且 涨停>0 | **state=多 优先**，再按 d1 降序前 5；state=空 标「结构未转多」 | 涨停家数 |
| 04 | 风险最高 · 共振流出 | d1<0 且 pct<0 | d1 升序前 5 | d1 |
| 05 | 分歧提示 · 杠杆背离 | **pct≥1% 且 d1<0 且 \|d1\|≥0.02pp** | pct 降序前 5；无合格标的显示「今日无」 | pct |
| 06 | 情绪焦点 · 涨停集中 | HHI（只给数值 + 近 5 日均值，不设档位阈值） | — | 家数 |

**四条口径修正（变更单 2026-11-03）**

- **A 温度区**：必须显式给 AMV 与 MA10 位置；state=多 但 AMV 在 MA10 下方 → `amv_vs_ma10.unconfirmed=true` + `label=单轨未确认`，headline 必带差额与幅度（例：「今日 AMV 134,985 亿，低于 MA10 1,080 亿（-0.79%），state=多·单轨未确认…」）。
- **B 强共振**：state=多 在前，state=空 排后并在行上标 `struct_note=结构未转多`；不允许空头板块占第一。
- **C 杠杆背离**：`PCT_LEV_MIN=1.0` / `D1_LEV_MIN=0.02`；全无合格标的时 `empty_note=今日无`、basis 以「今日无」开头。阈值初值，跑出来再校。
- **D 去重**：冷门背离排除已进持续吸金的板块（按 code），同一板块不许两格同挂。

阈值常量在生成器顶部（`TOP_N` / `PCT_COLD_MAX` / `HHI_MA_N`），改判据只改一处并同步本文件。

## 4. 每格四段（产物字段）

| 段 | 字段 | 谁生成 |
|---|---|---|
| 标题 | `no` + `title`（编号 + 定性词 · 格子名） | 生成器（`CARD_META`） |
| chips | `chips[{name, metric, metric_key, value}]`（3–5 个，括号带最关键一个数） | 生成器 |
| 依据 | `basis`（一行数值陈述，不写形容词） | 生成器 |
| 动作 | `action`（null=待填）/ `action_status`（pending/written） | **夏天手写**，生成器只留位 |

顶部总述 `headline`：`今日 AMV 多（空转多）、闸门 70%（多头家数 42.99%、涨停 131 家）` —— 状态机 + 口径数字，自动生成。

动作写入方式：

```powershell
python tools\review_six_build.py --date 2026-11-03 --actions actions.json
# actions.json = {"persistent_inflow": "…", "cold_divergence": "…", ...}
```

同日重跑会保留已写的 `action`（按格 key 对齐）。

## 5. 端点

| 端点 | 说明 |
|---|---|
| `GET /api/review_six.json` | 最新产物（`_latest`）；返回产物原文 + `available_dates` + `artifact` |
| `GET /api/review_six.json?date=YYYY-MM-DD` | 指定交易日（不回溯；缺该日 → 404 并列出已有日期） |

错误语义：缺日 404 / 参数非法 400 / 产物源日期 ≠ 目标日 500（不拿旧值顶替）。
读取器在 `app/core/review_six.py`（按日文件族超出 `READABLE_PANEL` 单文件白名单，与 `review_chart` 同例）。

## 6. 生成与验收

```powershell
cd projects\panel-v2
python tools\review_six_build.py --date 2026-11-03        # 落盘当日 + latest
python tools\review_six_build.py --relayout 2026-11-02    # 旧产物只补卡片版式字段（不重算指标）
python tools\review_six_reconcile.py --date 2026-11-03    # 对账表 md
python -m pytest app/plugins/review/tests/test_review_six.py -q
node app/plugins/review/tests/js/review_six_view.cdp.mjs  # 真浏览器截图 + 断言
```

源日期校验：任意一份源停在上交易日 → 生成器 `SystemExit`，不落盘；
读取端再校验一次（`meta.sources.*.date == meta.target_date`）。

## 7. 已知边界

- **两市成交额**源是 `ths_index.db`（同花顺口径），不在任务书列的七个源里；11-02 值与复盘 MD 一致（1.78 万亿）。
- **昨涨停今日溢价**当前无源（`sentiment.premium = NULL`）→ 显示「— 无源」，不编。
- 涨停池逐股统计与 `sentiment.lb2plus / height` 不一致时写 `meta.warnings`，不拦生成。
- 旧日期产物（11-02）生成于版式 v2 之前，`--relayout` 只补展示字段，指标一字未改。
- 与现有七节复盘 MD **并存**：六格是第四视图，不替换图表/纸面/文档，也不改 MD 模板。
