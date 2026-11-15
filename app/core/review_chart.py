# -*- coding: utf-8 -*-
"""app/core/review_chart.py · 复盘 Tab 图表/卡片模板的服务端数据装配（2026-10-23 迁移）

职责
    把「复盘图表模板」（engine/web/review_chart.js，知夏交付 v2 集成版）所需的 RV_DATA 同构对象，
    从工作区现有唯一数据源【只读】装配出来，供 engine/server.py 的 /api/review_chart.json 端点调用。

设计初心（为什么这么写）
    · 生产不得依赖知夏的静态快照 复盘_data.js——装配逻辑必须在服务端复刻（任务书 §一），
      本模块 = _build_data.py（预览装配）的生产版：同字段契约、同口径、只读。
    · 「同一口径只说一次」：所有数值一律取自源（json/db/MD 原文摘录），本模块只做字段搬运与
      显示格式化，不新建第二套口径；MD 专有文字段（head/news/md_quotes/tomorrow/gate/gaps）
      由本模块从复盘 MD 抽取（仍是一份事实源，不手抄进代码）。
    · 日期对齐：面板 json/db 是「最新日」文件，复盘 MD 可能是更早交易日——装配前逐源核对
      源内日期 == 复盘日，不齐的段标缺（不混日、不编造）；缺段汇总进 meta.miss，页面顶部提示。
    · 缓存：按 (MD 指纹, 各源文件指纹) LRU 缓存；前端 60s 轮询稳态零重算（指纹变化即失效）。

数据源与口径（全部只读；对应 复盘_组件说明.md §二 字段契约）
    · 指数/成交额/结构位：eltdx 指数日线（`quotes.index_daily_bars`，2026-11-01 路由换源；
    MA20/走势/距MA20/MA 现算）。ths_index.db（同花顺口径）不再作指数数据源——同花顺只留给 .TI 板块。
    · AMV 大盘：data/panel/amv_regime_latest.json
    · 宽度/三层传导：data/panel/amv_breadth_latest.json
    · 温度：夏天-activity/工具/temperature_data/temperature_latest.json（结构化四维/仓位上限）
      + 复盘 MD 0.2（环比读数；MD 表值作为四维原值展示，避免浮点舍入与原文不一致）
    · 板块：data/panel/sector_temp_latest.json（L2 boards 现算 TOP/BOTTOM/增配/减配/多头）
      **两字段语义分离（2026-10-21）**：top/bottom 排序用 `pct`（THS 板块指数当日涨跌·价）；
      `amv_pct`（板块 AMV 当日环比·钱）只作展示补充。pct 缺失的板块不进 top/bottom（不拿 amv_pct 顶替）。
    · 涨停梯度：data/panel/sector_ladder_{YYYYMMDD}.json（按复盘日取，缺文件即标缺）
    · 异动：data/panel/sector_anomaly_{YYYYMMDD}.json（按复盘日取）
    · 情绪：data/track_pool.db::sentiment（ts='close'）+ 复盘 MD 三节（补充指标/退潮核对/观察点）
    · 消息/判断/盯盘/闸门/缺口/持仓文字段：复盘 MD 抽取（原文摘录，仅去 {r} 色标与 ** 加粗）
    · 持仓数值：data/panel/real_positions.json（量/成本/市值/账户）+ 复盘 MD 4.1（收盘/锚/状态）
    · 情景：data/panel/battle_plan.json::scenarios（layer 英文键 → 中文层名映射在本模块单点定义）

上下游
    上游：review 插件 `/api/review_chart.json`（薄壳：pick_latest → build_cached）。
    下游：engine/web/index.html::renderReviewChart → window.RVC.render（review_chart.js 组件库）。
    单测：engine/tests/test_review_chart_data.py（monkeypatch 本模块常量注入 tmp 夹具，不触生产）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
from collections import OrderedDict

# paths 相对导入（与 engine/common 下其他模块同款；server.py 已把 engine 加入 sys.path）
# 2026-10-23 迁移：从旧 engine/common/review_chart_data.py 逐字搬运，
# 只把路径依赖改到 panel-v2 core（算法/字段/缓存键一行未改）。
from app.core.paths import DATA_DIR, PANEL_DIR, SUMMER_DIR, SUMMER_TOOLS_DIR, WS as WORKSPACE  # noqa: E402
from app.core.paths import READABLE_JSON  # noqa: E402 - stock_names 名称→代码补全（仅 ladder leader_code）

# ---------------- 常量：数据源路径（测试可 monkeypatch 本模块同名属性） ----------------
TEMPERATURE_FILE = os.path.join(SUMMER_TOOLS_DIR, "temperature_data", "temperature_latest.json")
TRACK_DB = os.path.join(DATA_DIR, "track_pool.db")

# ---------------- 2026-11-08 图表视图修整：持仓两本账 + 温度退役 ----------------
# 主人 2026-11-08 定：
#   · 实盘唯一账本 = data/panel/real_account.json（is_ledger=true，positions/closed）
#   · 纸面唯一账本 = engine/state/paper_account.json（cash/positions/realized_pnl）
#   · data/panel/real_positions.json 是 QMT 柜台缓存（is_ledger=false），**只作 degraded 连接提示，
#     不进任何读数**；其中 888880.SH 新标准券是质押券，任何情况下不计入市值/总资产。
#   · 两本账互不校验、互不比较（差额正常，不是超配）。
REAL_ACCOUNT_FILE = os.path.join(PANEL_DIR, "real_account.json")
PAPER_ACCOUNT_FILE = os.path.join(WORKSPACE, "engine", "state", "paper_account.json")
QMT_CACHE_FILE = os.path.join(PANEL_DIR, "real_positions.json")
# J0 市场温度自 2026-11-06 整项退役（源 temperature_latest.json 已删除、方法论废弃）。
# 退役后本模块不再做「源日 ≠ 复盘日」检查，也不显示温度读数；freshness 里只标「不适用」。
TEMP_RETIRED_AT = "2026-11-06"
# 2026-11-01 路由换源：指数日线走 eltdx（quotes.index_daily_bars），ths_index.db 退役为
# 板块（.TI）专用；INDEX_DB 常量保留仅作测试兼容（review_chart 不再读它）。
# 全A（880008）专用产物：ths_index.db 从来没有 880008（ths_index_fetch 清单不含它），
# 旧逻辑只能从 MD 抠数 → MD 没写就永久缺数。现由 fupan_all_a.py 走 eltdx:sh880008 现取。
ALL_A_FILE = os.path.join(PANEL_DIR, "all_a_latest.json")

# 指数卡网格（顺序=展示顺序；880008 全A 未落库 → 单独降级路径）
INDEX_CARDS = [
    ("880008.SH", "全A"), ("000001.SH", "上证"), ("399001.SZ", "深成"), ("399006.SZ", "创业板"),
    ("000688.SH", "科创50"), ("000300.SH", "沪深300"), ("000905.SH", "中证500"), ("000852.SH", "中证1000"),
]
# 三层传导层名（口径唯一源：仅此一处定义；组件端只消费 label 字段）
LAYER_LABEL = {
    "L0_market": "大盘 · 多头家数", "L1_sector_L2": "板块 L2", "L1_sector_L3": "板块 L3",
    "L1_sector_CONCEPT": "板块 CONCEPT", "L2_holdings": "个股 · 持仓样本",
}
# 情景 layer 英文键 → 中文层名（schema 枚举 discipline/news/selection/sentiment；market 为防御性映射）
SCENARIO_LAYER_CN = {"discipline": "纪律", "sentiment": "情绪", "selection": "选股", "news": "消息", "market": "大盘"}
# 简报档默认展开的情景层（知夏模板口径：纪律/情绪展开，其余折叠）
SCENARIO_OPEN_DEFAULT = ["纪律", "情绪"]
# 四维温度「回指」文案（原则③：同一口径只说一次；按维度名单点映射）
TEMP_DIM_REF = {"估值": "唯此卡有数", "量能": "成交额见明细卡", "趋势": "距MA20 见全A卡", "情绪": "宽度见上条"}

CACHE_MAX = 4
_cache_lock = threading.Lock()
_cache: "OrderedDict[tuple, dict]" = OrderedDict()


class ReviewChartError(Exception):
    """复盘图表装配失败（MD 缺失关键结构 / 源读取异常）——调用方降级到纸面路径。"""


# ============================================================ 通用小工具
def _rnd(v, n=2):
    return None if v is None else round(float(v), n)


def _num(v):
    """任意值 → float | None（'−' 负号归一，千分位逗号剔除）。"""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("−", "-").strip())
    except (TypeError, ValueError):
        return None


def _strip_marks(s):
    """MD 展示清洗：{r}{g}{y}{x} 色标、** 加粗、` 反引号 → 去除（不改写语句）。"""
    if not s:
        return ""
    s = re.sub(r"\{[rgyx]\}", "", str(s))
    s = s.replace("**", "").replace("`", "")
    return s.strip()


def _read_json(path):
    """只读 json；缺失/损坏 → None（调用方按段降级，不抛）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _ro_connect(db_path):
    """SQLite 只读连接（uri ro 模式，物理上排除写路径）。"""
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def _stat_sig(path):
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, -1, -1)


# ---------- 显示格式化（只做排版，不改口径；负数统一用 − 号与 MD 一致） ----------
def _th(v, n=2):
    """千分位；None → '—'。"""
    if v is None:
        return "—"
    return f"{float(v):,.{n}f}"


def _sg(v, n=2, unit=""):
    """带符号数字：+1.23 / −1.23；None → '—'。"""
    if v is None:
        return "—"
    v = float(v)
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return f"{sign}{abs(v):.{n}f}{unit}"


def _sg_th(v, n=2):
    """带符号 + 千分位（金额用）：−10,966.11 / +1,234.00；None → '—'。"""
    if v is None:
        return "—"
    v = float(v)
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return f"{sign}{abs(v):,.{n}f}"


def _int(v):
    """家数类整数显示：86.0 → '86'；None → '—'。"""
    return "—" if v is None else str(int(round(float(v))))


def _md_short(d):
    """'2026-11-05' → '11/5'（显示用；不认的格式原样返回）。"""
    m = re.match(r"^(?:\d{4}-)?(\d{1,2})-(\d{1,2})$", str(d or ""))
    return f"{int(m.group(1))}/{int(m.group(2))}" if m else (str(d or "—"))


# ============================================================ MD 抽取
# 设计：逐节正则扫描；缺失字段一律 None/[]，绝不编造。所有文本仅去色标与加粗标记。
def _find_line(md, prefix):
    """返回首个以 prefix 开头的行（整行，含换行前内容）；无 → None。"""
    for ln in md.split("\n"):
        if ln.startswith(prefix):
            return ln
    return None


def _after_colon(line, key=None):
    """取「：」后文本；key 给定则要求该行含 key（防误配）。"""
    if not line:
        return None
    if key and key not in line:
        return None
    i = line.find("：")
    return _strip_marks(line[i + 1:]) if i >= 0 else None


def _section(md, start_pat, end_pat):
    """截取 [start_pat 命中后, end_pat 首次命中前) 的文本；缺失 → ''。"""
    m = re.search(start_pat, md, re.M)
    if not m:
        return ""
    s = m.end()
    e = re.search(end_pat, md[s:], re.M)
    return md[s:s + e.start()] if e else md[s:]


def _table_rows(block):
    """md 表格行 → [[cell,…],…]（跳过表头分隔行；cell 已 strip 色标/加粗）。"""
    rows = []
    for ln in block.split("\n"):
        t = ln.strip()
        if not t.startswith("|") or not t.endswith("|"):
            continue
        cells = [c.strip() for c in t.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells if c is not None):
            continue
        if any(c for c in cells):
            rows.append([_strip_marks(c) for c in cells])
    return rows


def _bullets(block):
    """markdown 无序列表项文本（去 '- ' 前缀与色标/加粗）。"""
    out = []
    for ln in block.split("\n"):
        t = ln.strip()
        if t.startswith("- ") and not t.startswith("- ["):
            out.append(_strip_marks(t[2:]))
    return out


# ---------------- 2026-11-05 新增解析：判断按节 / 三问 / 闸门 / 情景 / 政策链 / 持仓表 ----------------
_QUOTE_PREFIXES = ("我的判断：", "我的判断:", "判断：", "判断:", "一句话归因：", "一句话归因:")


def _quotes_by_section(md):
    """全部 `> 💡` 判断行 → [{key,s_no,s_title,sub,kind,text,full}]（按出现顺序，key=s0..sn）。

    · 节归属按最近的 `## ` 标题判定、子节按最近的 `### ` 标题（不写死节号顺序，历史 MD 有变体）；
    · text = 判断的第一段（首屏用）；full = 整块引用（展开用，段落间以空格连接）；
    · 兼容三种写法：`**我的判断**：xxx` / `**我的判断：xxx**` / `**判断：xxx**`。
    """
    lines = md.split("\n")
    heads, subs = [], []
    for i, ln in enumerate(lines):
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            t = _strip_marks(m.group(1))
            m2 = re.match(r"^([〇一二三四五六七八九十]+)、\s*(.+)$", t)
            heads.append((i, m2.group(1), m2.group(2)) if m2 else (i, "", t))
            continue
        m = re.match(r"^###\s+(.+?)\s*$", ln)
        if m:
            subs.append((i, _strip_marks(m.group(1))))

    def _last_head(i):
        no, ti, idx = "", "", -1
        for j, n2, t2 in heads:
            if j >= i:
                break
            no, ti, idx = n2, t2, j
        return no, ti, idx

    def _last_sub(i, head_idx):
        """只取本节范围内的 `###` 子标题（跨节的旧 `###` 不能当本节 sub）。"""
        s = ""
        for idx, t2 in subs:
            if idx >= i:
                break
            if idx > head_idx:
                s = t2
        return s

    out = []
    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("> 💡"):
            continue
        head = _strip_marks(ln.lstrip()[1:].strip()).lstrip("💡").strip()
        kind = "我的判断"
        for p in _QUOTE_PREFIXES:
            if head.startswith(p):
                kind, head = p.rstrip(":："), head[len(p):]
                break
        buf = [head.strip()] if head.strip() else []
        j = i + 1
        while j < len(lines) and lines[j].startswith(">"):
            body = _strip_marks(lines[j][1:].strip())
            if body.startswith("💡"):
                break
            if not body:
                if buf and buf[-1] != "":
                    buf.append("")
            elif buf and buf[-1] == "":
                buf.append(body)
            elif buf:
                buf[-1] += body          # 引用块内折行：中文直接拼接
            else:
                buf.append(body)
            j += 1
        paras = [p for p in buf if p]
        if not paras:
            continue
        no, ti, hidx = _last_head(i)
        out.append({"key": "", "s_no": no or "附录", "s_title": ti,
                    "sub": _last_sub(i, hidx), "kind": kind,
                    "text": paras[0], "full": " ".join(paras)})
    # 只有「我的判断」占 s0..s7 主键（主人点名的那 8 处）；其余 💡（判断/一句话归因）编 n0..
    k = k2 = 0
    for q in out:
        if q["kind"] == "我的判断":
            q["key"], q["primary"] = "s%d" % k, True
            k += 1
        else:
            q["key"], q["primary"] = "n%d" % k2, False
            k2 += 1
    return out


def _parse_questions(md):
    """首节「三问作答」表格 → [{n,q,a,ev,ledger}]（列：# / 问题 / 答案 / 证据 / 台账）。"""
    sec = _section(md, r"^## [^\n]*三问", r"^## ")
    rows = []
    for r in _table_rows(sec):
        if len(r) >= 4 and re.fullmatch(r"\d+", (r[0] or "").strip()):
            rows.append({"n": int(r[0]), "q": r[1], "a": r[2], "ev": r[3],
                         "ledger": r[4] if len(r) > 4 else ""})
    return rows


