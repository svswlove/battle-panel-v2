# -*- coding: utf-8 -*-
"""selection 插件后端：**空 router 契约占位**（v0.1.0，任务书 docs/task_R2_selection.md）。

## 为什么这个文件几乎没有代码

本卡要用的四个端点（`/selection`、`/selection_prefs`、`/selection_pool`、
`/selection_pool/add|remove`、`/selection_board_options`）**全部已由 `compat_misc` 实现**，
且是 `mount="root"` 挂到 `/api` 根路径（见 `docs/compat_misc.md` §7~10）。
任务书 §3 写明「零后端改动」，所以这里不新增任何路由。

## 为什么还要留着 backend.py

core 的 `load_plugin()` 硬性要求插件目录里有 `backend.py`、能 import、且 `register(ctx)`
返回 `fastapi.APIRouter`（`app/core/contract.py::load_plugin`）。同时 `main.py` 的根路径
冲突检查是「同一 `/api/<path>` 被两个 root 插件注册 → 启动即 RuntimeError」——
本插件必须 `mount="root"`（前端消费的是根路径端点，不是 `/api/p/selection/*`），
但**一个路由都不能注册**，否则就会和 `compat_misc` 抢同一路径把 core 启动打挂。

所以本文件的正确形态就是：`mount=root` + 空 `APIRouter()`。
将来若把选股端点从 compat_misc 收编成 selection 自己的域，只需在这里加路由、
并从 compat_misc 删除对应路径（一次只挪一条，避免双源）。

## 硬约束

- 无 GET/POST 路由、无 `ctx.db_read` / `ctx.panel_json` / `ctx.panel_write` 调用；
- 无 `open()`、无 HTTP、无子进程、不写任何文件（`cutover_gate` 静态扫描同样约束本文件）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

# 本插件当前**不消费**任何数据通道：全部数据由前端直接打 compat_misc 的根路径端点。
# manifest 里 `data.*` 全空即为此意，不声明就用不了（core 的 ctx 会拒绝）。
DATA_CONTRACT: dict[str, str] = {
    "selection": "GET /api/selection —— 8.2MB 盘后快照（compat_misc，root 挂载）",
    "selection_prefs": "GET|POST /api/selection_prefs —— 筛选偏好（compat_misc 受控写）",
    "selection_pool": "GET /api/selection_pool?date= ／ POST /api/selection_pool/add|remove —— 选股池（compat_misc）",
    "selection_board_options": "GET /api/selection_board_options —— 板块筛选候选（compat_misc）",
}


def register(ctx: Any) -> APIRouter:
    """插件入口：返回**空** APIRouter（manifest `mount=root` ⇒ core 挂到 `/api`）。

    不注册任何路径：选股四端点归 `compat_misc`，两个 root 插件抢同一路径 core 会启动失败。
    """
    _ = ctx  # 本插件不使用任何 ctx 能力；显式消费避免 linter 误判未用参数
    return APIRouter()
