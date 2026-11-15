# -*- coding: utf-8 -*-
"""用 eltdx 重建股票名称表 `data/stock_names.json`（A 股 names + ETF etfs 两段）。

2026-10-31：产物从 `_tmp/stock_names_em.json` 迁到 `data/stock_names.json`。
临时区跟一堆日志混住，清理就断供（已断两次），且被 5 处消费（panel-v2 paths、
amv_store、stock_score_engine、build_pinyin_index、本脚本）。

背景（2026-10-25）：
  板块详情卡里部分股票只显示代码（如 688809 强一股份）。根因不是前端字段写错，
  而是名称表文件**不存在**——它原本由 `build_dashboard_data.py` 每日拉东财 clist 写入，
  该脚本在 2026-10-25 的历史淘汰物清理中被退役，此后没人再生成。
  少了它，`_stock_names()` 只能回退到 `stock_top` 当日快照，而那张表只存
  「各板块前 N 名」，覆盖 2253 / 6164 只，新股必然落在外面。

取数方式（主人 2026-10-25 指定）：走 eltdx，不联网东财。
  `TdxClient().codes.latest_stocks()` 一次返回全部 A 股 SecurityCode（含 name 字段），
  实测 5580 条 / 2.2 秒。与 8800 行情链同一个数据源，不再引入新的外部依赖。

写出的结构与旧文件一致（旧脚本 `fetch_names()` 读的是 `doc['names']`），
便于 `_stock_names()` 零改动复用：
    {"date": "YYYY-MM-DD", "source": "eltdx", "count": N, "names": {"688809": "强一股份", ...},
     "etf_count": M, "etfs": {"510300": "沪深300ETF华泰柏", ...}}

ETF 段（2026-11-11 主人：面板要能搜到 ETF、能加自选）：
  `TdxClient().codes.etfs("sh"/"sz")` → 1,707 只 ETF（含货币/债券/商品），实测 1.0s。
  **不混进 `names`**：names 被 5 处当「A 股个股名单」消费（板块成分股 / 打分 / 拼音索引），
  混进去等于让 ETF 冒充个股；搜索侧单独读 `etfs` 段，口径互不污染。

用法：
    python tools/build_stock_names.py            # 有当日缓存则跳过
    python tools/build_stock_names.py --force    # 强制重拉
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 2026-11-15 公开化：由 core.paths 推导工作区根（支持 PANEL_V2_WS 覆盖），不写死本机路径。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core import paths as _paths  # noqa: E402

WS = str(_paths.WS)
OUT = os.path.join(WS, "data", "stock_names.json")


def fetch_names() -> dict:
    """eltdx → {code6: name}。失败抛异常，由调用方决定是否降级。"""
    from eltdx import TdxClient  # noqa: PLC0415 - 重依赖，只在真正取数时导入

    with TdxClient(timeout=15, probe_hosts=False) as cli:
        rows = cli.codes.latest_stocks()
    out: dict[str, str] = {}
    for r in rows:
        code = getattr(r, "code", None)
        name = getattr(r, "name", None)
        if not code or not name:
            continue
        # 只收 6 位数字代码；名称去空白（eltdx 偶有首尾空格）
        code = str(code).strip()
        name = str(name).strip()
        if len(code) != 6 or not code.isdigit() or not name:
            continue
        out[code] = name
    return out


def fetch_etfs() -> dict:
    """eltdx → {code6: name}（ETF 全量，沪 + 深）。失败抛异常，由调用方决定是否降级。"""
    from eltdx import TdxClient  # noqa: PLC0415 - 重依赖，只在真正取数时导入

    out: dict[str, str] = {}
    with TdxClient(timeout=15, probe_hosts=False) as cli:
        for market in ("sh", "sz"):
            for r in cli.codes.etfs(market):
                code = getattr(r, "code", None)
                name = getattr(r, "name", None)
                if not code or not name:
                    continue
                code = str(code).strip()
                name = str(name).strip()
                if len(code) != 6 or not code.isdigit() or not name:
                    continue
                out[code] = name
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略当日缓存，强制重拉")
    args = ap.parse_args(argv)

    today = time.strftime("%Y-%m-%d")
    if not args.force and os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                doc = json.load(f)
            if doc.get("date") == today and doc.get("names") and doc.get("etfs"):
                print("当日缓存命中（%s，个股 %d 条 / ETF %d 条），跳过"
                      % (today, len(doc["names"]), len(doc["etfs"])))
                return 0
        except Exception:  # noqa: BLE001 - 缓存损坏就当没有
            pass

    t0 = time.time()
    try:
        names = fetch_names()
    except Exception as e:  # noqa: BLE001 - 网络/服务端不可用：如实报错，不写空表
        print("eltdx 取数失败：%s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1

    if not names:
        print("eltdx 返回 0 条名称，拒绝写空表", file=sys.stderr)
        return 1

    # ETF 段失败不拖垮个股段：个股名单是 5 处消费端的刚需，ETF 只是搜索增强。
    # 但失败要打印出来（不静默），下日链上重跑会自动补齐。
    etfs: dict[str, str] = {}
    try:
        etfs = fetch_etfs()
    except Exception as e:  # noqa: BLE001
        print("ETF 取数失败（本次不写 etfs 段）：%s: %s" % (type(e).__name__, e), file=sys.stderr)
    if not etfs:
        print("ETF 返回 0 条，etfs 段为空（搜索会只剩个股）", file=sys.stderr)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {
        "date": today,
        "source": "eltdx.codes.latest_stocks + codes.etfs",
        "count": len(names),
        "names": names,
        "etf_count": len(etfs),
        "etfs": etfs,
    }
    # 原子替换：先写临时文件再改名，避免读到半截 JSON
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)

    print("已写出 %s：个股 %d 条 / ETF %d 条，耗时 %.1fs" % (OUT, len(names), len(etfs), time.time() - t0))
    for probe in ("688809", "600519", "000001"):
        if probe in names:
            print("  抽样 %s -> %s" % (probe, names[probe]))
    for probe in ("510300", "159915"):
        if probe in etfs:
            print("  抽样 ETF %s -> %s" % (probe, etfs[probe]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
