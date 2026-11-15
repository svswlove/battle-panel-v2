# -*- coding: utf-8 -*-
"""score 插件后端：个股六维打分产物托管（只读）。

## 分工（与 valuation 插件同一模式，core 零改动）

| 事 | 谁做 | 位置 |
|---|---|---|
| 打分（取数 + 六维归一化 + 权重合成 + 排名 + 评级） | 外部脚本 `夏天-activity/工具/stock_score_engine.py` | 工具目录 |
| 产物托管（只读，原样返回 JSON） | **本插件** `/meta` 与 `/rows` | 这里 |
| 展示 | `frontend.js` 可排序榜单 | 本插件 |

**为什么托管放插件而不是 core**：主人要求「插件化、便于拆卸」。拆卸 = 删本目录 + 删
`shell.js` NAV 一行，core 一行不动（`app/core/main.py`、`app/core/paths.py` 里不含 score 字样）。

产物路径在插件内推导（`paths.OUT_DIR / "score"`），不往 core 的 paths.py 加常量——那是 core 的文件。

**不复制产物进插件目录**：复制一份就是双源，脚本一重跑副本就旧了。插件只按字节返回。

**不解析、不重算**：榜单的排序/筛选在前端内存里做；后端只提供原始 rows。

## `/map`（2026-10-29 补）：给别的插件用的扁平映射

sector / selection / watchlist 三个页面要在自己的列表里显示同一份打分。它们**不重算、
不各自实现匹配**，只拉一次 `/map`（`{code6: [score, grade, rank]}`）在本地按代码查表。
口径唯一源仍在这里（产物原样，只做投影，不重算）。

字段顺序用数组而不是对象，是为了控体积：5577 只实测约 138KB；改成对象（带键名）会翻到
200KB 以上。字段名在响应的 `fields` 里显式给出，前端不靠位置猜。

**拆分粒度**：本插件仍是独立插件，`/map` 只是新增一个只读投影端点；三个消费方各自
catch 404 后显示「无打分数据」，删掉本目录它们照常工作。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import paths

#: 产物目录名（`stock_score_engine.py --out` 的默认值，相对 paths.OUT_DIR）
PRODUCT_DIRNAME = "score"
LATEST_FILE = "latest.json"

#: 打分是日频（收盘后一次），一天一跑足够；留 20 小时，允许当日 22 点生成、次日下午再看仍算新鲜
STALE_AFTER_S = 20 * 3600

#: 产物缺失时的提示（唯一更新方式）
REFRESH_HINT = "先跑 夏天-activity\\工具\\stock_score_engine.py（约 10 秒，会联网拉估值快照）"


def product_dir() -> Path:
    """产物目录。**调用时求值**，不缓存模块级常量——测试可 monkeypatch `paths.OUT_DIR`。"""
    return Path(paths.OUT_DIR) / PRODUCT_DIRNAME


def latest_path() -> Path:
    return product_dir() / LATEST_FILE


def _read_latest() -> Optional[Dict[str, Any]]:
    path = latest_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def build_meta() -> Dict[str, Any]:
    """产物元信息 + 覆盖率摘要。文件不在不抛错——面板显示「还没生成」，不红屏。"""
    path = latest_path()
    if not path.is_file():
        return {
            "ok": True,
            "available": False,
            "file": LATEST_FILE,
            "dir": str(product_dir()),
            "reason": REFRESH_HINT,
        }
    payload = _read_latest()
    if payload is None:
        return {
            "ok": True,
            "available": False,
            "file": LATEST_FILE,
            "dir": str(product_dir()),
            "reason": "产物存在但解析失败，重跑 stock_score_engine.py",
        }

    st = path.stat()
    age = max(0.0, datetime.now().timestamp() - st.st_mtime)
    meta = payload.get("meta") or {}
    return {
        "ok": True,
        "available": True,
        "file": LATEST_FILE,
        "dir": str(product_dir()),
        "mtime": int(st.st_mtime),
        "mtime_text": _fmt_ts(st.st_mtime),
        "size": int(st.st_size),
        "age_seconds": int(age),
        "stale": age > STALE_AFTER_S,
        "asof": meta.get("asof"),
        "generated_at": meta.get("generated_at"),
        "n_rows": len(payload.get("rows") or []),
        "coverage": meta.get("coverage"),
        "distribution": meta.get("distribution"),
        "grades": meta.get("grades"),
        "grade_cutoffs": meta.get("grade_cutoffs"),
        "grade_labels": meta.get("grade_labels"),
        "weights": meta.get("weights"),
        "sources": meta.get("sources"),
        "caliber": meta.get("caliber"),
    }


#: `/map` 字段顺序（响应里显式回传，前端不靠位置猜）
MAP_FIELDS = ("score", "grade", "rank")

#: 扁平映射的进程内缓存：键 = (路径, mtime_ns, size)。产物一天一换，重复请求不再解析 3.6MB。
_MAP_CACHE: Dict[str, Any] = {"key": None, "payload": None}


def _map_cache_key(path: Path) -> tuple:
    st = path.stat()
    return (str(path), int(st.st_mtime_ns), int(st.st_size))


def build_map() -> Dict[str, Any]:
    """打分扁映射 `{code6: [score, grade, rank]}`（只投影，不重算）。

    产物不存在 / 解析失败 → `available=False` + `reason`（200，不抛、不 503）：
    消费方是别的插件的列表页，缺数据只该让那一列显示「—」，不该让整页报错。
    """
    path = latest_path()
    if not path.is_file():
        return {"ok": True, "available": False, "fields": list(MAP_FIELDS),
                "map": {}, "n": 0, "reason": REFRESH_HINT}

    key = _map_cache_key(path)
    if _MAP_CACHE["key"] == key and _MAP_CACHE["payload"] is not None:
        return _MAP_CACHE["payload"]

    payload = _read_latest()
    if payload is None:
        return {"ok": True, "available": False, "fields": list(MAP_FIELDS),
                "map": {}, "n": 0, "reason": "产物存在但解析失败，重跑 stock_score_engine.py"}

    flat: Dict[str, list] = {}
    for row in payload.get("rows") or []:
        code = row.get("code6")
        if not code:
            continue
        score = row.get("score")
        flat[str(code)] = [
            None if score is None else round(float(score), 1),
            row.get("grade"),
            row.get("rank"),
        ]

    st = path.stat()
    age = max(0.0, datetime.now().timestamp() - st.st_mtime)
    meta = payload.get("meta") or {}
    out = {
        "ok": True,
        "available": True,
        "fields": list(MAP_FIELDS),
        "asof": meta.get("asof"),
        "generated_at": meta.get("generated_at"),
        "mtime": int(st.st_mtime),
        "stale": age > STALE_AFTER_S,
        "n": len(flat),
        "map": flat,
    }
    _MAP_CACHE["key"] = key
    _MAP_CACHE["payload"] = out
    return out


def register(ctx) -> APIRouter:
    router = APIRouter()

    @router.get("/meta")
    def meta() -> Dict[str, Any]:
        """产物新鲜度 + 覆盖率摘要（纯 stat + 一次 JSON 解析，无外部依赖）。"""
        return build_meta()

    @router.get("/rows")
    def rows():
        """榜单原始行（code6/name/总分/六维/子项百分位/排名/板块/参与维度数）。

        产物不存在 → 503 + 重跑提示，不 500。
        """
        payload = _read_latest()
        if payload is None:
            return JSONResponse(
                {"error": "score product missing", "path": str(latest_path()), "hint": REFRESH_HINT},
                status_code=503,
            )
        return payload

    @router.get("/map")
    def map_():
        """扁平映射 `{code6: [score, grade, rank]}`（+ `asof` / `stale`），只投影不重算。

        供 sector / selection / watchlist 三个列表页一次拉齐、本地按代码查表。
        体积目标 < 200KB（5577 只实测约 138KB）；缺产物 → 200 + `available=false`。
        """
        return build_map()

    return router