def _parse_gate_factors(s0):
    """MD 0.1「仓位闸门的三个因子」表 → (rows[{k,v,fix}], final{pct,amount,raw})。"""
    rows, final = [], {}
    for r in _table_rows(_section(s0, r"### 0\.1", r"### 0\.2")):
        if len(r) < 3 or (r[0] or "").strip() == "因子":
            continue
        k, v, fix = (r[0] or "").strip(), r[1], r[2]
        if k == "最终":
            raw = fix or ""
            mm = re.search(r"([\d.]+)\s*%", raw)
            final = {"pct": _num(mm.group(1)) if mm else None, "amount": None, "raw": raw}
            mm = re.search(r"([\d,]+\.?\d*)\s*元", raw)
            if mm:
                final["amount"] = _num(mm.group(1))
            continue
        rows.append({"k": k, "v": v, "fix": fix or "—"})
    return rows, final


def _parse_scenarios(md):
    """MD 情景-应对表 → [{id,trigger,prob,prob_pct,action,layer_cn}]（四列：情景/触发/概率/应对）。"""
    sec = _section(md, r"^## [^\n]*情景", r"^## ")
    rows = []
    for r in _table_rows(sec):
        if len(r) < 4 or "%" not in (r[2] or ""):
            continue
        rows.append({"id": r[0], "trigger": r[1], "prob": r[2],
                     "prob_pct": _num(str(r[2]).replace("%", "")),
                     "action": r[3], "layer_cn": "情景"})
    return rows


def _parse_policy_chain(md):
    """MD 消息面节「政策链」条目 → [{t,m,v,tone,src}]（政策 → 受益/受损链 → 资金验证）。"""
    sec = _section(md, r"^## [^\n]*消息面", r"^## ")
    rows, cur = [], None
    for ln in sec.split("\n"):
        t = ln.strip()
        if t.startswith("- ") and "受益链" in t:
            body = _strip_marks(t[2:]).strip()
            head, _, rest = body.partition("→")
            benefit, hurt = rest.strip(), ""
            mm = re.search(r"受损链[：:]\s*(.+)$", rest)
            if mm:
                hurt = mm.group(1).strip()
                benefit = rest[:mm.start()].strip()
            benefit = re.sub(r"^受益链[：:]\s*", "", benefit).strip()
            cur = {"t": head.strip(), "m": ("受益：" + benefit if benefit else "")
                   + ("｜受损：" + hurt if hurt else ""),
                   "v": "", "tone": "neutral", "src": "md:§三"}
            rows.append(cur)
            continue
        if t.startswith("→") and cur is not None and not cur.get("v") and "资金" in t:
            body = re.sub(r"^资金验证[：:]\s*", "", _strip_marks(t[1:]).strip())
            cur["v"] = ("资金：" + body) if body else ""
            if ("没认账" in body) or ("不认" in body) or ("只有价" in body) or ("情绪有" in body):
                cur["tone"] = "warn"
            elif "认账" in body:
                cur["tone"] = "pos"
            elif "微弱" in body:
                cur["tone"] = "warn"
    return rows


def _parse_pos_table(s4):
    """MD 四节「表 1 持仓」→ [{book,name,code,qty,cost,close,dist_pct,pct,anchor,anchor_label,note}]。

    今日表头：账本 / 标的 / 数量 / 成本 / 收盘 / 相对成本 / 今日 / 备注（8 列）。
    关注池表只有 6 列且首列无 6 位代码 → 天然排除。
    """
    rows = []
    for r in _table_rows(s4):
        if len(r) < 8:
            continue
        mm = re.match(r"^(?:(.*?)\s*)?(\d{6})(?:\s+(.*))?$", (r[1] or "").strip())
        if not mm:
            continue
        name = (mm.group(1) or mm.group(3) or "").strip() or mm.group(2)
        code = mm.group(2)
        note = r[7] or ""
        anchor, anchor_label = None, "无锚"
        ma = re.search(r"最近档?\s*([A-Za-z_]+)\s*([\d.]+)\s*（([+\-−][\d.]+)%）", note)
        if ma:
            anchor_label, anchor = ma.group(1), _num(ma.group(2))
        rows.append({"book": r[0], "name": name, "code": code,
                     "qty": _num(r[2]), "cost": _num(r[3]), "close": _num(r[4]),
                     "dist_pct": _num(str(r[5]).rstrip("%")) if r[5] not in ("", "—") else None,
                     "pct": _num(str(r[6]).rstrip("%")) if r[6] not in ("", "—") else None,
                     "anchor": anchor, "anchor_label": anchor_label, "note": note})
    return rows


def _parse_manual_summary(s4):
    """MD 四节「手工单合计市值 … 成本 … 浮亏 …」→ {mv,cost,pl,pl_pct,raw}。"""
    for ln in s4.split("\n"):
        if "手工单合计" not in ln:
            continue
        t = _strip_marks(ln).lstrip("> ").strip()
        out = {"raw": t}
        mm = re.search(r"市值\s*([\d,]+)\s*元", t)
        if mm:
            out["mv"] = _num(mm.group(1))
        mm = re.search(r"成本\s*([\d,]+\.?\d*)", t)
        if mm:
            out["cost"] = _num(mm.group(1))
        mm = re.search(r"浮亏\s*([−\-]?[\d,]+)\s*元（([−\-]?[\d.]+)%）", t)
        if mm:
            out["pl"] = _num(mm.group(1))
            out["pl_pct"] = _num(mm.group(2))
        return out
    return None


def _temp_prev_from_history(date):
    """历史 J0 温度前值：temperature_data/history.json 或 temperature_YYYYMMDD.json 族。

    都找不到 → None（调用方写 miss 说明「差在哪、去哪补」，不在本模块重算第二套温度口径）。
    """
    base = os.path.join(SUMMER_TOOLS_DIR, "temperature_data")
    hist = _read_json(os.path.join(base, "history.json"))
    if isinstance(hist, list):
        cand = [x for x in hist if str((x or {}).get("date") or "") < date
                and (x or {}).get("temperature") is not None]
        if cand:
            cand.sort(key=lambda x: str(x.get("date")))
            last = cand[-1]
            return {"date": str(last.get("date")), "value": _num(last.get("temperature")),
                    "src": "temperature_data/history.json"}
    try:
        names = os.listdir(base)
    except OSError:
        return None
    best = None
    for n in names:
        mm = re.match(r"^temperature_(\d{8})\.json$", n)
        if not mm:
            continue
        d = "%s-%s-%s" % (mm.group(1)[:4], mm.group(1)[4:6], mm.group(1)[6:])
        if d < date and (best is None or d > best[0]):
            best = (d, os.path.join(base, n))
    if best:
        doc = _read_json(best[1]) or {}
        return {"date": best[0], "value": _num(doc.get("temperature")),
                "src": "temperature_data/" + os.path.basename(best[1])}
    return None


