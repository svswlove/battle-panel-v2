# -*- coding: utf-8 -*-
"""emotion 插件 · 离线证据导出（CLI，不参与面板运行时）。

用法（在 projects/panel-v2 目录下）：

    python tools/build_emotion_evidence.py              # 导出证据 JSON + 打印今日明细
    python tools/build_emotion_evidence.py --days 60    # 指定曲线窗口
    python tools/build_emotion_evidence.py --no-save    # 只看不落盘

产物：`data/panel_v2/emotion/evidence_{asof}.json`（面板不读它；它给复盘/回执当证据用，
与页面同源——同一个 `source.build_payload`，不是第二套算法）。

**2026-11-09 移位**：本文件原在 `app/plugins/emotion/build.py`，被 cutover gate 的
「插件无写代码」静态扫描命中（`.write_text(` / `.mkdir(`）——插件目录只应放运行时四件套，
离线证据脚本放 `tools/`。移位后只改路径推算与兄弟模块加载位置，算法一行未动。

**为什么这里直接 sqlite3.connect**：本文件是离线 CLI，不是插件运行时。运行时（backend.py）
走 `ctx.db_read()` 拿 core 白名单连接；离线脚本没有 ctx，只能自己开只读连接，
路径仍从 `paths.db_path()` 白名单取，不写死盘符，`mode=ro` + `query_only`。
"""
from __future__ import annotations

import argparse
import importlib.util as _ilu
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

# 2026-11-09 移位后：本文件在 tools/ 下 → 项目根 = 上溯 1 层；插件目录另指
_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_DIR = _ROOT / "app" / "plugins" / "emotion"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core import paths  # noqa: E402


def _load_sibling(name: str):
    """按路径加载插件目录里的兄弟模块（与 backend.py 同款兜底，避免依赖包结构）。"""
    try:
        mod = __import__("app.plugins.emotion.%s" % name, fromlist=[name])
        return mod
    except Exception:  # noqa: BLE001 - 任何导入失败都退到按文件加载
        spec = _ilu.spec_from_file_location(
            "_panel_v2_emotion_%s_cli" % name, _PLUGIN_DIR / ("%s.py" % name))
        if spec is None or spec.loader is None:
            raise ImportError("无法加载 %s" % name)
        mod = _ilu.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


source = _load_sibling("source")


def open_db():
    path = paths.db_path("track_pool.db")
    if path is None or not Path(path).is_file():
        raise SystemExit("track_pool.db 不在只读白名单或不存在: %s" % path)
    uri = "file:" + Path(path).resolve().as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _fmt(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return ("%." + str(digits) + "f") % v
    return str(v)


def print_today(payload: Dict[str, Any]) -> None:
    today = payload.get("today") or {}
    print("== 数据日 %s · 样本 %d 个交易日（收盘口径）· 窗口 %d 日 ==" % (
        payload.get("asof"), payload.get("n_trade_dates") or 0, payload.get("window") or 0))
    print("总分 %.2f  档位 %s   |  规则 stage=%s" % (
        today.get("score") or 0.0, today.get("band"), today.get("stage")))
    print("stage_note: %s" % (today.get("stage_note") or ""))
    print("-" * 78)
    print("%-14s %12s %12s %8s %6s" % ("因子", "原始值", "阈值/基准", "加减分", "参与"))
    for f in today.get("factors") or []:
        raw = f.get("raw")
        if isinstance(raw, dict):
            raw = "dt %s / zt %s" % (_fmt(raw.get("dt"), 0), _fmt(raw.get("zt"), 0))
        print("%-14s %12s %12s %+8s %6s  %s" % (
            f.get("label"), _fmt(raw, 4), _fmt(f.get("threshold"), 4),
            _fmt(f.get("points")), "是" if f.get("participated") else "否", f.get("note") or ""))
    print("-" * 78)
    print("基准 %.1f + 各因子 = %.2f（clamp 0–100）" % (
        payload.get("base_score") or 0.0, today.get("score") or 0.0))

    hist = payload.get("history") or []
    if hist:
        vals = sorted(p["score"] for p in hist)
        n = len(vals)
        def q(p):
            return vals[min(n - 1, max(0, int(round(p * (n - 1)))))]
        print("\n近 %d 日分数分布：min %.2f / q25 %.2f / q50 %.2f / q75 %.2f / max %.2f / mean %.2f" % (
            n, vals[0], q(0.25), q(0.5), q(0.75), vals[-1], sum(vals) / n))
        counts: Dict[str, int] = {}
        for p in hist:
            counts[p["band"]] = counts.get(p["band"], 0) + 1
        print("档位分布：" + "  ".join("%s %d" % (k, counts[k]) for k in ("亢奋", "偏热", "中性", "偏冷", "冰点") if k in counts))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="曲线窗口（交易日）")
    ap.add_argument("--no-save", action="store_true", help="只打印，不写证据 JSON")
    args = ap.parse_args()

    conn = open_db()
    try:
        payload = source.build_payload(conn, history_days=args.days)
    finally:
        conn.close()

    print_today(payload)
    if args.no_save:
        return
    out_dir = Path(paths.OUT_DIR) / "emotion"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / ("evidence_%s.json" % (payload.get("asof") or "unknown"))
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n证据已落盘：%s（%d KB）" % (target, target.stat().st_size // 1024))


if __name__ == "__main__":
    main()
