# -*- coding: utf-8 -*-
"""⚠️ **已退役（2026-10-30）——不得再跑、不得挂计划任务**。

参人当日拍板：两融是**逐票数据、无市场级接口**（880008 的 F10 `rzrq`：table0 空表、table1 全 None，已实测）；
300 只沪深300 成分股聚合需 2.6 分钟，边际信息量不抵成本 ⇒ **整项移出温度计资金维度**，
`postclose_chain.py` 组 C 的 `margin_balance` 步已摘除。
本脚本仅作追溯保留；若被调用会重新写 `margin_series.json`（已无任何消费端）。

以下为原文（保留）：

两融（融资融券）余额 · 盘后生产脚本（eltdx F10 `rzrq`，标的加权）。

## 入口（2026-10-30 主人实测）

    F10Client.params("CWServ.tdxf10_gg_ggzp", "rzrq", code, "", "")

返回两张表：
- `table0`（逐日，约 60 个交易日）：N001=日期 / N002=融资净买入(元) / N003=融资余额(元) /
  N004=融券净卖出(股) / N005=融券余额(元)
- `table1`（汇总）：N006=融资余额 / N011=融券余额 / N016=融资余额占流通市值比(%) / N017=融券余额占比(%)

## 为什么不做全市场

逐票查询，实测 **0.195 秒/只**；全市场 5,580 只 ≈ 27 分钟——不能进请求路径，也不值得为「全」跑半小时。
**标的池 = 沪深300 + 中证500 成分（800 只）**，来自本地 `data/ths_board.db::broad_index_member`
（`index_code in ('000300.SH','000905.SH')`，周更维护）。800 只实测约 **2.6 分钟**。

## 口径

- 日值 = 池内成分**求和**（不是简单平均）：`margin_bal_sum`（融资余额合计）、`short_bal_sum`（融券余额合计）、
  `net_buy_sum`（融资净买入合计）；`n` / `n_total` 是覆盖度，缺的票不计入、不补 0。
- **打分口径 = 融资余额合计的日环比变化率**：`(Σbal(t) / Σbal(t-1) − 1) × 100`。
  选它而不是余额绝对值的理由：余额是存量、随行情扩张（指数涨余额就涨），环比变化率把「存量水位」去掉，
  只留「增量资金进没进来」，且与标的总市值无关，跨日可比。
- **两融是 T+1**：今天收盘的两融余额明天才出，最新只到 T-1。产物里 `latest_date` 即 T-1，
  插件会把它写到页面（标「T-1」），不冒充当日值。

## 用法

```powershell
python projects/panel-v2/tools/margin_balance_daily.py --dry-run          # 只算不写（约 2.6 分钟）
python projects/panel-v2/tools/margin_balance_daily.py --limit 40 --dry-run   # 小样本校准用（约 8 秒）
```
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PANEL_DIR = Path(__file__).resolve().parents[1]
WS = PANEL_DIR.parents[1]
OUT_DIR = PANEL_DIR / "data" / "panel_v2" / "thermometer"
SERIES_FILE = OUT_DIR / "margin_series.json"
LATEST_FILE = OUT_DIR / "margin_latest.json"
THS_DB = WS / "data" / "ths_board.db"
ENTRY = "CWServ.tdxf10_gg_ggzp"
PARAM = "rzrq"
#: 默认标的池 = 沪深300 成分（300 只，实测约 4–5 分钟）；`--with-csi500` 加上中证500（800 只，约 10 分钟）
INDEXES = ("000300.SH",)
CSI500 = "000905.SH"
#: 并发数与重试：实测该接口约 30% 的调用会挂 6 秒（与 timeout 设置同向），重试即好 → 必须并发+重试
WORKERS = 8
#: 单日覆盖率下限：正常 n/池 ≈ 93%（279–281/300）；低于此值说明该日两融尚未发布完
MIN_COVERAGE = 0.85
ATTEMPTS = 3
CALL_TIMEOUT = 3
_THREAD = threading.local()


def universe(indexes=None) -> List[str]:
    """成分股池：默认沪深300（可选 +中证500），本地 `ths_board.db::broad_index_member`，只读。"""
    if not THS_DB.is_file():
        raise SystemExit("找不到 %s（成分股池唯一来源）" % THS_DB)
    idx = tuple(indexes or INDEXES)
    uri = "file:%s?mode=ro" % str(THS_DB).replace("\\", "/")
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        q = "SELECT DISTINCT stock FROM broad_index_member WHERE index_code IN (%s)" % ",".join("?" * len(idx))
        rows = conn.execute(q, idx).fetchall()
    finally:
        conn.close()
    return sorted({str(r[0]).strip() for r in rows if r[0]})


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _client_for_thread():
    """每线程一个 client（共享一个 client 并发时超时率明显上升）。"""
    cli = getattr(_THREAD, "client", None)
    if cli is None:
        from eltdx import TdxClient  # noqa: PLC0415
        cli = TdxClient()
        try:
            cli.f10.timeout = CALL_TIMEOUT
        except Exception:  # noqa: BLE001
            pass
        _THREAD.client = cli
    return cli


def _fetch_one(code: str) -> Dict[str, Any]:
    cli = _client_for_thread()
    last_exc = None
    for attempt in range(ATTEMPTS):
        try:
            res = cli.f10.params(ENTRY, PARAM, code, "", "")
            sets = list(getattr(res, "result_sets", ()) or ())
            table0 = next((s for s in sets if getattr(s, "key", "") == "table0"), None)
            rows = list(getattr(table0, "rows", ()) or ())
            if rows:
                return {"code": code, "rows": rows, "attempt": attempt + 1}
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.2 * (attempt + 1))
    return {"code": code, "rows": [], "error": type(last_exc).__name__ if last_exc else "empty"}


def fetch(codes: List[str], client=None) -> Dict[str, Any]:
    per_date: Dict[str, Dict[str, float]] = {}
    #: 逐票逐日融资余额：算环比时只用「两日都有值」的票（见下方 chg 口径）
    per_code: Dict[str, Dict[str, float]] = {}
    ok = 0
    retried = 0
    failed: List[str] = []
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for res in ex.map(_fetch_one, codes):
            if not res["rows"]:
                failed.append(res["code"])
                continue
            ok += 1
            if res["attempt"] > 1:
                retried += 1
            mine = per_code.setdefault(res["code"], {})
            for r in res["rows"]:
                day = str(r.get("N001") or "")
                if not day:
                    continue
                slot = per_date.setdefault(day, {"margin": 0.0, "short": 0.0, "net": 0.0, "n": 0.0})
                bal = _num(r.get("N003"))
                sbal = _num(r.get("N005"))
                net = _num(r.get("N002"))
                if bal is not None:
                    slot["margin"] += bal
                    slot["n"] += 1
                    mine[day] = bal
                if sbal is not None:
                    slot["short"] += sbal
                if net is not None:
                    slot["net"] += net
    seconds = time.perf_counter() - t0

    rows_out: Dict[str, Dict[str, Any]] = {}
    dates = sorted(per_date)
    prev_day = None
    prev_bal = None
    for day in dates:
        slot = per_date[day]
        bal = round(slot["margin"], 2) if slot["n"] else None
        # 环比口径（2026-10-30 改）：**只用两日都有余额的票**再比合计。
        # 旧口径直接比「两次拉取的成功样本合计」——样本数 279 与 281 之差就能把合计拉动 2%，
        # 而当日真实环比只有 0.1% 量级 ⇒ 会让读数被覆盖度噪声淹没。
        chg = None
        n_common = 0
        if bal is not None and prev_day:
            common = [c for c, d in per_code.items()
                      if d.get(day) is not None and d.get(prev_day) is not None]
            n_common = len(common)
            if common:
                cur = sum(per_code[c][day] for c in common)
                pre = sum(per_code[c][prev_day] for c in common)
                if pre:
                    chg = round((cur / pre - 1.0) * 100.0, 4)
        rows_out[day] = {
            "date": day,
            "margin_bal_sum": bal,
            "short_bal_sum": round(slot["short"], 2) if slot["n"] else None,
            "net_buy_sum": round(slot["net"], 2) if slot["n"] else None,
            "margin_chg_pct": chg,
            "chg_common_n": n_common,
            "n": int(slot["n"]),
            "n_total": len(codes),
        }
        if bal is not None:
            prev_bal = bal
            prev_day = day
    return {
        "rows": rows_out,
        "seconds": round(seconds, 1),
        "n_codes": len(codes),
        "ok": ok,
        "retried": retried,
        "failed": failed[:20],
        "n_failed": len(failed),
    }


def merge(existing: Dict[str, Any], rows: Dict[str, Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    series = {str(r.get("date")): r for r in (existing.get("series") or []) if r.get("date")}
    series.update(rows)
    ordered = [series[d] for d in sorted(series)]
    return {
        "meta": {
            "source": "eltdx F10 %s/%s（逐票，沪深300+中证500 成分加权）" % (ENTRY, PARAM),
            "caliber": ("margin_bal_sum = 池内成分融资余额求和；margin_chg_pct = 融资余额合计日环比 %%（打分口径，去存量水位）；"
                        "net_buy_sum = 融资净买入合计；n = 有记录只数，不补 0"),
            "t_plus_1": "两融余额 T+1 披露：最新只到 T-1，页面按 T-1 标注",
            "universe": "ths_board.db::broad_index_member（默认沪深300 成分 300 只，可选 +中证500）",
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
    ap = argparse.ArgumentParser(description="两融余额 · 盘后聚合（eltdx F10 rzrq，标的加权）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（校准用）")
    ap.add_argument("--with-csi500", action="store_true", help="标的池加上中证500（800 只，约 10 分钟）")
    ap.add_argument("--allow-partial", action="store_true",
                    help="允许 --limit 部分拉取写入序列（默认拒写：部分样本会把当日行改偏）")
    args = ap.parse_args()

    try:
        from eltdx import TdxClient  # noqa: F401, PLC0415 - 只验证依赖在（每个线程自己 create）
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("eltdx 不可用：%s: %s" % (type(exc).__name__, exc)) from exc

    indexes = tuple(INDEXES) + ((CSI500,) if args.with_csi500 else ())
    codes = universe(indexes)
    if args.limit:
        codes = codes[: args.limit]
    print("标的池 %d 只（%s）；%d 线程 / 最多 %d 次重试 / 单次超时 %ds" % (
        len(codes), " + ".join(indexes), WORKERS, ATTEMPTS, CALL_TIMEOUT))
    got = fetch(codes)
    print("拉取 %.1fs / 成功 %d / 重试过 %d / 失败 %d（%.3fs/只）" % (
        got["seconds"], got["ok"], got["retried"], got["n_failed"], got["seconds"] / max(1, len(codes))))
    if got["failed"]:
        print("  失败样例：%s" % got["failed"])
    days = sorted(got["rows"])
    for day in days[-3:]:
        r = got["rows"][day]
        print("  %s  融资余额 %8s 亿  融券 %7s 亿  环比 %7s%%  n=%d/%d" % (
            day,
            "缺" if r["margin_bal_sum"] is None else "%.2f" % (r["margin_bal_sum"] / 1e8),
            "缺" if r["short_bal_sum"] is None else "%.2f" % (r["short_bal_sum"] / 1e8),
            "缺" if r["margin_chg_pct"] is None else "%.3f" % r["margin_chg_pct"],
            r["n"], r["n_total"]))

    if args.dry_run:
        print("dry-run：不写文件")
        return 0

    # 覆盖率闸门（2026-10-30 加，实测踩到）：两融是 T+1、且交易所是**逐票陆续发布**的。
    # 正常交易日 n/池 ≈ 93%（279–281/300）；盘后 16:10 跑时 T-1 已发全。
    # 2026-10-30 10:20 实测：10-29 只发了 160/300（53%）→ 余额 4922 亿、环比 −41%（假数据）。
    # 故低于 85% 的日子**不入序列**（不补 0、不拿部分样本充一天）。
    keep: Dict[str, Dict[str, Any]] = {}
    dropped = []
    for day, r in got["rows"].items():
        cov = r["n"] / max(1, r["n_total"])
        if cov < MIN_COVERAGE:
            dropped.append((day, r["n"], r["n_total"], cov))
        else:
            keep[day] = r
    if dropped:
        print("!! 覆盖度不足 %.0f%%，不入序列：%s" % (
            MIN_COVERAGE * 100,
            "; ".join("%s n=%d/%d(%.0f%%)" % (d, n, t, c * 100) for d, n, t, c in dropped)))
    if args.limit and not args.allow_partial:
        print("拒绍写入：--limit 校准拉取不得覆盖正式序列（真需写入加 --allow-partial）")
        return 2
    if not keep:
        print("没有任何一天达到覆盖度下限 → 不写文件")
        return 1

    existing = {}
    if SERIES_FILE.is_file():
        try:
            existing = json.loads(SERIES_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
    doc = merge(existing, keep, {
        "seconds": got["seconds"], "n_codes": got["n_codes"],
        "ok": got["ok"], "n_failed": got["n_failed"], "retried": got["retried"],
    })
    _write_atomic(SERIES_FILE, doc)
    days_ok = sorted(keep)
    if days_ok:
        _write_atomic(LATEST_FILE, keep[days_ok[-1]])
    print("已写 %s（%d 天，最新 %s）" % (SERIES_FILE, doc["meta"]["n_days"], days_ok[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
