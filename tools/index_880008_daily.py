# -*- coding: utf-8 -*-
"""全A指数 880008 日线 · 位置维度底座（vipdoc 历史 + eltdx 补当日）。

## 为什么是 880008

位置维度要用**全A**，不用沪深300（不等于全A），也不用 `000985.SZ`（实测那不是全指序列：
收盘 10.58–24.70、日中位成交额 0.29 亿、与上证日收益相关 0.36）。
`880008`：vipdoc 3,643 个交易日（2011-10-19 ~ 2026-10-22），收盘 190.08 ~ 1638.31；
**10-22 成交额 15,418.64 亿，与 `880005` 同日 amount 完全一致 ⇒ 同源，是全A指数的权威序列**。

## 两个源，怎么合

1. **vipdoc `.day`（历史）**：`{tdx}/vipdoc/sh/lday/sh880008.day`，记录格式 `struct "<IIIIIfII"`
   （日期, 开, 高, 低, 收, 成交额(float), 量, 保留），价格 **×100**。实测停在 2026-10-22。
2. **eltdx（尾部补齐）**：`bars.get('sh880008', period='day', count=N)`，实测可到最新交易日（10-29）。
   两源重叠日用 eltdx 覆盖，并把「收盘价差」记进产物（`overlap_max_diff`），不静默假设一致。

**不能把 vipdoc 最后一天当今天**：产物里 `vipdoc_last` / `eltdx_last` / `merged_last` 三个日期都落盘，
插件按 `merged_last` 与情绪侧数据日比对，不齐就把位置维度标缺（不拿旧点位冒充今日）。

## 用法

```powershell
python projects/panel-v2/tools/index_880008_daily.py            # 约 1 秒
python projects/panel-v2/tools/index_880008_daily.py --dry-run
```
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PANEL_DIR = Path(__file__).resolve().parents[1]
WS = PANEL_DIR.parents[1]
OUT_DIR = PANEL_DIR / "data" / "panel_v2" / "thermometer"
SERIES_FILE = OUT_DIR / "index_880008_series.json"
CODE = "880008"
#: 客户端 vipdoc 根（可用 DSH_880008_VIPDOC 覆盖）
VIPDOC = Path(os.environ.get("DSH_880008_VIPDOC", r"C:\new_tdx_zyb\zd_zyb\vipdoc"))
REC = struct.Struct("<IIIIIfII")
#: eltdx 每次最多回 800 根（实测可用）；位置维度要 MA60+250 日窗口，取满 800 根
ELTDX_COUNT = 800


def parse_day(path: Path) -> List[Dict[str, Any]]:
    """vipdoc .day → 升序日线（价格 ÷100）。"""
    raw = path.read_bytes()
    size = REC.size
    out = []
    for i in range(len(raw) // size):
        d, o, h, l, c, amt, vol, _res = REC.unpack_from(raw, i * size)
        if not d:
            continue
        out.append({"date": str(d), "open": o / 100.0, "high": h / 100.0,
                    "low": l / 100.0, "close": c / 100.0, "amount": float(amt),
                    "vol": int(vol), "source": "vipdoc"})
    out.sort(key=lambda r: r["date"])
    return out


def fetch_eltdx(count: int = ELTDX_COUNT) -> List[Dict[str, Any]]:
    """eltdx 拉 880008 最近 N 根日线（价格已是点位，不再 ÷100）。"""
    from eltdx import TdxClient  # noqa: PLC0415
    series = TdxClient().bars.get("sh" + CODE, period="day", count=count)
    out = []
    for b in getattr(series, "bars", ()) or ():
        t = getattr(b, "time", None)
        day = t.strftime("%Y%m%d") if hasattr(t, "strftime") else str(getattr(b, "date", ""))
        if not day:
            continue
        out.append({"date": day, "open": float(getattr(b, "open", 0) or 0),
                    "high": float(getattr(b, "high", 0) or 0),
                    "low": float(getattr(b, "low", 0) or 0),
                    "close": float(getattr(b, "close", 0) or 0),
                    "amount": float(getattr(b, "amount", 0) or 0),
                    "vol": int(getattr(b, "vol", 0) or 0), "source": "eltdx"})
    out.sort(key=lambda r: r["date"])
    return out


def merge(vip: List[Dict[str, Any]], tail: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_date = {r["date"]: r for r in vip}
    overlap = []
    for r in tail:
        old = by_date.get(r["date"])
        if old is not None and old.get("close") and r.get("close"):
            overlap.append(abs(float(old["close"]) - float(r["close"])))
        by_date[r["date"]] = r
    series = [by_date[d] for d in sorted(by_date)]
    return {
        "series": series,
        "overlap_n": len(overlap),
        "overlap_max_diff": round(max(overlap), 4) if overlap else None,
    }


def drop_live_bar(series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """丢掉「今日未收盘」的那根（盘中跑会把半日线写进序列，把位置维度带偏）。

    仅当两根条件同时成立：末根日期 == 今天，且当前时间早于 15:05（收盘后留 5 分钟给尾盘数据）。
    盘后跑（盘后链 16:10）不会触发；手工盘中跑也能自动护住。
    """
    now = datetime.now()
    if not series:
        return series
    last = str(series[-1].get("date") or "")
    if last == now.strftime("%Y%m%d") and (now.hour, now.minute) < (15, 5):
        print("丢弃未收盘的今日根：%s（当前 %s，15:05 前不算完成日）" % (last, now.strftime("%H:%M")))
        return series[:-1]
    return series


def main() -> int:
    ap = argparse.ArgumentParser(description="880008 全A指数日线：vipdoc 历史 + eltdx 尾部")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vipdoc", default=str(VIPDOC))
    args = ap.parse_args()

    path = Path(args.vipdoc) / "sh" / "lday" / ("sh%s.day" % CODE)
    if not path.is_file():
        raise SystemExit("vipdoc 缺 %s" % path)
    vip = parse_day(path)
    print("vipdoc %d 根  %s..%s  末收 %.2f" % (len(vip), vip[0]["date"], vip[-1]["date"], vip[-1]["close"]))

    tail: List[Dict[str, Any]] = []
    try:
        tail = fetch_eltdx()
        print("eltdx  %d 根  %s..%s  末收 %.2f" % (
            len(tail), tail[0]["date"], tail[-1]["date"], tail[-1]["close"]))
    except Exception as exc:  # noqa: BLE001 - 拉不到就只用 vipdoc，并在产物里标
        print("eltdx 拉取失败：%s: %s（只用 vipdoc 历史）" % (type(exc).__name__, exc))

    got = merge(vip, tail)
    series = drop_live_bar(got["series"])
    got["series"] = series
    print("合并   %d 根  %s..%s  重叠 %d 日 收盘最大差 %s" % (
        len(series), series[0]["date"], series[-1]["date"], got["overlap_n"], got["overlap_max_diff"]))
    print("尾部来源分布：eltdx %d / vipdoc %d" % (
        sum(1 for r in series if r["source"] == "eltdx"),
        sum(1 for r in series if r["source"] == "vipdoc")))

    if args.dry_run:
        print("dry-run：不写文件")
        return 0

    doc = {
        "meta": {
            "source": "vipdoc sh880008.day（历史）+ eltdx bars.get('sh880008')（尾部）",
            "caliber": ("880008 = 全A指数（与 880005 同日 amount 一致，同源）；价格×100 存于 vipdoc，已 ÷100；"
                        "eltdx 点位直接用"),
            "vipdoc_last": vip[-1]["date"] if vip else None,
            "eltdx_last": tail[-1]["date"] if tail else None,
            "merged_last": series[-1]["date"] if series else None,
            "rows": len(series),
            "overlap_n": got["overlap_n"],
            "overlap_max_diff": got["overlap_max_diff"],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "series": series,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SERIES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, SERIES_FILE)
    print("已写 %s（%d 根，最新 %s）" % (SERIES_FILE, len(series), doc["meta"]["merged_last"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
