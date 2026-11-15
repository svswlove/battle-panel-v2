#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""面板产物一致性审计（只读）：M4 切流前查下游文档与 AMV 口径是否冲突。

铁律：
- 只读 `data/panel/*.json`。不连库、不写生产文件、不发 HTTP、不碰旧 8800/8002。
- 产出只落本项目：`docs/consistency_report.md` + `tools/consistency_result.json`。
- 提取不到数字就标 UNPARSED，不猜、不用近似值顶替。

检查项：
  A1  battle_plan.direction_forecast.judgment 文本 AMV 值  ↔ amv_regime_latest.amv
  A2  battle_plan.direction_forecast.judgment 文本 MA10    ↔ amv_regime_latest.ma10
  A3  battle_plan.direction_forecast.judgment 文本 ratio   ↔ amv_regime_latest.amv / ma10
  A4  battle_plan.direction_forecast.judgment 文本 当日涨跌% ↔ amv_regime_latest.pct
  A5  battle_plan.direction_forecast.judgment 文本 空档第N日 ↔ 由 last_switch 推的档内日数
  A6  文本「距 MA10 差 X%」↔ 现算 gap（证据项，不参与判定）
  B1  position_cap_latest.regime   ↔ amv_regime_latest.state
  B2  position_cap_latest.cap_amount ↔ battle_plan.position_cap（容差 1e-6）
  C1  amv_live.state_date          ↔ amv_regime_latest.date
  C2  position_cap_latest.trade_date ↔ amv_live.trade_date
  D1  battle_plan.plan_date        ↔ position_cap_latest.trade_date
  D2  battle_plan.meta.trade_date  ↔ position_cap_latest.trade_date

退出码：全 PASS = 0；任一 FAIL = 1；无 FAIL 但有 UNPARSED = 2。

用法：
    python tools/check_panel_consistency.py
    python tools/check_panel_consistency.py --data-dir <dir> --json <path> --md <path>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

TOOL_DIR = Path(__file__).resolve().parent
ROOT = TOOL_DIR.parent                      # panel-v2
DOCS_DIR = ROOT / "docs"
DEFAULT_JSON = TOOL_DIR / "consistency_result.json"
DEFAULT_MD = DOCS_DIR / "consistency_report.md"

TOL = 1e-6                                   # 数值容差（任务口径）

PASS, FAIL, UNPARSED, SKIP, INFO = "PASS", "FAIL", "UNPARSED", "SKIP", "INFO"
EXIT_OK, EXIT_FAIL, EXIT_UNPARSED = 0, 1, 2


# ---------------------------------------------------------------- 数据目录

def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else None


def resolve_panel_dir(explicit: str | None = None) -> Path:
    """数据目录推导，与 app/core/paths.py 同规则（不 import core，保持工具独立）。

    优先级：--data-dir > PANEL_V2_DATA_DIR > PANEL_V2_WS/data > <项目>/../data
    """
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p / "panel" if p.name != "panel" else p
    data_dir = _env_path("PANEL_V2_DATA_DIR")
    if data_dir:
        return data_dir / "panel"
    ws = _env_path("PANEL_V2_WS")
    if ws is None:
        ws = ROOT.parents[1] if len(ROOT.parents) >= 2 else ROOT.parent
    return ws / "data" / "panel"


def load_json(panel_dir: Path, name: str) -> tuple[dict[str, Any] | None, str | None]:
    path = panel_dir / f"{name}.json"
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, f"文件不存在：{path}"
    except Exception as exc:  # noqa: BLE001
        return None, f"读取失败：{path}：{exc}"
    if not isinstance(data, dict):
        return None, f"顶层不是对象：{path}"
    return data, None


# ---------------------------------------------------------------- 文本提取

# 数字：允许千分位与负号（支持 ASCII - 与 U+2212 减号）
_NUM = r"[−\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[−\-]?\d+(?:\.\d+)?"

