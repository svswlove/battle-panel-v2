# -*- coding: utf-8 -*-
"""valuation 插件后端：PEG 估值仪表盘产物托管（只读）。

## 分工（2026-10-29；托管在插件内，core 零改动）

| 事 | 谁做 | 位置 |
|---|---|---|
| 产物生成（取数 + 算法唯一源） | 外部脚本 `夏天-activity/工具/peg/peg_dashboard.py` | 工具目录 |
| 产物托管（只读，原样返回字节） | **本插件**：`/view` 与 `/files/{name}` | 这里 |
| 产物元信息（新鲜度） | 本插件 `/meta`：`os.stat` 报 mtime/大小 | 这里 |
| 展示 | `frontend.js`：一张铺满主区域的 iframe，src 取 `meta.url` | 本插件 |
| **按需重跑生成脚本** | **core** `POST /api/shell/run-script`（名字→脚本白名单在旧仓 `ops_status.MANUAL_SCRIPTS`） | core |

**为什么「刷新数据」不在这里起进程**（2026-11-04 定）：
插件侧有静态扫描禁令（`tools/cutover_gate.py` 禁子进程、禁外发请求），
本插件的定位是**只读托管**——解析、重算、写盘、起进程一律不做。
所以顶栏按钮由 `frontend.js` 直接打 core 的 `/api/shell/run-script`（与 `/api/shell/restart` 同口径），
本后端只负责「产物现在是什么样」与「新产物出来后前端怎么拿到」。

**为什么托管放插件而不是 core**：8810 已经是插件化结构，主人要求「这部分也要做成插件化，便于拆卸」。
插件自己返回 `FileResponse` 就够，core 不需要认识估值页；拆卸 = 删本目录 + 删 `shell.js` NAV 一行，
core 一行不动（`app/core/main.py`、`app/core/paths.py` 里不含 peg 字样，有测试锁住）。

产物路径在**插件内推导**（`paths.SUMMER_TOOLS_DIR / "peg" / "output"`），不往 core 的 paths.py 加常量：
那是 core 的文件，加一行就等于把估值页焊在 core 上。

**不复制产物进 panel-v2**：复制一份就是双源——脚本一重跑，副本就旧了，而两边都自称权威。
面板不解析产物、不重算任何指标，只按字节返回。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app.core import paths

#: 仪表盘产物文件名（`peg_dashboard.py --out` 的默认值）
DASHBOARD_FILE = "peg_dashboard.html"
#: 允许通过 `/files/{name}` 对外服务的后缀（同目录还放着 peg_study_report.html 等报告）
ALLOWED_SUFFIXES = (".html",)

#: 面板侧访问前缀（插件路由，core 统一挂 `/api/p/<id>`）
VIEW_URL = "/api/p/valuation/view"

#: 超过这个秒数就提示「产物偏旧」。数据是日频（T-1 收盘口径），一天一跑足够；
#: 留 20 小时，允许盘后 20:49 生成、次日下午再看时仍算新鲜。
STALE_AFTER_S = 20 * 3600

#: `FileResponse` 缓存口径与 core 现有 FileResponse 一致：
#: 允许缓存但每次强校验 ETag/Last-Modified，文件变了立即追上，没变只回 304。
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

#: 产物缺失时的提示（唯一更新方式：面板顶栏按钮 / 手动脚本）
REFRESH_HINT = "点页面顶部「刷新数据」重新生成，或跑 夏天-activity/工具/peg/refresh_dashboard.ps1"

#: 前端按钮触发的按需脚本名（白名单键，定义在旧仓 `engine/ops/ops_status.py::MANUAL_SCRIPTS`）
REFRESH_SCRIPT_KEY = "valuation_refresh"


def product_dir() -> Path:
    """产物目录。**调用时求值**，不缓存模块级常量——测试可 monkeypatch `paths.SUMMER_TOOLS_DIR`。"""
    return Path(paths.SUMMER_TOOLS_DIR) / "peg" / "output"


def resolve_product(name: str) -> Optional[Path]:
    """把 `/files/{name}` 的 name 解析成产物目录内的真实文件；越界/后缀不符/不存在一律 None。

    目录穿越防护：`..`、绝对路径、盘符、符号链接跳出去，最终都用 `resolve()` 归一后
    判断「是否仍在产物目录内」——不做字符串前缀比较（`...\\output2` 这类会骗过 startswith）。
    """
    base = product_dir().resolve()
    try:
        target = (base / name).resolve()
    except (OSError, ValueError):
        return None
    if target == base or base not in target.parents:
        return None
    if target.suffix.lower() not in ALLOWED_SUFFIXES:
        return None
    return target if target.is_file() else None


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _file_response(path: Path) -> FileResponse:
    return FileResponse(path, media_type="text/html; charset=utf-8", headers=NO_CACHE)


def build_meta() -> Dict[str, Any]:
    """产物元信息。文件不在不抛错——面板要能显示「还没生成」，而不是红屏。"""
    path = product_dir() / DASHBOARD_FILE
    if not path.is_file():
        return {
            "ok": True,
            "available": False,
            "file": DASHBOARD_FILE,
            "dir": str(product_dir()),
            "url": VIEW_URL,
            "reason": REFRESH_HINT,
        }

    st = path.stat()
    age = max(0.0, datetime.now().timestamp() - st.st_mtime)
    return {
        "ok": True,
        "available": True,
        "file": DASHBOARD_FILE,
        "dir": str(product_dir()),
        # mtime 用整数秒：前端拿它拼 `?v=`，浮点会每次不同、反而每次重下 232KB；
        #   刷新按钮也靠它判断「新产物到了没有」（变了 = 生成完成）
        "mtime": int(st.st_mtime),
        "mtime_text": _fmt_ts(st.st_mtime),
        "size": int(st.st_size),
        "age_seconds": int(age),
        "stale": age > STALE_AFTER_S,
        # 前端 iframe 的 src 唯一来源；`?v=` 由前端加（cache-busting 是展示层的事）
        "url": VIEW_URL,
        # 顶栏「刷新数据」要触发的脚本名（前端不自己写死业务名）
        "refresh_script": REFRESH_SCRIPT_KEY,
    }


def register(ctx) -> APIRouter:
    router = APIRouter()

    @router.get("/meta")
    def meta() -> Dict[str, Any]:
        """产物新鲜度。纯 `os.stat`，无 IO 内容读取、无外部依赖。"""
        return build_meta()

    @router.get("/view")
    def view():
        """仪表盘本体：产物原样返回，不解析、不重算、不加壳。

        产物不存在 → 503 + 刷新命令提示（面板显示「还没生成」，不红屏、不 500）。
        """
        path = product_dir() / DASHBOARD_FILE
        if not path.is_file():
            return JSONResponse(
                {
                    "error": "peg output missing",
                    "path": str(path),
                    "hint": REFRESH_HINT,
                },
                status_code=503,
            )
        return _file_response(path)

    @router.get("/files/{name:path}")
    def files(name: str):
        """同目录其他产物（如 `peg_study_report.html`）：只服务 `.html`，越界一律 404。"""
        target = resolve_product(name)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")
        return _file_response(target)

    return router