def parse_md(md_text: str, date: str) -> dict:
    """复盘 MD → 文字段字典（head/quotes/news/tomorrow/gate/gaps/positions/板块/情绪 等）。

    date = 复盘交易日（YYYY-MM-DD，来自文件名，权威）；H1 缺失 → ReviewChartError（视为损坏）。
    """
    md = (md_text or "").replace("\r\n", "\n").replace("\r", "\n")
    m = re.search(r"^# 日复盘 (\d{4}-\d{2}-\d{2})（(.+)）\s*$", md, re.M)
    if not m:
        raise ReviewChartError("复盘 MD 缺少标准标题行（# 日复盘 YYYY-MM-DD（…））")
    h1_date, h1_rest = m.group(1), m.group(2)
    parts = [p.strip() for p in h1_rest.split("·")]
    weekday = parts[0] if parts else ""
    title = " · ".join(parts[1:]) if len(parts) > 1 else ""

    out: dict = {"date": h1_date, "weekday": weekday, "title": title}

    # ---- 抬头（🎯/📌/🚨 + 数据时点/口径/温度/备注） ----
    out["one"] = _after_colon(_find_line(md, "> 🎯"), "🎯") or ""
    three_line = _find_line(md, "> 📌")
    three_body = _after_colon(three_line, "📌") or ""
    items = [x.strip().strip("；;。") for x in re.split(r"[①②③④⑤]", three_body) if x.strip()]
    out["three"] = items
    out["next"] = _after_colon(_find_line(md, "> 🚨"), "🚨") or ""
    out["caliber"] = _after_colon(_find_line(md, "> 口径"), "口径") or ""
    out["temp_line"] = _after_colon(_find_line(md, "> 温度"), "温度") or ""
    out["exec_note"] = _after_colon(_find_line(md, "> 备注"), "备注") or ""
    if not (out["one"] or out["three"]):
        raise ReviewChartError("复盘 MD 缺少 🎯/📌 结论带（视为损坏）")

    # ---- 「我的判断」按节解析（2026-11-05：当天 8 处全抽，s0..s7；不再只取第一节） ----
    quote_rows = _quotes_by_section(md)
    out["quote_rows"] = quote_rows
    quotes = [q["text"] for q in quote_rows if q["kind"] == "我的判断"]
    out["quotes"] = quotes
    # 首屏「关键数」行（MD 〇节 **关键数**：…）；顺带抽「三问作答」表
    key_line = _find_line(md, "**关键数**：") or _find_line(md, "**关键数**:")
    out["key_nums"] = ([x.strip() for x in re.split(r"｜", _after_colon(_strip_marks(key_line)) or "") if x.strip()]
                       if key_line else [])
    out["questions"] = _parse_questions(md)

    # ---- §〇 0.1/0.2/0.3/0.4 ----
    s0 = _section(md, r"^## 〇、", r"^## 一、")
    mm = re.search(r"第 (\d+) 个交易日", s0)
    out["switch_days"] = int(mm.group(1)) if mm else None
    mm = re.search(r"温度较\s*([\d/]+)（(\d+)°C）\s*\*?\*?回升\s*(\d+)\s*度", s0)
    out["temp_prev"] = int(mm.group(2)) if mm else None
    out["temp_delta"] = int(mm.group(3)) if mm else None
    out["temp_prev_disp"] = mm.group(1) if mm else None
    out["temp_note"] = None
    for ln in s0.split("\n"):
        if ln.strip().startswith("- 温度较") or ln.strip().startswith("- **温度较"):
            out["temp_note"] = _strip_marks(ln.strip().lstrip("- ").strip())
            break
    # 四维表（| 维度 | 数值 | 得分 |）
    dims = []
    for r in _table_rows(_section(s0, r"### 0\.2", r"### 0\.3")):
        if len(r) >= 3 and r[0] in ("估值", "量能", "趋势", "情绪") and re.fullmatch(r"\d+", r[2] or ""):
            dims.append({"k": r[0], "v": r[1], "score": int(r[2])})
    out["temp_dims"] = dims
    # 指数表：全A 行（880008 未落库 → close/pct 从 MD 取）
    for r in _table_rows(_section(s0, r"### 0\.3", r"### 0\.4")):
        if r and r[0] == "全A" and len(r) >= 3:
            out["allA_close"] = _num(r[1])
            out["allA_pct"] = _num(r[2].rstrip("%"))
            break
    # V 反句：10:00 宽度 / 11:30 盘中低点
    s03 = _section(s0, r"### 0\.3", r"### 0\.4")
    mm = re.search(r"10:00 的涨\s*(\d+)/跌\s*(\d+)", s03)
    out["intraday_1000"] = (int(mm.group(1)), int(mm.group(2))) if mm else None
    mm = re.search(r"11:30 全A\s*([−\-][\d.]+)%、科创50 盘中最低\s*([−\-][\d.]+)%、创业板\s*([−\-][\d.]+)%", s03)
    out["intraday_1130"] = tuple(_num(x) for x in mm.groups()) if mm else None
    mm = re.search(r"）\s*，(.+?)；", s03)
    out["intraday_tail"] = _strip_marks(mm.group(1)) if mm else None
    # 0.4 结构位
    s04 = _section(s0, r"### 0\.4", r"^## 一、")
    mm = re.search(r"上方压力\s*([\d,]+)\s*/\s*([\d,]+)，下方支撑\s*([\d,]+)\s*/\s*([\d,]+)", s04)
    out["levels_sse"] = [_num(x) for x in mm.groups()] if mm else None
    out["level_note"] = None
    for ln in s04.split("\n"):
        if "没有一条均线被收复" in ln:
            out["level_note"] = "八指数全部仍在 MA20 下方，没有一条均线被收复 → 反弹是「深跌后的技术修复」"
            break

    # ---- §一 消息面（1.1 表 / 1.2 表 / 1.3 驱动） ----
    s1 = _section(md, r"^## 一、", r"^## 二、")
    pre = []
    for r in _table_rows(_section(s1, r"### 1\.1", r"### 1\.2")):
        if len(r) >= 3 and r[0] != "消息面":
            v = r[2]
            tone = ("pos" if "✅" in v else "neg" if "🔴" in v else
                    "warn" if "🟡" in v else "neutral")
            pre.append({"t": r[0], "m": r[1], "v": v, "tone": tone})
    out["news_pre"] = pre
    inc = []
    for r in _table_rows(_section(s1, r"### 1\.2", r"### 1\.3")):
        if len(r) >= 3 and r[0] != "增量消息":
            a = r[2]
            has_g, has_r = "🟢" in a, "🔴" in a
            tone = "mixed" if (has_g and has_r) else "pos" if has_g else "neg" if has_r else "neutral"
            inc.append({"t": r[0], "b": r[1], "a": a, "tone": tone})
    out["news_inc"] = inc
    s13 = _section(s1, r"### 1\.3", r"^## 二、")
    driver = {"head": "", "rows": [], "net": [], "mutual": [], "src": []}
    for ln in s13.split("\n"):
        t = ln.strip()
        mm = re.fullmatch(r"\*\*(.+?)\*\*", t)
        if mm and not driver["head"]:
            driver["head"] = _strip_marks(mm.group(1))
            continue
        mm = re.fullmatch(r"(\d+)\.\s*\*\*(.+?)\*\*：(.+)", t)
        if mm:
            driver["rows"].append({"n": mm.group(1), "k": _strip_marks(mm.group(2)), "d": _strip_marks(mm.group(3))})
            continue
        mm = re.fullmatch(r"- (🟢|🔴|🟡|⚠)\s*(.+?)[：:](.+)", t)
        if mm:
            tone = {"🟢": "pos", "🔴": "neg", "🟡": "warn", "⚠": "danger"}[mm.group(1)]
            driver["net"].append({"tone": tone, "k": _strip_marks(mm.group(2)), "v": _strip_marks(mm.group(3))})
            continue
    # 消息层互验：§六 尾部「### 消息层互验」段（不是 §1.3；§1.3 无此块）
    s6 = _section(md, r"### 消息层互验", r"^## 七、")
    driver["mutual"] = _bullets(s6)
    # 源清单：1.2 块引用里的 news_increment 路径 + MD 自身
    mm = re.search(r"(OH-Works/[^\s`）]+\.md)", s1)
    if mm:
        driver["src"].append(mm.group(1))
    out["news_driver"] = driver

    # ---- §二 板块（2.1 多头 / 2.2 梯队 / 2.3 异动 / 我的判断①②③） ----
    s2 = _section(md, r"^## 二、", r"^## 三、")
    mm = re.search(r"多头板块扩至\s*(\d+)\s*个（([\d/]+)\s*为\s*(\d+)\s*个）", s2)
    out["sector_multi_n"] = int(mm.group(1)) if mm else None
    out["sector_multi_prev_n"] = int(mm.group(3)) if mm else None
    out["sector_multi_prev_disp"] = mm.group(2) if mm else None
    out["sector_multi_note"] = None
    for ln in s2.split("\n"):
        if "多头板块扩至" in ln and ln.strip().startswith(">"):
            out["sector_multi_note"] = "MD 2.1：" + _strip_marks(ln.strip().lstrip("> ").replace("结构性特征：", ""))
            break
    # 2.2 电池链 + 塔型判读
    mm = re.search(r"（([^（）]*?电池[^（）]*?)）合计\s*(\d+)\s*家", s2)
    out["ladder_chain"] = None
    if mm:
        members = [x.strip() for x in mm.group(1).split("+") if x.strip()]
        out["ladder_chain"] = {"members": members, "sum": int(mm.group(2))}
    mm = re.search(r"是\*?\*?唯一有跨板块梯队的方向\*?\*?（(.+?)）", s2)
    out["ladder_chain_note"] = ("MD 2.2：唯一有跨板块梯队的方向（" + mm.group(1) + "）") if mm else None
    out["ladder_md_verdict"] = None
    for ln in s2.split("\n"):
        if "塔型判读" in ln:
            out["ladder_md_verdict"] = _after_colon(ln, "塔型判读")
            break
    # 2.3 重点归因 → 异动卡（名称/金额走 anomalies 源核对；标签=原文）
    s23 = _section(s2, r"### 2\.3", r"### 2\.4")
    out["anomaly_notes_raw"] = []
    for ln in s23.split("\n"):
        if "重点归因" in ln:
            body = _after_colon(ln, "重点归因") or ""
            for seg in body.split("；"):
                seg = seg.strip().strip("。")
                if seg:
                    out["anomaly_notes_raw"].append(seg)
            break
    out["anomaly_flow_summary"] = None
    for ln in s23.split("\n"):
        if "资金主线" in ln:
            body = ln.split("资金主线", 1)[1]
            i = body.find("：")
            if i >= 0:
                out["anomaly_flow_summary"] = "MD 2.3：资金主线 = " + _strip_marks(body[i + 1:]).strip("。")
            break
    # 板块节判断（按节归属取，不按出现序号猜）→ 结构判读三句（有 ①②③ 分段时才拆）
    # 只有判断里真的分了 ①②③ 才拆成裁决行；整段陈述不硬拆（避免与 judgeBlock 重复同一段字）
    q2 = next((q["text"] for q in quote_rows if q["kind"] == "我的判断" and q["s_no"] == "二"), "")
    if q2 and "①" in q2:
        m1 = re.search(r"①", q2)
        segs = [x.strip().strip("；;。") for x in re.split(r"[①②③④]", q2[m1.start():] if m1 else q2) if x.strip()]
        verdict = []
        for seg in segs:
            # 标题 = 首逗号前整段去掉括号注解（保信息在正文）；正文 = 原文整段（MD 摘录不删字）
            head = seg.split("，")[0]
            t = re.sub(r"（[^）]*）", "", head).strip()
            if len(t) > 26:
                t = t[:24] + "…"
            if not t or t == seg:
                continue
            verdict.append({"t": t, "d": seg})
            if len(verdict) >= 3:
                break
        out["sector_verdict"] = verdict
    else:
        out["sector_verdict"] = []

    # ---- §三 情绪 ----
    s3 = _section(md, r"^## 三、", r"^## 四、")
    mm = re.search(r"连板晋级率\s*[\d\-]+\s*\*?\*?降至\s*(\d+)%", s3)
    out["promotion_today"] = _num(mm.group(1)) if mm else None
    out["promotion_note"] = "高度断层扩大" if "高度断层在扩大" in s3 else None
    out["premium_miss"] = ("缺数：" + date[5:].replace("-", "-") + " 溢价率脚本未落库（MD 缺口 4）"
                           if "溢价率" in s3 and "未落库" in s3 else None)
    unseal = []
    for ln in s3.split("\n"):
        t = ln.strip()
        if t.startswith("- ❌") or t.startswith("- ✅"):
            hit = t.startswith("- ✅")
            body = _strip_marks(t[3:].strip())
            mm = re.match(r"^(.*?)（(.+)）$", body)
            item, detail = (mm.group(1), mm.group(2)) if mm else (body, "")
            unseal.append({"item": item, "hit": hit, "detail": detail})
    out["unseal"] = unseal
    watch = []
    w_block = _section(s3, r"\*\*次日观察点\*\*", r"^> ")
    for ln in w_block.split("\n"):
        mm = re.match(r"^\d+\.\s*(.+)$", ln.strip())
        if mm:
            watch.append(_strip_marks(mm.group(1)))
    out["watch"] = watch
    out["quality_warn"] = None
    for ln in s3.split("\n"):
        if "攻守含义" in ln:
            out["quality_warn"] = _after_colon(ln, "攻守含义")
            break

    # ---- §四 持仓（2026-11-05：表头改 账本/标的/数量/成本/收盘/相对成本/今日/备注，持仓表在 4.1 之前） ----
    s4 = _section(md, r"^## 四、", r"^## 五、")
    s41 = _section(s4, r"### 4\.1", r"### 4\.2")
    out["pos_rows"] = _parse_pos_table(s4)
    out["manual_summary"] = _parse_manual_summary(s4)
    # 账户行（旧版 MD 才有「总资产…日变动」行；无 → None，账户改由 real_positions 供）
    acc_line = None
    for ln in s41.split("\n"):
        if "总资产" in ln and "日变动" in ln:
            acc_line = ln
            break
    out["account"] = None
    if acc_line:
        day_delta = day_delta_pct = None
        mm = re.search(r"日变动\s*\*?\*?([−\-][\d,]+\.\d+)", acc_line)
        if mm:
            day_delta = _num(mm.group(1))
        mm = re.search(r"日变动[^（]*（\*?\*?([−\-][\d.]+)%", acc_line)
        if mm:
            day_delta_pct = _num(mm.group(1))
        mm = re.search(r"——\s*\*?\*?(.+?)\*?\*?（主因", acc_line)
        note = _strip_marks(mm.group(1)) if mm else ""
        mm = re.search(r"（主因\s*([^）]+)）", acc_line)
        if mm:
            note = (note + "（主因 " + mm.group(1) + "）") if note else ("主因 " + mm.group(1))
        out["account"] = {"day_delta": day_delta, "day_delta_pct": day_delta_pct, "note": note}
    # 当日成交
    out["trade_today"] = None
    for ln in s41.split("\n"):
        if "当日成交" in ln and ln.strip().startswith("-"):
            mm = re.search(r"：(.+?)\s+(\d{6})\s*\*?\*?(BUY|SELL)\s*([\d,]+)\s*@([\d.]+)\*?\*?（(\d{2}:\d{2}:\d{2})）——(.+)",
                           ln)
            if mm:
                out["trade_today"] = {"name": mm.group(1), "code": mm.group(2), "side": mm.group(3),
                                      "qty": int(mm.group(4).replace(",", "")), "price": _num(mm.group(5)),
                                      "time": mm.group(6), "note": _strip_marks(mm.group(7))}
            break
    # 持仓板块对照
    out["pos_sector"] = {}
    for ln in s41.split("\n"):
        if "持仓板块对照" in ln:
            for mm in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9]+)（([^（）]+)）", ln):
                key, val = mm.group(1), mm.group(2)
                if key in ("持仓板块对照",):
                    continue
                val = val.replace("，pct ", " ").replace("／state ", "／")
                out["pos_sector"][key] = _strip_marks(val)
            break
    out["risk"] = None
    for ln in s41.split("\n"):
        if "风险提示" in ln and ln.strip().startswith(">"):
            out["risk"] = _after_colon(ln, "风险提示")
            break

    # ---- §4.2 准备买 / 三层闸门 ----
    s42 = _section(s4, r"### 4\.2", r"^## 五、")
    gate_rows = []
    for ln in s42.split("\n"):
        mm = re.match(r"^-\s*\*\*(J\d[^*]*)\*\*：(.+)$", ln.strip())
        if mm:
            k, body = _strip_marks(mm.group(1)), _strip_marks(mm.group(2))
            v, detail = (body.split("——", 1) + [""])[:2] if "——" in body else (body, "")
            tone = "danger" if ("⚠" in ln or "不可用" in v) else "none"
            gate_rows.append({"k": k, "v": v.strip(), "detail": detail.strip(), "tone": tone})
    out["gate_rows"] = gate_rows
    gates = []
    for ln in s42.split("\n"):
        mm = re.match(r"^-\s*\*\*(大盘层|板块层|个股层)\*\*：(.+)$", ln.strip())
        if not mm:
            continue
        layer, body = mm.group(1), _strip_marks(mm.group(2))
        mm2 = re.search(r"\*\*(红|黄|绿)\*\*", ln)
        lamp = mm2.group(1) if mm2 else "—"
        head = body.split("→")[0].strip().rstrip("→ ").strip()
        # 原则③：闸门不重复已在别处唯一呈现的数值（温度/占比）——数值一律回指
        head = re.sub(r"[\d.]+°C\s*", "", head)                      # 温度读数 → 只留「温度」
        head = re.sub(r"温度\s+温和", "温度温和", head)
        if layer == "板块层":
            head = re.sub(r"L2 多头\s*[\d.]+%", "L2 截面多头占比", head)
            head = re.sub(r"（低于大盘）", "低于大盘", head)
            ptr = " → 数值见三层传导"
        elif layer == "大盘层":
            ptr = " → 数值见大盘块"
        else:
            ptr = ""
        detail = (head + ptr).strip()
        if not detail:
            detail = body
        gates.append({"layer": layer, "lamp": lamp, "detail": detail})
    out["gates"] = gates
    out["gate_verdict"] = None
    for ln in s42.split("\n"):
        if "无个股层标的" in ln:
            body = ln.strip().lstrip("- ").strip().lstrip("→ ").strip()
            out["gate_verdict"] = _strip_marks(body)
            break

    # ---- §五 明日盯盘 ----
    s5 = _section(md, r"^## 五、", r"^## 六、")
    tomorrow = []
    cur = None
    for ln in s5.split("\n"):
        t = ln.strip()
        mm = re.match(r"^(\d+)\.\s*(?:[⭐★✱*]\s*)?\*\*(.+?)\*\*：?(.*)$", t)
        if mm:
            cur = {"p": int(mm.group(1)), "k": _strip_marks(mm.group(2)), "v": _strip_marks(mm.group(3)), "subs": []}
            tomorrow.append(cur)
            continue
        mm = re.match(r"^-\s+(.+)$", t)
        if mm and cur is not None:
            cur["subs"].append(_strip_marks(mm.group(1)))
    for it in tomorrow:
        if it["subs"]:
            it["v"] = "；".join(it["subs"]) if not it["v"] else it["v"] + "；" + "；".join(it["subs"])
        kk = it["k"]
        if "风险" in kk:
            it["tone"] = "danger"
        elif "催化" in kk:
            it["tone"] = "pos"
        elif any(x in kk for x in ("持续", "延续", "确认", "观察", "处置")):
            it["tone"] = "warn" if "处置" not in kk else "danger"
        else:
            it["tone"] = "neutral"
    out["tomorrow"] = tomorrow

    # ---- §七 缺口（未勾选项） ----
    s7 = _section(md, r"^## 七、", r"^\*产物")
    gaps = []
    for ln in s7.split("\n"):
        t = ln.strip()
        mm = re.match(r"^- \[ \]\s*(?:[⚠⏳]\s*)?\*\*(.+?)\*\*：(.+)$", t)
        if mm:
            k = re.sub(r"（[^）]*）", "", mm.group(1)).strip()
            gaps.append({"k": k, "v": _strip_marks(mm.group(2))})
    out["gaps"] = gaps

    # ---- 消息面时间戳（1.1/1.2 标题） ----
    mm = re.search(r"asof\s*([\d:]+)", s1)
    out["news_asof_pre"] = mm.group(1) if mm else None
    mm = re.search(r"盘后 → 复盘执行时点\s*([\d\-x:]+)", s1)
    out["news_asof_inc"] = mm.group(1) if mm else None

    # ---- 2026-11-05 新增：0.1 闸门因子 / 情景表 / 政策链 ----
    out["gate_factors"], out["gate_final"] = _parse_gate_factors(s0)
    out["scenarios"] = _parse_scenarios(md)
    out["policy_chain"] = _parse_policy_chain(md)
    return out