RE_MA10 = re.compile(r"MA10\s*([−\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[−\-]?\d+(?:\.\d+)?)")
RE_RATIO = re.compile(r"ratio\s*([−\-]?\d+(?:\.\d+)?)", re.IGNORECASE)
RE_GAP = re.compile(r"距\s*MA10\s*(?:仍)?差\s*([−\-]?\d+(?:\.\d+)?)\s*%")
RE_EMPTY_DAY = re.compile(r"空档第\s*(\d+)\s*[日个]")
# 文本 AMV 值：必须带「亿」或显式 AMV 前缀，避免误抓仓位金额
RE_AMV_YI = re.compile(r"([−\-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[−\-]?\d+(?:\.\d+)?)\s*亿")
RE_AMV_PREFIX = re.compile(
    r"AMV\s*([+\-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+\-−]?\d+(?:\.\d+)?)(?!\s*%)"
)
# 当日涨跌：优先「当日 +X%」，回退「AMV +X%」
RE_PCT_DAY = re.compile(r"当日\s*([+\-−]?\d+(?:\.\d+)?)\s*%")
RE_PCT_AMV = re.compile(r"AMV\s*([+\-−]?\d+(?:\.\d+)?)\s*%")

# 文本 AMV 取数时，向前看多远判断该「亿」是否属于 AMV 语境
AMV_CONTEXT_CHARS = 30


def to_num(raw: str) -> float | None:
    s = raw.replace("−", "-").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def find_num(pattern: re.Pattern[str], text: str) -> float | None:
    m = pattern.search(text)
    return to_num(m.group(1)) if m else None


def find_amv(text: str) -> tuple[float | None, str]:
    """文本里的 AMV 数值（亿）。

    优先取「前文含 AMV」的「X 亿」——面板文本里「亿」也用于成交额，不加语境会抓错；
    没有语境命中再回退到首个「X 亿」，最后才用「AMV X」。
    """
    hits = [(m.start(), to_num(m.group(1))) for m in RE_AMV_YI.finditer(text)]
    ctx = [(pos, val) for pos, val in hits
           if "AMV" in text[max(0, pos - AMV_CONTEXT_CHARS):pos]]
    if len(ctx) == 1:
        return ctx[0][1], "AMV语境·亿"
    if len(ctx) > 1:
        # 多处带 AMV 语境的「亿」：取首个，并在 note 侧可复核
        return ctx[0][1], f"AMV语境·亿（共{len(ctx)}处，取首个）"
    if hits:
        return hits[0][1], "亿（无AMV语境，取首个）"
    m = RE_AMV_PREFIX.search(text)
    if m:
        return to_num(m.group(1)), "AMV前缀"
    return None, ""


def find_pct(text: str) -> tuple[float | None, str]:
    """文本里的「当日涨跌%」；回退到「AMV +X%」。"""
    m = RE_PCT_DAY.search(text)
    if m:
        return to_num(m.group(1)), "当日"
    m = RE_PCT_AMV.search(text)
    if m:
        return to_num(m.group(1)), "AMV"
    return None, ""


def span_counts(start: str, end: str) -> tuple[int, int] | None:
    """(start, end] 的（自然日数, 周一至周五日数）。

    休市表未接入（不连库、不读日历），所以只能给出上界参考；
    文本值落不进这两个数时，说明它不来自 last_switch 起点，判 UNPARSED 而非 FAIL。
    """
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    if d1 <= d0:
        return 0, 0
    natural = (d1 - d0).days
    weekdays = 0
    cur = d0
    while cur < d1:
        cur = cur.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            weekdays += 1
    return natural, weekdays


# ---------------------------------------------------------------- 检查项

def make_item(
    item_id: str,
    name: str,
    status: str,
    left_label: str,
    left_value: Any,
    right_label: str,
    right_value: Any,
    note: str = "",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "status": status,
        "left": {"label": left_label, "value": left_value},
        "right": {"label": right_label, "value": right_value},
        "note": note,
    }


def _num_equal(a: float, b: float, tol: float = TOL) -> bool:
    return abs(float(a) - float(b)) <= tol


