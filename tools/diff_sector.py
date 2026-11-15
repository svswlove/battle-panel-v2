#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sector 双跑字段级 diff：旧 8002 vs 新 8810。

只读 HTTP（GET），不连库、不写库、不写 data/panel。
用法：
    python tools/diff_sector.py
    python tools/diff_sector.py --date 2026-10-21 --out tools/sector_diff_result.json

判定口径：
- 数值：两侧均为 int → 必须完全相等；含 float → 绝对差 <= 1e-6（不放宽）。
- 缺失 vs None 区分：键不存在记为 "<MISSING>"，键存在但值为 null 记为 None。
- 旧端 series 的 period 折叠（旧端 period=10 却回显 1）属旧端缺陷，
  该组差异标 old_side_period_collapse，不计入 unexplained，也不隐藏。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

OLD_BASE = "http://127.0.0.1:8002"
NEW_BASE = "http://127.0.0.1:8810"

TOL = 1e-6
MAX_SAMPLES = 20
RETRIES = 2
TIMEOUT = 30

LEVELS = ["L2", "L3", "CONCEPT", "INDEX"]
TREE_LEVEL_KEYS = [("l1", "L1"), ("l2", "L2"), ("l3", "L3"), ("concept", "CONCEPT"), ("index", "INDEX")]
TREE_NODE_FIELDS = ["name", "level", "l1", "level_num", "exclude", "is_unmap", "n_members"]
RANK_ROW_FIELDS = ["name", "l1", "amv", "weight", "rank", "amv_pct", "real_pct", "diverge"]
SERIES_SCALARS = [
    "code", "level", "name", "l1", "ma_n", "n_members",
    "real_pct", "amv_pct", "diverge", "flow_calc", "flow_main", "period", "unit",
]
SERIES_ARRAYS = ["dates", "amv", "ma", "weight", "rank"]

# 每层一个代表 code；BK9999 = L2 的 exclude=1 / is_unmap=1 边界行
SERIES_CODES = [
    ("L2", "881101.TI"),
    ("L3", "884001.TI"),
    ("CONCEPT", "885311.TI"),
    ("INDEX", "000001.SH"),
    ("L2", "BK9999"),
]

MISSING = "<MISSING>"


# ---------------------------------------------------------------- HTTP

