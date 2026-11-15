# -*- coding: utf-8 -*-
"""旧端点兼容性验收：逐端点比对「8810 新实现」与「旧端 8800/8002」的响应。

**用途**：兼容插件（compat / compat_kline / compat_sector / ...）由子代理实现，
但它们的环境没有 shell，无法自行验证。这个脚本是**唯一的验收依据**。

判定口径（与 shadow 一致）：
- **结构（键路径集合）必须一致** —— 键名缺失/多余一律判 fail
- **值允许差异**（数据在更新、精度不同），但会统计条数与最大相对差，供人工判断
- 数组：按**下标**对齐比结构（长度不同不判 fail，但记录长度差）

用法：
    python tools/compare_legacy.py                # 全部已登记端点
    python tools/compare_legacy.py --batch 1      # 只跑第 1 批
    python tools/compare_legacy.py --only /api/gates /api/status
    python tools/compare_legacy.py --json out.json

退出码：0 = 全部结构一致；1 = 有结构差异或请求失败。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any

try:
    import requests
except ImportError:
    print("需要 requests", file=sys.stderr)
    raise SystemExit(2)

NEW = "http://127.0.0.1:8810"
OLD_8800 = "http://127.0.0.1:8800"
OLD_8002 = "http://127.0.0.1:8002"
TIMEOUT = 30


@dataclass
class Pair:
    path: str          # 旧路径（也是新路径，root 挂载后完全同名）
    batch: int
    old_base: str = OLD_8800
    params: dict[str, str] | None = None
    note: str = ""
    method: str = "GET"
    body: dict | None = None
    # 「骨架实现」允许缺失的键（正则，匹配键路径）。
    # 用于那些数据源不在白名单、只返回 null 骨架的字段：结构上可以缺，
    # 但**必须显式声明**，否则缺一个键就判 fail。
    allow_missing: tuple[str, ...] = ()


PAIRS: list[Pair] = [
    # ---- 第 1 批：静态 JSON 与基础只读
    Pair("/api/dashboard.json", 1),
    Pair("/api/battle_plan.json", 1),
    Pair("/api/amv_live.json", 1),
    Pair("/api/amv_regime_latest.json", 1),
    Pair("/api/position_cap_latest.json", 1),
    Pair("/api/alert_todo.json", 1),
    Pair("/api/alert_plan.json", 1),
    Pair("/api/push_types.json", 1),
    Pair("/api/concept.json", 1),
    Pair("/api/positions.json", 1),
    Pair("/api/real_positions", 1),
    Pair("/api/gates", 1),
    Pair("/api/status", 1),
    Pair("/api/alerts", 1),
    Pair("/api/search/all", 1, method="POST", body={"q": "600519", "limit": 20}),
    # ---- 第 2 批：K 线域
    Pair("/api/kline/600519.SH", 2, params={"period": "d", "limit": "60"}),    Pair("/api/kline_bg/600519.SH", 2, params={"mode": "stock"}),
    Pair("/api/intraday/600519.SH", 2,
         # 旧端偶尔带 `cached`（磁盘缓存命中标记），时有时无；不是业务字段。
         allow_missing=(r"^cached$",)),
    Pair("/api/intraday_multi/600519.SH", 2, params={"days": "5"}),
    Pair("/api/quote5/600519.SH", 2),
    Pair("/api/volume_profile/600519.SH", 2),
    Pair("/api/volume_ticks/600519.SH", 2),
    Pair("/api/f10_brief/600519.SH", 2,
         # F10 的 B2 段（十大股东变动/融资融券/一致预期等）需 eltdx F10 HTTP 客户端，
         # 门面不暴露 → 只出 null 骨架。列表元素结构表达不出来，显式允许缺失。
         allow_missing=(r"^gd_plans\[\]\.", r"^rzrq\[\]\.", r"^perf_q\.periods\[\]\.",
                        r"^perf_q\.years\.", r"^gdhs\.periods\[\]\.", r"^north\.",
                        r"^eps_forecast\.", r"^perf\.")),
    # ---- 第 3 批：板块域（先按 8800 探；若 404 会提示改用 8002）
    Pair("/api/sector_card.json", 3),
    Pair("/api/sector_intraday.json", 3),
    Pair("/api/sector_spark.json", 3),
    Pair("/api/sector_track.json", 3),
    Pair("/api/sector_track_backfill_status", 3),
    Pair("/api/sector/health", 3),
    Pair("/api/etf_sector_map.json", 3),
    Pair("/api/breadth_880.json", 3),
    Pair("/api/temperature_latest.json", 3),
]


def fetch(session: requests.Session, base: str, path: str,
          params: dict[str, str] | None, method: str = "GET",
          body: dict | None = None) -> tuple[int, Any]:
    try:
        if method.upper() == "POST":
            r = session.post(base + path, json=body or {}, timeout=TIMEOUT)
        else:
            r = session.get(base + path, params=params, timeout=TIMEOUT)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:400]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def skeleton(value: Any, prefix: str = "", out: set[str] | None = None,
             depth: int = 0, big_dict: int = 20) -> set[str]:
    """收集键路径骨架。

    两个关键抽象：
    - 数组记成 `[]`，不把每个下标当结构
    - **动态键字典**（键是股票/板块代码，数量 > big_dict）记成 `{}` + 取样本值的骨架。
      否则「旧端多 200 个板块」会被当成「多 200 个结构键」误判 fail。
    """
    if out is None:
        out = set()
    if depth > 8:
        return out
    if isinstance(value, dict):
        if len(value) > big_dict:
            out.add(f"{prefix}{{}}")
            first = next(iter(value.values()))
            skeleton(first, f"{prefix}{{}}", out, depth + 1, big_dict)
            return out
        for k, v in value.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.add(p)
            skeleton(v, p, out, depth + 1, big_dict)
    elif isinstance(value, list):
        p = f"{prefix}[]"
        out.add(p)
        if value:
            skeleton(value[0], p, out, depth + 1, big_dict)
    return out


def empty_array_paths(value: Any, prefix: str = "", out: set[str] | None = None,
                      depth: int = 0) -> set[str]:
    """找出「旧端为空数组」的路径。

    用途：旧端某数组为空时 skeleton 不展开元素字段，新版有数据时会展开，
    于是看起来「多了一堆键」—— 那是数据差不是结构差。这些路径下的额外键要豁免。
    """
    if out is None:
        out = set()
    if depth > 8:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            empty_array_paths(v, p, out, depth + 1)
    elif isinstance(value, list):
        if not value:
            out.add(f"{prefix}[]")
        elif value:
            empty_array_paths(value[0], f"{prefix}[]", out, depth + 1)
    return out


def diff_values(old: Any, new: Any, prefix: str = "", out: list | None = None,
                depth: int = 0) -> list[dict]:
    """逐值比对（只比两侧都有的键），记录差异条数。"""
    if out is None:
        out = []
    if depth > 8 or len(out) > 200:
        return out
    if isinstance(old, dict) and isinstance(new, dict):
        for k in old:
            if k in new:
                diff_values(old[k], new[k], f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(old, list) and isinstance(new, list):
        for i, (a, b) in enumerate(zip(old, new)):
            diff_values(a, b, f"{prefix}[{i}]", out, depth + 1)
    else:
        if isinstance(old, bool) or isinstance(new, bool):
            if old != new:
                out.append({"path": prefix, "old": old, "new": new})
            return out
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            if old != new:
                base = max(abs(float(old)), abs(float(new)), 1e-9)
                out.append({"path": prefix, "old": old, "new": new,
                            "rel": round(abs(float(old) - float(new)) / base, 6)})
        elif old != new:
            out.append({"path": prefix, "old": str(old)[:80], "new": str(new)[:80]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=0, help="只跑某一批（0=全部）")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定路径")
    ap.add_argument("--json", default=None, help="把完整结果写到这个文件")
    args = ap.parse_args()

    pairs = PAIRS
    if args.batch:
        pairs = [p for p in pairs if p.batch == args.batch]
    if args.only:
        want = set(args.only)
        pairs = [p for p in pairs if p.path in want]

    s = requests.Session()
    rows, results = [], []
    bad = 0
    for p in pairs:
        oc, od = fetch(s, p.old_base, p.path, p.params, p.method, p.body)
        nc, nd = fetch(s, NEW, p.path, p.params, p.method, p.body)
        rec = {"path": p.path, "batch": p.batch, "old_status": oc, "new_status": nc}
        if oc != 200 or nc != 200:
            rec["status"] = "error"
            rec["detail"] = f"旧 {oc} / 新 {nc}"
            bad += 1
            rows.append((p.path, "ERROR", f"旧{oc} 新{nc}", ""))
            results.append(rec)
            continue
        so, sn = skeleton(od), skeleton(nd)
        missing = sorted(so - sn)
        extra = sorted(sn - so)
        # 旧端空数组导致的「新版多出元素字段」不是结构差：豁免它们。
        empty_in_old = empty_array_paths(od)
        if empty_in_old and extra:
            def _under_empty(p: str) -> bool:
                for pref in empty_in_old:
                    if p.startswith(pref + "."):
                        return True
                return False
            extra = [x for x in extra if not _under_empty(x)]
        # 骨架实现允许缺失的键（显式声明，不隐式放行）
        if p.allow_missing and missing:
            pats = [re.compile(x) for x in p.allow_missing]
            kept = []
            allowed_n = 0
            for m in missing:
                if any(pt.search(m) for pt in pats):
                    allowed_n += 1
                else:
                    kept.append(m)
            missing = kept
            rec["allowed_missing"] = allowed_n
        vd = diff_values(od, nd)
        rec.update({
            "structure_missing": missing[:20], "structure_extra": extra[:20],
            "missing_count": len(missing), "extra_count": len(extra),
            "value_diffs": len(vd), "value_samples": vd[:8],
        })
        if missing or extra:
            rec["status"] = "structure_mismatch"
            bad += 1
            rows.append((p.path, "STRUCT",
                         f"缺{len(missing)} 多{len(extra)}",
                         (missing[:2] + extra[:2]).__str__()[:60]))
        else:
            rec["status"] = "ok"
            rows.append((p.path, "ok", f"值差{len(vd)}", ""))
        results.append(rec)

    print(f"{'端点':46s} {'结果':8s} {'说明':16s} 备注")
    print("-" * 100)
    for path, st, note, extra in rows:
        print(f"{path:46s} {st:8s} {note:16s} {extra}")

    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n合计 {len(results)}：ok={ok}  结构不一致/失败={bad}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("明细 ->", args.json)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