def check_text_amv(plan: dict[str, Any], regime: dict[str, Any]) -> list[dict[str, Any]]:
    """A1-A6：battle_plan 判断文本里的 AMV 数字。"""
    items: list[dict[str, Any]] = []
    forecast = plan.get("direction_forecast")
    text = ""
    if isinstance(forecast, dict):
        text = str(forecast.get("judgment") or "")
    if not text:
        for iid, nm in (
            ("A1", "文本 AMV 值"),
            ("A2", "文本 MA10"),
            ("A3", "文本 ratio"),
            ("A4", "文本 当日涨跌%"),
            ("A5", "文本 空档第 N 日"),
        ):
            items.append(make_item(iid, nm, UNPARSED, "battle_plan.direction_forecast.judgment",
                                   None, "", None, "judgment 缺失或为空"))
        return items

    amv_json = regime.get("amv")
    ma10_json = regime.get("ma10")
    pct_json = regime.get("pct")
    has_amv = isinstance(amv_json, (int, float))
    has_ma10 = isinstance(ma10_json, (int, float))

    # A1 文本 AMV
    txt_amv, how = find_amv(text)
    if txt_amv is None or not has_amv:
        items.append(make_item("A1", "文本 AMV 值", UNPARSED,
                               "文本 AMV（亿）", txt_amv,
                               "amv_regime_latest.amv", amv_json,
                               "文本中未提取到带「亿」的 AMV 值" if txt_amv is None else "regime.amv 非数值"))
    else:
        st = PASS if _num_equal(txt_amv, float(amv_json)) else FAIL
        items.append(make_item("A1", "文本 AMV 值", st,
                               f"文本 AMV（亿，{how}）", txt_amv,
                               "amv_regime_latest.amv", amv_json))

    # A2 文本 MA10
    txt_ma10 = find_num(RE_MA10, text)
    if txt_ma10 is None or not has_ma10:
        items.append(make_item("A2", "文本 MA10", UNPARSED,
                               "文本 MA10", txt_ma10,
                               "amv_regime_latest.ma10", ma10_json,
                               "文本中未提取到 MA10" if txt_ma10 is None else "regime.ma10 非数值"))
    else:
        st = PASS if _num_equal(txt_ma10, float(ma10_json)) else FAIL
        note = ""
        if st == FAIL:
            diff = round(float(ma10_json) - txt_ma10, 2)
            note = f"文本 {txt_ma10} ／ JSON {ma10_json}，差 {diff}"
        items.append(make_item("A2", "文本 MA10", st,
                               "文本 MA10", txt_ma10,
                               "amv_regime_latest.ma10", ma10_json, note))

    # A3 文本 ratio = amv / ma10
    txt_ratio = find_num(RE_RATIO, text)
    if txt_ratio is None or not has_amv or not has_ma10 or float(ma10_json) == 0.0:
        items.append(make_item("A3", "文本 ratio", UNPARSED,
                               "文本 ratio", txt_ratio,
                               "amv/ma10", None,
                               "文本中未提取到 ratio" if txt_ratio is None else "amv/ma10 不可算"))
    else:
        calc = round(float(amv_json) / float(ma10_json), 4)
        # 文本 ratio 常规写 4 位小数；容差放宽到 1e-4 以吸收原文四舍五入
        st = PASS if _num_equal(txt_ratio, calc, 1e-4) else FAIL
        items.append(make_item("A3", "文本 ratio", st,
                               "文本 ratio", txt_ratio,
                               "round(amv/ma10, 4)", calc,
                               "" if st == PASS else f"原文写作 {txt_ratio}，口径算得 {calc}"))

    # A4 文本「当日涨跌%」↔ regime.pct
    txt_pct, pct_how = find_pct(text)
    if txt_pct is None or not isinstance(pct_json, (int, float)):
        items.append(make_item("A4", "文本 当日涨跌%", UNPARSED,
                               "文本当日涨跌%", txt_pct,
                               "amv_regime_latest.pct", pct_json,
                               "文本中未提取到「当日 X%」" if txt_pct is None else "regime.pct 非数值"))
    else:
        st = PASS if _num_equal(txt_pct, float(pct_json), 1e-2) else FAIL
        items.append(make_item("A4", "文本 当日涨跌%", st,
                               f"文本当日涨跌%（{pct_how}）", txt_pct,
                               "amv_regime_latest.pct", pct_json,
                               "" if st == PASS else "文本涨跌与 regime.pct（当日 AMV 环比）方向/量级不符"))

    # A5 空档第 N 日：JSON 内无权威起点（last_switch 是双轨确认日，不是空档起点）→ 只报区间，不判冲突
    txt_day = find_num(RE_EMPTY_DAY, text)
    last_switch = regime.get("last_switch") if isinstance(regime.get("last_switch"), dict) else {}
    sw_date = last_switch.get("date")
    regime_date = regime.get("date")
    span = span_counts(sw_date, regime_date) if sw_date and regime_date else None
    span_label = f"(last_switch.date={sw_date}, regime.date={regime_date}]"
    if txt_day is None:
        items.append(make_item("A5", "文本 空档第 N 日", UNPARSED,
                               "文本 N", None, "last_switch 推算区间",
                               {"natural_days": span[0], "weekdays": span[1]} if span else None,
                               "文本中未提取到「空档第 N 日」"))
    elif span is None:
        items.append(make_item("A5", "文本 空档第 N 日", UNPARSED,
                               "文本 N", int(txt_day), "last_switch 推算区间", None,
                               "last_switch.date 或 regime.date 缺失"))
    else:
        # 「空档第 N 日」的权威起点不在 JSON 里（last_switch 是双轨确认日，
        # 不是「AMV 首次跌破 MA10」那个起点）。只做能自证的那部分：
        # 文本 N 与 last_switch 起算的工作日数/自然日数任一相等 → PASS；
        # 不相等时无法区分「真冲突」还是「起点口径不同」→ UNPARSED，不猜。
        n = int(txt_day)
        refs = {"natural_days": span[0], "weekdays": span[1]}
        if n in (span[0], span[1]):
            items.append(make_item("A5", "文本 空档第 N 日", PASS,
                                   "文本 N", n, span_label, refs,
                                   "与 last_switch 起算的日数相符"))
        else:
            items.append(make_item("A5", "文本 空档第 N 日", UNPARSED,
                                   "文本 N", n, span_label, refs,
                                   f"文本 {n} 日与 last_switch 起算的 {span[1]}（工作日）/ {span[0]}（自然日）"
                                   "都不符；起点可能不是 last_switch（如首次跌破 MA10 日），JSON 无该字段，不判冲突"))

    # A6 证据：文本「距 MA10 差 X%」与现算 gap 对照，不参与判定
    txt_gap = find_num(RE_GAP, text)
    if txt_gap is not None and has_amv and has_ma10 and float(ma10_json):
        calc_gap = round((float(amv_json) / float(ma10_json) - 1) * 100, 2)
        items.append(make_item("A6", "文本「距 MA10 差 X%」（证据项，不判定）", "INFO",
                               "文本 gap %", txt_gap, "现算 gap %", calc_gap,
                               f"差 {round(abs(txt_gap - calc_gap), 2)} 个百分点"))
    return items


