# -*- coding: utf-8 -*-
"""自选池打开耗时 · 接口侧压测（只读，不改任何数据）。

量的是「24 只票的 /api/kline 在并发 N 下要多久」，用来判断前端把并发从 4 提到更高是否值得。
跑法：python app/plugins/watchlist/tests/bench_kline_concurrency.py [--conc 4,8,12]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:8810"


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read()


def timed(url: str) -> float:
    t = time.perf_counter()
    get(url)
    return (time.perf_counter() - t) * 1000.0


def codes() -> list:
    d = json.loads(get(BASE + "/api/watchlist.json").decode("utf-8"))
    return [it["code"] for it in d["items"] if it.get("kind") == "stock"]


def run(cs: list, conc: int) -> dict:
    urls = [BASE + "/api/kline/%s?period=d&limit=60" % c for c in cs]
    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=conc) as ex:
        per = list(ex.map(timed, urls))
    wall = (time.perf_counter() - t) * 1000.0
    per.sort()
    return {"conc": conc, "n": len(urls), "wall_ms": round(wall),
            "p50": round(per[len(per) // 2]), "max": round(per[-1])}


def main() -> int:
    concs = [4, 8, 12]
    for i, a in enumerate(sys.argv):
        if a == "--conc" and i + 1 < len(sys.argv):
            concs = [int(x) for x in sys.argv[i + 1].split(",") if x.strip()]
    cs = codes()
    print("codes=%d" % len(cs))
    # 暖场：先打一轮，避免把首次冷启动算进并发对比
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(timed, [BASE + "/api/kline/%s?period=d&limit=60" % c for c in cs]))
    for c in concs:
        rows = [run(cs, c) for _ in range(2)]
        best = min(rows, key=lambda r: r["wall_ms"])
        print(json.dumps(best, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
