# -*- coding: utf-8 -*-
"""⚠️ **已退役（2026-10-30）——不得再跑、不得挂计划任务**。

主人当日拍板：温度计「主力资金」改为 `eltdx money_flow.daily('sh880008')` **一次请求**（主值占成交 %、
副值净额、附 5 日对比，不分位），已在 `app/plugins/thermometer/backend._index_flow()` 实现。
本脚本是全市场 5,225 只的 L2 直连方案（实测 27.7 分钟），已从 `postclose_chain.py` 组 C 摘除；
仅作追溯保留，**若被调用会重新写 `l2_main_flow_series.json`（已不被任何消费端读取）**。

以下为原文（保留）：

全市场主力资金（L2 直连）· 盘后生产脚本。

## 口径（唯一源 = 金融数据获取卡 §7 + Hubagent/01/_tools/l2_mf.py）

- 取数：`tdx_l2_fetch.py`（7709 直连，`cmd=0x0fcc`）现拉当日，**走 `l2_mf.build_mf` 复用现成解析与公式**
  （`主力资金`/`主买`/`占成交`/`占流通`/`Q`），本脚本**不重写任何分档公式**。
- 本脚本只用 **`占成交`**（＝净额元 / 成交额 × 100，%）：全市场横截面取**中位数**作为当日「主力资金」读数。
  为什么用占成交中位而不是净额合计：净额合计被几只权重股主导，占成交是与量纲无关的横截面占比，
  与广度/接力/两融一样是「分位可比」的量。
- **不用 L2 历史库**（`l2_data/l2_master*.csv`）——那是回测口径；本序列**从今天起自己逐日累积**，
  头几天样本不足时分位会标「序列刚建立、暂无分位」（见插件）。
- 有效性照卡片：`分档总额>0 且 成交额>0`；净额=0 是有效值；整段恒 0 才算缺（由 `l2_mf.compute_daily` 判）。

## 用法

```powershell
python projects/panel-v2/tools/l2_main_flow_daily.py --limit 20 --dry-run   # 校准（约 20 秒）
python projects/panel-v2/tools/l2_main_flow_daily.py                        # 全市场（约 20–25 分钟）
```
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PANEL_DIR = Path(__file__).resolve().parents[1]
WS = PANEL_DIR.parents[1]
OUT_DIR = PANEL_DIR / "data" / "panel_v2" / "thermometer"
SERIES_FILE = OUT_DIR / "l2_main_flow_series.json"
LATEST_FILE = OUT_DIR / "l2_main_flow_latest.json"
CAH_TMP = OUT_DIR / "_cah_tmp"
L2_MF = WS / "Hubagent" / "01" / "_tools" / "l2_mf.py"
#: 每票只回最近 2 条（够当日 + 便于校验），减少流量与缓存体积
RECORDS = 2


def _l2():
    """按路径加载 l2_mf（口径唯一源；不在本脚本重写公式）。"""
    if not L2_MF.is_file():
        raise SystemExit("找不到口径源 %s" % L2_MF)
    spec = importlib.util.spec_from_file_location("_l2_mf_daily", L2_MF)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def detect_data_date(mod, records: int = 2) -> str:
    """探测「数据日」：拿一只流动性最好的票现拉 .cah，取其中最大日期。

    为什么不能默认今天：零点后 / 盘中未收盘时，「今天」在 L2 里可能还没有记录，
    直接拿今天当数据日会得到全空（实测 2026-10-30 凌晨：market 最后一条是 10-29）。
    """
    for code in ("600519", "000001", "600036"):
        for market in ("sh", "sz"):
            try:
                blob = mod.fetch_cah_live(market, code, records=records)
            except Exception:  # noqa: BLE001
                continue
            recs = mod.parse_cah(blob or b"")
            if recs:
                return str(max(recs.keys()))
    raise SystemExit("无法探测 L2 数据日（拿不到任何 .cah 记录）")


def universe() -> List[str]:
    """全 A（沪深，不含北交所：L2 直连不支持 bj）。"""
    from eltdx import TdxClient  # noqa: PLC0415
    codes = list(TdxClient().codes.all_a_shares())
    out = []
    for c in codes:
        s = str(c)
        if s[:2] in ("sh", "sz") and s[2:].isdigit():
            out.append(s[2:])
    return sorted(set(out))


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 4)


def run(codes: List[str], today8: str, mod, records: int) -> Dict[str, Any]:
    tmp_cache = str(CAH_TMP)
    zc_values: List[float] = []
    failed: List[str] = []
    err_kinds: Dict[str, int] = {}
    t0 = time.perf_counter()
    fetch = lambda mkt, code: mod.fetch_cah_live(mkt, code, records=records)  # noqa: E731
    for i, code in enumerate(codes, 1):
        try:
            res = mod.build_mf(code, [today8], closes=[None], shares_fn=lambda *_: None,
                               cache_dir=tmp_cache, today8=today8, fetch_fn=fetch)
            zc = res["占成交"][0]
            if zc is None:
                failed.append(code)
                err_kinds["empty"] = err_kinds.get("empty", 0) + 1
            else:
                zc_values.append(float(zc))
        except Exception as exc:  # noqa: BLE001 - 单票失败不打断整轮
            failed.append(code)
            key = type(exc).__name__
            err_kinds[key] = err_kinds.get(key, 0) + 1
        if i % 500 == 0:
            print("  …%d/%d  %.0fs  成功 %d" % (i, len(codes), time.perf_counter() - t0, len(zc_values)))
    seconds = time.perf_counter() - t0
    return {
        "row": {
            "date": today8,
            "zc_median": _pct(zc_values, 0.5),
            "zc_p10": _pct(zc_values, 0.10),
            "zc_p25": _pct(zc_values, 0.25),
            "zc_p75": _pct(zc_values, 0.75),
            "zc_p90": _pct(zc_values, 0.90),
            "zc_mean": round(statistics.fmean(zc_values), 4) if zc_values else None,
            "n": len(zc_values),
            "n_total": len(codes),
            "seconds": round(seconds, 1),
            "failed": failed[:20],
            "n_failed": len(failed),
            "err_kinds": err_kinds,
        },
        "seconds": seconds,
        "n_ok": len(zc_values),
        "failed": failed,
        "err_kinds": err_kinds,
    }


def merge(existing: Dict[str, Any], row: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    series = {str(r.get("date")): r for r in (existing.get("series") or []) if r.get("date")}
    series[row["date"]] = row
    ordered = [series[d] for d in sorted(series)]
    return {
        "meta": {
            "source": "tdx L2 7709 直连（cmd=0x0fcc）逐票现拉 → l2_mf.build_mf 算「占成交」",
            "caliber": ("当日值 = 全市场「占成交」（净额元/成交额×100，%）的横截面中位数；"
                        "同日本序列另存 p10/p25/p75/p90 与覆盖度，不补 0"),
            "history_note": "从 2026-10-30 起逐日累积；不用 L2 历史库（l2_master*.csv 属回测口径）",
            "records_per_code": RECORDS,
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
    ap = argparse.ArgumentParser(description="全市场主力资金（L2 直连，占成交中位）· 盘后聚合")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（校准用）")
    ap.add_argument("--date", default=None, help="数据日 YYYYMMDD（默认自动探测 L2 最新记录日）")
    ap.add_argument("--records", type=int, default=RECORDS, help="每票回多少条 .cah 记录（默认 2）")
    ap.add_argument("--allow-partial", action="store_true",
                    help="允许 --limit 部分拉取写入序列（默认拒写：部分样本会把当日行改偏）")
    args = ap.parse_args()

    mod = _l2()
    if args.date:
        today8 = args.date
    else:
        today8 = detect_data_date(mod, args.records)
        print("数据日探测：%s（L2 现拉的最新一条）" % today8)
    codes = universe()
    if args.limit:
        codes = codes[: args.limit]
    print("全 A（沪深）%d 只；数据日 %s；每票 %d 条记录" % (len(codes), today8, args.records))
    got = run(codes, today8, mod, args.records)
    row = got["row"]
    print("拉取 %.1fs（%.3fs/只）成功 %d/%d 失败 %d %s" % (
        got["seconds"], got["seconds"] / max(1, len(codes)), got["n_ok"], len(codes),
        row["n_failed"], row["err_kinds"] or ""))
    print("  占成交 中位 %s%%  p10 %s  p25 %s  p75 %s  p90 %s  均值 %s" % (
        row["zc_median"], row["zc_p10"], row["zc_p25"], row["zc_p75"], row["zc_p90"], row["zc_mean"]))

    if args.dry_run:
        print("dry-run：不写文件")
        return 0
    if args.limit and not args.allow_partial:
        # 部分拉取会把这个日期的行改写成 n=50 的偏差样本（2026-10-30 实测踩过）→ 默认拒写
        print("拒绝写入：--limit 校准拉取不得覆盖正式序列（真需写入加 --allow-partial）")
        return 2

    existing = {}
    if SERIES_FILE.is_file():
        try:
            existing = json.loads(SERIES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    doc = merge(existing, row, {
        "seconds": row["seconds"], "n": row["n"], "n_total": row["n_total"],
        "n_failed": row["n_failed"], "err_kinds": row["err_kinds"],
    })
    _write_atomic(SERIES_FILE, doc)
    _write_atomic(LATEST_FILE, row)
    print("已写 %s（%d 天，最新 %s）" % (SERIES_FILE, doc["meta"]["n_days"], row["date"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