# ============================================================ 数据段装配
def _sentiment_rows(date8):
    """sentiment 表（ts='close'）→ [row dict]（升序）；表缺失 → []。"""
    try:
        con = _ro_connect(TRACK_DB)
    except sqlite3.Error:
        return []
    try:
        cur = con.cursor()
        cur.execute("""select trade_date, stage, stage_note, zt_total, dt_total, height, lb2plus,
                       up_cnt, down_cnt, reseal_rate, premium, promotion_2b, promotion_3b, promotion_4b
                       from sentiment where ts='close' order by trade_date""")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _index_series(code, date8, n=20):
    """指数近 n 根日线（升序 [(date,o,h,l,c,turnover)]）+ 末行日期（'YYYY-MM-DD'）；缺失 → ([], None)。

    2026-11-01 路由换源（主人定：指数走 eltdx，同花顺只留给 .TI 板块）：
    原读 ths_index.db::index_daily（THS 口径 + 自算兜底行混在真实行里），现走
    `quotes.index_daily_bars()`（eltdx 直连，不复权）。返回形状与旧实现逐字段同构，
    调用点零改动；`date8` 过滤语义保持（只取 ≤ 复盘日的根，不混未来日）。
    """
    rows = _index_series_cached(code, max(int(n), 1))
    rows = [r for r in rows if r[0] <= str(date8)]
    if not rows:
        return [], None
    return rows[-int(n):], rows[-1][0]


def _load_all_a(date8):
    """全A（880008）最新产物 → (doc 或 None, 走降级的原因)。

    优先级：data/panel/all_a_latest.json（fupan_all_a.py 产，eltdx:sh880008 直取）；
    缺失/日期不等/无 close → (None, 原因)，调用方继续走原有 MD 降级。
    日期不等不静默接受（不混日），原因由调用方写进 miss。
    """
    if not os.path.exists(ALL_A_FILE):
        return None, f"all_a_latest.json 不存在（{os.path.basename(ALL_A_FILE)}）"
    try:
        with open(ALL_A_FILE, "r", encoding="utf-8-sig") as f:   # utf-8-sig：容忍别人写入的 BOM
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"all_a_latest.json 读取失败：{e}"
    src_date = str(doc.get("date") or "")
    if src_date != date8:
        return None, f"all_a_latest.json 日期 {src_date or '无'} ≠ 目标 {date8}"
    if doc.get("close") is None:
        return None, "all_a_latest.json 无 close"
    return doc, None


_idx_series_cache: "OrderedDict[tuple, list]" = OrderedDict()
_idx_series_cache_lock = threading.Lock()
_IDX_SERIES_TTL_S = 60.0          # eltdx 已有 60s 缓存；这里再挡同分钟内重复调用
_IDX_SERIES_MAX_KEY = 64


def _index_series_cached(code, n):
    """_index_series 的 60s 进程内缓存（键含 n；复盘同一次 build 会对同一码多次调用）。"""
    from datetime import datetime as _dt  # noqa: PLC0415
    key = (str(code), int(n), _dt.now().strftime("%Y%m%d%H%M"))
    with _idx_series_cache_lock:
        hit = _idx_series_cache.get(key)
        if hit is not None:
            _idx_series_cache.move_to_end(key)
            return hit
    from app.core import quotes as _q  # noqa: PLC0415 - 懒引入防环
    bars = _q.index_daily_bars(code, int(n))
    rows = [(b["date8"], b["o"], b["h"], b["l"], b["c"], b.get("amount")) for b in bars]
    with _idx_series_cache_lock:
        _idx_series_cache[key] = rows
        _idx_series_cache.move_to_end(key)
        while len(_idx_series_cache) > _IDX_SERIES_MAX_KEY:
            _idx_series_cache.popitem(last=False)
    return rows


def _prev_trade_date(date8):
    """复盘日的上一交易日（YYYYMMDD）——eltdx 交易日历（口径唯一源 common.trading_calendar 同链）；
    不可用 → 上证日线序列推算；再不可用 → None。"""
    try:
        from common.trading_calendar import get_trading_dates  # noqa: PLC0415
        from datetime import datetime as _dt, timedelta as _td  # noqa: PLC0415
        d = _dt.strptime(str(date8), "%Y%m%d").date()
        days = [x.replace("-", "") for x in
                get_trading_dates((d - _td(days=30)).strftime("%Y%m%d"), str(date8))]
        earlier = [x for x in days if x < str(date8)]
        if earlier:
            return earlier[-1]
    except Exception:  # noqa: BLE001 - 日历不可用 → 序列推算
        pass
    series, _ = _index_series("000001.SH", date8, 2)
    if len(series) >= 2:
        return series[-2][0]
    return None


def _dash_date(date8):
    """'20261008' → '2026-10-08'。"""
    return f"{date8[:4]}-{date8[4:6]}-{date8[6:8]}" if date8 and len(date8) == 8 else None


def _prev_date_from_disp(date, disp):
    """MD 里的前值日期显示（如 '10/8'）→ 'YYYY-MM-DD'（年份取复盘日年份；解析失败 → None）。"""
    if not disp:
        return None
    m = re.match(r"(\d{1,2})\s*/\s*(\d{1,2})", str(disp))
    if not m:
        return None
    return f"{date[:4]}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def _seg_miss(label, src_date, review_date):
    return f"{label}：源日 {src_date or '未知'} ≠ 复盘日 {review_date}"


# ============================================================ 持仓：两本账（2026-11-08 主人定）
# 硬约束（写在函数里，防以后又改回一本）：
#   ① 实盘只读 real_account.json；纸面只读 paper_account.json；两本不合并成一张表；
#   ② 两本账互不校验、互不比较（纸面 vs 实盘差额是正常的，不是超配）；
#   ③ real_positions.json 是 QMT 柜台缓存（is_ledger=false），只作 degraded 提示，不进读数；
#   ④ 888880.SH 新标准券是质押券，任何情况下不进总资产/市值（本模块不读它的市值字段）。
def _closed_rows(doc):
    """平仓明细（实盘账本 closed[]）→ 展示行；realized 为空 = 待回填（不补数）。"""
    rows = []
    for c in (doc.get("closed") or []):
        if not isinstance(c, dict):
            continue
        rows.append({
            "code": str(c.get("code") or "").split(".")[0],
            "name": c.get("name") or "",
            "qty": _num(c.get("qty_sold") if c.get("qty_sold") is not None else c.get("qty")),
            "cost": _rnd(_num(c.get("cost_price")), 3),
            "close": _rnd(_num(c.get("close_price")), 3),
            "closed_at": str(c.get("closed_at") or ""),
            "realized": _rnd(_num(c.get("realized_pnl_gross")), 2),
            "note": _strip_marks(c.get("note") or ""),
        })
    return rows


def _real_book(doc):
    """实盘 = real_account.json（唯一账本）：持仓 + 已实现 + 平仓明细。"""
    meta = doc.get("meta") or {}
    closed = _closed_rows(doc)
    done = [c for c in closed if c["realized"] is not None]
    open_rows = [p for p in (doc.get("positions") or [])
                 if isinstance(p, dict) and (_num(p.get("qty")) or 0) > 0]
    acct = doc.get("account") or {}
    return {
        "book": "实盘",
        "ledger": "data/panel/real_account.json",
        "status": meta.get("status") or "",
        "as_of": meta.get("updated_at") or meta.get("confirmed_at") or "",
        "is_ledger": bool(meta.get("is_ledger")),
        "positions_n": len(open_rows),
        "empty": not open_rows,
        "rows": [{"code": str(p.get("code") or "").split(".")[0], "name": p.get("name") or "",
                  "qty": _num(p.get("qty")), "cost": _rnd(_num(p.get("cost_price")), 3)}
                 for p in open_rows],
        "closed": closed,
        "closed_n": len(closed),
        "realized": _rnd(sum(c["realized"] for c in done), 2) if done else None,
        "realized_n": len(done),
        "closed_pending_n": len(closed) - len(done),
        "realized_meta": meta.get("realized_pnl_gross_known"),
        "realized_scope": _strip_marks(meta.get("realized_scope") or ""),
        # 账本里 cash/total_asset 为空是常态（不据此编数）
        "cash": _rnd(_num(acct.get("cash")), 2),
        "total": _rnd(_num(acct.get("total_asset")), 2),
        "note": _strip_marks(meta.get("note") or ""),
        "source": "data/panel/real_account.json（实盘唯一账本 is_ledger=true）",
    }


def _paper_book(doc):
    """纸面 = paper_account.json（唯一账本）：空仓时总资产=现金（不引第二套估值）。"""
    pos = doc.get("positions")
    if isinstance(pos, dict):
        n_pos = len([k for k, v in pos.items() if isinstance(v, dict) and (_num(v.get("qty")) or 0) > 0])
    else:
        n_pos = len([p for p in (pos or []) if isinstance(p, dict) and (_num(p.get("qty")) or 0) > 0])
    cash = _rnd(_num(doc.get("cash")), 2)
    init = _rnd(_num(doc.get("initial_cash")), 2)
    realized = _rnd(_num(doc.get("realized_pnl")), 2)
    stats = doc.get("stats") or {}
    return {
        "book": "纸面",
        "ledger": "engine/state/paper_account.json",
        "positions_n": n_pos,
        "empty": n_pos == 0,
        "cash": cash,
        "market_value": 0.0 if n_pos == 0 else None,
        "total": cash if n_pos == 0 else None,      # 有持仓时不重算总资产（不引第二套估值口径）
        "initial_cash": init,
        "realized": realized,
        "realized_pct": _rnd(realized / init * 100, 2) if (realized is not None and init) else None,
        "trade_date": doc.get("trade_date"),
        "updated_at": doc.get("updated_at"),
        "win_rate": stats.get("win_rate"),
        "closed_trades": stats.get("closed_trades"),
        "wins": stats.get("wins"),
        "source": "engine/state/paper_account.json（纸面账本；与实盘互不校验、互不比较）",
    }


def _qmt_degraded(doc):
    """QMT 柜台缓存 → 只给连接提示（不进读数；888880 不计入市值/总资产）。"""
    if not isinstance(doc, dict):
        return None
    status = doc.get("status") or ""
    if status == "CONNECTED":
        return None
    note = _strip_marks((doc.get("meta") or {}).get("note") or "")
    return {
        "status": status or "UNKNOWN",
        "last_synced_at": doc.get("last_synced_at"),
        "note": note,
        "caliber": ("QMT 柜台缓存（data/panel/real_positions.json，is_ledger=false）只作连接提示，不进任何读数："
                    "888880.SH 新标准券是质押券，不计入市值/总资产"),
    }


def _sector_direction(doc, date8):
    """六格产物 → 板块方向四块（强共振 / 共振流出 / 涨停集中 / 持续吸金）。

    只搬运 chips/basis/action，不重算任何量（一个指标一个 owner：六格消费者=生产者产物）。
    换手残差（§8.6）在本产物里只以文字形式出现在各格 action 中，没有独立数值字段 → 不另造。
    """
    if not isinstance(doc, dict):
        return None

    def cell(key):
        for c in (doc.get("cells") or []):
            if isinstance(c, dict) and c.get("key") == key:
                return c
        return None

    def blk(key, title):
        c = cell(key)
        if not c:
            return None
        chips = [{"name": x.get("name"), "metric": x.get("metric"), "state": x.get("state")}
                 for x in (c.get("chips") or [])[:3]]
        return {"key": key, "title": c.get("title") or title, "n": c.get("n_matched"),
                "chips": chips, "empty": not chips,
                "basis": _strip_marks(c.get("basis") or ""),
                "action": _strip_marks(c.get("action") or ""),
                "action_status": c.get("action_status")}

    blocks = [b for b in (blk("strong_resonance", "趋势确认·强共振"),
                          blk("resonance_outflow", "风险最高·共振流出"),
                          blk("limitup_concentration", "情绪焦点·涨停集中"),
                          blk("persistent_inflow", "重点配置·持续吸金")) if b]
    if not blocks:
        return None
    lu = cell("limitup_concentration") or {}
    for b in blocks:
        if b["key"] == "limitup_concentration":
            b["n"] = lu.get("total_limitup")
            b["hhi"] = lu.get("hhi")
            b["hhi_ma5"] = lu.get("hhi_ma5")
            b["top3_sum"] = lu.get("top3_sum")
            b["top3_share"] = _rnd((lu.get("top3_share") or 0) * 100, 1) if lu.get("top3_share") is not None else None
            b["first_board_ratio"] = _rnd((lu.get("first_board_ratio") or 0) * 100, 1) if lu.get("first_board_ratio") is not None else None
            b["max_height"] = lu.get("max_height")
            b["coverage"] = lu.get("coverage")
    line_parts = []
    by = {b["key"]: b for b in blocks}
    sr, ro = by.get("strong_resonance"), by.get("resonance_outflow")
    if sr and sr["chips"]:
        c0 = sr["chips"][0]
        tail = (" " + c0["metric"]) if c0.get("metric") else ""
        line_parts.append(f"强共振 {sr['n']} 个（{c0['name']}{tail}）")
    if ro and ro["chips"]:
        line_parts.append(f"共振流出 {ro['n']} 个（{ro['chips'][0]['name']} {ro['chips'][0]['metric']} 最弱）")
    lu_b = by.get("limitup_concentration")
    if lu_b and lu_b.get("hhi") is not None:
        line_parts.append(f"涨停 {lu_b['n']} 家、前三 {lu_b.get('top3_sum')} 家占 {lu_b.get('top3_share')}%，HHI {lu_b['hhi']:.4f}")
    pi = by.get("persistent_inflow")
    if pi and pi["chips"]:
        line_parts.append(f"持续吸金 {pi['n']} 个（{pi['chips'][0]['name']} {pi['chips'][0]['metric']}）")
    return {
        "date": (doc.get("meta") or {}).get("target_date"),
        "blocks": blocks,
        "line": ("｜".join(line_parts) if line_parts else ""),
        "source": (f"data/panel/review_six_cells_{date8}.json（六格产物 chips/basis/action 原样搬运，不重算；"
                   f"换手残差 §8.6 仅以文字出现在各格动作里，无独立数值字段）"),
    }