def check_cap_and_dates(
    regime: dict[str, Any],
    cap: dict[str, Any] | None,
    live: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    missing: dict[str, str],
) -> list[dict[str, Any]]:
    """B/C/D 项。每项只依赖自己需要的两份 JSON：缺什么就只跳过用到它的项。"""
    items: list[dict[str, Any]] = []

    def skip(iid: str, name: str, reason: str) -> None:
        items.append(make_item(iid, name, SKIP, "", None, "", None, reason))

    # B1 cap.regime ↔ regime.state
    if cap is None:
        skip("B1", "position_cap.regime ↔ regime.state", missing.get("position_cap_latest", "position_cap_latest 不可用"))
    else:
        cap_regime = cap.get("regime")
        reg_state = regime.get("state")
        if cap_regime is None or reg_state is None:
            items.append(make_item("B1", "position_cap.regime ↔ regime.state", UNPARSED,
                                   "position_cap_latest.regime", cap_regime,
                                   "amv_regime_latest.state", reg_state, "字段缺失"))
        else:
            st = PASS if str(cap_regime) == str(reg_state) else FAIL
            items.append(make_item("B1", "position_cap.regime ↔ regime.state", st,
                                   "position_cap_latest.regime", cap_regime,
                                   "amv_regime_latest.state", reg_state))

    # B2 cap.cap_amount ↔ plan.position_cap
    if cap is None or plan is None:
        skip("B2", "position_cap.cap_amount ↔ battle_plan.position_cap",
             missing.get("position_cap_latest") or missing.get("battle_plan", "battle_plan 不可用"))
    else:
        cap_amount = cap.get("cap_amount")
        plan_cap = plan.get("position_cap")
        if not isinstance(cap_amount, (int, float)) or not isinstance(plan_cap, (int, float)):
            items.append(make_item("B2", "position_cap.cap_amount ↔ battle_plan.position_cap", UNPARSED,
                                   "position_cap_latest.cap_amount", cap_amount,
                                   "battle_plan.position_cap", plan_cap,
                                   "一侧非数值（battle_plan.position_cap 若为对象需人工确认）"))
        else:
            st = PASS if _num_equal(cap_amount, plan_cap) else FAIL
            items.append(make_item("B2", "position_cap.cap_amount ↔ battle_plan.position_cap", st,
                                   "position_cap_latest.cap_amount", cap_amount,
                                   "battle_plan.position_cap", plan_cap))

    # C1 live.state_date ↔ regime.date
    if live is None:
        skip("C1", "amv_live.state_date ↔ regime.date", missing.get("amv_live", "amv_live 不可用"))
    else:
        state_date = live.get("state_date")
        reg_date = regime.get("date")
        if not state_date or not reg_date:
            items.append(make_item("C1", "amv_live.state_date ↔ regime.date", UNPARSED,
                                   "amv_live.state_date", state_date,
                                   "amv_regime_latest.date", reg_date, "字段缺失"))
        else:
            st = PASS if str(state_date) == str(reg_date) else FAIL
            items.append(make_item("C1", "amv_live.state_date ↔ regime.date", st,
                                   "amv_live.state_date", state_date,
                                   "amv_regime_latest.date", reg_date,
                                   "口径：live.state_date 是 regime 对应的上一收盘日，不与 trade_date 比"))

    # C2 cap.trade_date ↔ live.trade_date
    if cap is None or live is None:
        skip("C2", "position_cap.trade_date ↔ amv_live.trade_date",
             missing.get("position_cap_latest") or missing.get("amv_live", "amv_live 不可用"))
    else:
        cap_td = cap.get("trade_date")
        live_td = live.get("trade_date")
        if not cap_td or not live_td:
            items.append(make_item("C2", "position_cap.trade_date ↔ amv_live.trade_date", UNPARSED,
                                   "position_cap_latest.trade_date", cap_td,
                                   "amv_live.trade_date", live_td, "字段缺失"))
        else:
            st = PASS if str(cap_td) == str(live_td) else FAIL
            items.append(make_item("C2", "position_cap.trade_date ↔ amv_live.trade_date", st,
                                   "position_cap_latest.trade_date", cap_td,
                                   "amv_live.trade_date", live_td))

    # D1 / D2 plan 日期 ↔ cap.trade_date
    cap_td_for_plan = (cap or {}).get("trade_date")
    meta = (plan or {}).get("meta") if isinstance((plan or {}).get("meta"), dict) else {}
    for iid, label, value in (
        ("D1", "battle_plan.plan_date", (plan or {}).get("plan_date")),
        ("D2", "battle_plan.meta.trade_date", meta.get("trade_date")),
    ):
        if plan is None or cap is None:
            skip(iid, f"{label} ↔ position_cap.trade_date",
                 missing.get("battle_plan") or missing.get("position_cap_latest", "依赖文件不可用"))
        elif not value or not cap_td_for_plan:
            items.append(make_item(iid, f"{label} ↔ position_cap.trade_date", UNPARSED,
                                   label, value, "position_cap_latest.trade_date", cap_td_for_plan,
                                   "字段缺失"))
        else:
            st = PASS if str(value) == str(cap_td_for_plan) else FAIL
            items.append(make_item(iid, f"{label} ↔ position_cap.trade_date", st,
                                   label, value, "position_cap_latest.trade_date", cap_td_for_plan))
    return items


