# -*- coding: utf-8 -*-
"""review/source.py 单测（从旧仓同名单测移植，4.5）

覆盖（对齐任务书 §3 与旧端验收：
  · 无 MD / 只有旧 MD / 新交易日 MD / 同日 mtime 更新
  · 非法文件名（2026-13-01、2026-02-30、日复盘x.md、.txt）跳过
  · 扫描失败（PermissionError）→ 空列表不抛穿
  · 连续刷新不重渲染（命中缓存时 renderer 调用计数不增）
  · mtime 变 → miss
  · 降级：保留上一份成功内容 / 无历史成功 → html=None
  · 缓存键含 mainline 版本（本版 mainline 置空 → 恒定；monkeypatch 目录时仍生效）/ 含主题
  · renderer 抛 SystemExit（fupan_chart 无赛道）不穿透

**红线条**：全程 monkeypatch REVIEW_DIR / MAINLINE_DIR 到 tmp，
绝不读写生产 `夏天-activity/每日复盘`。
迁移补充的两条新断言：目录由调用方显式传入（`pick_latest(date, review_dir)`）；
无目录且无常量 → ValueError（不猜生产路径）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.plugins.review import source as RS  # noqa: E402


def _write(dirpath, name, text="x", mtime=None):
    p = os.path.join(str(dirpath), name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    if mtime is not None:
        os.utime(p, ns=(mtime, mtime))
    return p


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.hist = os.path.join(self.dir, "hist")
        os.makedirs(self.hist, exist_ok=True)
        self._p1 = mock.patch.object(RS, "REVIEW_DIR", self.dir)
        self._p2 = mock.patch.object(RS, "MAINLINE_DIR", self.hist)
        self._p1.start()
        self._p2.start()
        RS.reset_caches()

    def tearDown(self):
        RS.reset_caches()
        self._p1.stop()
        self._p2.stop()
        self.tmp.cleanup()


class ScanPickTest(_Base):
    def test_pick_latest_none_when_dir_empty(self):
        """无 MD → None（调用方返 404，不编造）。"""
        self.assertIsNone(RS.pick_latest())
        self.assertEqual(RS.scan_candidates(), [])

    def test_pick_latest_single_old_file(self):
        """只有旧 MD → 仍能发现（不要求是今天）。"""
        _write(self.dir, "日复盘 2026-08-28.md")
        pick = RS.pick_latest()
        self.assertIsNotNone(pick)
        self.assertEqual(pick.date, "2026-08-28")

    def test_pick_latest_prefers_max_date(self):
        """新交易日 MD > 旧交易日（按文件名交易日择大）。"""
        _write(self.dir, "日复盘 2026-09-10.md")
        _write(self.dir, "日复盘 2026-09-11.md")
        _write(self.dir, "日复盘 2026-09-09.md")
        self.assertEqual(RS.pick_latest().date, "2026-09-11")

    def test_pick_latest_max_mtime_same_date(self):
        """同一交易日多版本 → 取 mtime 最大。"""
        _write(self.dir, "日复盘 2026-09-11.md", "old", mtime=1_000_000_000_000_000_000)
        _write(self.dir, "日复盘 2026-09-11.md", "new", mtime=2_000_000_000_000_000_000)
        pick = RS.pick_latest()
        self.assertEqual(pick.date, "2026-09-11")
        self.assertEqual(pick.mtime_ns, 2_000_000_000_000_000_000)

    def test_pick_latest_specific_date_exact_no_backtrack(self):
        """?date= 精确日：该日不存在 → None（不回溯到更早）。"""
        _write(self.dir, "日复盘 2026-09-10.md")
        self.assertIsNone(RS.pick_latest("2026-09-12"))
        self.assertEqual(RS.pick_latest("2026-09-10").date, "2026-09-10")

    def test_scan_skips_illegal_names(self):
        """非法文件名/非法日期一律跳过：乱码、越界月、不存在日、错误前缀、非 .md。"""
        _write(self.dir, "日复盘 2026-13-01.md")   # 越界月
        _write(self.dir, "日复盘 2026-02-30.md")   # 不存在的日
        _write(self.dir, "日复盘x.md")             # 无日期
        _write(self.dir, "日复盘 2026-09-10.txt")  # 非 .md
        _write(self.dir, "别的文件 2026-09-10.md")  # 前缀不符
        _write(self.dir, "日复盘 2026-09-10.md")   # 唯一合法
        names = [p.path for p in RS.scan_candidates(force=True)]
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith("日复盘 2026-09-10.md"))

    def test_scan_empty_on_oserror(self):
        """扫描失败（PermissionError）→ 返回 []，不抛穿。"""
        with mock.patch.object(os, "scandir", side_effect=PermissionError("denied")):
            self.assertEqual(RS.scan_candidates(force=True), [])

    # ---- 以下两条是迁移新增：目录改为调用方显式传入（不再 from common.paths）----

    def test_pick_latest_accepts_explicit_dir(self):
        """显式传目录（生产 = ctx.text_dir('daily_review')）→ 不依赖模块常量。"""
        with mock.patch.object(RS, "REVIEW_DIR", None):
            RS.reset_scan()
            self.assertIsNone(RS.pick_latest())
            _write(self.dir, "日复盘 2026-09-11.md")
            pick = RS.pick_latest(None, self.dir)
            self.assertIsNotNone(pick)
            self.assertEqual(pick.date, "2026-09-11")

    def test_scan_version_cache_follows_directory(self):
        """扫描缓存带目录指纹：换目录立即重扫，不被上一个目录的 30s TTL 污染。"""
        other = os.path.join(self.dir, "other")
        os.makedirs(other, exist_ok=True)
        _write(self.dir, "日复盘 2026-09-11.md")
        self.assertEqual(RS.pick_latest().date, "2026-09-11")
        _write(other, "日复盘 2026-09-12.md")
        self.assertEqual(RS.pick_latest(None, other).date, "2026-09-12")

    def test_no_dir_degrades_to_empty(self):
        """目录常量与参数都缺 → 不猜路径，扫描返回空（旧端目录不存在 → [] 同口径）。"""
        with mock.patch.object(RS, "REVIEW_DIR", None):
            RS.reset_scan()
            self.assertEqual(RS.scan_candidates(force=True), [])


class CacheTest(_Base):
    def test_render_cached_hit_skips_renderer(self):
        """连续刷新不重渲染：指纹不变 → 第二次命中，renderer 只调 1 次。"""
        _write(self.dir, "日复盘 2026-09-11.md", "body")
        pick = RS.pick_latest()
        calls = {"n": 0}

        def _renderer(p):
            calls["n"] += 1
            return {"html": "<p>ok</p>", "title": "t", "charts": None}

        r1 = RS.render_cached(pick, "nightsail", _renderer)
        r2 = RS.render_cached(pick, "nightsail", _renderer)
        self.assertEqual(calls["n"], 1, "指纹未变不得重渲染")
        self.assertEqual(r1["cache"], "miss")
        self.assertEqual(r2["cache"], "hit")
        self.assertEqual(r2["html"], "<p>ok</p>")

    def test_render_cached_miss_on_mtime_change(self):
        """MD mtime 变 → 指纹变 → miss（重渲染）。"""
        p = _write(self.dir, "日复盘 2026-09-11.md", "v1")
        pick = RS.pick_latest()
        calls = {"n": 0}

        def _renderer(x):
            calls["n"] += 1
            return {"html": f"<p>{calls['n']}</p>"}

        RS.render_cached(pick, "nightsail", _renderer)
        os.utime(p, ns=(9_000_000_000_000_000_000, 9_000_000_000_000_000_000))
        RS.reset_scan()   # 模拟超过 SCAN_TTL_S：重扫磁盘拿到新指纹
        pick2 = RS.pick_latest()
        RS.render_cached(pick2, "nightsail", _renderer)
        self.assertEqual(calls["n"], 2, "mtime 变须重渲染")

    def test_cache_key_includes_mainline_version(self):
        """mainline 版本变化 → 指纹变化（MD 未变但图表输入变）。

        本版生产 MAINLINE_DIR 置空（恒 (0,0)）；这里 monkeypatch 回 tmp 目录，
        锁住「分量仍在缓存键里」这条口径（未来恢复图表不用改 key）。
        """
        _write(self.dir, "日复盘 2026-09-11.md")
        pick = RS.pick_latest()
        k1 = RS.cache_key(pick, "nightsail")
        _write(self.hist, "mainline_history_20260911.json", "{}")
        RS.reset_mainline()   # 模拟超过 MAINLINE_TTL_S
        k2 = RS.cache_key(pick, "nightsail")
        self.assertNotEqual(k1, k2)

    def test_cache_key_includes_charts_version(self):
        """charts/*.svg 出现/变化（MD 不动）→ 指纹变化（图单独重生成也要失效）。"""
        _write(self.dir, "日复盘 2026-09-11.md", "![图](charts/x.svg)")
        pick = RS.pick_latest()
        k1 = RS.cache_key(pick, "nightsail")
        charts = os.path.join(self.dir, "charts")
        os.makedirs(charts)
        _write(charts, "x.svg", "<svg>v1</svg>")
        k2 = RS.cache_key(pick, "nightsail")
        self.assertNotEqual(k1, k2)

    def test_render_cached_miss_on_svg_change(self):
        """只改 charts/*.svg（MD mtime/size 不变）→ 必须重渲染，不能回旧图。

        回归背景（2026-11-07）：旧键只看 MD，图表单独重生成时返回陈旧 HTML
        （实测 cache=hit、旧 SVG 内容仍在）。charts_version 进键后本测试锁死行为。
        """
        _write(self.dir, "日复盘 2026-09-11.md", "![图](charts/x.svg)")
        charts = os.path.join(self.dir, "charts")
        os.makedirs(charts)
        svg = _write(charts, "x.svg", "<svg>v1</svg>")
        pick = RS.pick_latest()
        calls = {"n": 0}

        def _renderer(p):
            calls["n"] += 1
            return {"html": f"<p>{calls['n']}</p>"}

        RS.render_cached(pick, "nightsail", _renderer)
        os.utime(svg, ns=(9_000_000_000_000_000_000, 9_000_000_000_000_000_000))
        RS.render_cached(pick, "nightsail", _renderer)
        self.assertEqual(calls["n"], 2, "图变须重渲染")

    def test_cache_key_includes_asset_files(self):
        """纸面 report.css 变化 → 指纹变化；缺失与出现也必须可区分。"""
        _write(self.dir, "日复盘 2026-09-11.md")
        pick = RS.pick_latest()
        css = os.path.join(self.dir, "report.css")
        k_missing = RS.cache_key(pick, "paper", asset_paths=(css,))
        _write(self.dir, "report.css", "body{color:red}")
        k1 = RS.cache_key(pick, "paper", asset_paths=(css,))
        os.utime(css, ns=(9_000_000_000_000_000_000, 9_000_000_000_000_000_000))
        k2 = RS.cache_key(pick, "paper", asset_paths=(css,))
        self.assertNotEqual(k_missing, k1, "文件新出现必须失效")
        self.assertNotEqual(k1, k2, "CSS 变化必须失效")

    def test_render_cached_miss_on_asset_change(self):
        """只改 report.css（MD 不变）→ 纸面也必须重渲染，不能回旧样式。"""
        _write(self.dir, "日复盘 2026-09-11.md")
        css = _write(self.dir, "report.css", "body{color:red}")
        pick = RS.pick_latest()
        calls = {"n": 0}

        def _renderer(p):
            calls["n"] += 1
            return {"html": f"<p>{calls['n']}</p>"}

        RS.render_cached(pick, "paper", _renderer, asset_paths=(css,))
        os.utime(css, ns=(9_000_000_000_000_000_000, 9_000_000_000_000_000_000))
        RS.render_cached(pick, "paper", _renderer, asset_paths=(css,))
        self.assertEqual(calls["n"], 2, "report.css 变须重渲染")

    def test_mainline_version_zero_when_dir_empty_constant(self):
        """生产口径：MAINLINE_DIR 为空 → mainline_version() 恒 (0, 0)（v6 不出图）。"""
        with mock.patch.object(RS, "MAINLINE_DIR", None):
            RS.reset_mainline()
            self.assertEqual(RS.mainline_version(), (0, 0))

    def test_cache_key_includes_theme(self):
        """主题不同 → 指纹不同（保留分量防未来需要）。"""
        _write(self.dir, "日复盘 2026-09-11.md")
        pick = RS.pick_latest()
        self.assertNotEqual(RS.cache_key(pick, "nightsail"),
                            RS.cache_key(pick, "moonwhite"))


class DegradeTest(_Base):
    def test_render_failure_keeps_last_good(self):
        """渲染失败 → 保留上一份成功内容 + degraded=True（不拖垮面板）。"""
        p = _write(self.dir, "日复盘 2026-09-11.md", "v1")
        pick = RS.pick_latest()

        def _ok(x):
            return {"html": "<p>good</p>", "title": "t"}

        r1 = RS.render_cached(pick, "nightsail", _ok)
        self.assertFalse(r1["degraded"])
        os.utime(p, ns=(9_000_000_000_000_000_000, 9_000_000_000_000_000_000))
        RS.reset_scan()   # 模拟超过 SCAN_TTL_S

        def _bad(x):
            raise RuntimeError("boom")

        pick2 = RS.pick_latest()
        r2 = RS.render_cached(pick2, "nightsail", _bad)
        self.assertTrue(r2["degraded"])
        self.assertIn("boom", r2["error"])
        self.assertEqual(r2["html"], "<p>good</p>", "须回退上一份成功内容")

    def test_render_failure_no_prior_degraded(self):
        """从无成功记录 + 渲染失败 → html=None，degraded=True（调用方据此显示降级态）。"""
        _write(self.dir, "日复盘 2026-09-11.md")
        pick = RS.pick_latest()

        def _bad(x):
            raise RuntimeError("nope")

        r = RS.render_cached(pick, "nightsail", _bad)
        self.assertIsNone(r["html"])
        self.assertTrue(r["degraded"])

    def test_render_systemexit_caught(self):
        """fupan_chart 无赛道 raise SystemExit（不继承 Exception）→ 必须被捕获，不穿透。"""
        _write(self.dir, "日复盘 2026-09-11.md")
        pick = RS.pick_latest()

        def _exit(x):
            raise SystemExit("[错误] 无赛道数据")

        r = RS.render_cached(pick, "nightsail", _exit)
        self.assertTrue(r["degraded"])
        self.assertIn("无赛道数据", r["error"])

    def test_pick_frozen(self):
        """ReviewPick 冻结（缓存键依赖不可变快照，别处改了指针会让 cache_key 失准）。"""
        _write(self.dir, "日复盘 2026-09-11.md")
        pick = RS.pick_latest()
        with self.assertRaises(Exception):
            pick.date = "2026-09-12"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