def _key_nums(amv, turnover, sentiment, gate, breadth):
    """首屏关键数（≤6 个，主人 2026-11-08 定）：AMV／成交／涨跌停／闸门／多头家数／首板溢价。

    数值全部搬自同一批段对象（不在本函数里重算），只做显示格式化。
    """
    out = []
    if amv and not amv.get("miss") and amv.get("amv") is not None:
        out.append({"k": "AMV", "v": f"{_th(amv.get('amv'), 0)} 亿",
                    "d": f"距 MA10 {_sg(amv.get('gap_pct'), 2, '%')} · {amv.get('state') or '—'}"})
    if turnover and not turnover.get("miss") and turnover.get("total") is not None:
        out.append({"k": "两市成交", "v": f"{_th((turnover.get('total') or 0) / 1e8, 0)} 亿",
                    "d": f"{_sg(turnover.get('delta_pct'), 1, '%')} vs {_md_short(turnover.get('prev_date'))}"})
    if sentiment and sentiment.get("day"):
        d, p = sentiment["day"], (sentiment.get("prev") or {})
        if d.get("zt") is not None or d.get("dt") is not None:
            out.append({"k": "涨停 / 跌停", "v": f"{_int(d.get('zt'))} / {_int(d.get('dt'))}",
                        "d": f"昨 {_int(p.get('zt'))} / {_int(p.get('dt'))}"})
        ex = sentiment.get("extra") or {}
        if ex.get("premium_today") is not None:
            out.append({"k": "首板溢价", "v": _sg(ex.get("premium_today"), 2, "%"),
                        "d": f"昨 {_sg(ex.get('premium_prev'), 2, '%')}"})
    if gate and gate.get("pct") is not None:
        out.append({"k": "仓位闸门", "v": f"{int(gate.get('pct'))}%",
                    "d": (f"上限 {_th(gate.get('amount'), 0)} 元" if gate.get("amount") is not None else "—")})
    mk = ((breadth or {}).get("market") or {})
    if mk.get("ratio_pct") is not None:
        pp = None
        if mk.get("prev_ratio_pct") is not None:
            pp = _rnd(mk["ratio_pct"] - mk["prev_ratio_pct"], 2)
        out.append({"k": "多头家数", "v": f"{mk.get('ratio_pct')}%",
                    "d": (f"{_sg(pp, 2, 'pp')} vs 昨" if pp is not None else "—")})
    return out[:6]


def _chart_lines(amv, turnover, sentiment, idx_rows, sector, positions):
    """图表结论句（一个图配一句：图给形态、话给判断）。全部由段对象现成数字拼出，不新增口径。"""
    lines = {}
    if amv and not amv.get("miss") and amv.get("state"):
        lines["amv"] = (f"AMV {_th(amv.get('amv'), 0)} 亿环比 {_sg(amv.get('pct'), 2, '%')}，"
                        f"站在 MA10 上方 {_sg(amv.get('gap_pct'), 2, '%')}——{amv.get('state')}档"
                        + (f"第 {amv.get('switch_days')} 天" if amv.get("switch_days") else ""))
    idx = [r for r in (idx_rows or []) if r.get("pct") is not None]
    if idx:
        up = [r for r in idx if r["pct"] > 0]
        dn = [r for r in idx if r["pct"] < 0]
        worst = min(idx, key=lambda r: r["pct"])
        head = (f"{len(idx)} 个指数全跌" if not up else f"{len(idx)} 个指数 {len(dn)} 跌 {len(up)} 涨")
        lines["index"] = f"{head}，{worst.get('name')} {_sg(worst.get('pct'), 2, '%')} 最弱"
    if sentiment and sentiment.get("day"):
        d, p, ex = sentiment["day"], (sentiment.get("prev") or {}), (sentiment.get("extra") or {})
        seg = [f"涨停 {_int(p.get('zt'))}→{_int(d.get('zt'))}",
               f"跌停 {_int(p.get('dt'))}→{_int(d.get('dt'))}"]
        if ex.get("premium_prev") is not None and ex.get("premium_today") is not None:
            seg.append(f"首板溢价 {_sg(ex.get('premium_prev'), 2, '%')}→{_sg(ex.get('premium_today'), 2, '%')}")
        lines["sentiment"] = f"{('、'.join(seg))}——阶段「{sentiment.get('stage') or '未判定'}」"
    if turnover and not turnover.get("miss") and turnover.get("delta_pct") is not None:
        word = "放量" if turnover["delta_pct"] > 0 else ("缩量" if turnover["delta_pct"] < 0 else "持平")
        lines["turnover"] = f"两市成交 {_th((turnover.get('total') or 0) / 1e8, 0)} 亿，{word} {_sg(turnover.get('delta_pct'), 1, '%')}"
    direction = (sector or {}).get("direction")
    if direction and direction.get("line"):
        lines["sector"] = direction["line"]
    sec = sector or {}
    top, bot = (sec.get("top") or []), (sec.get("bottom") or [])
    if top and top[0].get("pct") is not None:
        lines["sector_pct"] = (f"领涨 {top[0].get('name')} {_sg(top[0].get('pct'), 2, '%')}，"
                               f"领跌 {bot[0].get('name') if bot else '—'} {_sg(bot[0].get('pct') if bot else None, 2, '%')}")
    add, cut = (sec.get("add") or []), (sec.get("cut") or [])
    if add and add[0].get("wd_pp") is not None:
        lines["sector_wd"] = (f"权重Δ(N=5) 最大 {add[0].get('name')} {_sg(add[0].get('wd_pp'), 3, 'pp')}，"
                              f"最小 {cut[0].get('name') if cut else '—'} {_sg(cut[0].get('wd_pp') if cut else None, 3, 'pp')}"
                              "——AMV 份额迁移（活跃度相对变化，非资金）")
    amv_top, amv_bot = (sec.get("amv_top") or []), (sec.get("amv_bottom") or [])
    if amv_top and amv_top[0].get("amv_pct") is not None:
        lines["sector_amv"] = (f"AMV 环比最高 {amv_top[0].get('name')} {_sg(amv_top[0].get('amv_pct'), 2, '%')}，"
                               f"最低 {amv_bot[0].get('name') if amv_bot else '—'} "
                               f"{_sg(amv_bot[0].get('amv_pct') if amv_bot else None, 2, '%')}（价格×换手合成量，非资金）")
    real, paper = (positions or {}).get("real"), (positions or {}).get("paper")
    if real or paper:
        seg = []
        if real:
            seg.append("实盘" + ("空仓" if real.get("empty") else f"持仓 {real.get('positions_n')} 只")
                       + (f"、已实现 {_sg_th(real.get('realized'))}" if real.get("realized") is not None else ""))
        if paper:
            seg.append("纸面" + ("空仓" if paper.get("empty") else f"持仓 {paper.get('positions_n')} 只")
                       + (f"、现金 {_th(paper.get('cash'), 2)}" if paper.get("cash") is not None else "")
                       + (f"、已实现 {_sg_th(paper.get('realized'))}" if paper.get("realized") is not None else ""))
        lines["books"] = "；".join(seg)
    return lines



