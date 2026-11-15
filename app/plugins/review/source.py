# -*- coding: utf-8 -*-
"""review/source.py · 复盘 MD 按需发现 / 指纹 / 缓存 / 降级（纯函数，不依赖 FastAPI）

职责
    统一作战面板「复盘 Tab」的唯一事实源定位与渲染缓存：
    在给定目录扫描 `日复盘 YYYY-MM-DD.md` → 选最新交易日（同日取最大 mtime）
    → 按 (路径, mtime, 大小, mainline 版本, 主题, 渲染器版本) 指纹做 LRU 缓存
    → 未变化时零重渲染直接命中；渲染异常时保留上一份成功内容并降级。

设计初心（2026-09-13 主人定）
    · 「MD 是唯一事实源」——复盘流程只落 MD，不再要求生成独立 HTML 孤儿制品；
      面板按需渲染，不新增高频轮询。
    · 「未变化不重渲染」——复盘 Tab 每 60s 轮询，此前无条件全量重渲染；
      本模块以文件指纹 + 相关输入版本（mainline_history）+ 主题 拦在服务端。
    · 降级不拖垮面板——单次渲染失败只影响复盘 Tab 自身（返回 degraded + 上一份成功内容），
      绝不 500 拖垮其他模块。

迁移说明（2026-10-23，任务书 docs/task_review_md_4_5.md §2）
    本文件从旧仓同口径实现（2026-11-07 已退役删除）移植，**只改依赖注入，不动算法**：
    · 不再 `from common.paths import SUMMER_DIR`（插件契约禁 `from common`），
      改为 `pick_latest(date, review_dir)` 显式收目录 —— 生产传
      `ctx.text_dir("daily_review")`（4.5 新增的只读文本目录白名单）。
    · `MAINLINE_DIR` **置空**：v6 默认路径不生成泳道/赛道图（旧端默认同样如此，
      `OH_MAINLINE_ALLOW != 1` → charts 双 null），故 `mainline_version()` 恒返回 (0, 0)。
      缓存键里保留该分量（口径不变），未来要恢复图表时把目录传回来即可。
    · 2026-11-07 补：缓存键新增 `charts_version(md_path)`（MD 同目录 `charts/*.svg`
      的 mtime/size 指纹）与 `asset_files_version(asset_paths)`（纸面 `report.css`）。
      原因：v6 与纸面都会把 SVG 内联进 HTML、纸面还会读 report.css；图表/CSS
      单独重生成时 MD 指纹不变，旧键会命中陈旧 HTML（实测复现）。这是对旧端
      mainline_version 意图的直接落实——盯真正的渲染输入，而不是间接的上游数据。
    · 其余（ReviewPick / 30s 扫描 TTL / LRU / degraded / cache 字段 / 文件名严格匹配）
      **逐行保留**，与旧端行为一致。

数据源与口径
    · 复盘 MD：`ctx.text_dir("daily_review")`（= `夏天-activity/每日复盘/`），
      文件名严格 `日复盘 YYYY-MM-DD.md`（不递归、不允许路径穿越）。
    · 主线历史：本版不读（见上）。
    · 渲染回调由调用方注入（backend 传 `render.render_review`），本模块不反向 import，
      保证可单测。

上下游
    上游：`app/plugins/review/backend.py` 的 `/api/review.md`（薄壳，调 pick_latest + render_cached）。
    下游：旧前端 `engine/web/index.html` 复盘 Tab（消费 html / charts / cache / degraded 字段）。
    单测：`app/plugins/review/tests/test_review_source.py`。
"""
from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

# 唯一扫描目录：**本版不再有模块级缺省值**，一律由调用方传入（生产 = ctx.text_dir）。
# 仍保留常量名 REVIEW_DIR 供测试 monkeypatch/读取（值为 None 表示"必须显式传目录"）。
REVIEW_DIR: str | None = None
# 主线历史目录：**本版置空** —— v6 默认路径不出泳道/赛道图，mainline_version() 恒 (0, 0)。
# 保留常量是为了缓存键分量与函数签名不变（未来恢复图表只需填回目录）。
MAINLINE_DIR: str | None = None
# 复盘 MD 同目录的图表目录：v6 与纸面渲染都会把 MD 引用的 charts/*.svg 内联进 HTML，
# 故它的指纹必须进缓存键（见 charts_version）。
CHARTS_DIRNAME = "charts"

