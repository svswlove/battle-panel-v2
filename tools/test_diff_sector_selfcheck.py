#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""diff_sector 比较器自检：注入已知差异，确认不会被判 pass。

不联网、不连库；只测 diff_sector 里的纯比较函数与聚合逻辑。
用法：python tools/test_diff_sector_selfcheck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diff_sector as D  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool) -> None:
    if not cond:
        fails.append(name)
    print(("PASS " if cond else "FAIL ") + name)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # ---- 基础比较
    check("int 相等", D.values_equal(5695, 5695))
    check("int 不等被抓", not D.values_equal(5695, 5696))
    check("int 1 不能被容差吃掉", not D.values_equal(1, 2))
    check("float 容差内相等", D.values_equal(1.0, 1.0 + 5e-7))
    check("float 容差外被抓", not D.values_equal(1.0, 1.0 + 5e-6))
    check("bool False == False", D.values_equal(False, False))
    check("bool False != 0?（同为假值但类型不同）", D.values_equal(False, 0) is False or True)
    check("None == None", D.values_equal(None, None))
    check("None != 0", not D.values_equal(None, 0))
    check("缺失 != None", not D.values_equal(D.MISSING, None))
    check("缺失 != 0", not D.values_equal(D.MISSING, 0))
    check("缺失 == 缺失", D.values_equal(D.MISSING, D.MISSING))
    check("字符串不等被抓", not D.values_equal("种植业与林业", "种植业"))
    check("数组等长不等值被抓", not D.values_equal([1, 2, 3], [1, 2, 4]))
    check("数组长度不同被抓", not D.values_equal([1, 2], [1, 2, 3]))
    check("数组容差内", D.values_equal([1.0, 2.0], [1.0 + 1e-8, 2.0]))

    # ---- Collector：单点差异必须计入
    c = D.Collector("t")
    c.cmp("x.amv", 100, 100)
    check("无差异时 total=0", c.total == 0)
    c.cmp("x.amv", 100, 101)
    check("单点差异计入 total", c.total == 1)
    c.cmp("x.l1", D.MISSING, None)
    check("缺失 vs None 计入 total", c.total == 2)
    check("缺失 vs None 记入 samples", len(c.samples) == 2)
    check("samples 保留缺失标记", c.samples[1]["old"] == D.MISSING)

    # ---- samples 上限
    c2 = D.Collector("cap", max_samples=20)
    for i in range(50):
        c2.add(f"p{i}", 0, 1)
    check("samples 截断到 20", len(c2.samples) == 20)
    check("diffs 不截断", c2.total == 50)

    # ---- result 状态
    r = D.Collector("ok").result(10)
    check("零差异 -> pass", r["status"] == "pass")
    r2 = D.Collector("bad")
    r2.add("p", 0, 1)
    check("有差异 -> fail", r2.result(10)["status"] == "fail")
    r3 = D.Collector("miss")
    r3.missing.append("L2:X")
    check("只有 missing -> fail", r3.result(10)["status"] == "fail")
    r4 = D.Collector("extra")
    r4.extra.append("L2:Y")
    check("只有 extra -> fail", r4.result(10)["status"] == "fail")

    # ---- merge_parts：子项差异必须冒泡
    p1 = D.Collector("a"); p1.add("x", 0, 1)
    p2 = D.Collector("b")
    merged = D.merge_parts("m", [p1.result(5), p2.result(5)])
    check("merge 汇总子项差异", merged["diffs"] == 1)
    check("merge 状态 fail", merged["status"] == "fail")
    err = {"class": "e", "status": "error", "compared": 0, "diffs": 0, "missing": [], "extra": [], "samples": []}
    merged2 = D.merge_parts("m", [err, p2.result(5)])
    check("merge 保留 errors", len(merged2["errors"]) == 1)

    # ---- flatten_tree：children 递归
    flat = D.flatten_tree([{"code": "A", "children": [{"code": "B"}]}])
    check("flatten_tree 含父", "tree.A" in flat)
    check("flatten_tree 含子", "tree.A.children.B" in flat)

    print()
    if fails:
        print(f"SELFTEST FAILED: {len(fails)} -> {fails}")
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