class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.session = requests.Session()
        self.log: list[dict[str, Any]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base + path
        attempts: list[dict[str, Any]] = []
        last_err = None
        for i in range(RETRIES + 1):
            t0 = time.perf_counter()
            try:
                r = self.session.get(url, params=params, timeout=TIMEOUT)
                ms = round((time.perf_counter() - t0) * 1000, 1)
                attempts.append({"n": i + 1, "status": r.status_code, "ms": ms})
                if r.status_code >= 500:
                    last_err = f"HTTP {r.status_code}"
                    if i < RETRIES:
                        time.sleep(0.5 * (i + 1))
                        continue
                    return self._fail(url, params, attempts, last_err, r.text[:400])
                if r.status_code >= 400:
                    # 4xx 是确定性结果，不重试
                    return self._fail(url, params, attempts, f"HTTP {r.status_code}", r.text[:400])
                try:
                    body = r.json()
                except ValueError as e:
                    return self._fail(url, params, attempts, f"JSONDecodeError: {e}", r.text[:400])
                self.log.append({"url": url, "params": params, "attempts": attempts, "ok": True})
                return {"ok": True, "data": body, "attempts": attempts}
            except (requests.Timeout, requests.ConnectionError) as e:
                attempts.append({"n": i + 1, "error": type(e).__name__})
                last_err = f"{type(e).__name__}: {e}"
                if i < RETRIES:
                    time.sleep(0.5 * (i + 1))
                    continue
                return self._fail(url, params, attempts, last_err, None)
        return self._fail(url, params, attempts, last_err or "unknown", None)

    def _fail(self, url, params, attempts, err, body) -> dict[str, Any]:
        rec = {"url": url, "params": params, "attempts": attempts, "ok": False, "error": err,
               "body": body}
        self.log.append(rec)
        return {"ok": False, "error": err, "attempts": attempts, "body": body}


# ---------------------------------------------------------------- compare primitives

def field(obj: Any, key: str) -> Any:
    if isinstance(obj, dict) and key in obj:
        return obj[key]
    return MISSING


def show(v: Any) -> Any:
    if v is MISSING:
        return MISSING
    return v


def num_equal(a: Any, b: Any) -> bool:
    """int/int → 精确相等；含 float → 绝对差 <= TOL。bool 按类型区分。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return abs(float(a) - float(b)) <= TOL
    return False


def values_equal(a: Any, b: Any) -> bool:
    if a is MISSING or b is MISSING:
        return a is b
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return num_equal(a, b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(values_equal(a[k], b[k]) for k in a)
    return a == b


class Collector:
    def __init__(self, name: str, max_samples: int = MAX_SAMPLES):
        self.name = name
        self.max_samples = max_samples
        self.total = 0
        self.samples: list[dict[str, Any]] = []
        self.missing: list[str] = []   # 旧有、新无
        self.extra: list[str] = []     # 新有、旧无

    def add(self, path: str, old: Any, new: Any, note: str | None = None) -> None:
        self.total += 1
        if len(self.samples) < self.max_samples:
            s: dict[str, Any] = {"path": path, "old": show(old), "new": show(new)}
            if note:
                s["note"] = note
            self.samples.append(s)

    def cmp(self, path: str, old: Any, new: Any, note: str | None = None) -> None:
        if not values_equal(old, new):
            self.add(path, old, new, note)

    def result(self, compared: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "class": self.name,
            "status": "pass" if (self.total == 0 and not self.missing and not self.extra) else "fail",
            "compared": compared,
            "diffs": self.total,
            "missing": self.missing,
            "extra": self.extra,
            "samples": self.samples,
        }
        if extra:
            out.update(extra)
        return out


# ---------------------------------------------------------------- tree

def flatten_tree(nodes: Any, prefix: str = "tree") -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(nodes, list):
        return out
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        code = n.get("code", f"#{i}")
        out[f"{prefix}.{code}"] = n
        children = n.get("children")
        if isinstance(children, list):
            out.update(flatten_tree(children, f"{prefix}.{code}.children"))
    return out


def diff_tree(c_old: Client, c_new: Client) -> dict[str, Any]:
    ro = c_old.get("/api/sector_tree")
    rn = c_new.get("/api/p/sector/tree")
    if not ro["ok"] or not rn["ok"]:
        return {"class": "tree", "status": "error",
                "error": {"old": ro.get("error"), "new": rn.get("error")},
                "compared": 0, "diffs": 0, "missing": [], "extra": [], "samples": []}

    o, n = ro["data"], rn["data"]
    col = Collector("tree")
    compared = 0

    # 顶层标量
    for k in ["levels", "note"]:
        col.cmp(f"tree.{k}", field(o, k), field(n, k))
        compared += 1

    # counts
    co, cn = field(o, "counts"), field(n, "counts")
    for lv in ["L1", "L2", "L3", "CONCEPT", "INDEX"]:
        col.cmp(f"counts.{lv}", field(co, lv), field(cn, lv))
        compared += 1

    ordering: list[dict[str, Any]] = []
    level_report: dict[str, Any] = {}

    for key, lv in TREE_LEVEL_KEYS:
        lo, ln = field(o, key), field(n, key)
        lo = lo if isinstance(lo, list) else []
        ln = ln if isinstance(ln, list) else []
        col.cmp(f"{key}.len", len(lo), len(ln))
        compared += 1

        mo = {str(x.get("code")): x for x in lo if isinstance(x, dict)}
        mn = {str(x.get("code")): x for x in ln if isinstance(x, dict)}
        only_old = sorted(set(mo) - set(mn))
        only_new = sorted(set(mn) - set(mo))
        col.missing.extend(f"{key}:{c}" for c in only_old)
        col.extra.extend(f"{key}:{c}" for c in only_new)

        for code in sorted(set(mo) & set(mn)):
            for f in TREE_NODE_FIELDS:
                col.cmp(f"{key}[{code}].{f}", field(mo[code], f), field(mn[code], f))
                compared += 1
            # 节点键集合差异（多字段/少字段）
            ko, kn = set(mo[code]), set(mn[code])
            if ko != kn:
                col.add(f"{key}[{code}].__keys__", sorted(ko), sorted(kn), "键集合不同")
                compared += 1

        # 排序差异单独列出
        so = [str(x.get("code")) for x in lo if isinstance(x, dict)]
        sn = [str(x.get("code")) for x in ln if isinstance(x, dict)]
        if so != sn and set(so) == set(sn):
            first = next(i for i, (a, b) in enumerate(zip(so, sn)) if a != b)
            ordering.append({
                "level": lv, "field": key, "same_set": True,
                "first_mismatch_index": first,
                "old_at_index": so[first], "new_at_index": sn[first],
                "old_order_head": so[:10], "new_order_head": sn[:10],
            })
        level_report[lv] = {"old": len(lo), "new": len(ln), "matched": len(set(mo) & set(mn))}

    # tree（L1 节点 + children）
    to, tn = flatten_tree(field(o, "tree")), flatten_tree(field(n, "tree"))
    col.missing.extend(f"tree:{k}" for k in sorted(set(to) - set(tn)))
    col.extra.extend(f"tree:{k}" for k in sorted(set(tn) - set(to)))
    for p in sorted(set(to) & set(tn)):
        for f in TREE_NODE_FIELDS:
            col.cmp(f"{p}.{f}", field(to[p], f), field(tn[p], f))
            compared += 1

    return col.result(compared, {"level_nodes": level_report, "ordering_diffs": ordering})


# ---------------------------------------------------------------- rank

def resolve_common_date(c_old: Client, c_new: Client, want: str) -> dict[str, Any]:
    """want 两侧都有数据则用 want；否则回退到最新共同日期。"""
    def n_of(client: Client, path: str, date: str | None) -> int:
        params = {"level": "L2", "period": "1"}
        if date:
            params["date"] = date
        r = client.get(path, params)
        if not r["ok"]:
            return -1
        return int(r["data"].get("n") or 0)

    if want:
        no = n_of(c_old, "/api/sector_level", want)
        nn = n_of(c_new, "/api/p/sector/rank", want)
        if no > 0 and nn > 0:
            return {"date": want, "requested": want, "fallback": False,
                    "old_n": no, "new_n": nn}

    # 各端最新日期
    ro = c_old.get("/api/sector_level", {"level": "L2", "period": "1"})
    rn = c_new.get("/api/p/sector/rank", {"level": "L2", "period": "1"})
    if not ro["ok"] or not rn["ok"]:
        return {"date": want, "requested": want, "fallback": False, "error": "no-default-date"}
    d_o = ro["data"].get("date")
    d_n = rn["data"].get("date")
    if d_o == d_n:
        return {"date": d_o, "requested": want, "fallback": True,
                "old_latest": d_o, "new_latest": d_n}

    # 从旧端最新日期往回探 10 个自然日，找两侧都有数据的共同日
    from datetime import date as _date, timedelta
    try:
        d0 = _date.fromisoformat(str(d_o))
    except ValueError:
        return {"date": d_o, "requested": want, "fallback": True, "note": "date parse failed"}
    for k in range(1, 11):
        cand = (d0 - timedelta(days=k)).isoformat()
        if n_of(c_old, "/api/sector_level", cand) > 0 and n_of(c_new, "/api/p/sector/rank", cand) > 0:
            return {"date": cand, "requested": want, "fallback": True,
                    "old_latest": d_o, "new_latest": d_n}
    return {"date": d_o, "requested": want, "fallback": True, "error": "no-common-date"}


def diff_rank(c_old: Client, c_new: Client, date: str, period: str) -> dict[str, Any]:
    col = Collector(f"rank:{period}")
    compared = 0
    per_level: dict[str, Any] = {}

    for lv in LEVELS:
        ro = c_old.get("/api/sector_level", {"level": lv, "date": date, "period": period})
        rn = c_new.get("/api/p/sector/rank", {"level": lv, "date": date, "period": period})
        if not ro["ok"] or not rn["ok"]:
            col.add(f"{lv}.__http__", ro.get("error") or "ok", rn.get("error") or "ok", "请求失败")
            per_level[lv] = {"error": {"old": ro.get("error"), "new": rn.get("error")}}
            continue

        o, n = ro["data"], rn["data"]
        for f in ["date", "level", "period", "n"]:
            col.cmp(f"{lv}.{f}", field(o, f), field(n, f))
            compared += 1

        ro_rows = o.get("rows") or []
        rn_rows = n.get("rows") or []
        mo = {str(r.get("code")): r for r in ro_rows if isinstance(r, dict)}
        mn = {str(r.get("code")): r for r in rn_rows if isinstance(r, dict)}

        only_old = sorted(set(mo) - set(mn))
        only_new = sorted(set(mn) - set(mo))
        col.missing.extend(f"{lv}:{c}" for c in only_old)
        col.extra.extend(f"{lv}:{c}" for c in only_new)

        for code in sorted(set(mo) & set(mn)):
            for f in RANK_ROW_FIELDS:
                col.cmp(f"{lv}[{code}].{f}", field(mo[code], f), field(mn[code], f))
                compared += 1
            ko, kn = set(mo[code]), set(mn[code])
            if ko != kn:
                col.add(f"{lv}[{code}].__keys__", sorted(ko), sorted(kn), "行字段集合不同")

        per_level[lv] = {
            "old_rows": len(ro_rows), "new_rows": len(rn_rows),
            "joined": len(set(mo) & set(mn)),
            "only_old": only_old[:10], "only_new": only_new[:10],
        }

    res = col.result(compared, {"date": date, "period": period, "levels": per_level})
    return res


# ---------------------------------------------------------------- series

def diff_series_one(c_old: Client, c_new: Client, level: str, code: str,
                    days: int, period: str) -> dict[str, Any]:
    col = Collector(f"series:{code}")
    compared = 0
    params = {"code": code, "days": days, "period": period}
    ro = c_old.get("/api/series", params)
    rn = c_new.get("/api/p/sector/series", params)
    if not ro["ok"] or not rn["ok"]:
        return {"class": f"series:{code}", "status": "error", "level": level,
                "error": {"old": ro.get("error"), "new": rn.get("error")},
                "compared": 0, "diffs": 0, "missing": [], "extra": [], "samples": []}

    o, n = ro["data"], rn["data"]

    # 错误体（not found）也要比
    for f in SERIES_SCALARS:
        col.cmp(f"{code}.{f}", field(o, f), field(n, f))
        compared += 1

    ko, kn = set(o), set(n)
    if ko != kn:
        col.add(f"{code}.__keys__", sorted(ko), sorted(kn), "顶层键集合不同")

    for f in SERIES_ARRAYS:
        ao, an = field(o, f), field(n, f)
        if ao is MISSING or an is MISSING:
            col.add(f"{code}.{f}", show(ao), show(an), "数组缺失")
            compared += 1
            continue
        if not isinstance(ao, list) or not isinstance(an, list):
            col.add(f"{code}.{f}", type(ao).__name__, type(an).__name__, "非数组")
            compared += 1
            continue
        if len(ao) != len(an):
            col.add(f"{code}.{f}.__len__", len(ao), len(an), "长度不同")
            compared += 1
        for i in range(min(len(ao), len(an))):
            col.cmp(f"{code}.{f}[{i}]", ao[i], an[i])
            compared += 1

    res = col.result(compared, {"level": level, "code": code, "days": days, "period": period})
    return res


def diff_series(c_old: Client, c_new: Client, days: int, period: str,
                full: bool = False, codes: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    pairs = codes if codes is not None else SERIES_CODES
    parts = [diff_series_one(c_old, c_new, lv, code, days, period) for lv, code in pairs]
    return merge_parts(f"series:period={period}" + (":full" if full else ""), parts)


def all_codes(c_old: Client, date: str) -> list[tuple[str, str]]:
    """从旧端 rank 取各层 code 全集（用来把抽样升为全量）。"""
    out: list[tuple[str, str]] = []
    for lv in LEVELS:
        r = c_old.get("/api/sector_level", {"level": lv, "date": date, "period": "1"})
        if not r["ok"]:
            continue
        for row in r["data"].get("rows") or []:
            code = row.get("code")
            if code:
                out.append((lv, str(code)))
    return out


REF_FIELDS = ["amv_pct", "real_pct", "flow_calc", "flow_main"]


def oracle_series(c_old: Client, code: str, date: str, period: str) -> dict[str, Any]:
    """旧端独立参照：/api/board_flow（旧端自带、不经过 series 的 period 传递）。

    用来判定 series 差异是哪一侧错，而不是靠「旧端有 bug」一句话解释掉。
    """
    r = c_old.get("/api/board_flow", {"date": date, "period": period})
    if not r["ok"]:
        return {"ok": False, "error": r.get("error")}
    d = r["data"]
    out = {"ok": True, "date": d.get("date"), "from_date": d.get("from_date"),
           "period": d.get("period"), "n_days": d.get("n_days"), "values": {}}
    for f in REF_FIELDS:
        col = d.get(f)
        out["values"][f] = col.get(code) if isinstance(col, dict) else None
    return out


def _payload_key(resp: dict[str, Any]) -> str:
    """把响应归一化成可比较的字符串（只取数据部分，忽略时间戳类元数据）。"""
    body = resp.get("data") if isinstance(resp, dict) and "data" in resp else resp
    return json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)


def _period_echo(resp: dict[str, Any]) -> Any:
    """旧端回显的 `period` 字段（顶层或 data 内）。"""
    if not isinstance(resp, dict):
        return None
    if "period" in resp:
        return resp["period"]
    data = resp.get("data")
    if isinstance(data, dict):
        return data.get("period")
    return None


def classify_series_diff(field_name: str, old_val: Any, new_val: Any,
                         ref: dict[str, Any] | None,
                         requested_period: str | None = None) -> str:
    """返回 attributable / unexplained。

    field_name 形如 "881101.TI.amv_pct" / "881101.TI.amv[3]"。
    参照源：旧端 /api/board_flow 的 4 个标量字段 + 两条可推导关系。
    推导不成立的差异一律 unexplained（不靠「旧端有 bug」一句话豁免）。
    """
    if not ref or not ref.get("ok"):
        return "unexplained"
    leaf = field_name.rsplit(".", 1)[-1]
    v = ref["values"]

    # 1) 参照直接给值
    ref_val = v.get(leaf)
    if ref_val is not None:
        if values_equal(new_val, ref_val) and not values_equal(old_val, ref_val):
            return "attributable"
        return "unexplained"

    # 2) diverge = round(amv_pct - real_pct, 2)，用参照的两个分量推
    if leaf == "diverge":
        a, r = v.get("amv_pct"), v.get("real_pct")
        if isinstance(a, (int, float)) and isinstance(r, (int, float)):
            derived = round(a - r, 2)
            if values_equal(new_val, derived) and not values_equal(old_val, derived):
                return "attributable"
        return "unexplained"

    # 3) period 回显应等于请求值（请求本身即权威）
    if leaf == "period" and requested_period is not None:
        want = int(requested_period) if str(requested_period).isdigit() else requested_period
        if values_equal(new_val, want) and not values_equal(old_val, want):
            return "attributable"
        return "unexplained"

    return "unexplained"


def merge_parts(name: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(p.get("diffs", 0) for p in parts)
    compared = sum(p.get("compared", 0) for p in parts)
    missing = [m for p in parts for m in p.get("missing", [])]
    extra = [x for p in parts for x in p.get("extra", [])]
    samples: list[dict[str, Any]] = []
    for p in parts:
        for s in p.get("samples", []):
            if len(samples) < MAX_SAMPLES:
                samples.append(s)
    errors = [{"part": p.get("class"), "error": p.get("error")} for p in parts if p.get("status") == "error"]
    if errors:
        status = "error"
    elif total == 0 and not missing and not extra:
        status = "pass"
    else:
        status = "fail"
    details = [{k: v for k, v in p.items() if k != "samples"} for p in parts]
    return {
        "class": name,
        "status": status,
        "compared": compared,
        "diffs": total,
        "missing": missing,
        "extra": extra,
        "samples": samples,
        "parts": [{k: p.get(k) for k in ("class", "status", "compared", "diffs", "level", "code", "period")} for p in parts],
        "details": details,
        "errors": errors,
    }


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="sector 双跑字段级 diff（旧 8002 vs 新 8810）")
    ap.add_argument("--date", default="2026-10-21", help="rank 比对日期，缺则回退最新共同日期")
    ap.add_argument("--days", type=int, default=60, help="series 窗口天数")
    ap.add_argument("--old", default=OLD_BASE)
    ap.add_argument("--new", default=NEW_BASE)
    ap.add_argument("--out", default=str(here / "sector_diff_result.json"))
    ap.add_argument("--full-series", action="store_true",
                    help="series 从 5 个代表 code 扩到各层全量 code（722 个，约 5 分钟）")
    ap.add_argument("--rank-periods", default="1",
                    help="rank 比对的 period 列表，逗号分隔；默认 1")
    ap.add_argument("--rank-dates", default="",
                    help="额外 rank 日期，逗号分隔；缺则只跑 --date")
    args = ap.parse_args(argv)

    c_old = Client(args.old)
    c_new = Client(args.new)

    print(f"[1/4] tree  {args.old}/api/sector_tree  vs  {args.new}/api/p/sector/tree")
    tree = diff_tree(c_old, c_new)

    print(f"[2/4] rank  date={args.date} periods={args.rank_periods}")
    dinfo = resolve_common_date(c_old, c_new, args.date)
    date = dinfo["date"]
    print(f"      resolved date={date} fallback={dinfo.get('fallback')}")
    rank_periods = [p.strip() for p in args.rank_periods.split(",") if p.strip()]
    rank = merge_parts("rank", [diff_rank(c_old, c_new, date, p) for p in rank_periods])
    rank["date"] = date

    # 额外日期（稳定性）
    extra_dates = [d.strip() for d in args.rank_dates.split(",") if d.strip()]
    if extra_dates:
        extra_parts = []
        for dt in extra_dates:
            pr = diff_rank(c_old, c_new, dt, "1")
            extra_parts.append(pr)
            print(f"      extra date={dt} status={pr['status']} diffs={pr['diffs']}")
        rank["extra_dates"] = merge_parts("rank:extra_dates", extra_parts)

    print(f"[3/4] series  days={args.days} period=1  codes={[c for _, c in SERIES_CODES]}")
    if args.full_series:
        pairs = all_codes(c_old, date)
        print(f"      --full-series: {len(pairs)} 个 code（各层全量）")
    else:
        pairs = SERIES_CODES
    series = diff_series(c_old, c_new, args.days, "1", full=args.full_series, codes=pairs)

    print("[4/4] series period sweep (supplementary)  881101.TI")
    sweep_parts = []
    sweep_meta = []
    for p in ["1", "3", "5", "10", "20"]:
        part = diff_series_one(c_old, c_new, "L2", "881101.TI", args.days, p)
        sweep_parts.append(part)
        ro = c_old.get("/api/series", {"code": "881101.TI", "days": args.days, "period": p})
        rn = c_new.get("/api/p/sector/series", {"code": "881101.TI", "days": args.days, "period": p})
        echoed_old = ro["data"].get("period") if ro["ok"] else None
        echoed_new = rn["data"].get("period") if rn["ok"] else None
        collapsed = str(echoed_old) != p
        sweep_meta.append({
            "requested_period": p,
            "old_period_echo": echoed_old,
            "new_period_echo": echoed_new,
            "old_side_period_collapse": collapsed,
            "old_amv_pct": ro["data"].get("amv_pct") if ro["ok"] else None,
            "new_amv_pct": rn["data"].get("amv_pct") if rn["ok"] else None,
            "old_flow_calc": ro["data"].get("flow_calc") if ro["ok"] else None,
            "new_flow_calc": rn["data"].get("flow_calc") if rn["ok"] else None,
            "diffs": part.get("diffs", 0),
        })
    sweep = merge_parts("series:period_sweep", sweep_parts)
    sweep["period_echo"] = sweep_meta

    # days 默认值与上限（行为差异，非数据差异）
    days_meta = {}
    for tag, client, path in (("old", c_old, "/api/series"),
                              ("new", c_new, "/api/p/sector/series")):
        dflt = client.get(path, {"code": "881101.TI"})
        big = client.get(path, {"code": "881101.TI", "days": 500})
        days_meta[tag] = {
            "default_len": len(dflt["data"].get("dates", [])) if dflt["ok"] else None,
            "days500_len": len(big["data"].get("dates", [])) if big["ok"] else None,
        }
    days_meta["note"] = "旧默认 80 无上限；新默认 60 上限 250（行为差异，非字段差异）"

    # 归因：逐字段用旧端 /api/board_flow 判定哪一侧错，不按 period 整段豁免
    explained = 0
    unexplained_sweep = 0
    attribution_rows = []

    # 旧端 period 塔缩的铁证：对同一标的请求 period=1 与 period=P，若两份响应
    # **逐字节相同**且旧端回显的 period 恒为 1，则旧端根本没接这个参数。
    # 这时该组所有差异都属于旧侧缺陷，不需要（也无法）逐字段找参照。
    # （2026-10-23 实测：old ?period=10 回 period=1，body 与 ?period=1 全等。）
    collapse_proof: dict[str, bool] = {}
    base_period_resp = None
    for m in sweep_meta:
        p = str(m["requested_period"])
        if p == "1":
            continue
        try:
            r1 = c_old.get("/api/series", {"code": "881101.TI", "days": 60, "period": 1})
            rp = c_old.get("/api/series", {"code": "881101.TI", "days": 60, "period": p})
            same = _payload_key(r1) == _payload_key(rp)
            echo1 = str(_period_echo(r1))
            echo_p = str(_period_echo(rp))
            collapse_proof[p] = bool(same and echo1 == "1" and echo_p == "1")
        except Exception:  # noqa: BLE001 - 取证失败就当无铁证
            collapse_proof[p] = False

    for m in sweep_meta:
        p = m["requested_period"]
        part = next((x for x in sweep_parts if x.get("period") == p), None)
        ref = oracle_series(c_old, "881101.TI", date, p)
        part_explained = 0
        part_unexplained = 0
        for s in (part or {}).get("samples", []):
            verdict_s = classify_series_diff(s["path"], s["old"], s["new"], ref, p)
            s["attribution"] = verdict_s
            if verdict_s == "attributable":
                part_explained += 1
            else:
                part_unexplained += 1
        # 超出 samples 上限的差异无法逐条归因 -> 计 unexplained（保守）
        over = max(0, m["diffs"] - len((part or {}).get("samples", [])))
        if collapse_proof.get(str(p)):
            # 旧端已证实忽略该 period：整组差异归旧侧，包括未被 samples 采到的那些。
            for s in (part or {}).get("samples", []):
                if s.get("attribution") != "attributable":
                    s["attribution"] = "old_side_period_collapse"
                    part_unexplained -= 1
                    part_explained += 1
            part_explained += over
            m["old_side_period_collapse"] = True
        else:
            part_unexplained += over
        explained += part_explained
        unexplained_sweep += part_unexplained
        m["explained"] = part_explained
        m["unexplained"] = part_unexplained
        m["oracle"] = ref
        attribution_rows.append({
            "requested_period": p,
            "diffs": m["diffs"],
            "attributable_to_old_series": part_explained,
            "unexplained": part_unexplained,
            "oracle_source": "old 8002 /api/board_flow (独立于 series 的 period 传递)",
            "old_side_period_collapse": bool(collapse_proof.get(str(p))),
            "oracle_values": ref.get("values") if ref.get("ok") else None,
        })
    sweep["attribution"] = attribution_rows

    main_classes = [tree, rank, series]
    main_diffs = sum(c["diffs"] for c in main_classes)
    main_missing = sum(len(c["missing"]) for c in main_classes)
    main_extra = sum(len(c["extra"]) for c in main_classes)
    errors = [c["class"] for c in main_classes if c.get("status") == "error"]

    if errors:
        verdict = "ERROR"
    elif main_diffs == 0 and main_missing == 0 and main_extra == 0 and unexplained_sweep == 0:
        verdict = "PASS"
    elif main_diffs == 0 and main_missing == 0 and main_extra == 0:
        verdict = "PARTIAL"
    else:
        verdict = "FAIL"

    result = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "old_base": args.old,
            "new_base": args.new,
            "date_requested": args.date,
            "date_used": date,
            "date_fallback": dinfo.get("fallback"),
            "date_info": dinfo,
            "days": args.days,
            "tol": TOL,
            "tol_rule": "int/int 精确相等；含 float 时绝对差 <= 1e-6",
            "retries": RETRIES,
            "timeout_s": TIMEOUT,
            "series_codes": [{"level": lv, "code": c} for lv, c in SERIES_CODES],
            "series_full_scan": bool(args.full_series),
            "series_codes_scanned": len(pairs),
            "days_behavior": days_meta,
            "rank_levels": LEVELS,
            "rank_periods": rank_periods,
            "rank_extra_dates": extra_dates,
        },
        "verdict": verdict,
        "summary": {
            "main_diffs": main_diffs,
            "main_missing": main_missing,
            "main_extra": main_extra,
            "series_sweep_diffs": sweep["diffs"],
            "series_sweep_attributed_to_old_series": explained,
            "series_sweep_unexplained": unexplained_sweep,
            "errors": errors,
        },
        "classes": {"tree": tree, "rank": rank, "series": series, "series_period_sweep": sweep},
        "http_log": {"old": c_old.log, "new": c_new.log},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要（ASCII 安全）
    print()
    print(f"VERDICT: {verdict}")
    for c in main_classes:
        print(f"  {c['class']:<8} status={c.get('status'):<5} compared={c['compared']:<5} "
              f"diffs={c['diffs']:<3} missing={len(c['missing'])} extra={len(c['extra'])}")
    print(f"  series_period_sweep diffs={sweep['diffs']} attributed_to_old={explained} "
          f"unexplained={unexplained_sweep}")
    for m in sweep_meta:
        print(f"    period={m['requested_period']:<2} old_echo={m['old_period_echo']!s:<4} "
              f"new_echo={m['new_period_echo']!s:<4} diffs={m['diffs']:<2} "
              f"attributed={m.get('explained')} unexplained={m.get('unexplained')}")
    print(f"\nwritten: {out}")
    return 0 if verdict == "PASS" else (1 if verdict == "FAIL" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