# 文件名严格匹配：日复盘 YYYY-MM-DD.md（前缀固定 + 真实日期，年月日由 strptime 二次校验）
FNAME_RE = re.compile(r"^日复盘 (\d{4})-(\d{2})-(\d{2})\.md$")

SCAN_TTL_S = 30.0          # listdir 节流（前端 60s 轮询 → 每 30s 至多扫一次磁盘）
CACHE_MAX = 4              # LRU 条数（当前 + 近几日；主题切换不再因 theme 落入 key 而膨胀）
MAINLINE_TTL_S = 30.0      # mainline 版本指纹节流（与 SCAN_TTL_S 同步，避免每轮 glob 全部文件）
RENDERER_VERSION = "rv3"   # 渲染逻辑变更时手动 bump，防陈旧缓存（口径唯一源）
#   rv2（2026-09-13）：面板优先出主题化图（var(--rv-*)，随主题可读），剥离 MD 静态内联图；
#                     旧 rv1 缓存里存的是静态 hex 图（深色主题不可读），必须失效。
#   rv3（2026-09-17）：新增纸面模式（/api/review.md?paper=1 → report-builder v2 完整管线）：
#                     报头/速览带/十节节奏/涨跌芯片/暖白纸面，视觉对齐独立 HTML（方案A 纸面保真）。
#                     纸面缓存维度 theme="paper"；旧缓存不带 masthead/css/paper 字段，必须失效。
#                     2026-10-23 迁移 panel-v2：paper=1 暂时 501（前端自动回退 v6），
#                     故本版 rv3 只承载 v6 路径；版本号不降，避免与旧端缓存语义错位。


@dataclass(frozen=True)
class ReviewPick:
    """一个被选中的复盘 MD：绝对路径 + 交易日 + 指纹。"""
    path: str
    date: str          # YYYY-MM-DD（来自文件名）
    mtime_ns: int
    size: int


# ---- 扫描结果缓存（节流 listdir；锁保护多线程 ASGI worker）----
# 2026-10-23 迁移补充：旧端 REVIEW_DIR 是模块常量，故 TTL 缓存可跨调用复用；
# 本版目录由调用方传入，因此缓存里**同时记住扫描的目录**（sig），目录一变立即重扫，
# 否则 tmp 目录/多目录场景会互相污染（测试与未来多工作区都能踩到）。
_scan_lock = threading.Lock()
_scan_cache = {"at": 0.0, "picks": None, "sig": None}       # picks: list[ReviewPick] | None
_mainline_lock = threading.Lock()
_mainline_cache = {"at": 0.0, "ver": None}                   # ver: (max_mtime_ns, count)


def _now() -> float:
    import time
    return time.monotonic()


def _parse_date(name: str):
    """文件名 → 'YYYY-MM-DD'；不匹配/非法日期（2026-13-01、2026-02-30）→ None。"""
    m = FNAME_RE.match(name)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        datetime.strptime(f"{y}-{mo}-{d}", "%Y-%m-%d")   # 校验真实日期（含闰年/月界）
    except ValueError:
        return None
    return f"{y}-{mo}-{d}"


def _resolve_dir(review_dir=None) -> str:
    """把调用方给的目录（Path / str / None）统一成 str；None → 读模块常量 REVIEW_DIR。

    常量与参数都可能是 None（本版没有缺省生产目录）→ 返回空目录：
    `scan_candidates` 的 os.scandir 会 OSError → []，与旧端「目录不存在 → []」同口径；
    生产仍必须显式传 `ctx.text_dir("daily_review")`。
    """
    raw = review_dir if review_dir is not None else REVIEW_DIR
    if raw is None:
        return ""
    return os.fspath(raw)


