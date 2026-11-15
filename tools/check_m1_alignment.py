# -*- coding: utf-8 -*-
"""M1 活体对齐抽查：8811（新代码）vs 8800（旧引擎），四个 GET 端点同参比对。

只读比对，不改任何文件。用法：
    python tools/check_m1_alignment.py
退出码：0 全部一致；1 有差异；2 抓取失败。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

NEW = "http://127.0.0.1:8811"
OLD = "http://127.0.0.1:8800"

CASES = [
    ("GET /api/panic", "/api/panic", "panic"),
    ("GET /api/kline/000001.SH/xdxr", "/api/kline/000001.SH/xdxr", "xdxr"),
    ("GET /api/kline/600519.SH/xdxr", "/api/kline/600519.SH/xdxr", "xdxr"),
    ("GET /api/kline-indicator-settings", "/api/kline-indicator-settings", "settings"),
]


def _get(base: str, path: str) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, {"_error": "%s: %s" % (type(exc).__name__, exc)}


def _strip_volatile(kind: str, body: object) -> object:
    """去掉「每次请求都不同」的时间字段，只比口径值。"""
    if not isinstance(body, dict):
        return body
    out = json.loads(json.dumps(body))
    if kind == "panic":
        # asof 是请求时刻，必然不同；其余（trade_date/mode/source/value/state/items/series）
        # 只要同一拍就是同一份。
        out.pop("asof", None)
    return out


def main() -> int:
    rows = []
    failed = 0
    for label, path, kind in CASES:
        sn, nb = _get(NEW, path)
        so, ob = _get(OLD, path)
        if sn == 0 or so == 0:
            rows.append((label, "FETCH-FAIL", "new=%s old=%s" % (sn, so)))
            failed = 2
            continue
        nn, oo = _strip_volatile(kind, nb), _strip_volatile(kind, ob)
        same = (sn == so) and (nn == oo)
        if not same:
            failed = 1
        detail = "HTTP %s/%s" % (sn, so)
        if isinstance(nn, dict) and isinstance(oo, dict):
            if nn.keys() != oo.keys():
                detail += " keys-new=%s keys-old=%s" % (sorted(nn), sorted(oo))
            elif nn != oo:
                diffs = [k for k in nn if nn[k] != oo[k]]
                detail += " diff-keys=%s" % diffs[:5]
                for k in diffs[:2]:
                    detail += " | %s new=%s old=%s" % (
                        k, str(nn[k])[:120], str(oo[k])[:120])
            else:
                if kind == "panic":
                    detail += " value=%s state=%s mode=%s series=%d" % (
                        nn.get("value"), nn.get("state"), nn.get("mode"),
                        len(nn.get("series") or []))
                elif kind == "xdxr":
                    detail += " events=%d" % len(nn.get("events") or [])
                else:
                    detail += " keys=%d" % len(nn)
        rows.append((label, "SAME" if same else "DIFF", detail))

    print("| 端点 | 结果 | 依据 |")
    print("|---|---|---|")
    for label, verdict, detail in rows:
        print("| %s | %s | %s |" % (label, verdict, detail.replace("|", "/")[:400]))
    print()
    print("结论：", "PASS" if failed == 0 else "FAIL")
    return failed


if __name__ == "__main__":
    sys.exit(main())