def assemble(md_text: str, date: str, md_rel: str) -> dict:
    """主装配：MD 文字段 + 各唯一源（json/db）→ RV_DATA 同构 dict（字段契约见组件说明 §二）。"""
    date8 = date.replace("-", "")
    M = parse_md(md_text, date)
    miss = []           # 数据源未对齐段（页面顶部提示）
    freshness = []      # 本次渲染实际读取（收尾自查卡）

    # ---------- 指数 ----------
    idx_rows = []
    idx_db_ok = False
    for code, name in INDEX_CARDS:
        series, last_date = _index_series(code, date8)
        if not series:
            if code == "880008.SH":     # 全A：① all_a_latest.json（eltdx） → ② MD 表值降级
                aa, aa_why = _load_all_a(date8)
                if aa:
                    # 字段契约同其他指数行（close/pct/high/low/ma20/d20/spark），
                    # 距离用 eltdx 原算的 dist20_pct（与其他卡的现算口径同义：距 MA20）
                    idx_rows.append({
                        "code": code, "name": name,
                        "close": _rnd(aa.get("close"), 2), "pct": _rnd(aa.get("pct"), 2),
                        "high": _rnd(aa.get("high"), 2), "low": _rnd(aa.get("low"), 2),
                        "ma20": _rnd(aa.get("ma20"), 2), "d20": _rnd(aa.get("dist20_pct"), 2),
                        "spark": ([_rnd(c, 2) for c in aa.get("spark")]
                                  if isinstance(aa.get("spark"), list) else []),
                        "ma20_db": _rnd(aa.get("ma20"), 2),
                        "miss": "",
                        "note": ("MA5 %s / MA10 %s / MA20 %s（eltdx 现算）；涨跌家数 %s/%s" % (
                            _rnd(aa.get("ma5"), 2), _rnd(aa.get("ma10"), 2), _rnd(aa.get("ma20"), 2),
                            aa.get("up_count"), aa.get("down_count"))) if aa.get("ma5") is not None else None,
                        "src": "all_a_latest.json (eltdx:sh880008)",
                    })
                    freshness.append(f"全A = {date8} ✓（all_a_latest.json / eltdx:sh880008）")
                    continue
                d20 = None
                ma20 = None
                mm = re.search(r"([\d,.]+) vs MA20 ([\d,.]+)（([−\-][\d.]+)%）", md_text)
                if mm:
                    ma20, d20 = _num(mm.group(2)), _num(mm.group(3))
                idx_rows.append({
                    "code": code, "name": name, "close": M.get("allA_close"), "pct": M.get("allA_pct"),
                    "high": None, "low": None, "ma20": ma20, "d20": d20, "spark": [], "ma20_db": None,
                    "miss": (aa_why + "；已回退 MD 表值（close/pct）；无近 20 收盘序列（走势缺数）"),
                    "note": "距 MA20 取 MD 0.2 趋势维；收盘为 MD 0.3 表值，两者不同指数口径，未换算",
                    "src": "MD 0.2 / 0.3（all_a_latest.json 不可用）",
                })
                freshness.append(f"全A = MD 降级 ⚠（{aa_why}）")
                continue
            idx_rows.append({"code": code, "name": name, "close": None, "pct": None, "high": None,
                             "low": None, "ma20": None, "d20": None, "spark": [], "ma20_db": None,
                             "miss": "该指数当日无数据", "note": None, "src": "eltdx:指数日线"})
            continue
        if last_date != date8:
            # 该指数序列未覆盖复盘日（陈旧）→ 标缺不混日
            idx_rows.append({"code": code, "name": name, "close": None, "pct": None, "high": None,
                             "low": None, "ma20": None, "d20": None, "spark": [], "ma20_db": None,
                             "miss": f"eltdx 最新 {last_date} ≠ 复盘日 {date8}", "note": None, "src": "eltdx:指数日线"})
            continue
        idx_db_ok = True
        closes = [r[4] for r in series]
        close = closes[-1]
        prev = closes[-2] if len(closes) > 1 else None
        ma20 = (sum(closes[-20:]) / 20) if len(closes) >= 20 else None
        note = None
        if code == "000001.SH":
            ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
            if ma5 and ma10 and ma20:
                note = (f"结构位：MA5 {ma5:,.0f} / MA10 {ma10:,.0f} / MA20 {ma20:,.0f}；"
                        f"盘中低 {series[-1][3]:,.2f}（MD 0.4）")
        idx_rows.append({
            "code": code, "name": name, "close": _rnd(close, 2),
            "pct": _rnd((close / prev - 1) * 100, 2) if prev else None,
            "high": _rnd(series[-1][2], 2), "low": _rnd(series[-1][3], 2),
            "ma20": _rnd(ma20, 2), "d20": _rnd((close / ma20 - 1) * 100, 2) if ma20 else None,
            "spark": [_rnd(c, 2) for c in closes[-20:]], "ma20_db": _rnd(ma20, 2),
            "miss": "" if len(closes) >= 20 else "序列不足 20 根", "note": note, "src": "eltdx:指数日线",
        })
    if not idx_db_ok:
        miss.append(f"指数卡片：eltdx 指数日线未覆盖复盘日 {date}（或全部缺数）")
    # 结构位（上证）：MA 现算 + MD 0.4 支撑压力原文
    sse = next((r for r in idx_rows if r["code"] == "000001.SH" and r["close"]), None)
    levels = None
    if sse:
        series = _index_series("000001.SH", date8, 20)[0]
        cl = [r[4] for r in series]
        lv = {"close": sse["close"],
              "ma5": _rnd(sum(cl[-5:]) / 5, 2) if len(cl) >= 5 else None,
              "ma10": _rnd(sum(cl[-10:]) / 10, 2) if len(cl) >= 10 else None,
              "ma20": _rnd(sum(cl[-20:]) / 20, 2) if len(cl) >= 20 else None}
        md_lv = M.get("levels_sse")
        if md_lv:
            lv["res"] = [md_lv[0], md_lv[1]]
            lv["sup"] = [md_lv[2], md_lv[3]]
        else:
            lv["res"] = [round(x) for x in (lv["ma5"], lv["ma10"]) if x]
            lv["sup"] = []
        if not lv["sup"]:
            lv["sup_miss"] = "支撑位：MD 0.4 未给下方支撑，现算口径只出 MA5/MA10 作压力，支撑不凭空造数"
        levels = {"sse": lv}

    # ---------- AMV 大盘 ----------
    reg = _read_json(os.path.join(PANEL_DIR, "amv_regime_latest.json"))
    amv = None
    if reg and reg.get("date") == date:
        sw = reg.get("last_switch") or {}
        days = M.get("switch_days")
        note = (f"MD 0.1：最近一次切换 {sw.get('date')} 转{sw.get('dir')}·{sw.get('mark')}"
                f"（trigger {sw.get('trigger')}），至今未变"
                + (f"（第 {days} 个交易日）" if days else ""))
        amv = {"date": reg.get("date"), "state": reg.get("state"), "resonance": reg.get("resonance"),
               "ma10": reg.get("ma10"), "amv": reg.get("amv"), "pct": reg.get("pct"),
               "last_switch": sw, "switch_days": days,
               "gap_pct": _rnd((reg["amv"] / reg["ma10"] - 1) * 100, 2) if reg.get("amv") and reg.get("ma10") else None,
               "caliber": reg.get("caliber"), "source": reg.get("source"), "md_note": note, "miss": None}
        freshness.append(f"AMV 大盘 = {reg.get('date')} ✅")
    else:
        amv = {"miss": _seg_miss("AMV 大盘", (reg or {}).get("date"), date)}
        miss.append(amv["miss"])
        freshness.append(f"AMV 大盘 ⚠ 未对齐（{amv['miss']}）")

    # ---------- 宽度 / 三层传导 ----------
    bd = _read_json(os.path.join(PANEL_DIR, "amv_breadth_latest.json"))
    srows = _sentiment_rows(date8)
    today = next((r for r in srows if r["trade_date"] == date8), None)
    yday = None
    if today:
        prior = [r for r in srows if r["trade_date"] < date8]
        yday = prior[-1] if prior else None
    breadth = None
    if bd and bd.get("date") == date:
        mk = bd.get("market") or {}
        l2 = (bd.get("sector_level") or {}).get("L2") or {}
        ser = mk.get("series") or []
        l2ser = l2.get("series") or []

        def _pts(arr, n=30):
            return [{"d": x["date"][5:], "r": _rnd(x["ratio"] * 100, 2)} for x in arr[-n:]]
        prev_pt = ser[-2] if len(ser) >= 2 else None
        prev_l2 = l2ser[-2] if len(l2ser) >= 2 else None
        gap_pp = _rnd((mk.get("ratio", 0) - l2.get("ratio", 0)) * 100, 2)
        prev_gap = (_rnd((prev_pt["ratio"] - prev_l2["ratio"]) * 100, 2)
                    if prev_pt and prev_l2 else None)
        layers = []
        for k, v in ((bd.get("transmission") or {}).get("layers") or {}).items():
            if not isinstance(v, dict):      # 旧产物可能给 None，防 AttributeError
                v = {}
            layer = {"key": k, "label": LAYER_LABEL.get(k, k), "multi": v.get("multi"),
                     "valid": v.get("valid"), "n": v.get("n"), "note": v.get("note"),
                     "ratio_pct": _rnd((v.get("ratio") or 0) * 100, 2)}
            if k == "L2_holdings":
                # 个股层不出百分比条（单只持仓画成 100% 就是假信号），只给文字读数；
                # 文案由数据源单点给（sample_state=ok/empty/unavailable），此处不写死、不引内部文档编号。
                layer["ratio_pct"] = None
                layer["sample_state"] = v.get("sample_state")
                layer["sample_txt"] = v.get("sample_txt") or "无个股层样本"
            layers.append(layer)
        md_b = {"up": None, "down": None, "ratio": None, "prev_up": None, "prev_down": None,
                "prev_ratio": None, "zt": None, "dt": None, "zt_prev": None, "dt_prev": None}
        if today:
            md_b.update({"up": today["up_cnt"], "down": today["down_cnt"],
                         "ratio": _rnd(today["up_cnt"] / today["down_cnt"], 3) if today["down_cnt"] else None,
                         "zt": today["zt_total"], "dt": today["dt_total"]})
        if yday:
            md_b.update({"prev_up": yday["up_cnt"], "prev_down": yday["down_cnt"],
                         "prev_ratio": _rnd(yday["up_cnt"] / yday["down_cnt"], 3) if yday["down_cnt"] else None,
                         "zt_prev": yday["zt_total"], "dt_prev": yday["dt_total"]})
        intraday = None
        if M.get("intraday_1000"):
            up, dn = M["intraday_1000"]
            intraday = [{"t": "10:00", "up": up, "down": dn}]
        breadth = {"date": bd.get("date"), "source": bd.get("source"), "caliber": bd.get("caliber"),
                   "market": {"multi": mk.get("multi"), "valid": mk.get("valid"),
                              "ratio_pct": _rnd(mk.get("ratio", 0) * 100, 2),
                              "weight_ratio_pct": _rnd(mk.get("weight_ratio", 0) * 100, 2),
                              "prev_ratio_pct": _rnd(prev_pt["ratio"] * 100, 2) if prev_pt else None},
                   "market_series": _pts(ser), "l2_series": _pts(l2ser), "layers": layers,
                   "layer_note": (bd.get("transmission") or {}).get("note"),
                   "gap_pp": gap_pp, "prev_gap_pp": prev_gap, "prev_date": _dash_date(prev_pt["date"].replace("-", "")) if prev_pt else None,
                   "md": md_b, "intraday": intraday, "miss": None}
        freshness.append(f"宽度/传导 = {bd.get('date')} ✅")
    else:
        breadth = {"miss": _seg_miss("宽度/三层传导", (bd or {}).get("date"), date)}
        miss.append(breadth["miss"])
        freshness.append(f"宽度/传导 ⚠ 未对齐（{breadth['miss']}）")

    # ---------- 仓位上限（AMV 档位 × 情绪修正）；J0 温度 2026-11-06 整项退役 ----------
    # 退役依据：温度计方法论 2026-11-06 撤除、源 temperature_latest.json 已删除。
    # 本模块不再做「源日 ≠ 复盘日」检查（那项检查本身已过时），也不再显示温度读数；
    # 仓位上限仍由 position_cap_latest.json 供给（AMV 档位 70%/50%/30%/0% × 情绪修正系数）。
    cap_doc = _read_json(os.path.join(PANEL_DIR, "position_cap_latest.json"))
    cap_final = _num((cap_doc or {}).get("final_pct"))
    cap_block = {
        "total_pct": int(cap_final) if cap_final is not None else None,
        "base_pct": (cap_doc or {}).get("base_pct"),
        "regime": (cap_doc or {}).get("regime"),
        "amount": (cap_doc or {}).get("cap_amount"),
        "total_asset": (cap_doc or {}).get("total_asset"),
        "extreme": bool((cap_doc or {}).get("extreme")),
        "trade_date": (cap_doc or {}).get("trade_date"),
        "factors": [{"detail": f.get("detail") or "", "applied": bool(f.get("applied"))}
                    for f in ((cap_doc or {}).get("factors") or [])],
        "note": (cap_doc or {}).get("note") or "",
        "source": "data/panel/position_cap_latest.json（AMV 档位 × 情绪修正）",
    }
    temp = {
        "retired": True,
        "retired_at": TEMP_RETIRED_AT,
        "note": ("J0 市场温度自 %s 整项退役（温度计方法论废弃、源文件已删）"
                 "——不再参与新鲜度校验，本卡不显示温度读数；仓位档位改由 AMV 档位给出") % TEMP_RETIRED_AT,
        "cap": cap_block,
        "source": cap_block["source"],
        "miss": None if cap_final is not None else "仓位上限：position_cap_latest.json 缺失/未生成",
    }
    if cap_final is None:
        miss.append(temp["miss"])
        freshness.append("J0 温度 = 已退役（不适用）｜仓位档位 ⚠ position_cap_latest.json 缺失/未生成")
    else:
        freshness.append("J0 温度 = 已退役（不适用，源头 %s 撤除）" % TEMP_RETIRED_AT)
        freshness.append("仓位档位 = AMV %s档 → %d%%（position_cap_latest.json %s）✅"
                         % ((cap_doc or {}).get("regime") or "—", int(cap_final),
                            (cap_doc or {}).get("trade_date") or "—"))

    # ---------- 板块 ----------
    # 口径分离（2026-10-21 主人定）：源 JSON 里两个百分比含义不同，**不得互替**——
    #   · pct      = THS 板块指数当日涨跌幅（价维度，源 board_flow.pct）→ 涨幅榜/跌幅榜用它
    #   · amv_pct  = 板块 AMV 当日环比（钱维度）→ 只作补充展示，不参与涨跌幅排序
    # 旧版把 AMV 环比当涨跌幅排「领涨/领跌」，已修正；pct 为 None 的板块不进 top/bottom。
    st = _read_json(os.path.join(PANEL_DIR, "sector_temp_latest.json"))
    sector = None
    if st and st.get("date") == date:
        L2 = [b for b in (st.get("boards") or []) if b.get("level") == "L2"]

        def brow(b):
            return {"name": b.get("name"), "code": b.get("code"), "state": b.get("state"),
                    "pct": _rnd(b.get("pct"), 2),                     # 指数涨跌（价）
                    "amv_pct": _rnd(b.get("amv_pct"), 2),             # AMV 环比（价格×换手合成量）
                    "pct_source": b.get("pct_source"),
                    "wd_pp": _rnd((b.get("weight_delta") or 0) * 100, 3),
                    "weight_pct": _rnd((b.get("weight") or 0) * 100, 2)}
        # 涨幅榜/跌幅榜只认真涨跌：pct 缺失的板块排除（不拿 amv_pct 顶替）
        L2_by_pct = [b for b in L2 if b.get("pct") is not None]
        # AMV 环比榜：与涨跌幅榜**分开**（两个维度，不互替）
        L2_by_amv = [b for b in L2 if b.get("amv_pct") is not None]
        multi_names = sorted(b["name"] for b in L2 if b.get("state") == "多")
        # 口径措辞（2026-11-08 改注）：源文件里的「钱维度」写法已废，见 0AMV 参考卡片 §8.4/§8.5/§8.6——
        # AMV = 换手强度（价格×换手合成量），Δw = AMV 份额迁移（活跃度相对变化），两者都不是资金。
        pct_sem = dict(st.get("pct_semantics") or {})
        pct_sem["amv_pct"] = ("AMV 环比 %（价格×换手合成量，非资金）——板块活跃市值当日变化率；"
                              "源=sector_daily 的 AMV 序列末值；读法见 参考卡片/0AMV活跃市值.md §8.4")
        pct_sem["amv_share"] = ("Δw = AMV 份额迁移（活跃度相对变化，非资金）——板块 AMV 占大盘 AMV 的份额变化，N=5；"
                                "零和相对量，禁止折算成金额（§8.5）；跌幅日的份额上升需剥离价格后再读（§8.6）")
        pct_sem["rule"] = ("三者不同义、禁止互替：pct=板块指数当日涨跌（价）；amv_pct=AMV 环比（价格×换手合成量，非资金）；"
                           "Δw=AMV 份额迁移（活跃度相对变化，非资金）。判断钱是否进场只能看主力资金")
        caliber = ("板块口径 = THS 行业(二级881xxx/三级884xxx) + 自建板块(999xxx)；板块温度 = ①双轨多空"
                   "（MA10+pct±4%/−2.3%）②AMV 份额迁移 Δw（N=5）。"
                   "**三个百分比不同义**：pct=板块指数当日涨跌（价）；amv_pct=AMV 环比（价格×换手合成量，非资金）；"
                   "Δw=AMV 份额迁移（活跃度相对变化，非资金）——与活跃市值涨幅是两个维度（§8.5），禁止互替")
        sector = {
            "date": st.get("date"), "caliber": caliber, "state_caliber": st.get("state_caliber"),
            "pct_semantics": pct_sem, "counts": st.get("counts"), "source": st.get("source"),
            "top": [brow(b) for b in sorted(L2_by_pct, key=lambda x: -x["pct"])[:10]],
            "bottom": [brow(b) for b in sorted(L2_by_pct, key=lambda x: x["pct"])[:10]],
            "add": [brow(b) for b in sorted(L2, key=lambda x: -(x.get("weight_delta") or 0))[:8]],
            "cut": [brow(b) for b in sorted(L2, key=lambda x: (x.get("weight_delta") or 0))[:8]],
            "n_rank_pct": len(L2_by_pct), "n_pct_missing": len(L2) - len(L2_by_pct),
            "amv_top": [brow(b) for b in sorted(L2_by_amv, key=lambda x: -x["amv_pct"])[:8]],
            "amv_bottom": [brow(b) for b in sorted(L2_by_amv, key=lambda x: x["amv_pct"])[:8]],
            "multi_l2": multi_names, "multi_n": len(multi_names),
            "multi_prev_n": M.get("sector_multi_prev_n"),
            "prev_date": _dash_date(_prev_trade_date(date8)),
            "md_multi_note": M.get("sector_multi_note"),
            "verdict": M.get("sector_verdict") or [],
            # 2026-11-08：不再写「无 J3 裁决产物」——方向结论改由六格产物（强共振/共振流出/涨停集中/持续吸金）
            # 在下方 six 段注入 sector.direction；这里只保留 MD 自带的 ①②③ 裁决（若有）。
            "verdict_miss": None,
            "direction": None,          # 待 six 段填充
            "miss": None,
        }
        freshness.append(f"J2b 板块温度 = {st.get('date')} ✅")
    else:
        sector = {"miss": _seg_miss("板块温度", (st or {}).get("date"), date), "verdict": []}
        miss.append(sector["miss"])
        freshness.append(f"J2b 板块温度 ⚠ 未对齐（{sector['miss']}）")

    # ---------- 涨停梯度 ----------
    ld = _read_json(os.path.join(PANEL_DIR, f"sector_ladder_{date8}.json"))
    ladder = None
    if ld and ld.get("trade_date") == date:
        boards = []
        for b in ld.get("boards") or []:
            h = b.get("height") or {}
            stg = b.get("strength") or {}
            rhy = b.get("rhythm") or {}
            leader = stg.get("leader") or {}
            boards.append({
                "name": b.get("name"), "code": b.get("code"), "level": b.get("level"),
                "count": b.get("limitup_count"), "grade": b.get("grade"), "state": b.get("amv_state"),
                "wd_pp": _rnd((b.get("weight_delta") or 0) * 100, 3),
                "H": h.get("H"), "gap": h.get("gap"), "gap_detail": h.get("gap_detail"), "ladder": h.get("ladder"),
                "seal_ratio": _rnd((stg.get("seal_ratio") or 0) * 100, 1),
                "leader": leader.get("name"), "leader_cnt": leader.get("cnt"),
                "leader_time": rhy.get("leader_time"), "early": rhy.get("early_leader"),
                "tail_pct": _rnd((rhy.get("tail_ratio") or 0) * 100, 0),
                "cross": b.get("cross"), "reason": b.get("select_reason"),
            })
        chain = M.get("ladder_chain")
        ladder = {"date": ld.get("trade_date"), "total": ld.get("limitup_total"),
                  "front": ld.get("front_count"), "caliber": ld.get("caliber"), "source": ld.get("source"),
                  "boards": boards,
                  "chain": ({"name": "电池链", "members": chain["members"], "sum": chain["sum"],
                             "note": M.get("ladder_chain_note") or ""} if chain else None),
                  "md_verdict": M.get("ladder_md_verdict"), "miss": None}
        ok = (ld.get("limitup_total") == (today or {}).get("zt_total")) if today else None
        tag = "✅（涨停数与 sentiment 一致）" if ok else ("⚠（涨停数与 sentiment 不一致）" if ok is False else "")
        freshness.append(f"涨停梯度 = {ld.get('trade_date')} {tag}")
    else:
        ladder = {"miss": f"涨停梯度：sector_ladder_{date8}.json 缺失或日期不符（复盘日 {date}）"}
        miss.append(ladder["miss"])
        freshness.append("涨停梯度 ⚠ 缺失/未对齐")

    # ---------- 异动（2026-11-05：主源改 sector_anomaly 的 anomalies，按 ex_pct 降序取前 N） ----------
    an = _read_json(os.path.join(PANEL_DIR, f"sector_anomaly_{date8}.json"))
    anomaly = None
    if an and an.get("trade_date") == date:
        pool = [x for x in (an.get("anomalies") or []) if x.get("ex_pct") is not None]
        highlights = []
        for x in sorted(pool, key=lambda z: -z["ex_pct"])[:8]:
            flow_yi = _rnd((x.get("flow_calc") or 0) / 1e8, 1)
            highlights.append({
                "name": x.get("name"), "level": x.get("level"),
                "pct": _rnd(x.get("pct"), 2),
                "ex_pct": _rnd(x.get("ex_pct"), 2),
                "d1": _rnd((x.get("d1") or 0) * 100, 3),
                "d5": _rnd((x.get("d5") or 0) * 100, 3),
                "quadrant": x.get("quadrant"),
                "flow_yi": flow_yi,
                "tag": x.get("quadrant") or "",
                "tone": "warn" if ((x.get("d1") or 0) < 0) else "up",
            })
        anomaly = {"date": an.get("trade_date"), "caliber": an.get("caliber"), "counts": an.get("counts"),
                   "source": f"data/panel/sector_anomaly_{date8}.json::anomalies（共 {len(an.get('anomalies') or [])} 条，"
                             f"按 ex_pct 降序取前 {len(highlights)}）",
                   "highlights": highlights, "md_notes": M.get("anomaly_notes_raw") or [],
                   "flow_summary": M.get("anomaly_flow_summary"),
                   "miss": None if highlights else "异动归因：anomalies 为空（源文件存在但无 ex_pct 有效行）"}
        if not highlights:
            miss.append(anomaly["miss"])
        freshness.append(f"异动归因 = {an.get('trade_date')} ✅（{len(highlights)}/{len(an.get('anomalies') or [])} 条）")
    else:
        anomaly = {"miss": f"异动归因：sector_anomaly_{date8}.json 缺失或日期不符（复盘日 {date}）", "highlights": []}
        miss.append(anomaly["miss"])
        freshness.append("异动归因 ⚠ 缺失/未对齐")

    # ---------- 情绪 ----------
    sentiment = None
    if today:
        def pack(r):
            return {"d": r["trade_date"][4:6] + "-" + r["trade_date"][6:8], "stage": r.get("stage") or "未判定",
                    "zt": r.get("zt_total"), "dt": r.get("dt_total"), "h": r.get("height"),
                    "lb2": r.get("lb2plus"), "reseal": _rnd(r.get("reseal_rate"), 2),
                    "ratio": _rnd(r["up_cnt"] / r["down_cnt"], 3) if r.get("down_cnt") else None}
        prev = yday or {}
        # 2026-11-08：回封率保留 2 位（主人口径 71.74%，1 位会写成 71.70 → 看着像误差）
        reseal_today = _rnd(today.get("reseal_rate"), 2)
        reseal_prev = _rnd(prev.get("reseal_rate"), 2)
        sentiment = {
            "stage": today.get("stage"), "stage_note": today.get("stage_note"),
            "prev_stage": prev.get("stage"), "prev_stage_note": prev.get("stage_note"),
            "prev_date": _dash_date(prev.get("trade_date")),
            "day": {"zt": today.get("zt_total"), "dt": today.get("dt_total"), "h": today.get("height"),
                    "lb2": today.get("lb2plus"), "up": today.get("up_cnt"), "down": today.get("down_cnt"),
                    "reseal": reseal_today},
            "prev": {"zt": prev.get("zt_total"), "dt": prev.get("dt_total"), "h": prev.get("height"),
                     "lb2": prev.get("lb2plus"), "up": prev.get("up_cnt"), "down": prev.get("down_cnt"),
                     "reseal": reseal_prev},
            "series": [pack(r) for r in srows][-20:], "n_series": len(srows),
            "extra": {
                "premium_today": today.get("premium"), "premium_prev": prev.get("premium"),
                "premium_miss": M.get("premium_miss"),
                "promotion_today": M.get("promotion_today"), "promotion_note": M.get("promotion_note"),
                "promotion_prev": {"2b": prev.get("promotion_2b"), "3b": prev.get("promotion_3b"),
                                   "4b": prev.get("promotion_4b")},
                "boom": _rnd(100 - reseal_today, 2) if reseal_today is not None else None,
                "boom_prev": _rnd(100 - reseal_prev, 2) if reseal_prev is not None else None,
            },
            "unseal": M.get("unseal") or [], "watch": M.get("watch") or [],
            "unseal_miss": (None if (M.get("unseal") or []) else
                            "退潮四条核对：MD 无 ❌/✅ 核对条（今日 MD 第三节是消息面，情绪数据走 track_pool.sentiment）"),
            "watch_miss": (None if (M.get("watch") or []) else
                           "次日观察点：MD 无「次日观察点」块（今日在五节「明日盯盘重点」里给，不另列）"),
            "quality_warn": M.get("quality_warn"),
            "source": (f"data/track_pool.db::sentiment（trade_date={date8}，全窗 {len(srows)} 个交易日）"
                       f" + {md_rel} 三节"),
            "miss": None,
        }
        freshness.append(f"情绪 = {date8} ✅")
    else:
        sentiment = {"miss": f"情绪：sentiment 表无 {date} 收盘行（ts='close'）", "unseal": [], "watch": [],
                     "unseal_miss": "退潮四条核对：sentiment 无当日行，无可核对条目",
                     "watch_miss": "次日观察点：sentiment 无当日行，无输入"}
        miss.append(sentiment["miss"])
        freshness.append("情绪 ⚠ 缺失/未对齐")

    # ---------- 量能（eltdx：沪=000001.SH / 深=399001.SZ 成交额，指数日线不复权） ----------
    turnover = None
    sh_series, sh_last = _index_series("000001.SH", date8, 2)
    sz_series, sz_last = _index_series("399001.SZ", date8, 2)
    if sh_series and sz_series and sh_last == date8 and sz_last == date8:
        total = (sh_series[-1][5] or 0) + (sz_series[-1][5] or 0)
        prev_total = ((sh_series[-2][5] or 0) + (sz_series[-2][5] or 0)) if len(sh_series) > 1 and len(sz_series) > 1 else None
        note = None
        if M.get("intraday_1130") and M.get("intraday_tail"):
            a, b, c = M["intraday_1130"]
            note = f"V 型结构：11:30 全A {a}%、科创50 盘中最低 {b}%、创业板 {c}% → {M['intraday_tail']}"
        turnover = {"total": total, "prev_total": prev_total,
                    "delta_pct": _rnd((total / prev_total - 1) * 100, 1) if prev_total else None,
                    "diff_pct": _rnd((total / prev_total - 1) * 100, 2) if prev_total else None,
                    "sh_yi": _rnd((sh_series[-1][5] or 0) / 1e8, 0), "sz_yi": _rnd((sz_series[-1][5] or 0) / 1e8, 0),
                    "prev_total_wan_yi": _rnd(prev_total / 1e12, 2) if prev_total else None,
                    "prev_date": _dash_date(_prev_trade_date(date8)),
                    "source": "eltdx 指数日线（000001.SH + 399001.SZ，不复权收盘定格）+ 日复盘 MD 0.3",
                    "intraday_note": note, "miss": None}
    else:
        turnover = {"miss": f"成交额：eltdx 指数日线未覆盖复盘日 {date}"}
        miss.append(turnover["miss"])

    # ---------- 持仓：两本账分开（2026-11-08 主人定；QMT 缓存只作降级提示） ----------
    # 历史教训（写在现场，防回退）：旧版直接读 real_positions.json 的 account 字段，
    # 把 888880 新标准券的 217,000 算进市值，页面显示「总资产 217,389.40」——那是 QMT
    # 2026-10-23 的旧快照，与当下实盘（空仓）无关。现在两本账各读各自的账本，不合并。
    ra = _read_json(REAL_ACCOUNT_FILE)
    pa = _read_json(PAPER_ACCOUNT_FILE)
    qmt = _read_json(QMT_CACHE_FILE)
    real_book = _real_book(ra) if isinstance(ra, dict) else None
    paper_book = _paper_book(pa) if isinstance(pa, dict) else None
    degraded = _qmt_degraded(qmt)
    positions = {
        "real": real_book,
        "paper": paper_book,
        "degraded": degraded,
        "rows": [],          # 两本账不合并成一跃表（rows 保留字段只为旧调用不炸）
        "closed": (real_book or {}).get("closed") or [],
        "trade_today": M.get("trade_today"),
        "risk": M.get("risk") or "",
        "source": "实盘 data/panel/real_account.json ｜ 纸面 engine/state/paper_account.json ｜ "
                  "QMT 缓存 data/panel/real_positions.json 仅作连接提示"
                  f" ｜ {md_rel} 四节（判断/风险原文）",
        "miss": None,
    }
    if real_book:
        freshness.append(f"实盘账本 = real_account.json（status={real_book.get('status') or '—'}，"
                         f"持仓 {real_book.get('positions_n')} 只，平仓 {real_book.get('closed_n')} 笔）✅")
    else:
        positions["miss"] = "实盘账本：data/panel/real_account.json 缺失或不是对象"
    if paper_book:
        freshness.append(f"纸面账本 = paper_account.json（trade_date={paper_book.get('trade_date') or '—'}，"
                         f"持仓 {paper_book.get('positions_n')} 只，现金 {_th(paper_book.get('cash'), 2)}）✅")
    else:
        positions["miss"] = ((positions["miss"] + "；") if positions["miss"] else "") + \
            "纸面账本：engine/state/paper_account.json 缺失或不是对象"
    if positions["miss"]:
        miss.append(positions["miss"])
    if degraded:
        freshness.append(f"QMT 柜台缓存 = {degraded.get('status')}（最后同步 {degraded.get('last_synced_at') or '—'}）"
                         "——仅连接提示，不进读数")

    # ---------- 情景（2026-11-05：改读 MD 六节；battle_plan.json 2026-10-27 已退役，不再作源） ----------
    sc_rows = M.get("scenarios") or []
    if sc_rows:
        scenarios = {"trade_date": date, "plan_version": "MD 六节·情景-应对表",
                     "source": f"{md_rel} 六节（battle_plan.json 已退役，本卡不读旧文件）",
                     "rows": sc_rows, "open_default": ["情景"], "miss": None}
        freshness.append(f"情景-应对 = MD 六节 ✅（{len(sc_rows)} 条）")
    else:
        scenarios = {"miss": (f"情景-应对：{md_rel} 未解析到四列「情景/触发条件/概率/应对」表"
                             "（battle_plan.json 已退役，不再回退）"),
                     "rows": [], "open_default": ["情景"]}
        miss.append(scenarios["miss"])
        freshness.append("情景-应对 ⚠ MD 六节未解析到表")

    # ---------- 明日盯盘 / 缺口 / 消息 / 判断 ----------
    tomorrow = {"date": None, "rows": M.get("tomorrow") or [], "src": f"{md_rel} 五节", "miss": None}
    mm = re.search(r"（(\d{1,2})/(\d{1,2})\s*(周[一二三四五六日])）", _section(md_text, r"^## 五、", r"^## 六、"))
    if mm:
        tomorrow["date"] = f"{date[:4]}-{int(mm.group(1)):02d}-{int(mm.group(2)):02d} {mm.group(3)}"
    gaps = M.get("gaps") or []
    gaps_note = None if gaps else "今日无缺口：MD 七节未勾选任何未完成项"
    news = {"asof_pre": M.get("news_asof_pre"), "asof_inc": M.get("news_asof_inc"),
            "pre": M.get("news_pre") or [], "inc": M.get("news_inc") or [],
            "driver": dict(M.get("news_driver") or {}, src=[]), "pre_source": None, "miss": None}
    news["driver"]["src"] = [md_rel] + (M.get("news_driver", {}).get("src") or [])
    if not (M.get("news_driver") or {}).get("rows"):
        news["driver"]["rows_miss"] = "驱动归因：MD 无 1.3 编号驱动段（今日驱动走 §三 政策链）"
    snap_name = f"news_snapshot_{date8}.json"
    news["snapshot"] = {"path": f"data/panel/{snap_name}", "exists": os.path.exists(os.path.join(PANEL_DIR, snap_name))}
    news["inc_miss"] = None if news["inc"] else (
        f"盘后增量：{snap_name} 不存在且无 news_increment_* 落盘件 → 增量消息无源（不回退其他日快照）")
    if not news["pre"] and not news["inc"]:
        pol = M.get("policy_chain") or []
        if pol:
            news["pre"] = pol
            news["pre_source"] = f"md:§三 政策链（{snap_name} 不存在，回退 MD 原文）"
            freshness.append(f"消息面 = MD 三节政策链回退 ⚠（{snap_name} 不存在，{len(pol)} 条）")
        else:
            news["miss"] = (f"消息面：{snap_name} 不存在，且 {md_rel} 三节无「受益链/资金验证」可解析条目")
            miss.append(news["miss"])
            freshness.append("消息面 ⚠ 无源（快照缺失 + MD 无政策链）")
    quotes_all = M.get("quote_rows") or []
    md_quotes = {
        "who": f"我的判断（摘自 {md_rel}，仅去除 MD 加粗标记，未改写语句）",
        "rows": quotes_all, "count": len(quotes_all),
        "s0": next((q["text"] for q in quotes_all if q["key"] == "s0"), ""),
        "s1": next((q["text"] for q in quotes_all if q["key"] == "s1"), ""),
        "s2": next((q["text"] for q in quotes_all if q["key"] == "s2"), ""),
        "s3": next((q["text"] for q in quotes_all if q["key"] == "s3"), ""),
        "s4": next((q["text"] for q in quotes_all if q["key"] == "s4"), ""),
        "s5": next((q["text"] for q in quotes_all if q["key"] == "s5"), ""),
        "s6": next((q["text"] for q in quotes_all if q["key"] == "s6"), ""),
        "s7": next((q["text"] for q in quotes_all if q["key"] == "s7"), ""),
        "miss": None if quotes_all else "我的判断：MD 无 `> 💡` 判断行",
    }
    head = {"date": date, "weekday": M.get("weekday") or "", "title": M.get("title") or "",
            "one": M.get("one") or "", "three": M.get("three") or [], "next": M.get("next") or "",
            "temp": M.get("temp_line") or "", "caliber": M.get("caliber") or "",
            "exec_note": M.get("exec_note") or "", "key_nums": []}
    # 仓位闸门：优先 MD 0.1 三因子表（今日口径）；旧版 4.2 三层闸门作兼容保留
    gate = {"rows": M.get("gate_factors") or [], "final": M.get("gate_final") or {},
            "gates": M.get("gates") or [], "verdict": M.get("gate_verdict") or "",
            "pct": (M.get("gate_final") or {}).get("pct"),
            "amount": (M.get("gate_final") or {}).get("amount"),
            "source": f"{md_rel} 0.1（仓位闸门三因子）+ 〇节", "miss": None}
    if not gate["gates"]:
        gate["gates_miss"] = ("三层闸门（大盘/板块/个股 红黄绿）：本日 MD 无 4.2 节，"
                              "闸门改由 0.1「仓位闸门三因子」表给出")
    if not gate["rows"] and not gate["gates"]:
        gate["miss"] = f"仓位闸门：{md_rel} 未解析到 0.1 三因子表，也无 4.2 三层闸门"
        miss.append(gate["miss"])

    # ---------- 今日三问（MD 首节表格） ----------
    questions = {"rows": M.get("questions") or [], "source": f"{md_rel} 三问作答（首节表）",
                 "miss": None}
    if not questions["rows"]:
        questions["miss"] = f"今日三问：{md_rel} 首节无「问题/答案/证据/台账」表"
        miss.append(questions["miss"])

    # ---------- 假设台账（data/panel/hyp_ledger.json，只读） ----------
    led = _read_json(os.path.join(PANEL_DIR, "hyp_ledger.json"))
    ledger = {"hit": [], "miss": [], "pending": [], "source": "data/panel/hyp_ledger.json", "miss_note": None}
    if isinstance(led, list):
        ledger["hit"] = [x for x in led if x.get("status") == "hit" and str(x.get("verified") or "") == date]
        ledger["miss"] = [x for x in led if x.get("status") == "miss" and str(x.get("verified") or "") == date]
        ledger["pending"] = [x for x in led if x.get("status") == "pending" and str(x.get("raised") or "") == date8]
        ledger["total"] = len(led)
        if not (ledger["hit"] or ledger["miss"] or ledger["pending"]):
            ledger["miss_note"] = f"假设台账：hyp_ledger.json 共 {len(led)} 条，本日无核销/新增行"
    else:
        ledger["miss_note"] = "假设台账：data/panel/hyp_ledger.json 缺失或不是数组"
        miss.append(ledger["miss_note"])

    # ---------- 六格摘要（与第四视图同源：review_six_cells_YYYYMMDD.json，不重算） ----------
    six = {"cells": [], "miss": None}
    try:
        from app.core import review_six as _rs  # noqa: PLC0415 - 懒引入防环
        doc = _rs.load(date)
        for c in doc.get("cells") or []:
            six["cells"].append({"key": c.get("key"), "title": c.get("title"),
                                 "n": c.get("n_matched"), "chips": c.get("chips") or [],
                                 "basis": c.get("basis") or c.get("criteria") or "",
                                 "action": c.get("action") or "",
                                 "action_status": c.get("action_status")})
        six["date"] = (doc.get("meta") or {}).get("target_date")
        six["source"] = f"data/panel/review_six_cells_{date8}.json（六格视图同源，不重算）"
        # 2026-11-08：板块方向结论直接用六格产物（不再写「无 J3 裁决产物」）
        direction = _sector_direction(doc, date8)
        if sector is not None and direction:
            sector["direction"] = direction
        if not six["cells"]:
            six["miss"] = "六格：产物里 cells 为空"
            miss.append(six["miss"])
        else:
            freshness.append(f"六格摘要 = review_six_cells_{date8}.json ✅（{len(six['cells'])} 格）")
            if direction:
                freshness.append(f"板块方向 = 六格产物 ✅（{len(direction['blocks'])} 块："
                                 + "、".join(b["title"] for b in direction["blocks"]) + "）")
    except Exception as e:  # noqa: BLE001 - 六格产物缺失不影响主图
        direction = None
        six["miss"] = f"六格：{e}"
        miss.append(six["miss"])
        freshness.append("六格摘要 ⚠ 读取失败")
    if M.get("news_asof_pre") or M.get("news_asof_inc"):
        freshness.append(f"消息面 = 盘前快照 asof {M.get('news_asof_pre') or '—'} + 盘后增量覆盖至 "
                         f"{M.get('news_asof_inc') or '—'} ✅")

    # ---------- 首屏三层（2026-11-08 主人定）：关键数 ≤6 + 板块方向一句 + 图表结论句 ----------
    # 全部由上面已装配的段对象现成字段拼出，不新增数据源、不重算任何量。
    head["key_nums"] = _key_nums(amv, turnover, sentiment, gate, breadth)
    if sector is not None and sector.get("direction"):
        head["sector_line"] = sector["direction"].get("line") or ""
    else:
        head["sector_line"] = ""
    # 图表结论句（一个图配一句）——放 head.lines，不新增顶层键（保持 /api/review_chart.json 字段契约不变）
    head["lines"] = _chart_lines(amv, turnover, sentiment, idx_rows, sector, positions)

    return {
        "ok": True,
        "meta": {"trade_date": date, "idx_date": date8, "generated_by": "app/core/review_chart.py（服务端装配）",
                 "md_source": md_rel, "miss": miss, "freshness": freshness,
                 "note": "本数据由服务端从唯一源实时装配（非静态快照）；缺数处标 rv-miss，不补数"},
        "head": head, "levels": levels,
        "index": {"rows": idx_rows, "source": "eltdx:指数日线 + MD 0.2/0.3/0.4"},
        "amv": amv, "breadth": breadth, "temp": temp, "turnover": turnover,
        "sector": sector, "ladder": ladder, "anomaly": anomaly, "sentiment": sentiment,
        "news": news, "positions": positions, "gate": gate, "tomorrow": tomorrow,
        "scenarios": scenarios, "gaps": gaps, "gaps_note": gaps_note, "md_quotes": md_quotes,
        "questions": questions, "ledger": ledger, "six": six,
    }