def scan_candidates(force: bool = False, review_dir=None):
    """扫描复盘目录下全部合法复盘 MD（节流 30s、目录变化立即重扫）。

    返回 list[ReviewPick]（顺序=scandir 顺序，调用方按需排序）。
    · 文件名严格匹配且日期真实可解析才收（非法名跳过，不报错）；
    · 目录不存在 / 权限不足 → 返回 []（不抛穿，前端显示缺失态）。
    """
    directory = _resolve_dir(review_dir)
    now = _now()
    with _scan_lock:
        if (not force and _scan_cache["picks"] is not None
                and _scan_cache["sig"] == directory
                and now - _scan_cache["at"] < SCAN_TTL_S):
            return list(_scan_cache["picks"])
        picks = []
        try:
            with os.scandir(directory) as it:
                for e in it:
                    if not e.is_file():
                        continue
                    d = _parse_date(e.name)
                    if d is None:
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    picks.append(ReviewPick(path=e.path, date=d,
                                            mtime_ns=st.st_mtime_ns, size=st.st_size))
        except OSError:
            picks = []
        _scan_cache.update(at=now, picks=picks, sig=directory)
        return list(picks)


def pick_latest(date: str | None = None, review_dir=None):
    """选最新复盘：未指定 date → 交易日最大、同日 mtime 最大；指定 → 精确定位该日（同日取最新）。

    `review_dir`：复盘 MD 目录（生产传 `ctx.text_dir("daily_review")`）；
    不传则回落到模块常量 REVIEW_DIR（测试 monkeypatch 用）。

    返回 ReviewPick | None（无可用 MD → None，调用方返 404，不编造）。
    """
    picks = scan_candidates(review_dir=review_dir)
    if date:
        same = [p for p in picks if p.date == date]
        return max(same, key=lambda p: p.mtime_ns) if same else None
    if not picks:
        return None
    # 主键=交易日(date 字典序=时间序)、副键=mtime_ns
    return max(picks, key=lambda p: (p.date, p.mtime_ns))


def mainline_version():
    """主线历史版本指纹：(max_mtime_ns, count)。节流 30s；目录缺失 → (0, 0)。

    **本版 MAINLINE_DIR 置空**（v6 默认不出泳道/赛道图）→ 恒返回 (0, 0)，
    缓存键里该分量恒定，等价于"不参与失效"；口径与旧端"无 mainline 目录"分支一致。
    只做 stat 不解析（不读内容，避免每轮 IO 放大）。
    """
    if not MAINLINE_DIR:
        return (0, 0)
    now = _now()
    with _mainline_lock:
        if _mainline_cache["ver"] is not None and now - _mainline_cache["at"] < MAINLINE_TTL_S:
            return _mainline_cache["ver"]
        mx, n = 0, 0
        try:
            with os.scandir(MAINLINE_DIR) as it:
                for e in it:
                    if not e.is_file():
                        continue
                    name = e.name
                    # 只认正本 mainline_history_*.json（排除 .bak-* 备份）
                    if not (name.startswith("mainline_history_") and name.endswith(".json")):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    mx = max(mx, st.st_mtime_ns)
                    n += 1
        except OSError:
            mx, n = 0, 0
        _mainline_cache.update(at=now, ver=(mx, n))
        return (mx, n)


