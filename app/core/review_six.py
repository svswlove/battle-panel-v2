# -*- coding: utf-8 -*-
"""复盘六格 · 产物读取器（只读，core 侧）。

职责
    给 8810 提供 `data/panel/review_six_cells_{YYYYMMDD}.json` 的按日读取：
    列可用日期、按日期（或最新）加载、校验「源日期 == 目标日」。
    本模块不做任何业务计算 —— 六格口径全部在生产者 `tools/review_six_build.py` 里算完；
    这里只负责「哪份能读、读出来是否自洽」。

为什么放 core 不放进插件
    `review_six_cells_*` 是按日期的文件族，超出 `paths.READABLE_PANEL`（单文件白名单）的表达范围，
    插件拿不到按日读取的通道。同类先例是 `app/core/review_chart.py`（同样直读 panel 产物）。
    插件 `review_six` 只做 HTTP 参数校验与错误映射，业务口径不在这里。

边界
    · 只读：不写任何文件、不建目录、不触发表；
    · 只认 `review_six_cells_` 前缀 + 8 位日期文件名，其余文件一律不列；
    · 源日期 ≠ 目标日 → `ReviewSixError`（不拿旧值顶替，任务书硬约束）。

上下游
    上游：`tools/review_six_build.py`（生产者，盘后手动/日跑）。
    下游：`app/plugins/review_six/backend.py` → `GET /api/p/review_six/data`。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import paths

DATED_RE = re.compile(r"^review_six_cells_(\d{8})\.json$")
DATED_FMT = "review_six_cells_%s.json"
LATEST_NAME = "review_six_cells_latest.json"


class ReviewSixError(Exception):
    """产物缺失 / 日期非法 / 源日期不自洽。"""


def panel_dir() -> Path:
    return paths.PANEL_DIR


def _norm_date(raw: Optional[str]) -> Optional[str]:
    """'2026-11-02' / '20261102' / '2026/11/02' → '2026-11-02'；空 → None；非法 → ValueError。"""
    s = str(raw or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})$", s)
    if not m:
        raise ValueError(f"日期格式不认识：{raw!r}（要 YYYY-MM-DD 或 YYYYMMDD）")
    y, mo, d = m.groups()
    try:
        datetime(int(y), int(mo), int(d))
    except ValueError as exc:
        raise ValueError(f"日期不存在：{raw!r}") from exc
    return f"{y}-{mo}-{d}"


def available_dates() -> List[str]:
    """已落盘的产物日期，升序（YYYY-MM-DD）。"""
    out: List[str] = []
    for p in panel_dir().glob("review_six_cells_*.json"):
        m = DATED_RE.match(p.name)
        if not m:
            continue
        d8 = m.group(1)
        out.append(f"{d8[:4]}-{d8[4:6]}-{d8[6:]}")
    return sorted(out)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError as exc:
        raise ReviewSixError(f"产物不存在：{path.name}") from exc
    except (OSError, ValueError) as exc:
        raise ReviewSixError(f"产物读取失败：{path.name}（{exc}）") from exc
    if not isinstance(doc, dict):
        raise ReviewSixError(f"产物顶层必须是对象：{path.name}")
    return doc


def _check_sources(doc: Dict[str, Any]) -> None:
    """源日期一致性：meta.target_date 必须存在，且 sources 里每个 date 都等于它。

    宁可 500 也不拿旧值顶替 —— 任务书：「源日期 ≠ 目标日必须报错，不许拿旧值顶替」。
    """
    meta = doc.get("meta") or {}
    target = _norm_date(meta.get("target_date"))
    if not target:
        raise ReviewSixError("产物缺 meta.target_date")
    sources = meta.get("sources") or {}
    if not isinstance(sources, dict) or not sources:
        raise ReviewSixError("产物缺 meta.sources（源日期台账）")
    bad = []
    for name, item in sources.items():
        if not isinstance(item, dict):
            continue
        raw = item.get("date")
        if raw in (None, ""):
            continue
        try:
            got = _norm_date(str(raw))
        except ValueError:
            bad.append(f"{name}={raw!r}")
            continue
        if got != target:
            bad.append(f"{name}={raw}≠{target}")
    if bad:
        raise ReviewSixError("源日期与目标日不一致：" + "；".join(bad))


def load(date: Optional[str] = None) -> Dict[str, Any]:
    """读指定日期；date 为空读 `_latest`。

    返回产物原文 + 附加 `available_dates`（列表）与 `artifact`（'latest' 或 'YYYY-MM-DD'）。
    缺文件 → ReviewSixError（HTTP 层映射 404）；不自洽 → ReviewSixError（映射 500）。
    """
    dates = available_dates()
    if date:
        norm = _norm_date(date)
        path = panel_dir() / (DATED_FMT % norm.replace("-", ""))
        if not path.is_file():
            raise ReviewSixError(
                f"没有 {norm} 的六格产物（已有：{', '.join(dates) or '无'}）")
        doc = _read_json(path)
        artifact = norm
    else:
        path = panel_dir() / LATEST_NAME
        doc = _read_json(path)
        artifact = "latest"

    _check_sources(doc)
    target = _norm_date((doc.get("meta") or {}).get("target_date"))
    if date and target != _norm_date(date):
        raise ReviewSixError(f"产物目标日 {target} ≠ 请求日 {date}")

    out = dict(doc)
    out["available_dates"] = dates
    out["artifact"] = artifact
    return out