# ---------------------------------------------------------------- 输出

def summarize(items: list[dict[str, Any]]) -> dict[str, int]:
    """计数；INFO 是证据项，不计入 PASS/FAIL/UNPARSED。"""
    counts = {PASS: 0, FAIL: 0, UNPARSED: 0, SKIP: 0, INFO: 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return counts


def exit_code_for(counts: dict[str, int]) -> int:
    if counts.get(FAIL):
        return EXIT_FAIL
    if counts.get(UNPARSED):
        return EXIT_UNPARSED
    return EXIT_OK


def verdict_for(counts: dict[str, int]) -> str:
    if counts.get(FAIL):
        return "FAIL"
    if counts.get(UNPARSED):
        return "UNPARSED"
    return "PASS"


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value}"
    return str(value)


def render_md(payload: dict[str, Any]) -> str:
    items = payload["items"]
    counts = payload["counts"]
    lines: list[str] = []
    lines.append("# 面板产物一致性审计报告")
    lines.append("")
    lines.append(f"**结论：{payload['verdict']}**"
                 f"（PASS {counts[PASS]} / FAIL {counts[FAIL]} / UNPARSED {counts[UNPARSED]}"
                 f"{f' / SKIP {counts[SKIP]}' if counts[SKIP] else ''}）")
    lines.append("")
    lines.append(f"- 运行时间：{payload['generated_at']}")
    lines.append(f"- 数据目录（只读）：`{payload['panel_dir']}`")
    lines.append(f"- 原始结果：`tools/consistency_result.json`")
    lines.append(f"- 退出码：{payload['exit_code']}")
    lines.append("")

    fails = [it for it in items if it["status"] == FAIL]
    unparsed = [it for it in items if it["status"] == UNPARSED]
    if fails:
        lines.append("## 冲突项（必须处理）")
        lines.append("")
        for it in fails:
            lines.append(f"- **{it['id']} {it['name']}**："
                         f"`{it['left']['label']}` = {_fmt(it['left']['value'])} ／ "
                         f"`{it['right']['label']}` = {_fmt(it['right']['value'])}")
        lines.append("")
    if unparsed:
        lines.append("## 未解析项（需人工确认，不判冲突）")
        lines.append("")
        for it in unparsed:
            lines.append(f"- **{it['id']} {it['name']}**：{it['note'] or '无法提取'}")
        lines.append("")

    lines.append("## 逐项结果")
    lines.append("")
    lines.append("| ID | 检查项 | 结果 | 左值 | 右值 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for it in items:
        left = f"{it['left']['label']} = {_fmt(it['left']['value'])}" if it["left"]["label"] else "—"
        right = f"{it['right']['label']} = {_fmt(it['right']['value'])}" if it["right"]["label"] else "—"
        note = (it.get("note") or "").replace("|", "\\|")
        lines.append(f"| {it['id']} | {it['name']} | **{it['status']}** | {left} | {right} | {note} |")
    lines.append("")

    lines.append("## 证据：源字段原值")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(payload["evidence"], ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")

    lines.append("## 边界")
    lines.append("")
    lines.append("- 只读 `data/panel/*.json`；不连库、不发 HTTP、不写生产文件、不碰旧 8800/8002。")
    lines.append("- 数值容差 1e-6（ratio 按文本 4 位小数放宽到 1e-4，涨跌% 放宽到 1e-2）。")
    lines.append("- A5「空档第 N 日」：JSON 里没有权威起点字段（last_switch 是双轨确认日），"
                 "工具只报 last_switch 推算区间，不判冲突。")
    lines.append("- A6 是证据项，不进入退出码判定；它与 A2/A3 同根，用于说明文本 gap 与现算 gap 的差。")
    lines.append("- 本工具不改 `battle_plan.json`；发现冲突只报告。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程

def run(panel_dir: Path) -> dict[str, Any]:
    names = ("amv_regime_latest", "position_cap_latest", "amv_live", "battle_plan")
    loaded: dict[str, dict[str, Any] | None] = {}
    missing: dict[str, str] = {}
    for name in names:
        data, err = load_json(panel_dir, name)
        loaded[name] = data
        if err:
            missing[name] = err

    regime = loaded["amv_regime_latest"] or {}
    items: list[dict[str, Any]] = []

    if loaded["amv_regime_latest"] is None:
        items.extend(make_item(iid, nm, SKIP, "", None, "", None, missing["amv_regime_latest"])
                     for iid, nm in (("A1", "文本 AMV 值"), ("A2", "文本 MA10"),
                                     ("A3", "文本 ratio"), ("A4", "文本 当日涨跌%"),
                                     ("A5", "文本 空档第 N 日")))
    else:
        items.extend(check_text_amv(loaded["battle_plan"] or {}, regime))

    items.extend(check_cap_and_dates(regime, loaded["position_cap_latest"],
                                     loaded["amv_live"], loaded["battle_plan"], missing))

    counts = summarize(items)
    code = exit_code_for(counts)

    cap = loaded["position_cap_latest"] or {}
    live = loaded["amv_live"] or {}
    plan = loaded["battle_plan"] or {}
    forecast = plan.get("direction_forecast") if isinstance(plan.get("direction_forecast"), dict) else {}

    evidence = {
        "amv_regime_latest": {
            "date": regime.get("date"),
            "state": regime.get("state"),
            "ma10": regime.get("ma10"),
            "pct": regime.get("pct"),
            "amv": regime.get("amv"),
            "resonance": regime.get("resonance"),
            "last_switch": regime.get("last_switch"),
            "generated_at": regime.get("generated_at"),
        },
        "position_cap_latest": {
            "trade_date": cap.get("trade_date"),
            "regime": cap.get("regime"),
            "final_pct": cap.get("final_pct"),
            "cap_amount": cap.get("cap_amount"),
            "degraded_mode": (cap.get("degraded") or {}).get("mode")
            if isinstance(cap.get("degraded"), dict) else None,
            "computed_at": cap.get("computed_at"),
        },
        "amv_live": {
            "trade_date": live.get("trade_date"),
            "state_date": live.get("state_date"),
            "asof": live.get("asof"),
        },
        "battle_plan": {
            "plan_date": plan.get("plan_date"),
            "meta_trade_date": (plan.get("meta") or {}).get("trade_date")
            if isinstance(plan.get("meta"), dict) else None,
            "position_cap": plan.get("position_cap"),
            "judgment_head": (str(forecast.get("judgment") or "")[:400] or None),
        },
        "derived": {
            "amv_div_ma10": round(regime.get("amv") / regime.get("ma10"), 6)
            if isinstance(regime.get("amv"), (int, float)) and isinstance(regime.get("ma10"), (int, float))
            and regime.get("ma10") else None,
            "gap_pct": round((regime.get("amv") / regime.get("ma10") - 1) * 100, 2)
            if isinstance(regime.get("amv"), (int, float)) and isinstance(regime.get("ma10"), (int, float))
            and regime.get("ma10") else None,
        },
    }

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "panel_dir": str(panel_dir),
        "verdict": verdict_for(counts),
        "counts": counts,
        "exit_code": code,
        "missing": missing,
        "items": items,
        "evidence": evidence,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="面板产物一致性审计（只读）")
    ap.add_argument("--data-dir", default=None,
                    help="data 目录或 panel 目录；默认按 PANEL_V2_DATA_DIR / PANEL_V2_WS 推导")
    ap.add_argument("--json", default=str(DEFAULT_JSON), help="原始结果输出路径")
    ap.add_argument("--md", default=str(DEFAULT_MD), help="报告输出路径")
    ap.add_argument("--quiet", action="store_true", help="不打印逐项结果")
    args = ap.parse_args(argv)

    panel_dir = resolve_panel_dir(args.data_dir)
    if not panel_dir.is_dir():
        print(f"数据目录不存在：{panel_dir}", file=sys.stderr)
        return EXIT_FAIL

    payload = run(panel_dir)

    json_path = Path(args.json)
    md_path = Path(args.md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_md(payload), encoding="utf-8")

    if not args.quiet:
        print(f"结论：{payload['verdict']}  "
              f"PASS {payload['counts'][PASS]} / FAIL {payload['counts'][FAIL]} / "
              f"UNPARSED {payload['counts'][UNPARSED]}"
              + (f" / SKIP {payload['counts'][SKIP]}" if payload["counts"][SKIP] else ""))
        for it in payload["items"]:
            if it["status"] in (FAIL, UNPARSED):
                print(f"  {it['status']:8} {it['id']} {it['name']}: "
                      f"{_fmt(it['left']['value'])} vs {_fmt(it['right']['value'])}"
                      + (f"  # {it['note']}" if it.get("note") else ""))
        print(f"报告：{md_path}")
        print(f"原始：{json_path}")
    return payload["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
