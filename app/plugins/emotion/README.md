# emotion · 情绪打分插件

把本地情绪数据合成一个 **0–100 连续情绪分**，与 `track_pool.db::sentiment.stage`（规则硬判）
**并列**展示：`stage` 给离散档位（冰点/启动/发酵/高潮/分歧/退潮），本插件给强弱幅度。
两者背离时一眼可见——这是本插件的用途，任何一侧都不覆盖另一侧。

## 文件

| 文件 | 作用 |
|---|---|
| `engine.py` | 六因子口径 + 滚动分位 + 档位（**纯函数、零 IO**，口径唯一源） |
| `source.py` | 取数：`sentiment` 收盘行、晋级率本地复算（只读） |
| `backend.py` | `/api/p/emotion/payload`（一次拿全）、`/meta`（轻量口径） |
| `frontend.js` | 独立页面：大数字 + 六因子拆解 + 近 60 日曲线 + stage 并列 |
| `tools/build_emotion_evidence.py`（项目根下） | 离线 CLI：打印今日明细 + 落证据 JSON（`data/panel_v2/emotion/evidence_YYYYMMDD.json`）。**2026-11-09 从插件目录移到 tools/**：插件目录只放运行时四件套，离线脚本不得被 cutover gate 当成运行时写通道 |
| `tests/test_emotion_engine.py` | 公式/边界单测（32 项） |
| `tests/test_emotion_contract.py` | 插件形态 + 真实接口 + 2026-10-29 对答案 |
| `tests/js/emotion_card.cdp.mjs` | 真实 Chrome 核对 + 截图（1600×1000 / 1440×900） |

## 口径（**v1.1**，2026-10-29 修订；基准 50，clamp [0,100]）

修订记录：炸板率符号改为 `20 − 炸板率×100`（原任务书从总表转写时抄错符号）；
晋级率加小样本保护（分母 <10 只不给分）。

| # | 因子 | 原始值 | 阈值 | 加减分 |
|---|---|---|---|---|
| 1 | 涨停家数 | `zt_total` | 滚动 60 日 q50（含当日；样本不足用全部） | `clamp((zt − 阈值) × 0.4, ±20)` |
| 2 | 晋级率 | `promotion_2b`（比例） | 15%；**分母 <10 只不给分** | `clamp((率 − 0.15) × 100, ±15)` |
| 3 | 炸板率 | `(zt_ever − zt_total) / zt_ever` | 20% | `clamp(20 − 率 × 100, ±20)`（**率越低越加分**） |
| 4 | 跌停 vs 涨停 | `dt_total` / `zt_total` | — | 跌停>涨停 −10；涨停≥跌停×1.5 +10；其余 0 |
| 5 | 最高板 | `height` | — | ≤2 板 −10；≥5 板 +10；其余 0 |
| 6 | 连板数 | `lb2plus` | 滚动 60 日分位 | `clamp((分位 − 0.5) × 20, ±10)` |

档位：≥70 亢奋 / ≥55 偏热 / ≥40 中性 / ≥25 偏冷 / <25 冰点。

**阈值不用固定 68/100**：68 是 2026-08 的市场状态，2026-10-29 涨停只有 39 家，固定阈值会连续
几个月判冰点、失去区分度；滚动分位跟市场自身中枢走。

**因子 6 为什么用分位**：连板家数没有绝对中枢（同时受涨停总数与连板生态影响），与因子 5
（最高板，阶跃）分工互补；线性映射避免阶跃抖动 10 分。详见 `engine.py` 模块头 §因子 6。

## 数据源（全部本地，不联网）

- `data/track_pool.db::sentiment`，**只取 `ts='close'`**（同一天还有 `close_sc` 与盘中时点行，
  混进来会让一个交易日出现多个分数）。当前 58 个交易日有收盘行。
- `夏天-activity/工具/xiaoshi_cache/hithink_{date}_limitup.json` / `_ladder.json`：晋级率复算。
- `data/panel/sector_ladder_{date}.json`：**只做交叉校验**（`limitup_total` vs `zt_total`，
  当前 17 个文件里 5 天差 1–3 家，差异记在 `payload.checks`，两边都不改）。

**晋级率口径**：`晋级率_N = |昨日 N 板 ∩ 今日 (N+1) 板| / |昨日 N 板|`。
库内已有值优先（原妙想 MCP 口径，`source="db"`），缺失用本地复算（`source="limitup"`）。
可对答案：2026-10-13 复算 5/14 = 35.71%，与库内旧值一致；2026-10-08 不同（66.67% vs 50.00%），
所以每个点都带来源标记，不混口径冒充同一源。

**premium（溢价）不可用**：需要昨日涨停股**今日**的全A行情快照，本地无此源。明确标注，不编。

## 已定的口径修订（2026-10-29）

- **炸板率反向**：`20 − 炸板率×100`，炸板率越低越加分。今日 39.06% → **−19.06**。
- **晋级率小样本保护**：分母（昨日 2 板家数）<10 只 → `participated=False`、`points=0`，
  note 写「样本不足」。今日 2/5 = 40% 但分母只有 5 只 → 不给分（不再拿 5 只样本换 +15 封顶）。
  库内原值没有分母样本数，优先取本地涨停池同日分母；两处都没有才按原口径参与并注明。
- 页面不再展示「反向试算」，只保留一套正式口径，并在顶栏/页脚显示 **口径 v1.1 · 2026-10-29 修订**。
- **档位阈值（70/55/40/25）暂不改**：当前 58 日分布仍偏热，但怎么调等另一路评审结论。

### 晋级率 10 只门槛是刻意设定，不是 bug

58 天里只有 8 天分母 ≥10 只、晋级率项真正参与，其余 50 天标「样本不足」。**这是事实，不是缺陷**：
本地涨停池里「昨日 2 板家数」常态只有 3–17 只，分母太小的时候一个 2/5 = 40% 就能拿到 +15 封顶，
这种晋级率没有统计意义。**宁可标缺项，也不要拿 2/5 去凑分**。

缺项不影响总分的水平：`总分 = 50 + Σ参与项`，缺项 `points=0`、`participated=False`，
既不加也不减（等价于按中性计入），**不会压低分数**。看到「大部分天数不参与」不要去放宽门槛、
也不要把它改成 0 分硬参与——那才会把分数压低。真要改，先拿晋级率的历史分布做检验再说。

同一条口径已写进 `engine.MIN_PROMOTION_SAMPLE` 的注释与测试 `test_promotion_small_sample_guard`。

## 接口

- `GET /api/p/emotion/payload?history_days=60` → `{asof, today{score,band,factors[6],stage,stage_note}, history[], promotion{}, checks{}, caliber{}}`
- `GET /api/p/emotion/meta` → 数据日、样本数、分数、晋级率可用性（不返回全序列）

无收盘行 → `503` + 补数据提示（页面显示「无数据」，不 500、不编分数）。

## 拆卸

删 `app/plugins/emotion/` 整个目录 + 删 `app/web/shell.js` 里 `NAV` 的
`emotion::emotion.main` 一行。core 零改动（`test_core_does_not_know_emotion` 锁住
`app/core/{main,paths,contract}.py` 里不出现 emotion 字样）。

## 跑测试

```powershell
cd projects/panel-v2
python -m pytest app/plugins/emotion/tests -q                 # 46 项
node app/plugins/emotion/tests/js/emotion_card.cdp.mjs --base http://127.0.0.1:8810
python tools/build_emotion_evidence.py                            # 打印今日明细 + 落证据 JSON
```