# ============================================================ 缓存与入口
def _sources_fingerprint(date8):
    """全部输入文件指纹（缺文件也记一条，出现/消失即失效）。"""
    paths = [
        os.path.join(PANEL_DIR, "amv_regime_latest.json"),
        os.path.join(PANEL_DIR, "amv_breadth_latest.json"),
        os.path.join(PANEL_DIR, "sector_temp_latest.json"),
        REAL_ACCOUNT_FILE, PAPER_ACCOUNT_FILE, QMT_CACHE_FILE,
        os.path.join(PANEL_DIR, "hyp_ledger.json"),
        os.path.join(PANEL_DIR, f"review_six_cells_{date8}.json"),
        os.path.join(PANEL_DIR, f"sector_ladder_{date8}.json"),
        os.path.join(PANEL_DIR, f"sector_anomaly_{date8}.json"),
        TEMPERATURE_FILE, TRACK_DB,
        os.path.join(PANEL_DIR, "position_cap_latest.json"),
        ALL_A_FILE,   # 2026-10-21：全A 卡数据源，漏在这里会让改盘后仍读旧缓存
    ]
    return tuple(_stat_sig(p) for p in paths)


_A_SHARE_TS = re.compile(r"^([0236]\d{5})\.(SH|SZ|BJ)$")
_NAME_CODE_CACHE = None


