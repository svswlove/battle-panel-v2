# -*- coding: utf-8 -*-
"""口径守门：个股四态的缺失值语义必须只有一种。

背景（2026-10-25，实测）：
  同一个指标在三个地方被算了三遍，且缺失值处理各不相同：
    · app/lib/amv_regime.py::stock_state_series  正本
    · app/plugins/compat_watchlist/backend.py:467  缺失 → 0.0
    · app/plugins/sector/backend.py:446-447        缺失 → continue（删元素，日期错位）
  实测后果：全市场 1073 只有缺失记录的股票，历史累计分歧 27001 天；
  最新交易日两边都有值但结论不同的 8 只（000668 / 002388 / 002667 / 002860 /
  300082 / 301716 / 603183 / 603759），其中多只是「空 vs 多」的方向性相反。

本测试守三件事：
  1. 正本对 None 的处理是「保留位置、不参与计算」，不是置零、也不是删元素
  2. 置零与跳过在真实量级序列上会给出不同结论（证明这条规则不是形式主义）
  3. 消费端不得自己把 None 转成 0.0（静态扫描）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]   # app/（本文件在 app/lib/tests/ 下，上溯三级）
PLUGINS = APP / "plugins"
sys.path.insert(0, str(APP.parent))

from app.lib import amv_regime as L  # noqa: E402


def _series_with_null_at(pos: int, n: int = 40) -> list:
    """构造真实量级（亿元级）的上升序列，指定位置置 None。"""
    base = 5e9
    vals = [base * (1 + 0.004 * i + 0.002 * ((i * 7) % 5)) for i in range(n)]
    vals[pos] = None
    return vals


class TestNullSemantics:
    def test_length_preserved(self) -> None:
        """缺失日必须占位：返回长度与输入等长（不能删元素导致日期错位）。"""
        for pos in (0, 1, 20, 38):
            vals = _series_with_null_at(pos)
            out = L.stock_state_series(vals)
            assert len(out) == len(vals), "位置 %d：长度被改变，日期会错位" % pos

    def test_null_day_has_no_state(self) -> None:
        """缺失当日不出判定（不是 0，也不是沿用前一日）。"""
        vals = _series_with_null_at(20)
        out = L.stock_state_series(vals)
        assert out[20] is None, "缺失日应留空，实际给出 %r" % (out[20],)

    def test_seed_is_first_valid_not_zero(self) -> None:
        """EMA 种子取第一个有效值，不是 vals[0]。

        做法：首日置 None 后，结果应与「序列整体从第 1 天开始」一致。
        若种子错误地用了 0，EMA 要从 0 收敛，前面若干天会全部不同。
        """
        vals = _series_with_null_at(0)
        out_null_first = L.stock_state_series(vals)
        out_trimmed = L.stock_state_series(vals[1:])
        # out_null_first[1:] 应与 out_trimmed 逐位相同
        assert out_null_first[1:] == out_trimmed, "首日缺失污染了后续整条 EMA"

    def test_zero_fill_gives_different_verdict(self) -> None:
        """证明这条规则有实际后果：置零与跳过会给出不同四态。

        不用固定股票代码，用构造序列：真实量级 + 一处缺失，
        置零会压低 EMA，导致金叉/死叉判断翻转。
        """
        differing = 0
        for pos in (0, 1, 5, 20, 30):
            vals = _series_with_null_at(pos)
            skipped = L.stock_state_series(vals)
            zeroed = L.stock_state_series([0.0 if v is None else v for v in vals])
            if any(a != b for a, b in zip(skipped, zeroed)):
                differing += 1
        assert differing == 5, (
            "置零与跳过在 5 个位置上都应产生差异（实测全市场 27001 天分歧、"
            "8 只票方向相反）；只差 %d 处说明构造的序列不具代表性" % differing)


class TestNoConsumerSideZeroFill:
    """消费端不得自己把 None 转成 0.0 —— 那是第二份口径。"""

    # 允许出现的文件（正本自己可以处理 None，但它不该写 0.0）
    SCAN_DIRS = [PLUGINS, APP / "core", APP / "lib"]

    def _py_files(self):
        for d in self.SCAN_DIRS:
            for p in d.rglob("*.py"):
                if "tests" in p.parts or "__pycache__" in p.parts:
                    continue
                yield p

    def test_no_none_to_zero_on_amv(self) -> None:
        """扫 `if ... is not None else 0.0` 且上下文含 amv / vals 的写法。"""
        offenders = []
        pat = re.compile(r"if\s+\w+(?:\[[^\]]*\])?\s+is\s+not\s+None\s+else\s+0\.0")
        for p in self._py_files():
            src = p.read_text(encoding="utf-8", errors="replace")
            for m in pat.finditer(src):
                line_no = src[: m.start()].count("\n") + 1
                line = src.split("\n")[line_no - 1]
                if re.search(r"amv|vals|series", line, re.I):
                    offenders.append("%s:%d  %s" % (p.relative_to(APP.parent), line_no, line.strip()[:90]))
        assert not offenders, (
            "发现消费端把 AMV 缺失值置 0.0（第二份口径，会把缺失日当真实 0，"
            "压低 EMA/MA10 并翻转多空）。改用正本 stock_state_series，让它自己处理 None：\n  "
            + "\n  ".join(offenders))

    def test_stock_state_series_is_single_entry(self) -> None:
        """四态判定只允许出现在正本；插件里不得自建 EMA/MA10 四态。"""
        offenders = []
        for p in self._py_files():
            if p.parent.name == "lib" and p.name == "amv_regime.py":
                continue
            src = p.read_text(encoding="utf-8", errors="replace")
            # 插件里自己写「白线/多空线」判定
            if re.search(r"偏多.*偏空|偏空.*偏多", src) and "stock_state" not in src:
                offenders.append(str(p.relative_to(APP.parent)))
        assert not offenders, "插件内自建四态判定（应调 amv_regime.stock_state_series）：%s" % offenders
