# -*- coding: utf-8 -*-
"""dashboard 插件：仪表盘卡的**薄壳**后端（前端批次 1b，docs/task_fe_batch1b.md）。

本卡的数据全部来自既有 root 挂载端点，前端用 `ctx.api.get('/api/...')` 直接消费
（shell.js 的 makeCtx 对 `/api/` 开头的路径不拼插件前缀）：

| 端点 | 提供方 | 用途 |
|---|---|---|
| `/api/ledger?mode=paper|live` | `paper` 插件（root） | 账户摘要 / 持仓 / 当日成交 / 活跃单 / 流水（主数据） |
| `/api/dashboard.json` | `compat` 插件（root） | 引擎 / 执行器 / 看门狗 / 环境徽章 |
| `/api/gates` | `compat` 插件（root） | 三道门状态灯 |

因此本模块**只做健康自省**：不读 panel JSON、不连库、不写盘、不解析行情。
`ctx.panel` 留空 —— 加这条路径等于把「谁算账本」复制出第二份口径（口径唯一源 =
`app/core/paper/` + `app/plugins/paper/views.py`），与项目「一个指标一个 owner」冲突。

健康检查里的三个数据源名字是**给排障用的清单**，不代表本插件能读到它们的文件。
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

# 卡的数据源清单（前端 frontend.js 的 DATA_SOURCES 与此逐条对应，测试锁一致性）
DATA_SOURCES = (
    "/api/ledger?mode=paper|live",
    "/api/dashboard.json",
    "/api/gates",
    "/api/status",
)

CARD_ID = "dashboard.main"


def build_health() -> Dict[str, Any]:
    """GET /health：薄壳自省（无 IO、无写、无外部依赖）。"""
    return {
        "ok": True,
        "plugin": "dashboard",
        "card": CARD_ID,
        "read_only": True,
        "backend": "thin-shell",
        "data_via": "frontend ctx.api.get（绝对 /api/ 路径）",
        "sources": list(DATA_SOURCES),
        "write_ops": [],
        "refresh_every_ms": 3000,
    }


def register(ctx: Any) -> APIRouter:
    """入口：只挂健康检查（不改 ctx、不读数据、不注册任何写路由）。"""
    router = APIRouter()

    @router.get("/health")
    def health() -> Dict[str, Any]:
        return build_health()

    return router