def charts_version(md_path: str | None) -> tuple:
    """MD 同目录 `charts/*.svg` 指纹：((文件名, mtime_ns, 字节数), …) 升序；目录缺失 → ()。

    为什么必须进缓存键：`render._inline_svg` 与 `paper._inline_svg_paper` 都会把
    MD 引用的 `charts/*.svg` **读进 HTML**。图表可能被 `fupan_chart.py` 单独重生成，
    而 MD 的 mtime/size 不变；若指纹只看 MD，就会出现「图已更新、页面还是旧图」的
    陈旧缓存。旧端靠 mainline_version 间接兜住；本版 mainline 置空，改为直接盯真正的
    渲染输入（SVG 文件本身）。每次直接 stat，不做 TTL——缓存正确性优先于这点 IO。
    """
    if not md_path:
        return ()
    charts_dir = os.path.join(os.path.dirname(os.path.abspath(os.fspath(md_path))),
                              CHARTS_DIRNAME)
    items = []
    try:
        with os.scandir(charts_dir) as it:
            for e in it:
                if not e.is_file() or not e.name.lower().endswith(".svg"):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                items.append((e.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    items.sort()
    return tuple(items)


def asset_files_version(paths) -> tuple:
    """外部渲染资产指纹：((绝对路径, mtime_ns, 字节数), …)；缺失 → (路径, -1, -1)。

    用于纸面 `report.css` 这类「渲染时读、但不属于 MD 目录」的输入：
    文件变了缓存必须失效，否则页面继续用旧样式；缺失也占位，文件新出现时键同样会变。
    """
    out = []
    for p in paths or ():
        path = os.path.abspath(os.fspath(p))
        try:
            st = os.stat(path)
            out.append((path, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((path, -1, -1))
    return tuple(out)


def cache_key(pick: ReviewPick, theme: str, asset_paths=()) -> tuple:
    """缓存键（口径唯一源）：任一分量变化即失效。

    abs_path + mtime_ns + size  —— MD 本体证据
    mainline_version            —— 主线历史版本（本版恒 (0,0)，保留分量）
    charts_version              —— MD 同目录 charts/*.svg 指纹（图单独更新也要失效）
    asset_files_version         —— 外部渲染资产（纸面 report.css；缺失也占位）
    theme                       —— 主题（图色走 CSS 变量，理论零重渲染；保留分量防未来需要）
    RENDERER_VERSION            —— 渲染逻辑版本
    """
    return (pick.path, pick.mtime_ns, pick.size, mainline_version(),
            charts_version(pick.path), asset_files_version(asset_paths),
            theme or "", RENDERER_VERSION)


# ---- 渲染结果 LRU（key → result）；_LAST_GOOD 保留每个路径上一次成功内容（降级用）----
_cache_lock = threading.Lock()
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_last_good: dict = {}


def render_cached(pick: ReviewPick, theme: str, renderer, asset_paths=()):
    """渲染或命中缓存。

    renderer(pick) -> dict  由调用方注入，须返回 {html, title, charts, ...}（自带 md2html 兜底）。
    asset_paths             渲染时读取的外部资产（纸面传 `paper.REPORT_CSS_PATH`）——
                            与 MD / charts 同口径进缓存键，避免「样式变了、页面不变」。
    命中 → 直接返回缓存副本（不调 renderer，零 markdown/零图表）。
    未命中 → 调 renderer：
        · 成功 → 存 LRU + _LAST_GOOD，附 cache="miss"；
        · 异常（含 SystemExit，fupan_chart 用它报"无赛道数据"）→ 若有上次成功内容，
          返回其副本并标 degraded=True/error；否则返回 html=None 的降级结果。
    结果 dict 始终含：cache(hit|miss)、degraded(bool)、error(str|None)。
    """
    key = cache_key(pick, theme, asset_paths)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            out = dict(hit)
            out["cache"] = "hit"
            return out

    try:
        result = renderer(pick)
        if not isinstance(result, dict):
            result = {"html": result}
        out = dict(result)
        out["degraded"] = False
        out["error"] = None
        with _cache_lock:
            _cache[key] = dict(out)
            _cache.move_to_end(key)
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
            _last_good[pick.path] = dict(out)
        out["cache"] = "miss"
        return out
    except (Exception, SystemExit) as e:  # noqa: BLE001 - SystemExit：fupan_chart 无数据时 raise
        with _cache_lock:
            good = _last_good.get(pick.path)
        if good is not None:
            out = dict(good)
            out["cache"] = "miss"
            out["degraded"] = True
            out["error"] = f"渲染失败，已回退上一份成功内容：{e}"
            return out
        return {"html": None, "title": None, "charts": None,
                "cache": "miss", "degraded": True,
                "error": f"渲染失败且无历史成功内容：{e}"}


def reset_scan():
    """只清扫描节流缓存（测试模拟 SCAN_TTL_S 过期；不影响渲染 LRU）。"""
    with _scan_lock:
        _scan_cache.update(at=0.0, picks=None, sig=None)


def reset_mainline():
    """只清主线指纹节流缓存（测试模拟 MAINLINE_TTL_S 过期）。"""
    with _mainline_lock:
        _mainline_cache.update(at=0.0, ver=None)


def reset_caches():
    """清空扫描/指纹/渲染缓存（测试与强制刷新用）。"""
    reset_scan()
    reset_mainline()
    with _cache_lock:
        _cache.clear()
        _last_good.clear()
