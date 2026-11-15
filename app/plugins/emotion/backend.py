# -*- coding: utf-8 -*-
"""emotion 插件后端：情绪连续打分（0–100）。

## 分工

| 事 | 谁做 | 位置 |
|---|---|---|
| 六因子口径 + 滚动分位 + 档位 | `engine.py`（纯函数，零 IO） | 本插件 |
| 取数（sentiment 收盘行 / 涨停池晋级率复算） | `source.py`（只读） | 本插件 |
| HTTP | **本模块**：`/payload`（一次拿全）与 `/meta`（轻量健康/口径） | 本插件 |
| 展示 | `frontend.js` | 本插件 |

**为什么算在插件里、不做「外部脚本产 JSON + 插件只托管」**（valuation / score 的模式）：
那两个插件的产物体量大（232KB HTML / 3.6MB 榜单）、生成要联网几十秒；本插件的输入是
58 行收盘数据 + 50 个小 JSON，纯本地、毫秒级，现场算比「先跑脚本再刷新」更少一步操作，
也避免了「产物过期但页面不知道」。拆卸仍然只是删本目录 + 删 `shell.js` NAV 一行：
core 一行不动（`app/core/main.py`、`app/core/paths.py` 里不含 emotion 字样）。

**只读**：`ctx.db_read("track_pool.db")`（core 只读白名单，`PRAGMA query_only=ON`），
`xiaoshi_cache` 与 `data/panel` 只读文件。本插件不写任何数据源，`manifest.data.write` 为空。
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

try:                                        # 1) 包导入（pytest / 其他模块 import）
    from . import source  # type: ignore
except ImportError:                         # pragma: no cover - core 按文件加载时走这里
    try:                                    # 2) 项目根在 sys.path 时的绝对导入
        from app.plugins.emotion import source  # type: ignore
    except ImportError:                     # 3) 按路径加载同目录兄弟模块
        _here = Path(__file__).resolve().parent
        _spec = _ilu.spec_from_file_location("_panel_v2_emotion_source", _here / "source.py")
        if _spec is None or _spec.loader is None:
            raise ImportError("无法加载 emotion 插件的 source 模块")
        source = _ilu.module_from_spec(_spec)  # type: ignore
        sys.modules[_spec.name] = source
        _spec.loader.exec_module(source)

#: 缺数据时的提示（唯一补数据方式）
REFRESH_HINT = "检查 data/track_pool.db::sentiment 是否有 ts='close' 行；晋级率需要 xiaoshi_cache 涨停池"


def register(ctx) -> APIRouter:
    router = APIRouter()

    def _payload(history_days: int) -> Dict[str, Any]:
        conn = ctx.db_read("track_pool.db")
        try:
            return source.build_payload(conn, history_days=history_days)
        finally:
            conn.close()

    @router.get("/meta")
    def meta() -> Dict[str, Any]:
        """轻量口径/健康：数据日、样本数、因子可用性。前端首屏也可以只打这一个。"""
        payload = _payload(0)
        return {
            "ok": True,
            "asof": payload.get("asof"),
            "n_trade_dates": payload.get("n_trade_dates"),
            "window": payload.get("window"),
            "score": (payload.get("today") or {}).get("score"),
            "band": (payload.get("today") or {}).get("band"),
            "stage": (payload.get("today") or {}).get("stage"),
            "promotion": payload.get("promotion"),
            "checks": payload.get("checks"),
            "caliber": payload.get("caliber"),
            "hint": REFRESH_HINT,
        }

    @router.get("/payload")
    def payload(history_days: int = Query(60, ge=1, le=400)) -> Any:
        """今日明细（六因子逐项）+ 近 N 日序列 + 口径元信息，一次拿全。

        没有收盘行 → 503 + 提示（面板显示「还没数据」，不 500、不编分数）。
        """
        data = _payload(history_days)
        if not data.get("today"):
            return JSONResponse(
                {"error": "emotion: no close rows in sentiment", "hint": REFRESH_HINT},
                status_code=503,
            )
        return data

    return router
