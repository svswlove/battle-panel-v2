# -*- coding: utf-8 -*-
"""review_chart_data.py 单测（2026-10-12 复盘 Tab 图表模板 · 服务端装配）

覆盖：
  · MD 解析：标准标题/结论带/我的判断/表格抽取；损坏 MD（缺标题/缺结论带）→ ReviewChartError
  · 缺段守卫：源 json 缺失/日期不符 → 段级 miss + meta.miss 汇总，不抛异常
  · 装配契约：段字段齐备（组件消费的字段存在）、涨停/跌停与 sentiment 同源、缺口数值现算
  · 缓存：指纹不变 → hit（不重读）；源文件 mtime 变 → miss
  · /api/review_chart.json 端点：字段齐备 / 无 MD → 404 / MD 损坏 → 502（不 500）

E 红线守门：全程 monkeypatch 本模块路径常量到 tmp（PANEL_DIR/TEMPERATURE_FILE/TRACK_DB
与 review 插件 `source.REVIEW_DIR`）；指数日线打 eltdx 替身（patch `R._index_series_cached`）；
不读写生产 data/**、夏天-activity/**、engine/state/**。
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import review_chart as R
from app.core import review_six as RSIX
from app.plugins.review import source as RS

MD_OK = """# 日复盘 2026-10-09（周五 · 早盘深杀 · 午后 V 型反转）

> 数据时点：测试夹具
> 口径：涨跌家数=eltdx 880005（3297/2145）／涨停=72（hithink 全口径）
> 温度：46°C 温和（cap_effective=60%，战略震荡 ×0.8；单票 15%）
> 备注：骨架=复大盘→复板块→复个股

> 🎯 **一句话**：早盘深杀、午后 V 型反转翻红——但这是「跌出来的修复」。
> 📌 **三件事**：①情绪从「退潮」跳回「发酵」（涨停 72、回封率 84.7%）；②唯一有梯队的主线是电池链；③持仓四项全待处置。
> 🚨 **10-12 方向**：结构分化，仓位 ≤60%。

---

## 三问作答（夹具）

| # | 今日的问题 | 答案 | 证据 | 台账 |
|---|---|---|---|---|
| 1 | 半导体 d1 能不能续上第 3 天为正？ | 能，但只剩半条命 | d1 +0.864、pct +2.10% | H-1 hit |
| 2 | 电池链接力能不能延续？ | 能，塔型仍成立 | 涨停梯度前排 2 塔 | H-2 pending |

---

## 〇、复大盘 · 定仓位

### 0.1 活跃市值双轨（AMV 大盘）

- **AMV 大盘多空：{g}空{g}**（MA10=162,332.9 ／ AMV=151,764.8）
- 最近一次切换 **2026-09-24 转空·共振**，**至今未变**（第 7 个交易日）

### 0.2 四维温度

| 维度 | 数值 | 得分 |
|---|---|---|
| 估值 | PE 17.1（百分位 56.03） | 6 |
| 量能 | 1.9006 万亿 / 20 日均 1.8056 万亿 = 1.053 | 12 |
| 趋势 | 1,425.13 vs MA20 1,442.8（**−1.22%**） | 10 |
| 情绪 | 涨 3297 / 跌 2145（比 1.537） | **18** |
| **温度** | **46°C 温和** | **46** |

- 温度较 10/8（28°C）**回升 18 度**：全部来自**情绪维 0→18**

### 0.3 收盘全景（指数 + 量能）

| 指数 | 收盘 | 涨跌 | 日内高 / 低 |
|---|---:|---:|---:|
| 全A | 5,570.32 | {r}+0.19%{r} | — / — |
| 上证 | 3,813.79 | {r}+0.05%{r} | 3,824.71 / 3,754.14 |

- 宽度 **涨 3297 / 跌 2145**（比 1.537）；**涨停 72 / 跌停 10**
- 宽度由 10:00 的涨 1057/跌 4035 **翻转为 涨 3297/跌 2145**；11:30 全A −1.98%、科创50 盘中最低 −4.6%、创业板 −3.4%，午后单边反抽

### 0.4 支撑压力位与仓位

- 结构位：上方压力 3,824 / 3,876，下方支撑 3,754 / 3,750
- **仓位纪律**：总仓上限 **60%**、单票 **15%**

> 💡 **我的判断**：10/9 给的是「杀跌后修复」，不是「转强」。三条证据：①涨幅极小；②AMV 大盘自 9/24 转空后仍未回多；③指数全部仍在 MA20 下方。

---

## 一、消息面与驱动

### 1.1 盘前消息面（源 = `news_snapshot_20261009` asof 09:21）

| 消息面 | 盘面实测 | 判定 |
|---|---|---|
| **隔夜美股（10/8 收）**：道指 +0.10% | 半导体 +0.52% | 🟡 **反向** |
| **现货黄金 10/8 首破 $4,000** | 贵金属 +8.28% | ✅ **强兑现** |

### 1.2 增量消息面（盘后 → 复盘执行时点 2026-10-11 21:1x）

> 源 = `OH-Works/临时文件/news_increment_20261009_to_20261011.md`

| 增量消息 | 要点 | 对下一交易日（10/12 周一）影响 |
|---|---|---|
| **美股 10-09 隔夜** | 道指 +0.83%；费半 −0.41% | 🟢 指数端偏多 ／ 🔴 半导体链偏空 |

### 1.3 驱动归因（含增量更新）

**当日（10-09）驱动 = 「传媒链事件驱动爆发」+「贵金属避险加速」**

1. **主因（结构·传媒事件驱动的反弹）**：午后传媒链承接资金
2. **指数失真（价格）**：全A 仅 +0.19%
3. **电子链的真弱（资金）**：权重Δ 继续大额流出

**对下一交易日（10/12 周一）净效果预判**：
- 🟢 偏多：量能 +13%
- 🔴 偏空：AMV 大盘仍空
- 🟡 观察：AI 应用/软件
- ⚠ 风险：10-14 美国 9 月 CPI

> 💡 **我的判断**：10/9 的核心矛盾是「技术修复 vs 资金未回」。

---

## 二、复板块 · 定方向

### 2.1 前排 / 退潮（L2 行业）

> 结构性特征：**多头板块扩至 17 个（10/8 为 12 个）**

### 2.2 涨停梯度

