# -*- coding: utf-8 -*-
"""全市场主力资金流 · 盘后生产脚本（eltdx `money_flow.daily`）。

## 为什么需要这个脚本，而不是插件直接拉

1. **eltdx 全市场循环禁止出现在请求路径**（金融数据获取卡 §4 铁律：禁止对 eltdx 全市场循环）：
   全市场 5,580 只按 75 一批 = 75 批，实测 0.17–0.22 秒/批（约 13–17 秒），
   放进页面请求会拖死面板，也会在主站留下高频轮询。
2. **插件不许 import eltdx**（CONTRACT §4.1 静态扫描），所以插件只读产物。

因此：**本脚本盘后跑一次**，把「全市场主力资金」聚合成一天一行，写到插件自己的输出目录
`data/panel_v2/thermometer/money_flow_series.json`；面板只读这份，零行情连接。

## 口径

- `main_net`（主力净额）与 `main_ratio`（主力净占比 %）都是 eltdx 给的原值，本脚本**只做汇总，不重算分档**。
- 一天两条口径都落：
  - `sum_main_net` = 全市场 main_net 求和（元，展示用；易被几只大票主导）
  - `median_main_ratio` = 全市场 main_ratio 中位数（%，**打分用这个**：量纲无关、抗异动）
- `n` / `n_total` = 有记录只数 / 请求只数（覆盖度，落盘留证，不补齐、不填 0）。
- 单次调用每个代码返回最近 **5 个交易日**（服务端窗口固定，无 count 参数），
  所以一次全市场遍历就能把这 5 天一起算出来，不必按天重复拉。

## 用法

```powershell
python projects/panel-v2/tools/money_flow_daily.py            # 拉一次，合并进序列（幂等：同一天覆盖）
python projects/panel-v2/tools/money_flow_daily.py --dry-run   # 只算不写
```
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PANEL_DIR = Path(__file__).resolve().parents[1]          # projects/panel-v2
WS = PANEL_DIR.parents[1]                                 # OH-WorkSpace
OUT_DIR = PANEL_DIR / "data" / "panel_v2" / "thermometer"
SERIES_FILE = OUT_DIR / "money_flow_series.json"
LATEST_FILE = OUT_DIR / "money_flow_latest.json"
BATCH = 75
#: 覆盖度下限：某日只有零星几只（停牌股的旧记录）→ 该日不落库。
#: 实测：全市场 5,580 只里，正常交易日的 n 是 5569–5580；零星日的 n 只有 1–3。
MIN_COVERAGE = 0.5


def _client():
    try:
        from eltdx import TdxClient  # noqa: PLC0415 - 只有本脚本能 import eltdx
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("eltdx 不可用：%s: %s" % (type(exc).__name__, exc)) from exc
    return TdxClient()


def fetch(codes: List[str]) -> Dict[str, Any]:
    """全市场一次遍历 → {date: {"sum_main_net", "median_main_ratio", "n"}} + 覆盖度。"""
    client = _client()
    t0 = time.perf_counter()
    batch = client.money_flow.daily(codes, batch_size=BATCH)
    seconds = time.perf_counter() - t0
    blocks = list(getattr(batch, "blocks", ()) or ())
    per_date: Dict[str, Dict[str, List[float]]] = {}
    empty = 0
    for blk in blocks:
        recs = list(getattr(blk, "records", ()) or ())
        if not recs:
            empty += 1
            continue
        for rec in recs:
            day = getattr(rec, "date", None)
            if day is None:
                continue
            key = day.strftime("%Y%m%d") if hasattr(day, "strftime") else str(day)
            ratio = getattr(rec, "main_ratio", None)
            net = getattr(rec, "main_net", None)
            slot = per_date.setdefault(key, {"ratio": [], "net": []})
            if ratio is not None:
                slot["ratio"].append(float(ratio))
            if net is not None:
                slot["net"].append(float(net))
    out = {}
    skipped = []
    for day, slot in per_date.items():
        n = len(slot["ratio"])
        if n < len(codes) * MIN_COVERAGE:
            skipped.append({"date": day, "n": n})
            continue
        out[day] = {
            "date": day,
            "median_main_ratio": round(statistics.median(slot["ratio"]), 4) if slot["ratio"] else None,
            "sum_main_net": round(sum(slot["net"]), 2) if slot["net"] else None,
            "n": n,
            "n_total": len(codes),
        }
    return {
        "rows": out,
        "skipped": sorted(skipped, key=lambda x: x["date"]),
        "seconds": round(seconds, 2),
        "blocks": len(blocks),
        "empty_blocks": empty,
        "n_codes": len(codes),
        "batches": (len(codes) + BATCH - 1) // BATCH,
    }


def merge(existing: Dict[str, Any], rows: Dict[str, Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    series = {}
    for r in (existing.get("series") or []):
        day = r.get("date")
        total = r.get("n_total") or 0
        # 旧文件里可能已有「零星日」（早期未加覆盖度阀时写进去的）→ 一并剔除
        if day and total and (r.get("n") or 0) >= total * MIN_COVERAGE:
            series[str(day)] = r
    for day, row in rows.items():
        series[day] = row
    ordered = [series[d] for d in sorted(series)]
    return {
        "meta": {
            "source": "eltdx money_flow.daily（全市场 main_net / main_ratio）",
            "caliber": ("sum_main_net = 全市场主力净额求和（展示）；median_main_ratio = 全市场主力净占比中位数（打分用）；"
                        "n = 有记录只数，不补 0"),
            "window_note": "eltdx 单次每票回最近 5 个交易日；序列只增不减，攒够 250 日才谈 250 日分位",
            "batch_size": BATCH,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": meta,
            "n_days": len(ordered),
        },
        "series": ordered,
    }


def _write_atomic(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="全市场主力资金流 · 盘后聚合（eltdx money_flow）")
    ap.add_argument("--dry-run", action="store_true", help="只算不写")
    args = ap.parse_args()

    client = _client()
    t0 = time.perf_counter()
    codes = list(client.codes.all_a_shares())
    print("全市场代码 %d 只（%.2fs）" % (len(codes), time.perf_counter() - t0))

    got = fetch(codes)
    print("拉取 %.2fs / %d 批 / %d 块 / 空块 %d" % (
        got["seconds"], got["batches"], got["blocks"], got["empty_blocks"]))
    if got["skipped"]:
        print("覆盖不足跳过 %d 天：%s" % (len(got["skipped"]), [s["date"] + "(n=%d)" % s["n"] for s in got["skipped"]][:6]))
    for day in sorted(got["rows"])[-5:]:
        row = got["rows"][day]
        print("  %s  净占比中位 %s%%  净额合计 %s 亿  n=%d/%d" % (
            day,
            "缺" if row["median_main_ratio"] is None else "%.3f" % row["median_main_ratio"],
            "缺" if row["sum_main_net"] is None else "%.2f" % (row["sum_main_net"] / 1e8),
            row["n"], row["n_total"]))

    if args.dry_run:
        print("dry-run：不写文件")
        return 0

    existing = {}
    if SERIES_FILE.is_file():
        try:
            existing = json.loads(SERIES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    doc = merge(existing, got["rows"], {
        "seconds": got["seconds"], "batches": got["batches"],
        "empty_blocks": got["empty_blocks"], "n_codes": got["n_codes"],
        "skipped_low_coverage": [s["date"] for s in got["skipped"]],
    })
    _write_atomic(SERIES_FILE, doc)
    latest_day = sorted(got["rows"])[-1] if got["rows"] else None
    if latest_day:
        _write_atomic(LATEST_FILE, got["rows"][latest_day])
    print("已写 %s（%d 天）" % (SERIES_FILE, doc["meta"]["n_days"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
