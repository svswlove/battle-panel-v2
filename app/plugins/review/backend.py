# -*- coding: utf-8 -*-
"""review 插件：复盘 Tab（旧 8800 `/api/review.md` 的迁移实现，任务书 4.5）。

**本次范围**
- 默认 v6 路径（任务书 docs/task_review_md_4_5.md §0/§2）：`/api/review.md?date=&theme=`
  → 扫 MD → 渲染正文 HTML + 指纹缓存；
- `paper=1` 纸面保真完整管线（任务书 docs/task_paper_4_8.md）：`?paper=1` 走
  `paper.render_paper`（report-builder v2 完整管线 + 作用域 CSS），与旧 8800 同构；
  4.5 时的 501 占位已替换为真实实现。

**为什么 root 挂载**：旧前端（`engine/web/index.html` 复盘 Tab）直接请求根路径
`/api/review.md`。迁移策略是「复用旧前端、换数据源」，故 manifest 里
`backend.mount = "root"`，路径与旧端**完全同名**。
（同时 `compat_misc` 里那条 `/review.md` 骨架路由已删除——两个 root 插件抢同一路径，
`main.py` 启动就报冲突。）

**取数与口径**
- MD 目录：`ctx.text_dir("daily_review")`（4.5 新增的只读文本目录白名单，
  = `夏天-activity/每日复盘/`）。插件只拿 Path 自己读，**不写任何文件**。
- 发现/指纹/缓存/降级：`source.py`（从旧仓同口径模块移植，旧副本 2026-11-07 已退役删除；算法逐行保留）。
- 渲染：`render.py`（从旧 `server.py::_md_to_body_html` + `_inline_svg` 移植）+
  同目录 `md2html.py`（旧 report-builder 版**逐字复制**，冻结接口不改）；
  纸面分支渲染：`paper.py`（旧 `server.py` 1346-1765 纸面段逐行移植，CSS 唯一源固定
  `~/.hanako/skills/report-builder/report.css`，不复制进插件）。
- 图表：v6 默认不出泳道/赛道图 → `charts = {swimlane: null, track: null}`
  （旧端默认同样是这个分支：`OH_MAINLINE_ALLOW != 1` → 双 null）。

**返回键必须与旧端一致**（`docs/misc_samples/keys_expected.json` 的 `/api/review.md`）：
v6 分支 = `{date, file, title, html, mtime_ns, charts{swimlane,track}, degraded, error, cache, theme}`；
纸面分支 = 上述 10 键 ∪ `{paper, masthead, css, footer}`（共 14 键，`paper: true`、`theme: "paper"`）。

**本插件严禁**（契约 §4/§8，`cutover_gate.py` 静态扫描）：
`sqlite3.connect` / `from common` / `from core` / `xtquant` / `from eltdx` / 任何写文件代码。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

# 复盘图表装配器在 core（它直读 sqlite/生产 panel，插件契约不允许直连）
from app.core import review_chart as review_chart_mod
# 复盘六格读取器在 core（按日文件族读 data/panel/review_six_cells_*；插件只做 HTTP 映射）
from app.core import review_six as review_six_mod

# 插件模块加载方式说明（与 concept / paper 插件同款处理）：
# 生产时 core 用 `spec_from_file_location("_panel_v2_plugin_review_backend", ...)` 加载，
# 那种情况下本模块**没有父包**，相对导入不存在；测试里走 `app.plugins.review.backend` 包导入。
# 因此三级兜底：相对导入 → 包绝对导入 → 按文件路径加载兄弟模块。
try:                                        # 1) 正常包导入（pytest / 其他模块 import）
    from . import paper, render, source  # type: ignore
except ImportError:                         # pragma: no cover - 由 core 按文件加载时走这里
    try:                                    # 2) 项目根在 sys.path 时的绝对导入
        from app.plugins.review import paper, render, source  # type: ignore
    except ImportError:                     # 3) 兜底：按路径加载同目录兄弟模块
        import importlib.util as _ilu

        _here = Path(__file__).resolve().parent

        def _sibling(mod_name: str):
            """按文件路径加载同目录模块（core 以 module 形式加载 backend 时用）。"""
            _spec = _ilu.spec_from_file_location(
                "_panel_v2_review_%s" % mod_name, _here / ("%s.py" % mod_name))
            if _spec is None or _spec.loader is None:
                raise ImportError("无法加载 review 插件的兄弟模块: %s" % mod_name)
            _mod = _ilu.module_from_spec(_spec)
            sys.modules[_spec.name] = _mod
            _spec.loader.exec_module(_mod)
            return _mod

        source = _sibling("source")   # type: ignore
        render = _sibling("render")   # type: ignore
        paper = _sibling("paper")     # type: ignore（paper 依赖 render，已先加载）

PLUGIN_ID = "review"

TEXT_DIR_NAME = "daily_review"    # manifest.data.text 里声明的只读文本目录名

# 旧端 `api_review_md` 的返回键（`docs/misc_samples/keys_expected.json` 逐字，v6 分支）；
# 纸面分支 = 这 10 键 ∪ {paper, masthead, css, footer}（共 14 键，见模块头）
REVIEW_MD_KEYS = ("date", "file", "title", "html", "mtime_ns", "charts",
                  "degraded", "error", "cache", "theme")
# 纸面分支额外四键（旧端 paper=1 的载荷口径）
REVIEW_MD_PAPER_KEYS = ("paper", "masthead", "css", "footer")

# 空 charts（v6 不出图）；用新 dict 而不是共享常量，避免调用方改到别处
def _empty_charts() -> Dict[str, Any]:
    return {"swimlane": None, "track": None}


def _review_dir(ctx: Any) -> Path:
    """取复盘 MD 目录（`ctx.text_dir("daily_review")`），并把不可用情况翻成 HTTP 语义。

    · 未在 manifest / 未在白名单 → PermissionError → 500（配置错，必须暴露，不能假装没数据）
    · 登记了但目录不存在 → FileNotFoundError → 404（同「没有复盘 MD」，前端显示缺失态）
    """
    try:
        return ctx.text_dir(TEXT_DIR_NAME)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404,
                            detail="复盘 MD 目录不存在（%s）" % TEXT_DIR_NAME) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=500,
                            detail="复盘 MD 目录未授权：%s" % exc) from exc


def build_review_md(ctx: Any, date: Optional[str] = None,
                    theme: Optional[str] = None) -> Dict[str, Any]:
    """复盘 markdown → HTML（旧 8800 `api_review_md` 的 v6 分支）。

    链路：`source.pick_latest`（扫目录、选最新/指定交易日）
          → `source.render_cached`（指纹 LRU + 降级）
          → `render.render_review`（md2html 渲染 + SVG 内联）。

    · 无 MD / 指定日期不存在 → 404（**精确日是精确定位，不回溯到更早**，旧端口径）；
    · 渲染异常 → `render_cached` 降级：有历史成功内容就回退并标 degraded，没有就
      返回 html=None + degraded=True；后者前端会显示「渲染降级」而非「文件未找到」。
    """
    review_dir = _review_dir(ctx)
    pick = source.pick_latest(date, review_dir)
    if pick is None:
        raise HTTPException(status_code=404,
                            detail="复盘 MD 缺失（%s）" % (date or "最新交易日"))
    res = source.render_cached(pick, theme or "", render.render_review)
    return {
        "date": pick.date,
        "file": os.path.basename(pick.path),
        "title": res.get("title"),
        "html": res.get("html"),
        "mtime_ns": pick.mtime_ns,
        "charts": res.get("charts") or _empty_charts(),
        "degraded": bool(res.get("degraded")),
        "error": res.get("error"),
        "cache": res.get("cache"),
        "theme": theme or "",
    }


def build_review_md_paper(ctx: Any, date: Optional[str] = None) -> Dict[str, Any]:
    """复盘 markdown → 纸面载荷（旧 8800 `api_review_md(paper=1)` 分支）。

    链路：`source.pick_latest` → `source.render_cached(pick, "paper", paper.render_paper)`
          → `paper.render_paper`（md2html 完整管线 + report.css 作用域派生）。

    · 缓存维度固定 `theme="paper"`：纸面配色不随面板主题变，与 v6（真主题名）绝不互污染；
    · 渲染异常由 `render_cached` 统一降级（保留上一份成功内容 + degraded=True），**不 500**；
    · 防御层（旧端 server.py:1907-1912 逐字保留）：`css is None` 且未 degraded 属「静默伪成功」，
      按渲染失败处理；
    · 有 MD 但从未成功渲染出正文 → 502（旧端口径：有文件但渲染不出，前端显示降级态）。
    """
    review_dir = _review_dir(ctx)
    pick = source.pick_latest(date, review_dir)
    if pick is None:
        raise HTTPException(status_code=404,
                            detail="复盘 MD 缺失（%s）" % (date or "最新交易日"))
    res = source.render_cached(pick, "paper", paper.render_paper,
                               asset_paths=(paper.REPORT_CSS_PATH,))
    if res.get("css") is None and not res.get("degraded"):
        # 防御层：纸面渲染器异常但被缓存"静默成功"了（字段缺失）→ 按渲染失败处理
        res = dict(res, html=None, degraded=True,
                   error=res.get("error") or "纸面渲染结果缺 css 字段")
    if res.get("html") is None and res.get("degraded"):
        # 从无成功渲染 → 502 语义（有 MD 但渲染不出），前端显示缺失/降级态
        raise HTTPException(status_code=502, detail=res.get("error") or "复盘渲染失败")
    return {
        "date": pick.date,
        "file": os.path.basename(pick.path),
        "title": res.get("title"),
        "html": res.get("html"),
        "mtime_ns": pick.mtime_ns,
        "charts": res.get("charts") or _empty_charts(),
        "degraded": bool(res.get("degraded")),
        "error": res.get("error"),
        "cache": res.get("cache"),
        "theme": "paper",
        "paper": bool(res.get("paper")),
        "masthead": res.get("masthead"),
        "css": res.get("css"),
        "footer": res.get("footer"),
    }


# ---------------------------------------------------------------- 路由
def register(ctx: Any) -> APIRouter:
    """root 挂载：注册 `/review.md`，完整路径 = `/api/review.md`（与旧端同名）。"""
    router = APIRouter()

    @router.get("/review.md")
    def review_md(date: Optional[str] = Query(default=None),
                  theme: Optional[str] = Query(default=None),
                  paper: int = Query(default=0)) -> Dict[str, Any]:
        """复盘 Tab 数据源（旧 8800 `/api/review.md` 同构）。

        · 无参数 → 最新交易日复盘；`?date=YYYY-MM-DD` → 精确定位该日（不回溯）；
        · `?theme=` 只回显并参与缓存键（正文样式由前端 #panel-review 提供）；
        · `?paper=1` → 纸面保真完整管线（report-builder v2：报头/速览带/暖白纸面/作用域 CSS），
          缓存维度固定 theme="paper"；降级走同一 `render_cached`（不 500）。
        · 返回 v6 10 键；paper=1 额外含 {paper, masthead, css, footer}（共 14 键）。
        """
        try:
            if paper:
                return build_review_md_paper(ctx, date)
            return build_review_md(ctx, date, theme)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 - 兜底：不 500 拖垮面板其他模块
            raise HTTPException(status_code=500, detail=f"复盘渲染失败：{e}") from e

    @router.get("/review_chart.json")
    def review_chart_json(date: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        """复盘图表模板数据源（旧 8800 `/api/review_chart.json` 同构）。

        装配器在 core（`app/core/review_chart.py`，直读 `ths_index.db` / `track_pool.db`
        与多份生产 panel JSON；插件契约禁止直连，所以放 core）。
        无 MD / 指定日期不存在 → 404；装配失败 → 502（旧前端回退 paper → v6）。
        """
        review_dir = _review_dir(ctx)
        pick = source.pick_latest(date, review_dir)
        if pick is None:
            raise HTTPException(status_code=404,
                                detail="复盘 MD 缺失（%s）" % (date or "最新交易日"))
        try:
            return review_chart_mod.build_cached(pick)
        except review_chart_mod.ReviewChartError as e:
            raise HTTPException(status_code=502, detail=f"复盘图表装配失败：{e}") from e
        except Exception as e:  # noqa: BLE001 - 不 500 拖垮面板
            raise HTTPException(status_code=502, detail=f"复盘图表装配异常：{e}") from e

    @router.get("/review_six.json")
    def review_six_json(date: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        """复盘六格产物（第四个视图「六格」的数据源；同一份 JSON 也供板块页顶部消费）。

        · 无参 → `_latest`；`?date=YYYY-MM-DD` → 该日精确读取（不回溯）；
        · 缺该日 → 404（附已有日期）；源日期不自洽 / 产物损坏 → 500；日期非法 → 400；
        · 只读：读取器在 `app/core/review_six.py`，本插件不写盘、不连库。
        """
        try:
            return review_six_mod.load(date)
        except review_six_mod.ReviewSixError as exc:
            msg = str(exc)
            if "没有" in msg and "产物" in msg or "产物不存在" in msg:
                raise HTTPException(status_code=404, detail=msg) from exc
            raise HTTPException(status_code=500, detail=msg) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"date 参数不合法：{exc}") from exc

    return router