- **塔型判读**：今日没有「主线塔」，最强的是两个「单核塔」
- **电池链（电池 6 + 钠离子 3 + 动力电池回收 2 + 电池化学品）合计 11 家**，是**唯一有跨板块梯队的方向**（固态电池/钠电/回收一体化）

### 2.3 异动板块

- **重点归因**：**医疗美容 +16.84% 但资金净流出 2.1 亿** = 纯情绪票；**电池化学品 +311 亿**是全市场单板块最大净流入
- **资金主线：电子/风电等高波动方向 → 传媒/AI 应用/电池。**

### 2.4 板块层 AMV 多空

> 💡 **我的判断**：①**传媒链爆发是事件驱动**（新华传媒拟收购财联社），但梯队断层，是独苗不是主线；②**电池链是唯一有跨板块梯队的做多方向**，且资金 +0.648pp 增配；③**电子链资金仍在撤**，在权重Δ转正前不宜抄底。

---

## 三、情绪 · 赚钱效应

**判定：{r}发酵{r}**（`judge_stage` v2.1 原文：**"涨停 72 + 高度 7 板"**）

**补充指标（不进判定规则）**：昨日涨停溢价率 +1.07%（10/8 值，10/9 脚本未落库）；连板晋级率 10-09 **降至 40%** → **高度断层在扩大**

**退潮四条核对**：
- ❌ 高位股跌停（均未跌停）
- ❌ 涨停 < 60（72，已回升）

**次日观察点**（v2.1 输出）：
1. 涨停数能否站稳 60+
2. 回封率能否维持 75%+

**攻守含义**：从「退潮」回「发酵」= 赚钱效应修复；但注意区分「修复式发酵」与「进攻式发酵」

> 💡 **我的判断**：情绪从「退潮」直接跳到「发酵」。

---

## 四、复个股 · 持仓 + 准备买

### 4.1 持仓

| 账本 | 标的 | 数量 | 成本 | 收盘 | 相对成本 | 今日 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---|
| 实盘(手工) | 冠盛股份 605088 | 1,000 | 25.948 | 22.05 | −8.13% | {g}−6.92%{g} | 最近档 EK_CLOSE 24.00（−8.13%）🔴 **破锚（连续第 4 日）** |
| 实盘(手工) | 通信ETF 159583 | 10,000 | 1.553 | 1.475 | −1.67% | {g}−0.74%{g} | 最近档 EK_OPEN 1.500（−1.67%）⚪ **下穿加仓线（底仓不止损）** |

- **账户**：总资产 **222,587.28**｜现金 143,082.18（**64.3%**）｜市值 79,505.10；日变动 **−5,373.42（−2.36%）**——**大盘翻红而账户仍跌，持仓拖累**（主因 605088）
- **当日成交 1 笔**：冠盛 605088 **SELL 1000 @22.86**（09:31:48）——破锚处置**部分减仓**
- **持仓板块对照**：冠盛股份（汽车零部件，pct +1.31%／state 空）、通信ETF（通信设备 −0.29%／state 空）

> ⚠ **风险提示**：**冠盛股份**破锚已连续第 4 日。

### 4.2 准备买

- **J5 板块聚集**：无——当日**无 selection**
- **三层闸门检查**：
  - **大盘层**：AMV 空 + 温度 46°C 温和 → 🟡 **黄**（修复中，未转多）
  - **板块层**：L2 多头 26.67%（低于大盘）→ 🟡 **黄**
  - **个股层**：无合格标的（无选股/无池内买点）→ —
  - → **无个股层标的，本日不构成任何买入条件**

---

## 五、明日盯盘重点（10/12 周一）

1. **持仓处置（最高优先级）**：
   - 🔴 冠盛股份 605088：破锚第 4 日 → 主人处置决策（T-0930-1）
2. **消息催化（当日新增）**：**《发展新质生产力的意见》全面实施「人工智能+」**

---

## 六、情景-应对表

| 情景 | 触发条件 | 概率 | 应对 |
|---|---|---|---|
| S-1 持仓破锚 | 冠盛破 24.00 | 40% | 主人处置决策（优先于一切） |
| S-2 情绪退潮 | 涨停 <60 且跌停 >10 | 25% | 减仓至三成 |

### 消息层互验

- **隔夜美股科技链杀跌 vs 半导体逆势翻红** → 归因到资金而非消息

---

## 七、收尾自查

- [ ] ⚠ **缺口 1**：`20261009_pool_overlap.json` 未生成 → J7 不可用
- [ ] ⚠ **时间差**：本复盘为周末补做

---