def _name_code_map():
    """名称 → ts_code（stock_names 反查；同名多码剔除）。懒加载，失败 → {}。"""
    global _NAME_CODE_CACHE
    if _NAME_CODE_CACHE is not None:
        return _NAME_CODE_CACHE
    out = {}
    try:
        path = READABLE_JSON.get("stock_names")
        if path is not None and path.is_file():
            with open(path, encoding="utf-8") as f:
                raw = json.load(f).get("names") or {}
            seen = {}
            for code6, nm in raw.items():
                code6 = str(code6 or "").strip()
                nm = str(nm or "").replace(" ", "").strip()
                if len(code6) != 6 or not code6.isdigit() or not nm:
                    continue
                ts = code6 + ({"6": ".SH", "5": ".SH", "9": ".SH"}.get(code6[0], ".SZ"))
                if code6[:3] == "920" or code6[0] in "48":
                    ts = code6 + ".BJ"
                prev = seen.get(nm)
                if prev is not None and prev != ts:
                    seen[nm] = None       # 同名多码 → 歧义，不猜
                else:
                    seen[nm] = ts
            out = {k: v for k, v in seen.items() if v}
    except Exception:  # noqa: BLE001 - 名表不可用只影响补码，不影响图表主数据
        out = {}
    _NAME_CODE_CACHE = out
    return out


def _code_of_stock(name):
    """名称 → A 股 ts_code；未命中/非个股 → ""。"""
    nm = str(name or "").replace(" ", "").strip()
    if not nm:
        return ""
    code = _name_code_map().get(nm) or ""
    return code if (code and _A_SHARE_TS.match(code)) else ""


def backfill_ladder_codes(payload):
    """涨停梯队 leader/member 名称 → code（旧 server._backfill_ladder_codes 同口径，幂等）。"""
    try:
        lad = payload.get("ladder") or {}
        for b in lad.get("boards") or []:
            if isinstance(b, dict) and "leader_code" not in b:
                b["leader_code"] = _code_of_stock(b.get("leader"))
        ch = lad.get("chain") or {}
        mems = ch.get("members") or []
        if mems and "member_codes" not in ch:
            ch["member_codes"] = [_code_of_stock(m) for m in mems]
    except Exception:  # noqa: BLE001 - 补码失败不拖垮图表
        pass
    return payload


def build(pick):
    """从 ReviewPick 装配（读 MD → assemble）。MD 读取失败/结构缺失 → ReviewChartError。"""
    try:
        with open(pick.path, encoding="utf-8") as f:
            md_text = f.read()
    except OSError as e:
        raise ReviewChartError(f"复盘 MD 不可读：{e}") from e
    md_rel = os.path.relpath(pick.path, WORKSPACE).replace(os.sep, "/")
    data = assemble(md_text, pick.date, md_rel)
    data["file"] = os.path.basename(pick.path)
    data["mtime_ns"] = pick.mtime_ns
    return backfill_ladder_codes(data)


def build_cached(pick):
    """LRU 缓存包装：key = (MD 指纹, 源文件指纹)。返回副本 + cache=hit|miss。"""
    key = (pick.path, pick.mtime_ns, pick.size, _sources_fingerprint(pick.date.replace("-", "")))
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            out = dict(hit)
            out["cache"] = "hit"
            return out
    data = build(pick)
    out = dict(data)
    out["cache"] = "miss"
    with _cache_lock:
        _cache[key] = dict(out)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return out


def reset_cache():
    """清空装配缓存（测试/排障用）。"""
    with _cache_lock:
        _cache.clear()


if __name__ == "__main__":   # 手动装配核对：python -m app.core.review_chart [YYYY-MM-DD]
    import sys as _sys
    _date = _sys.argv[1] if len(_sys.argv) > 1 else None
    # 2026-11-07：旧 common 复盘解析模块已删除；唯一实现 = review 插件的 source.py。
    from app.core import paths as _paths  # noqa: PLC0415
    from app.plugins.review import source as _rv_source  # noqa: PLC0415
    _pick = _rv_source.pick_latest(_date, _paths.text_dir("daily_review"))
    if _pick is None:
        print("无可用复盘 MD")
        raise SystemExit(1)
    _data = build(_pick)
    print(json.dumps({k: (v if k in ("meta", "head") else "…") for k, v in _data.items()},
                     ensure_ascii=False, indent=2))
    print("sections:", list(_data.keys()))