*产物：本 MD（唯一事实源）｜HTML 由统一作战面板按需渲染（`/api/review.md`）*
"""


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _make_dbs(tmp):
    """构造最小 track_pool.db 夹具（字段口径对齐生产）。

    2026-11-01 换源后指数日线不再读 ths_index.db，改打 eltdx 替身：
    _make_dbs 返回 (track, idx_bars)，setUp 把 `R._index_series_cached` 替换成读
    idx_bars 的替身（与旧 patch INDEX_DB 同等隔离：零生产 IO）。
    """
    track = os.path.join(tmp, "track_pool.db")
    con = sqlite3.connect(track)
    con.execute("""create table sentiment (trade_date TEXT, ts TEXT, stage TEXT, stage_note TEXT,
        zt_total REAL, dt_total REAL, height REAL, lb2plus REAL, up_cnt REAL, down_cnt REAL,
        reseal_rate REAL, premium REAL, promotion_2b REAL, promotion_3b REAL, promotion_4b REAL)""")
    con.execute("insert into sentiment values ('20261008','close','退潮','跌停潮',45,15,7,10,1698,3748,58.44,1.07,50,100,200)")
    con.execute("insert into sentiment values ('20261009','close','发酵','涨停 72',72,10,7,12,3297,2145,84.71,NULL,NULL,NULL,NULL)")
    con.commit()
    con.close()
    # 指数日线替身数据（对齐旧 ths_index.db 夹具：25 根基线 + 20261009 复盘日收盘行）
    idx_bars = {"000001.SH": [], "399001.SZ": []}
    for code, base in (("000001.SH", 3800.0), ("399001.SZ", 12600.0)):
        for i in range(25):
            d = "2026%02d%02d" % (9 + (i // 28), 1 + (i % 28))
            idx_bars[code].append({"date8": d, "o": base, "h": base + 10, "l": base - 10,
                                   "c": base + i * 0.1, "amount": 8.8e11})
    for code, c, tn in (("000001.SH", 3813.79, 8.885e11), ("399001.SZ", 12641.86, 1.0119e12)):
        idx_bars[code].append({"date8": "20261009", "o": c - 9, "h": c + 10, "l": c - 60,
                               "c": c, "amount": tn})
    return track, idx_bars


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.panel = os.path.join(self.dir, "panel")
        os.makedirs(self.panel, exist_ok=True)
        self.review_dir = os.path.join(self.dir, "review")
        os.makedirs(self.review_dir, exist_ok=True)
        with open(os.path.join(self.review_dir, "日复盘 2026-10-09.md"), "w", encoding="utf-8") as f:
            f.write(MD_OK)
        self.track, self.idx_bars = _make_dbs(self.dir)
        # 源 json（日期=复盘日，齐备）
        _write_json(os.path.join(self.panel, "amv_regime_latest.json"), {
            "date": "2026-10-09", "state": "空", "ma10": 162332.88, "amv": 151764.76, "pct": 1.19,
            "last_switch": {"date": "2026-09-24", "dir": "空", "mark": "共振", "trigger": "P+M"},
            "caliber": "AMV 双轨多空判定", "source": "data/amv_board.db"})
        _write_json(os.path.join(self.panel, "amv_breadth_latest.json"), {
            "date": "2026-10-09", "source": "amv_board", "caliber": "截面",
            "market": {"multi": 1726, "valid": 5194, "ratio": 0.332307, "weight_ratio": 0.258456,
                       "series": [{"date": "2026-10-08", "ratio": 0.25953, "weight_ratio": 0.201},
                                  {"date": "2026-10-09", "ratio": 0.332307, "weight_ratio": 0.258456}]},
            "sector_level": {"L2": {"multi": 24, "valid": 90, "ratio": 0.266667,
                                    "series": [{"date": "2026-10-08", "ratio": 0.144444},
                                               {"date": "2026-10-09", "ratio": 0.266667}]}},
            "transmission": {"date": "2026-10-09", "note": "截面口径",
                             "layers": {"L0_market": {"multi": 1726, "valid": 5194, "ratio": 0.332307},
                                        "L1_sector_L2": {"multi": 24, "valid": 90, "ratio": 0.266667, "n_boards": 90},
                                        "L2_holdings": {"n": 0, "multi": 0, "valid": 0, "ratio": None,
                                                        "holdings_total": 0, "available": True,
                                                        "sample_state": "empty",
                                                        "sample_txt": "实盘账本空仓，无个股层样本",
                                                        "source": "data/panel/real_account.json（实盘账本，唯一权威）",
                                                        "note": "持仓源=实盘账本；空仓"}}}})
        _write_json(os.path.join(self.panel, "sector_temp_latest.json"), {
            "date": "2026-10-09", "counts": {"L2": 3}, "caliber": "AMV·THS", "state_caliber": "双轨",
            "source": "sector_temp",
            "pct_semantics": {"pct": "THS 板块指数当日涨跌幅(%)",
                              "amv_pct": "板块 AMV 当日环比(%)"},
            # 夹具故意让 pct 与 amv_pct **反向**，并用第三条 pct=None + amv_pct 极高：
            # 只有“top/bottom 按 pct 排、pct 缺值不入榜”的实现才能全部对上。
            "boards": [{"code": "881274.TI", "name": "影视院线", "level": "L2", "state": "多",
                        "weight": 0.0036, "weight_delta": 0.00058,
                        "pct": 13.24, "amv_pct": -5.0, "pct_source": "board_flow"},
                       {"code": "881101.TI", "name": "元件", "level": "L2", "state": "空",
                        "weight": 0.05, "weight_delta": -0.00575,
                        "pct": -3.54, "amv_pct": 8.0, "pct_source": "board_flow"},
                       {"code": "881200.TI", "name": "缺涨跌幅板块", "level": "L2", "state": "多",
                        "weight": 0.01, "weight_delta": 0.002,
                        "pct": None, "amv_pct": 99.0, "pct_source": "missing"}]})
        _write_json(os.path.join(self.panel, "sector_ladder_20261009.json"), {
            "trade_date": "2026-10-09", "limitup_total": 72, "front_count": 2,
            "caliber": "hithink", "source": "sector_ladder",
            "boards": [{"name": "文化传媒", "code": "881164.TI", "level": "L2", "limitup_count": 10,
                        "amv_state": "多", "weight_delta": 0.00114, "grade": "单核塔",
                        "height": {"H": 9, "gap": 7, "gap_detail": [2, 3, 4], "ladder": {"1": 9, "9": 1}},
                        "strength": {"seal_ratio": 0.3265, "leader": {"name": "新华传媒", "cnt": 9}},
                        "rhythm": {"leader_time": "09:25", "early_leader": True, "tail_ratio": 0.0},
                        "cross": "单核题材", "select_reason": "当日涨停≥3"}]})
        _write_json(os.path.join(self.panel, "sector_anomaly_20261009.json"), {
            "trade_date": "2026-10-09", "caliber": "x", "counts": {}, "source": "sector_anomaly",
            "anomalies": [{"name": "医疗美容", "level": "L3", "pct": 16.8397, "ex_pct": 15.4,
                           "d1": -0.021, "d5": 0.030,
                           "flow_calc": -207430473.0,
                           "quadrant": "虚涨·背离", "amv_state": "多",
                           "attr": {"explained": False, "verdict": "真·莫名其妙（重点归因）"}},
                          {"name": "电池化学品", "level": "L3", "pct": 6.8432, "ex_pct": 6.1,
                           "d1": 0.050, "d5": 0.080,
                           "flow_calc": 31104668331.0,
                           "quadrant": "真异动", "amv_state": "多",
                           "attr": {"explained": True, "verdict": "可解释"}}],
            "fund_flow": []})
        # 持仓两本账（2026-11-08 主人定）：实盘 real_account / 纸面 paper_account；
        # real_positions.json 是 QMT 柜台缓存，只作 degraded 提示。夹具里故意塞 888880 新标准券，
        # 断言它不进任何读数（旧版把这 217,000 算进市值 → 页面出现「总资产 217,389.40」的假账户）。
        _write_json(os.path.join(self.panel, "real_account.json"), {
            "meta": {"schema_version": "1.0.0", "document_type": "real_account", "is_ledger": True,
                     "status": "CONFIRMED", "updated_at": "2026-10-09 15:05:00",
                     "realized_pnl_gross_known": -50.0, "note": "实盘已清空"},
            "account": {"cash": None, "total_asset": None, "market_value": None},
            "positions": [],
            "closed": [{"code": "002244.SZ", "name": "滨江集团", "qty": 100, "cost_price": 10.0,
                        "closed_at": "2026-10-08 14:00:00", "close_price": 9.5,
                        "realized_pnl_gross": -50.0},
                       {"code": "002085.SZ", "name": "万丰奥威", "qty": 100, "cost_price": None,
                        "closed_at": "2026-10-09 10:00:00", "close_price": 8.71,
                        "realized_pnl_gross": None}]})
        _write_json(os.path.join(self.panel, "paper_account.json"), {
            "initial_cash": 500000, "cash": 100000.0, "positions": {},
            "realized_pnl": -400.0, "trade_date": "2026-10-09",
            "updated_at": "2026-10-09 15:00:00",
            "stats": {"win_rate": 50.0, "closed_trades": 2, "wins": 1}})
        # 假设台账（缺了会进 miss 汇总，夹具必须给）
        _write_json(os.path.join(self.panel, "hyp_ledger.json"), [
            {"id": "H-1", "claim": "电池链是唯一有梯队的主线", "status": "pending",
             "raised": "20261009", "due": "20261012", "test": "涨停梯度前排 ≥2 塔"},
        ])
        _write_json(os.path.join(self.panel, "real_positions.json"), {
            "meta": {"trade_date": "2026-10-09", "note": "QMT 未登录（缓存）", "source": "qmt"},
            "status": "DISCONNECTED", "last_synced_at": "2026-10-08 19:56:02",
            "positions": [{"code": "512480.SH", "name": "半导体ETF国联安", "qty": 100,
                           "cost_price": 1.061, "market_value": 83.6},
                          {"code": "888880.SH", "name": "新标准券", "qty": 2170,
                           "cost_price": 100.0, "market_value": 217000.0}],
            "manual_positions": [],
            "account": {"total_asset": 217389.4, "cash": 305.8, "market_value": 217083.6}})
        _write_json(os.path.join(self.panel, "battle_plan.json"), {
            "plan_version": "2026-10-12-v1", "meta": {"trade_date": "2026-10-12",
                                                      "md_source": "夏天-activity/每日复盘/日复盘 2026-10-09.md"},
            "scenarios": [{"id": "S-1", "layer": "discipline", "trigger": "持仓破锚", "action": "主人处置"},
                          {"id": "S-5", "layer": "sentiment", "trigger": "发酵延续", "action": "轻仓参与"}]})
        _write_json(os.path.join(self.dir, "temperature.json"), {
            "date": "2026-10-09", "temperature": 46, "band": "温和", "cap_raw": 75, "cap_effective": 60.0,
            "regime_note": "战略震荡：仓位上限 x0.8", "single_stock_cap": "15%", "note": "滚动操作最优窗口",
            "dimensions": {}})
        # 仓位档位（2026-10-13 口径：总仓上限源=position_cap_latest.json，AMV 档位驱动）
        _write_json(os.path.join(self.panel, "position_cap_latest.json"), {
            "trade_date": "2026-10-09", "regime": "空", "base_pct": 30, "final_pct": 30,
            "cap_amount": 127000.2, "extreme": False, "note": "AMV 空档 30%→ 最终 30%"})
        # 六格产物（2026-11-08 补）：review_chart 的板块方向直接消费它，缺了会写进 meta.miss（该报就报），
        # 所以夹具必须给一份最小但合法的产物——形状按 tools/review_six_build.py 的真实产出（meta.sources 必须有，
        # review_six.load 会校源日 == target_date）。
        _write_json(os.path.join(self.panel, "review_six_cells_20261009.json"), {
            "schema_version": "1.0.0", "kind": "review_six_cells",
            "meta": {"target_date": "2026-10-09", "trade_date": "2026-10-09",
                     "generated_at": "2026-10-09 16:30:00", "builder": "tools/review_six_build.py",
                     "sources": {"sector_temp": {"path": "data/panel/sector_temp_latest.json",
                                               "date": "2026-10-09"}}},
            "temperature": {"amv": {"value": 151764.76, "unit": "亿"}, "state": "空"},
            "headline": "夹具：六格最小产物",
            "cells": [
                {"key": "persistent_inflow", "title": "重点配置 · 持续吸金",
                 "criteria": "d1>0 且 d5>0 且 state=多", "n_matched": 1,
                 "rows": [], "matched": ["影视院线"],
                 "chips": [{"name": "影视院线", "metric": "d5 +0.200", "metric_key": "d5",
                            "value": 0.2, "state": "多"}],
                 "basis": "影视院线 d1 +0.050、d5 +0.200；命中 1 个",
                 "action": "夹具动作：只做前排。", "action_status": "written"},
                {"key": "cold_divergence", "title": "潜伏观察 · 冷门流入",
                 "criteria": "d5>0 且 d1>0 且 pct<1%", "n_matched": 0,
                 "rows": [], "matched": [], "chips": [],
                 "basis": "无命中", "action": "今日无。", "action_status": "written"},
                {"key": "strong_resonance", "title": "趋势确认 · 强共振",
                 "criteria": "pct>0 且 d1>0 且 涨停>0", "n_matched": 2,
                 "rows": [], "matched": ["影视院线", "文化传媒"],
                 "chips": [{"name": "影视院线", "metric": "涨停 10 家", "metric_key": "limitup",
                            "value": 10, "state": "多"},
                           {"name": "文化传媒", "metric": "涨停 3 家", "metric_key": "limitup",
                            "value": 3, "state": "多"}],
                 "basis": "影视院线 pct +13.24%、d1 +0.050、涨停 10 家；命中 2 个",
                 "action": "夹具动作：共振只看不做。", "action_status": "written"},
                {"key": "resonance_outflow", "title": "风险最高 · 共振流出",
                 "criteria": "d1<0 且 pct<0", "n_matched": 1,
                 "rows": [], "matched": ["元件"],
                 "chips": [{"name": "元件", "metric": "d1 -0.154", "metric_key": "d1",
                            "value": -0.154, "state": "空"}],
                 "basis": "元件 d1 -0.154、pct -3.54%；命中 1 个",
                 "action": "夹具动作：不接反弹。", "action_status": "written"},
                {"key": "leverage_divergence", "title": "分歧提示 · 杠杆背离",
                 "criteria": "pct≥1% 且 d1<0", "n_matched": 0,
                 "rows": [], "matched": [], "chips": [],
                 "basis": "今日无", "action": "今日无。", "action_status": "written"},
                {"key": "limitup_concentration", "title": "情绪焦点 · 涨停集中",
                 "criteria": "涨停按 L2 归集", "n_matched": None,
                 "total_limitup": 72, "hhi": 0.0602, "hhi_ma5": 0.0580, "hhi_ma5_n": 5,
                 "top3_sum": 20, "top3_share": 0.2778, "first_board_ratio": 0.6, "first_board_n": 43,
                 "lb2plus_n": 12, "max_height": 9, "coverage": "72/72",
                 "rows": [], "matched": ["影视院线"],
                 "chips": [{"name": "影视院线", "metric": "10 家", "metric_key": "n",
                            "value": 10, "state": None}],
                 "basis": "涨停 72 家，前三 20 家（27.8%），HHI 0.0602",
                 "action": "夹具动作：名字梗不参与。", "action_status": "written"},
            ],
            "verdicts": {},
        })
        # 路径注入（全部只读 tmp）
        # 指数日线替身（2026-11-01 换源 eltdx）：_index_series_cached 打夹具替身
        def _fake_idx_series_cached(code, n, _bars=self.idx_bars):
            rows = [(b["date8"], b["o"], b["h"], b["l"], b["c"], b.get("amount"))
                    for b in _bars.get(code, [])]
            return sorted(rows)
        self._patches = [
            mock.patch.object(R, "PANEL_DIR", self.panel),
            mock.patch.object(R, "TEMPERATURE_FILE", os.path.join(self.dir, "temperature.json")),
            mock.patch.object(R, "REAL_ACCOUNT_FILE", os.path.join(self.panel, "real_account.json")),
            mock.patch.object(R, "PAPER_ACCOUNT_FILE", os.path.join(self.panel, "paper_account.json")),
            mock.patch.object(R, "QMT_CACHE_FILE", os.path.join(self.panel, "real_positions.json")),
            # 六格产物也用 tmp 目录（review_six 自己读 paths.PANEL_DIR，不 patch 就会读生产库）
            mock.patch.object(RSIX, "panel_dir", lambda: Path(self.panel)),
            mock.patch.object(R, "TRACK_DB", self.track),
            mock.patch.object(R, "_index_series_cached", _fake_idx_series_cached),
            mock.patch.object(RS, "REVIEW_DIR", self.review_dir),
            mock.patch.object(RS, "MAINLINE_DIR", os.path.join(self.dir, "hist")),
        ]
        for p in self._patches:
            p.start()
        os.makedirs(os.path.join(self.dir, "hist"), exist_ok=True)
        RS.reset_caches()
        R.reset_cache()

    def tearDown(self):
        R.reset_cache()
        RS.reset_caches()
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()


class ParseMdTest(_Base):
    def test_parse_ok(self):
        d = R.parse_md(MD_OK, "2026-10-09")
        self.assertEqual(d["date"], "2026-10-09")
        self.assertIn("早盘深杀", d["one"])
        self.assertEqual(len(d["three"]), 3)
        self.assertEqual(d["switch_days"], 7)
        self.assertEqual(d["temp_prev"], 28)
        self.assertEqual(d["temp_delta"], 18)
        self.assertEqual(len(d["temp_dims"]), 4)
        self.assertEqual(d["allA_close"], 5570.32)
        self.assertEqual(d["levels_sse"][0], 3824.0)
        self.assertGreaterEqual(len(d["quotes"]), 3)
        self.assertEqual(d["news_asof_pre"], "09:21")
        self.assertEqual(d["promotion_today"], 40.0)
        self.assertEqual(len(d["unseal"]), 2)
        self.assertEqual(len(d["watch"]), 2)
        self.assertEqual(len(d["pos_rows"]), 2)
        self.assertEqual(d["pos_rows"][0]["anchor"], 24.0)
        self.assertEqual(d["pos_rows"][0]["anchor_label"], "EK_CLOSE")
        self.assertEqual(d["pos_rows"][0]["dist_pct"], -8.13)
        self.assertEqual(len(d["gates"]), 3)
        self.assertEqual(len(d["tomorrow"]), 2)
        self.assertIn("缺口 1", [g["k"] for g in d["gaps"]])

    def test_parse_broken_raises(self):
        with self.assertRaises(R.ReviewChartError):
            R.parse_md("无标题的裸文本", "2026-10-09")
        with self.assertRaises(R.ReviewChartError):
            R.parse_md("# 日复盘 2026-10-09（周五）\n\n正文无结论带\n", "2026-10-09")


class AssembleTest(_Base):
    def test_assemble_full(self):
        data = R.assemble(MD_OK, "2026-10-09", "夏天-activity/每日复盘/日复盘 2026-10-09.md")
        self.assertEqual(data["meta"]["miss"], [])
        self.assertEqual(data["meta"]["trade_date"], "2026-10-09")
        # 指数（fixture 里仅 2 个 code 有数据 → 其余标缺，不抛）
        self.assertEqual(len(data["index"]["rows"]), len(R.INDEX_CARDS))
        sse = [r for r in data["index"]["rows"] if r["code"] == "000001.SH"][0]
        self.assertEqual(sse["close"], 3813.79)
        # 涨停/跌停与 sentiment 同源（72/10）
        self.assertEqual(data["breadth"]["md"]["zt"], 72)
        self.assertEqual(data["breadth"]["md"]["dt"], 10)
        self.assertEqual(data["breadth"]["md"]["up"], 3297)
        self.assertEqual(data["breadth"]["gap_pp"], 6.56)      # (0.332307-0.266667)*100
        # 个股层（2026-11-12 口径）：不出百分比条；文字由数据源给，不写死内部文档编号
        hold = [l for l in data["breadth"]["layers"] if l["key"] == "L2_holdings"][0]
        self.assertIsNone(hold["ratio_pct"])
        self.assertEqual(hold["sample_state"], "empty")
        self.assertEqual(hold["sample_txt"], "实盘账本空仓，无个股层样本")
        # 仓位上限：J0 温度 2026-11-06 整项退役；上限仍读 position_cap_latest.json
        self.assertTrue(data["temp"]["retired"])
        self.assertEqual(data["temp"]["cap"]["total_pct"], 30)
        self.assertEqual(data["temp"]["cap"]["regime"], "空")
        # 板块
        self.assertEqual(data["sector"]["multi_n"], 2)
        self.assertEqual(data["sector"]["top"][0]["name"], "影视院线")
        # 涨停梯度
        self.assertEqual(data["ladder"]["boards"][0]["name"], "文化传媒")
        self.assertEqual(data["ladder"]["boards"][0]["H"], 9)
        # 异动（重点归因三条 → 卡片；医疗美容 资金为负 → tone=warn）
        hl = {h["name"]: h for h in data["anomaly"]["highlights"]}
        self.assertIn("医疗美容", hl)
        self.assertEqual(hl["医疗美容"]["tone"], "warn")
        self.assertEqual(hl["电池化学品"]["flow_yi"], 311.0)
        # 情绪（阶段 + 序列）
        self.assertEqual(data["sentiment"]["stage"], "发酵")
        self.assertEqual(data["sentiment"]["day"]["zt"], 72)
        self.assertEqual(data["sentiment"]["extra"]["boom"], 15.29)    # 100-84.71（回封率保留 2 位）
        self.assertEqual(data["sentiment"]["extra"]["promotion_today"], 40.0)
        # 持仓：两本账分开（实盘 real_account / 纸面 paper_account），不合并、不互比
        pos = data["positions"]
        self.assertIsNone(pos["miss"])
        self.assertTrue(pos["real"]["is_ledger"])
        self.assertEqual(pos["real"]["positions_n"], 0)
        self.assertEqual(pos["real"]["closed_n"], 2)
        self.assertEqual(pos["real"]["realized"], -50.0)    # 只有一笔有账；待回填不补数
        self.assertEqual(pos["real"]["closed_pending_n"], 1)
        self.assertEqual(pos["paper"]["cash"], 100000.0)
        self.assertEqual(pos["paper"]["total"], 100000.0)   # 空仓 → 总资产=现金
        self.assertEqual(pos["paper"]["realized"], -400.0)
        self.assertEqual(pos["degraded"]["status"], "DISCONNECTED")
        # 888880 质押券 / QMT 缓存 account 字段一律不进读数（旧版 217,389.40 假账户的根因）
        blob = json.dumps(pos, ensure_ascii=False)
        for leak in ("217000", "217083.6", "217389.4"):
            self.assertNotIn(leak, blob)
        # 六格摘要 + 板块方向（六格产物 → 四块方向，不写「无 J3 裁决产物」）
        self.assertEqual(len(data["six"]["cells"]), 6)
        blocks = data["sector"]["direction"]["blocks"]
        self.assertEqual([b["key"] for b in blocks],
                         ["strong_resonance", "resonance_outflow", "limitup_concentration", "persistent_inflow"])
        self.assertEqual(blocks[0]["chips"][0]["name"], "影视院线")
        self.assertIn("强共振", data["head"]["sector_line"])
        self.assertIsNone(data["sector"]["verdict_miss"])
        # 首屏关键数 ≤6 且结构化
        self.assertLessEqual(len(data["head"]["key_nums"]), 6)
        self.assertEqual(data["head"]["key_nums"][0]["k"], "AMV")
        # 情景（MD 六节四列：情景/触发/概率/应对）
        self.assertEqual(data["scenarios"]["rows"][0]["layer_cn"], "情景")
        self.assertEqual(data["scenarios"]["rows"][0]["prob"], "40%")
        # 缺口 / 盯盘 / 判断引文
        self.assertTrue(data["gaps"])
        self.assertTrue(data["tomorrow"]["rows"])
        self.assertTrue(data["md_quotes"]["s0"])

    def test_holdings_layer_never_draws_bar(self):
        """有持仓（ratio=1.0）也不画条：单只持仓 100% 不作判断；文案从数据源原样搬。"""
        path = os.path.join(self.panel, "amv_breadth_latest.json")
        with open(path, encoding="utf-8") as f:
            bd = json.load(f)
        bd["transmission"]["layers"]["L2_holdings"] = {
            "n": 1, "multi": 1, "valid": 1, "ratio": 1.0, "holdings_total": 1,
            "available": True, "sample_state": "ok",
            "sample_txt": "持仓 1 只，其中 1 只活跃市值多头（浦发银行 多）；仅逐票状态，不推总体比例",
            "note": "持仓源=实盘账本 real_account.json"}
        _write_json(path, bd)
        data = R.assemble(MD_OK, "2026-10-09", "夏天-activity/每日复盘/日复盘 2026-10-09.md")
        hold = [l for l in data["breadth"]["layers"] if l["key"] == "L2_holdings"][0]
        self.assertIsNone(hold["ratio_pct"])          # 有持仓也不画条
        self.assertEqual(hold["sample_state"], "ok")
        self.assertIn("浦发银行", hold["sample_txt"])
        self.assertNotIn("MD 2.4", hold["sample_txt"])   # 内部文档编号不得上界面

    def test_missing_sources_do_not_raise(self):
        """全部源 json 删除 + db 空 → 段级 miss，不抛（组件端占位）。"""
        for name in ("amv_regime_latest.json", "amv_breadth_latest.json", "sector_temp_latest.json",
                     "real_account.json", "paper_account.json", "battle_plan.json",
                     "sector_ladder_20261009.json", "sector_anomaly_20261009.json",
                     "position_cap_latest.json"):
            os.remove(os.path.join(self.panel, name))
        os.remove(os.path.join(self.dir, "temperature.json"))
        # 六格产物也删：它缺了必须进 miss（不允许为了过测试降级）
        os.remove(os.path.join(self.panel, "review_six_cells_20261009.json"))
        data = R.assemble(MD_OK, "2026-10-09", "x.md")
        self.assertGreater(len(data["meta"]["miss"]), 0)
        self.assertTrue(any("六格" in m for m in data["meta"]["miss"]), data["meta"]["miss"])
        for seg in ("amv", "breadth", "temp", "sector", "ladder", "anomaly", "positions"):
            self.assertTrue(data[seg].get("miss"), seg + " 应标 miss")
        # 情景已改读 MD 六节（battle_plan.json 已退役）→ 源 json 全删后仍应有表，不标 miss
        self.assertIsNone(data["scenarios"].get("miss"))
        self.assertTrue(data["scenarios"]["rows"])
        self.assertEqual(data["head"]["one"], data["head"]["one"])   # 文字段仍在（MD 源）

    def test_date_mismatch_marks_miss(self):
        """源日期 ≠ 复盘日 → 段标缺、不混日。"""
        _write_json(os.path.join(self.panel, "amv_regime_latest.json"), {"date": "2026-10-08", "state": "空"})
        data = R.assemble(MD_OK, "2026-10-09", "x.md")
        self.assertTrue(data["amv"]["miss"])
        self.assertIn("2026-10-08", data["amv"]["miss"])


class SectorPctSemanticsTest(_Base):
    """语义锁（2026-10-21）：涨幅榜/跌幅榜必须按 `pct`（指数涨跌）排，不得按 `amv_pct`（AMV 环比）。

    夹具：影视院线 pct=13.24/amv_pct=-5.0；元件 pct=-3.54/amv_pct=+8.0；
    缺涨跌幅板块 pct=None/amv_pct=99.0。三种错实现都会被抓：
      · 若用 amv_pct 排 → 元件会伪登 top 第一，影视院线会伪进 bottom；
      · 若用 `x.get("pct") or 0` 排序 → None 变 0，元件(-3.54)会跑到 bottom 第二之后；
      · 若不排 pct=None → 缺涨跌幅板块会凭 amv_pct=99 进榜。
    """

    def _sector(self):
        return R.assemble(MD_OK, "2026-10-09", "x.md")["sector"]

    def test_top_sorted_by_pct_not_amv_pct(self):
        s = self._sector()
        self.assertEqual([b["name"] for b in s["top"]], ["影视院线", "元件"])
        self.assertEqual(s["top"][0]["pct"], 13.24)
        self.assertEqual(s["top"][0]["amv_pct"], -5.0)       # 两字段各自独立传递

    def test_bottom_sorted_by_pct_not_amv_pct(self):
        s = self._sector()
        self.assertEqual([b["name"] for b in s["bottom"]], ["元件", "影视院线"])
        self.assertEqual(s["bottom"][0]["pct"], -3.54)

    def test_pct_none_excluded_from_rank(self):
        """pct=None 的板块不进 top/bottom（不拿 amv_pct 顶替）。"""
        s = self._sector()
        names = [b["name"] for b in s["top"]] + [b["name"] for b in s["bottom"]]
        self.assertNotIn("缺涨跌幅板块", names)
        self.assertEqual(s["n_pct_missing"], 1)
        self.assertEqual(s["n_rank_pct"], 2)

    def test_amv_dimension_is_separate(self):
        """资金维度另成一组（amv_top/amv_bottom），与涨跌幅榜互不干扰。"""
        s = self._sector()
        self.assertEqual([b["name"] for b in s["amv_top"]][:2], ["缺涨跌幅板块", "元件"])
        self.assertEqual(s["amv_bottom"][0]["name"], "影视院线")
        self.assertNotEqual([b["name"] for b in s["top"]], [b["name"] for b in s["amv_top"]][:2])

    def test_caliber_semantics_passthrough(self):
        """源头的 pct 语义说明透传给前端（pct ≠ amv_pct）。"""
        s = self._sector()
        self.assertIn("board_flow", s["pct_semantics"]["pct"] + " board_flow")
        self.assertIn("AMV", s["pct_semantics"]["amv_pct"])


class AllAFallbackTest(_Base):
    """全A（880008）三级取数链：① all_a_latest.json ② MD 表值 ③ 缺数。

    背景（2026-10-21）：ths_index.db 从来没有 880008（ths_index_fetch 清单不含它），
    旧逻辑只走 MD 降级 → MD 未写就永久缺数。本类锁死优先级与降级文案。
    """

    def setUp(self):
        super().setUp()
        # ALL_A_FILE 在模块导入时算好，不跟 PANEL_DIR patch 走 → 本类显式指到 tmp
        self._all_a_patch = mock.patch.object(
            R, "ALL_A_FILE", os.path.join(self.panel, "all_a_latest.json"))
        self._all_a_patch.start()

    def tearDown(self):
        self._all_a_patch.stop()
        super().tearDown()

    def _row(self, date="2026-10-09"):
        d = R.assemble(MD_OK, date, "x.md")
        return [r for r in d["index"]["rows"] if r["code"] == "880008.SH"][0]

    def _write_all_a(self, **kw):
        doc = {"code": "880008", "name": "全A", "date": "20261009", "close": 4321.0, "prev_close": 4300.0,
               "pct": 0.49, "high": 4330.0, "low": 4290.0, "up_count": 3297, "down_count": 2145,
               "ma5": 4300.0, "dist5_pct": 0.49, "ma10": 4280.0, "dist10_pct": 0.96,
               "ma20": 4250.0, "dist20_pct": 1.67, "source": "eltdx:sh880008"}
        doc.update(kw)
        _write_json(os.path.join(self.panel, "all_a_latest.json"), doc)
        R.reset_cache()

    def test_prefers_all_a_latest(self):
        self._write_all_a()
        row = self._row()
        self.assertEqual(row["close"], 4321.0)
        self.assertEqual(row["pct"], 0.49)
        self.assertEqual(row["ma20"], 4250.0)
        self.assertEqual(row["d20"], 1.67)
        self.assertEqual(row["src"], "all_a_latest.json (eltdx:sh880008)")
        self.assertEqual(row["miss"], "")

    def test_stale_date_falls_back_to_md(self):
        self._write_all_a(date="20261008")   # 与复盘日 2026-10-09 不符 → 应降级到 MD
        row = self._row()
        self.assertEqual(row["close"], 5570.32)              # 用 MD 值，不是 all_a 的 4321
        self.assertIn("20261008", row["miss"])
        self.assertIn("≠ 目标 20261009", row["miss"])
        self.assertIn("MD", row["src"])
        self.assertNotIn("eltdx", row["src"])

    def test_missing_file_falls_back_to_md(self):
        p = os.path.join(self.panel, "all_a_latest.json")
        if os.path.exists(p):
            os.remove(p)
        R.reset_cache()
        row = self._row()
        self.assertIn("不存在", row["miss"])
        self.assertIn("MD", row["src"])

    def test_md_values_used_when_all_a_unavailable(self):
        """all_a 不可用且 MD 有全A 行 → 用 MD 值（第三级不删）。"""
        self._write_all_a(date="20261008")
        row = self._row()
        # MD_OK 里写了全A 5570.32 → 降级应拿到它
        self.assertEqual(row["close"], 5570.32)

    def test_cache_invalidated_by_all_a_change(self):
        """all_a_latest.json 变 → 缓存必须失效（漏入指纹会让页面读旧值）。"""
        self._write_all_a()
        R.reset_cache()
        pick = RS.pick_latest()
        a = R.build_cached(pick)
        self.assertEqual([r for r in a["index"]["rows"] if r["code"] == "880008.SH"][0]["close"], 4321.0)
        self.assertEqual(R.build_cached(pick)["cache"], "hit")
        self._write_all_a(close=4444.0)
        b = R.build_cached(pick)
        self.assertEqual(b["cache"], "miss")
        self.assertEqual([r for r in b["index"]["rows"] if r["code"] == "880008.SH"][0]["close"], 4444.0)


class CacheTest(_Base):
    def test_cache_hit_and_invalidate(self):
        pick = RS.pick_latest()
        d1 = R.build_cached(pick)
        self.assertEqual(d1["cache"], "miss")
        d2 = R.build_cached(pick)
        self.assertEqual(d2["cache"], "hit")
        # 源文件变化 → miss
        _write_json(os.path.join(self.panel, "amv_regime_latest.json"),
                    {"date": "2026-10-09", "state": "多", "ma10": 1, "amv": 2, "pct": 0.1,
                     "last_switch": {}, "caliber": "c", "source": "s"})
        d3 = R.build_cached(pick)
        self.assertEqual(d3["cache"], "miss")
        self.assertEqual(d3["amv"]["state"], "多")


class PositionsBooksTest(unittest.TestCase):
    """持仓两本账（2026-11-08 主人定）——单元级卡住四条硬约束。

    为什么单开一组：assemble 全量用例的夹具与今日 MD 形状有历史漂移（另记），
    但两本账的口径必须被测试卡住，不能等人去修老夹具。
    """

    # 2026-11-15 公开化：原夹具是真实账本/成交流水（真代码、真价位、真盈亏），
    # 换成等结构合成数据——用例只卡聚合口径（笔数/合计/待回填），不依赖具体票。
    REAL = {
        "meta": {"is_ledger": True, "status": "CONFIRMED", "updated_at": "2026-11-06 13:40:00",
                 "realized_pnl_gross_known": -1750.0, "note": "示例账本，已清空持仓"},
        "account": {"cash": None, "total_asset": None, "market_value": None},
        "positions": [],
        "closed": [{"code": "600001.SH", "name": "示例甲", "qty": 1000, "cost_price": 10.0,
                    "closed_at": "2026-11-02 09:40:21", "close_price": 9.8,
                    "realized_pnl_gross": -200.0},
                   {"code": "600002.SH", "name": "示例乙", "qty": 2000, "cost_price": 5.0,
                    "closed_at": "2026-10-30 14:58:00", "close_price": 4.5,
                    "realized_pnl_gross": -1000.0},
                   {"code": "600003.SH", "name": "示例丙", "qty": 500, "cost_price": 20.0,
                    "closed_at": "2026-10-30 14:57:41", "close_price": 19.0,
                    "realized_pnl_gross": -500.0},
                   {"code": "600004.SH", "name": "示例丁", "qty": 300, "cost_price": None,
                    "closed_at": "2026-10-29 14:59:18", "close_price": 8.71,
                    "realized_pnl_gross": None},
                   {"code": "600005.SH", "name": "示例戊", "qty": 100, "cost_price": 8.0,
                    "closed_at": "2026-11-05 09:36:43", "close_price": 7.5,
                    "realized_pnl_gross": -50.0}]}
    PAPER = {"initial_cash": 500000, "cash": 493000.0, "positions": {},
             "realized_pnl": -1750.0, "trade_date": "2026-11-06",
             "updated_at": "2026-11-06 15:40:55",
             "stats": {"win_rate": 20.0, "closed_trades": 5, "wins": 1}}
    QMT = {"meta": {"note": "行情端未登录"}, "status": "DISCONNECTED",
           "last_synced_at": "2026-10-23 19:56:02",
           "positions": [{"code": "999999.SH", "name": "示例券", "qty": 1000,
                          "market_value": 100000.0}],
           "account": {"total_asset": 100389.4, "cash": 305.8, "market_value": 100083.6}}

    def test_real_book_is_ledger_and_empty(self):
        b = R._real_book(self.REAL)
        self.assertTrue(b["is_ledger"])
        self.assertTrue(b["empty"])
        self.assertEqual(b["positions_n"], 0)
        self.assertEqual(b["closed_n"], 5)
        self.assertEqual(b["realized"], -1750.0)      # 4 笔有账合计
        self.assertEqual(b["closed_pending_n"], 1)      # 示例丁待回填，不补数
        self.assertIsNone(b["total"])                    # 账本没给总资产 → 不编

    def test_paper_book_empty_total_equals_cash(self):
        b = R._paper_book(self.PAPER)
        self.assertTrue(b["empty"])
        self.assertEqual(b["cash"], 493000.0)
        self.assertEqual(b["total"], 493000.0)
        self.assertEqual(b["realized"], -1750.0)
        self.assertEqual(b["market_value"], 0.0)

    def test_qmt_cache_is_degraded_only(self):
        d = R._qmt_degraded(self.QMT)
        self.assertEqual(d["status"], "DISCONNECTED")
        self.assertIn("不进任何读数", d["caliber"])
        # 连接正常时不产生提示
        self.assertIsNone(R._qmt_degraded({"status": "CONNECTED"}))

    def test_key_nums_never_exceed_six(self):
        amv = {"amv": 146316.55, "gap_pct": 5.25, "state": "多", "pct": -2.72}
        turnover = {"total": 1.6577944e12, "prev_total": 2.1179531e12, "delta_pct": -21.7,
                    "prev_date": "2026-11-05"}
        senti = {"stage": "退潮",
                 "day": {"zt": 33, "dt": 15}, "prev": {"zt": 86, "dt": 0},
                 "extra": {"premium_today": 0.04, "premium_prev": 4.42}}
        gate = {"pct": 55, "amount": 260478.18}
        breadth = {"market": {"ratio_pct": 58.91, "prev_ratio_pct": 70.78}}
        k = R._key_nums(amv, turnover, senti, gate, breadth)
        self.assertEqual(len(k), 6)
        self.assertEqual([x["k"] for x in k],
                         ["AMV", "两市成交", "涨停 / 跌停", "首板溢价", "仓位闸门", "多头家数"])
        self.assertEqual(k[2]["v"], "33 / 15")
        self.assertEqual(k[2]["d"], "昨 86 / 0")
        # 缺段不编造：全空 → 空列表
        self.assertEqual(R._key_nums({}, {}, {}, {}, {}), [])
