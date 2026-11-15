# -*- coding: utf-8 -*-
"""ops 插件后端：UI 备注 + 运维快照 + 交易模式 + 长期预警启停 + 四个旧端动作（14 条根路径路由）。

`backend.mount = "root"`：core 统一挂到 `/api`，旧前端直调的 `/api/ui_notes/*`、
`/api/ops.json`、`/api/trade_mode`、`/api/alert_plan/*`、`/api/panic`、`/api/amv_refresh`、
`/api/sector_track_backfill`、`/api/sector/restart` 原路径照旧可用。

## 语义唯一源（旧端 `engine/server.py`，只读参考，不复制逻辑结构）

| 路径 | 旧 handler | 数据落点 | 新写通道 |
|---|---|---|---|
| GET  /ui_notes/list | 828 `api_ui_notes_list` | engine/state/ui_notes.json | 只读 `ctx.state_json` |
| POST /ui_notes/add | 793 `api_ui_notes_add` | 同上 | `ctx.state_write("ui_notes", …)` |
| POST /ui_notes/update | 812 `api_ui_notes_update` | 同上 | 同上 |
| POST /ui_notes/resolve | 836 `api_ui_notes_resolve` | 同上 | 同上 |
| GET  /ops.json | 2063 `api_ops` | data/panel/ops_status.json | 只读 `ctx.panel_json("ops")` |
| POST /ops/run | 2081 `api_ops_run` | —（触发计划任务） | `ctx.ops_run`（core 门面→旧仓 run_manual） |
| GET  /trade_mode | 无（旧端只经 /api/status 带出） | engine/state/engine_state.json | 只读 `ctx.state_json` |
| POST /trade_mode | 7628 `trade_mode` | 同上 | `ctx.state_write("engine_state", …)` |
| POST /alert_plan/toggle | 7587 `alert_plan_toggle` | data/panel/alert_plan_persistent.json | `ctx.panel_write("alert_plan", …)` |
| POST /alert_plan/delete | 7609 `alert_plan_delete` | 同上 | 同上 |
| GET  /panic | 1268 `api_panic` | data/panel/panic_breadth.json + eltdx 快照 | 只读 `ctx.panel_json` + `ctx.quotes` |
| POST /amv_refresh | 2235 `api_amv_refresh` | data/panel/amv_live.json（`run_once` 自己写） | `ctx.ops_action` |
| POST /sector_track_backfill | 2292 `api_sector_track_backfill` | engine/state/sector_track_backfill_status.json | `ctx.ops_action` |

## 2026-10-24 M1 补缺（A1~A4，嗅嗅审查 P0/P1）

| 端点 | 做法 | 依据 |
|---|---|---|
| `GET /panic` | 装配层在 core `app/core/panic_calc.py`：算法（250 日滚动分位 / 状态标签 / 快照取原始值）**动态加载旧仓** `engine/collectors/panic_breadth.py`，不抄公式；快照改走 `ctx.quotes`（插件禁自连行情源）；序列库走 `ctx.panel_json("panic_breadth")`。 | `docs/HANDOFF_20261024_M1_fix.md` §A1 |
| `POST /amv_refresh` | `ctx.ops_action("amv_refresh")` → core 动态加载 `executor/amv_live.py` 调 `run_once(publish=True)`（**同进程函数调用，不是 schtasks**），锁+20s join 语义照旧端。 | §A2 |
| `POST /sector_track_backfill` | `ctx.ops_action("sector_backfill")` → core 调 `backfill_sector_track.run(check_only=True)`；有缺口转后台线程 `run(apply=True)`（工具自己写 status 文件，前端轮询路径不变）。 | §A3 |
| ~~`POST /sector/restart`~~ | **2026-10-30 摘除**：8002 下线并销毁，该动作与服务一并移出代码（旧口径为留 410 桩，已废）。 | — |

**这四个都保留旧语义的「锁 + 超时 + 如实报错」**，不静默吞：busy→429、timeout→504、
旧仓缺失/导入失败→503（不谎报「已触发」）。

## POST /api/ops/run 的实现路径（主人 2026-10-24 授权方案 B）

旧 handler 走 `ops.ops_status.run_manual(name)` → `schtasks /run` 起 Windows 计划任务，
属**跨进程系统操作**。插件沙箱禁子进程，所以这条能力放 core：
`ctx.ops_run(name)` → `app/core/ops_run.py` → 动态加载旧仓
`engine/ops/ops_status.py` 并调它的 `run_manual(name)`。

**白名单/冷却/任务状态/跨进程文件锁一律不复制**，单源在旧仓：否则两套名单一漂，
就是「新端能触发旧端拒绝的任务」这种最坏方向的漂移。core 不在 gate 静态扫描范围
（gate 只扫 `app/plugins/**`），子进程红线由「只经 `run_manual` 这一层闸」保证。

插件 manifest 必须显式声明 `data.ops_run: true`（默认 false）。
（决策与备选方案见 `docs/HANDOFF_20261024_N2_ops.md` §3。）

## 与旧端的已知差异（如实登记，不静默）

1. `GET /ops.json`：旧端 `ops_status.build(force=refresh)` **现场跑** schtasks/WMI/DB 探测并
   回写 ops_status.json；本插件只读该快照（禁子进程、禁写生产 panel 文件）。`?refresh=1`
   仍接受但不触发重算，返回体与旧端**逐键同构**，时间戳取决于聚合器最近一次落盘。
2. `POST /trade_mode`：目标值等于当前值时不写盘（旧端每次都刷 `updated_at`）；这样重复点同
   一模式零副作用。响应沿用旧键并多带一个 `changed` 布尔。
3. 三个写端点都不再写 `append_progress` 进度日志（那是旧进程内函数，插件无该通道）。
4. ui_notes 落盘缩进由 1 变 2 空格（`ctx.state_write` 统一 indent=2）；键名/嵌套/枚举不动。
5. `GET /trade_mode` 是 v2 新增的直读入口（旧端只在 `/api/status` 里带出 `trade_mode`）；
   旧的 `/api/status` 由 `compat` 插件提供，本插件不改它。
6. `POST /ops/run` 多一层 30s 硬上限（旧端无超时壳）：到点返回 `{ok:false, reason:'timeout'}`，
   但**已在跑的那次调用不杀**（强杀 schtasks 可能留半提交态）。`run_manual` 自身
   25s schtasks 超时 + 5s 文件锁，正常路径秒回。

## 硬约束

- 无直连数据库、无自连行情源、无 HTTP、无子进程、不自己开写句柄；
- 写只走 `ctx.state_write` / `ctx.panel_write`（白名单 + 备份 + 原子替换 + 审计都在 core）；
- 每次读-改-写用模块级锁串行化，避免并发请求互踩整份文档。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------- 常量

STATE_UI_NOTES = "ui_notes"
STATE_ENGINE = "engine_state"
PANEL_OPS = "ops"
PANEL_PANIC = "panic_breadth"
PANEL_ALERT = "alert_plan_persistent"
WRITE_ALERT = "alert_plan"
# K 线指标设置（M1 B1）：读走 READABLE_JSON，写走 WRITABLE_MISC（同一文件两份声明）
JSON_KLINE_SETTINGS = "kline_indicator_settings"
MISC_KLINE_SETTINGS = "kline_indicator_settings"

# 旧端 `_ui_notes_save` 对 html / style 的截断长度（逐字照搬）
HTML_MAX = 800
STYLE_MAX = 400

# 标注生命周期（2026-10-27）。旧口径只有 status=open/resolved，新口径加 stage + history，
# **status 由 stage 派生**，旧读法/旧前端不用改：
#   stage ∈ {verified, rejected} → status=resolved
#   其余                         → status=open
# 老数据没有 stage：resolved→verified、open→open；只在读时补（_norm_note），不写回。
STAGES = ("open", "doing", "fixed", "verified", "rejected")
CLOSED_STAGES = ("verified", "rejected")

# 同一文件的读-改-写串行化（三份文件互不相关，各一把锁，不互相阻塞）
_LOCK_NOTES = threading.Lock()
_LOCK_ENGINE = threading.Lock()
_LOCK_ALERT = threading.Lock()


def _now() -> str:
    """旧端 `time.strftime("%Y-%m-%d %H:%M:%S")` 同格式（本地时间，不带毫秒）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 请求体模型
#
# 字段与默认值逐字对齐旧端 `engine/server.py` 的 UiNoteBody / UiNoteUpdate /
# UiNoteResolve / AlertPlanToggleBody / AlertPlanDeleteBody / TradeModeBody；
# OpsRunBody 对应旧端 `api_ops_run(body: dict)` 的 `key`/`task` 二选一口径。


class UiNoteBody(BaseModel):
    selector: str = ""
    text: str = ""
    page: str = ""
    elem: str = ""
    html: str = ""
    style: str = ""
    x: float = 0
    y: float = 0


class UiNoteResolve(BaseModel):
    id: str = ""
    note: str = ""
    by: str = "01"


class UiNoteUpdate(BaseModel):
    id: str = ""
    text: str = ""


class UiNoteStage(BaseModel):
    """阶段流转（新增）。`by` 写 history；`expected` 非空时做乐观并发校验。"""

    id: str = ""
    to: str = ""
    note: str = ""
    ref: str = ""
    by: str = "01"
    expected: str = ""


class UiNoteDelete(BaseModel):
    """删一条标注（2026-11-06）。`expected` 非空时同样做乐观并发校验。"""

    id: str = ""
    expected: str = ""


class UiNotePurge(BaseModel):
    """按阶段批量清理（2026-11-06）。只允许 closed 阶段；`expect_count`>=0 时核条数。"""

    stage: str = ""
    expect_count: int = -1


class OpsRunBody(BaseModel):
    """手动触发 body（旧端 `api_ops_run(body: dict)`）：`key` 或 `task`，二选一。"""

    key: str = ""
    task: str = ""


class AlertPlanToggleBody(BaseModel):
    dp_id: str
    enabled: bool = True
    signer: str = "主人"


class AlertPlanDeleteBody(BaseModel):
    dp_id: str
    signer: str = "主人"


class TradeModeBody(BaseModel):
    trade_mode: str = "live"
    signer: str = "主人"


# ---------------------------------------------------------------- 读通道


def _notes_doc(ctx: Any) -> Dict[str, Any]:
    """ui_notes 文档（旧端 `_ui_notes_doc` 同口径：读不到当 `{"notes": []}`）。

    与旧端的唯一区别：**JSON 损坏/顶层不是对象**时不静默返回空文档（否则下一次写入
    就把 23KB 生产标注覆盖成 0 条），直接 503 让人看见。
    """
    try:
        doc = ctx.state_json(STATE_UI_NOTES)
    except FileNotFoundError:
        return {"notes": []}
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="ui_notes 未在插件清单声明：%s" % (exc,))
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="ui_notes.json 读取失败，拒绝操作（防覆盖）：%s: %s" % (type(exc).__name__, exc),
        )
    if not isinstance(doc, dict):
        raise HTTPException(status_code=503, detail="ui_notes.json 顶层不是对象，拒绝操作")
    if not isinstance(doc.get("notes"), list):
        doc["notes"] = []
    return doc


def _engine_state(ctx: Any) -> Dict[str, Any]:
    """engine_state 文档；文件不存在当空文档（旧端 `_read_json(...) or {}` 同口径）。"""
    try:
        doc = ctx.state_json(STATE_ENGINE)
    except FileNotFoundError:
        return {}
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="engine_state 未在插件清单声明：%s" % (exc,))
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=503,
            detail="engine_state.json 读取失败，拒绝写入（防覆盖）：%s: %s"
                   % (type(exc).__name__, exc),
        )
    if not isinstance(doc, dict):
        raise HTTPException(status_code=503, detail="engine_state.json 顶层不是对象，拒绝写入")
    return doc


def _open_count(notes: List[Dict[str, Any]]) -> int:
    return sum(1 for n in notes if isinstance(n, dict) and n.get("status") == "open")


def _stage_of(n: Dict[str, Any]) -> str:
    """当前阶段；老数据按 status 推导（resolved→verified），不认非法值。"""
    st = str(n.get("stage") or "").strip()
    if st in STAGES:
        return st
    return "verified" if n.get("status") == "resolved" else "open"


def _history_of(n: Dict[str, Any]) -> List[Dict[str, Any]]:
    h = n.get("history")
    return [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []


def _norm_note(n: Dict[str, Any]) -> Dict[str, Any]:
    """读时视图：补齐 stage/history/owner/stage_at，**不改盘**（不借一次 list 重写历史数据）。"""
    out = dict(n)
    out["stage"] = _stage_of(n)
    out["history"] = _history_of(n)
    out["owner"] = str(n.get("owner") or "")
    out["stage_at"] = str(n.get("stage_at") or n.get("resolved_at") or n.get("created_at") or "")
    return out


def _apply_stage(n: Dict[str, Any], to: str, note: str, ref: str, by: str, at: str) -> Dict[str, Any]:
    """改一条标注的阶段并追加 history（调用方持 _LOCK_NOTES）。"""
    frm = _stage_of(n)
    n["stage"] = to
    n["stage_at"] = at
    if by:
        n["owner"] = by
    h = _history_of(n)
    h.append({"at": at, "by": by or "", "from": frm, "to": to,
              "note": note or "", "ref": ref or ""})
    n["history"] = h
    closed = to in CLOSED_STAGES
    n["status"] = "resolved" if closed else "open"
    if closed:
        n["resolved_at"] = at
        n["resolve_note"] = note or ""
    else:  # 重开/退回：旧结论清掉，但 history 里仍留着（可追溯，不是删除）
        n["resolved_at"] = ""
        n["resolve_note"] = ""
    return n


def _stats(notes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """全量统计（不受 list 过滤影响）：各阶段条数 + 各页面条数。"""
    by_stage = {s: 0 for s in STAGES}
    by_page: Dict[str, int] = {}
    valid = [n for n in notes if isinstance(n, dict)]
    for n in valid:
        by_stage[_stage_of(n)] += 1
        p = str(n.get("page") or "(未标页)")
        by_page[p] = by_page.get(p, 0) + 1
    return {"total": len(valid),
            "open": sum(by_stage[s] for s in STAGES if s not in CLOSED_STAGES),
            "by_stage": by_stage, "by_page": by_page}


def _save_notes(ctx: Any, doc: Dict[str, Any]) -> None:
    try:
        ctx.state_write(STATE_UI_NOTES, doc)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="ui_notes 未在 data.state 声明：%s" % (exc,))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="ui_notes 写入失败：%s: %s"
                            % (type(exc).__name__, exc))


def _alert_doc(ctx: Any) -> Dict[str, Any]:
    """长期预警文档（旧端 `_read_json(ALERT_PLAN_FILE)` + 两个 isinstance 检查）。"""
    try:
        doc = ctx.panel_json(PANEL_ALERT)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="长期预警文件不存在")
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="alert_plan 未在插件清单声明：%s" % (exc,))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="长期预警文件读取失败：%s: %s"
                            % (type(exc).__name__, exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("plans"), list):
        raise HTTPException(status_code=404, detail="长期预警文件不存在")
    return doc


def _save_alert(ctx: Any, doc: Dict[str, Any]) -> None:
    try:
        ctx.panel_write(WRITE_ALERT, doc)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="alert_plan 未在 data.write 声明：%s" % (exc,))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=503, detail="长期预警写入失败：%s: %s"
                            % (type(exc).__name__, exc))


# ---------------------------------------------------------------- 恐慌广度（M1 A1）

def _panic_payload(ctx: Any) -> Any:
    """旧 `api_panic`（`engine/server.py:1268`）的装配。

    分档与旧端逐条一致：无序列库 → 503 `{ok:false, error:"no_store", hint}`；
    有库但算不出行 → 200 `{ok:false, error:"no_data"}`（旧端 `build_panic_payload` 的返回）。

    与旧端的差异：**不跑后台追加自愈**（旧端收盘后会起单飞线程 `append_day` 写生产库）。
    v2 渲染路径纯只读；补齐由盘后链 `panic_breadth.py --append` 负责（见 HANDOFF §A1）。
    """
    if PANEL_PANIC not in getattr(ctx.manifest, "panel_read", ()):
        raise HTTPException(
            status_code=500,
            detail="panic_breadth 未在 manifest.data.panel 声明（白名单双校验的第一半）",
        )
    from app.core import panic_calc  # noqa: PLC0415 - 懒引入：插件加载期不拉旧仓模块

    try:
        doc = panic_calc.load_store_doc()
    except panic_calc.PanicError as exc:
        raise HTTPException(status_code=503, detail="恐慌广度口径模块不可用：%s" % (exc,))
    if not (doc.get("series") or []):
        return JSONResponse(
            {"ok": False, "error": "no_store",
             "hint": "先跑 python collectors/panic_breadth.py --backfill"},
            status_code=503,
        )
    try:
        payload = panic_calc.payload(ctx.quotes, store_doc=doc)
    except panic_calc.PanicError as exc:
        raise HTTPException(status_code=503, detail="恐慌广度口径模块不可用：%s" % (exc,))
    return JSONResponse(payload)


# ---------------------------------------------------------------- 固定动作（M1 A2~A4）

def _run_action(ctx: Any, name: str) -> Any:
    """调 `ctx.ops_action`，把旧端形状译成 HTTP；错误分档与旧端逐条对应。

    · `http=429`（旧端 `HTTPException(429)`）→ 429
    · `http=504`（旧端超时）→ 504
    · `http=503`（旧仓模块缺失/导入失败）→ 503
    · `http=500`（旧端 `detail=f"刷新失败：…"`）→ 500
    · 其余（含 `status=ok/started/busy` 的正常体）→ 200，体原样透传（去掉内部 `http` 键）

    429/504/503/500 的体统一成 `{ok, detail, status}`：旧端这些分支都是 `HTTPException(detail=...)`，
    前端读 `detail` 显示；而正常体（`ok:false` + `reason`）旧端是 **200**，这里不动。
    """
    try:
        out = ctx.ops_action(name)
    except PermissionError as exc:
        raise HTTPException(status_code=500,
                            detail="ops_run 未在 manifest.data.ops_run 声明：%s" % (exc,))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="动作名非法：%s" % (exc,))
    if not isinstance(out, dict):
        raise HTTPException(status_code=503, detail="后台动作返回了非对象：%s" % type(out).__name__)
    body = {k: v for k, v in out.items() if k != "http"}
    code = out.get("http")
    if isinstance(code, int) and code >= 400:
        detail = body.get("detail") or body.get("reason") or "后台动作失败"
        return JSONResponse({"ok": False, "detail": detail, "status": body.get("status")},
                            status_code=code)
    return JSONResponse(body)


# ---------------------------------------------------------------- register


def register(ctx: Any) -> APIRouter:
    """插件入口：返回 APIRouter（manifest `mount=root` ⇒ core 挂到 `/api`）。"""
    router = APIRouter()

    # ============================================================ 1. UI 备注四件套

    @router.get("/ui_notes/list")
    def ui_notes_list(status: str = Query(default=""), stage: str = Query(default="")) -> Dict[str, Any]:
        """备注清单 + 生命周期统计。

        `status`（旧口径）/ `stage`（新口径）为空 = 全部；`stats` 恒为全量，给列表头部用。
        """
        if stage and stage not in STAGES:
            raise HTTPException(status_code=422,
                                detail="stage 不合法：%s（可选 %s）" % (stage, "/".join(STAGES)))
        doc = _notes_doc(ctx)
        notes = [n for n in doc["notes"] if isinstance(n, dict)]
        rows = [n for n in notes
                if (not status or n.get("status") == status)
                and (not stage or _stage_of(n) == stage)]
        return {"ok": True, "notes": [_norm_note(n) for n in rows],
                "open": _open_count(notes), "stats": _stats(notes)}

    @router.post("/ui_notes/add")
    def ui_notes_add(body: UiNoteBody) -> Dict[str, Any]:
        """新增页面标注。id 规则照旧端：`N` + `%Y%m%d-%H%M%S` + `-` + (现有条数 % 1000)。"""
        txt = (body.text or "").strip()
        if not txt:
            raise HTTPException(status_code=422, detail="标注内容不能为空")
        with _LOCK_NOTES:
            doc = _notes_doc(ctx)
            notes = doc["notes"]
            nid = "N" + time.strftime("%Y%m%d-%H%M%S") + "-" + str(len(notes) % 1000)
            note = {
                "id": nid,
                "selector": body.selector or "",
                "elem": body.elem or "",
                "html": (body.html or "")[:HTML_MAX],
                "style": (body.style or "")[:STYLE_MAX],
                "text": txt,
                "page": body.page or "",
                "x": int(round(body.x or 0)),
                "y": int(round(body.y or 0)),
                "status": "open",
                "stage": "open",
                "stage_at": "",
                "owner": "",
                "history": [],
                "created_at": _now(),
                "resolve_note": "",
            }
            notes.append(note)
            _save_notes(ctx, doc)
        return {"ok": True, "note": note, "open": _open_count(notes)}

    @router.post("/ui_notes/update")
    def ui_notes_update(body: UiNoteUpdate) -> Dict[str, Any]:
        """改已有标注的文字（只动 text + edited_at；位置变了算新标注，故不动 selector/page）。"""
        txt = (body.text or "").strip()
        if not txt:
            raise HTTPException(status_code=422, detail="标注内容不能为空")
        with _LOCK_NOTES:
            doc = _notes_doc(ctx)
            for n in doc["notes"]:
                if isinstance(n, dict) and n.get("id") == body.id:
                    n["text"] = txt
                    n["edited_at"] = _now()
                    _save_notes(ctx, doc)
                    return {"ok": True, "note": n}
        raise HTTPException(status_code=404, detail="标注不存在：%s" % (body.id,))

    @router.post("/ui_notes/resolve")
    def ui_notes_resolve(body: UiNoteResolve) -> Dict[str, Any]:
        """标记已解决 = stage→verified（写 history；status/resolved_at/resolve_note 照旧维护）。"""
        with _LOCK_NOTES:
            doc = _notes_doc(ctx)
            for n in doc["notes"]:
                if isinstance(n, dict) and n.get("id") == body.id:
                    note = _apply_stage(n, "verified", body.note or "", "", body.by or "", _now())
                    _save_notes(ctx, doc)
                    return {"ok": True, "note": note}
        raise HTTPException(status_code=404, detail="标注不存在：%s" % (body.id,))

    @router.post("/ui_notes/stage")
    def ui_notes_stage(body: UiNoteStage) -> Dict[str, Any]:
        """推进/回退标注阶段并留痕（生命周期唯一写口）。

        `expected` 非空时做乐观并发校验：与当前 stage 不一致 → 409，
        防两个页面各按旧视图覆盖（列表与页面 pin 可能同时开）。
        """
        to = (body.to or "").strip()
        if to not in STAGES:
            raise HTTPException(status_code=422,
                                detail="to 不合法：%s（可选 %s）" % (to, "/".join(STAGES)))
        with _LOCK_NOTES:
            doc = _notes_doc(ctx)
            for n in doc["notes"]:
                if isinstance(n, dict) and n.get("id") == body.id:
                    cur = _stage_of(n)
                    if body.expected and body.expected != cur:
                        raise HTTPException(
                            status_code=409,
                            detail="标注阶段已变为 %s（提交时是 %s），请刷新后再操作" % (cur, body.expected))
                    note = _apply_stage(n, to, (body.note or "").strip(), (body.ref or "").strip(),
                                        (body.by or "").strip(), _now())
                    _save_notes(ctx, doc)
                    return {"ok": True, "note": note, "stats": _stats(doc["notes"])}
        raise HTTPException(status_code=404, detail="标注不存在：%s" % (body.id,))

    @router.post("/ui_notes/delete")
    def ui_notes_delete(body: UiNoteDelete) -> Dict[str, Any]:
        """删一条标注（2026-11-06 主人：标注不能只能加不能删）。

        硬删（连同 history）：可追溯性由文件历史（HANA / git）兜底，不在 JSON 里堆墓碑。
        `expected` 与 /stage 同口径：不一致 → 409，防两个页面各按旧视图覆盖。
        """
        nid = (body.id or "").strip()
        if not nid:
            raise HTTPException(status_code=422, detail="id 不能为空")
        with _LOCK_NOTES:
            doc = _notes_doc(ctx)
            keep: List[Any] = []
            gone = None
            for n in doc["notes"]:
                if isinstance(n, dict) and n.get("id") == nid:
                    gone = n
                    continue
                keep.append(n)
            if gone is None:
                raise HTTPException(status_code=404, detail="标注不存在：%s" % (nid,))
            cur = _stage_of(gone)
            if body.expected and body.expected != cur:
                raise HTTPException(
                    status_code=409,
                    detail="标注阶段已变为 %s（提交时是 %s），请刷新后再操作" % (cur, body.expected))
            doc["notes"] = keep
            _save_notes(ctx, doc)
        return {"ok": True, "removed": 1, "stats": _stats(keep)}

    @router.post("/ui_notes/purge")
    def ui_notes_purge(body: UiNotePurge) -> Dict[str, Any]:
        """按阶段批量清理（2026-11-06）。**只允许 closed 阶段**（verified/rejected）。

        未完成的标注不提供批量删除：那是一批还没处理完的意见，删掉等于把待办抹了。
        `expect_count` >= 0 时先核条数，不一致 → 409（防列表停留时别人又关了单）。
        """
        stage = (body.stage or "").strip()
        if stage not in CLOSED_STAGES:
            raise HTTPException(
                status_code=422,
                detail="只允许清理已验证/驳回（%s），不能批量删未完成的标注"
                       % ("/".join(CLOSED_STAGES),))
        with _LOCK_NOTES:
            doc = _notes_doc(ctx)
            victims = [n for n in doc["notes"] if isinstance(n, dict) and _stage_of(n) == stage]
            if body.expect_count >= 0 and len(victims) != body.expect_count:
                raise HTTPException(
                    status_code=409,
                    detail="条数已变（提交时 %d，现在 %d），刷新后再操作"
                           % (body.expect_count, len(victims)))
            keep = [n for n in doc["notes"]
                    if not (isinstance(n, dict) and _stage_of(n) == stage)]
            doc["notes"] = keep
            _save_notes(ctx, doc)
        return {"ok": True, "removed": len(victims), "stats": _stats(keep)}

    # ============================================================ 2. 运维快照（只读）+ 手动触发（未实现）

    @router.get("/ops.json")
    def ops_json(refresh: int = Query(default=0)) -> Dict[str, Any]:
        """后台任务监控快照（读落盘产物；`refresh=1` 先重算再读）。

        2026-10-26 旧端口下线：改前 `refresh` 只是兼容旧前端的空参数（v2 不跑 schtasks/WMI），
        重算靠 8800 的 GET 现场 build。 8800 退役后没有现场重算口，快照会停在上一夜，
        所以把 `refresh=1` 接成**真重算**（`ctx.ops_action("ops_snapshot")` → core
        调 `ops_status.build(force=True, write=True)`，耗时 ~5s，只给刷新按钮按需用）。

        重算失败**不丢旧快照**：回退读已有文件，并在 `_refresh` 字段如实告知失败原因。
        """
        refresh_error = None
        if refresh:
            try:
                out = ctx.ops_action("ops_snapshot")
                if not (isinstance(out, dict) and out.get("ok")):
                    refresh_error = (out or {}).get("detail") or "重算未成功"
            except Exception as exc:  # noqa: BLE001 - 重算失败不顶掉可读的旧快照
                refresh_error = "%s: %s" % (type(exc).__name__, exc)
        try:
            doc = ctx.panel_json(PANEL_OPS)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail="ops_status.json 缺失（聚合器尚未落盘；现由 postclose_chain 的 opssnap 步产出）",
            )
        except PermissionError as exc:
            raise HTTPException(status_code=500, detail="ops 未在插件清单声明：%s" % (exc,))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=503, detail="后台状态聚合快照读取失败：%s: %s"
                                % (type(exc).__name__, exc))
        if refresh_error:
            doc = dict(doc)
            doc["_refresh"] = {"ok": False, "detail": refresh_error}
        return doc

    @router.post("/ops/run")
    def ops_run(body: OpsRunBody) -> Dict[str, Any]:
        """手动触发数据类计划任务（旧端 `api_ops_run` → `ops_status.run_manual`）。

        语义单源在旧仓（白名单/任务状态/冷却/跨进程锁全在那里），本层只做：
        取名字 → `ctx.ops_run`（core 门面）→ 原样透传返回体。
        闸口失败（旧仓缺失）→ 503，不谎报「已触发」。
        """
        name = (body.key or body.task or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="缺少 key（计划任务名）")
        try:
            return ctx.ops_run(name)
        except PermissionError as exc:
            raise HTTPException(status_code=500,
                                detail="ops_run 未在 manifest.data.ops_run 声明：%s" % (exc,))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="手动更新不可用：%s" % (exc,))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="任务名非法：%s" % (exc,))

    # ============================================================ 3. 交易模式

    @router.get("/trade_mode")
    def get_trade_mode() -> Dict[str, Any]:
        """当前交易模式。旧端没有独立 GET（徽章读 /api/status），本路由为 v2 直读入口。

        缺省/非法值一律 live（旧 `load_trade_mode` 同口径：脏配置不静默切纸面）。
        """
        st = _engine_state(ctx)
        mode = st.get("trade_mode", "live")
        if mode not in ("live", "paper"):
            mode = "live"
        return {"ok": True, "trade_mode": mode, "engine_state": st.get("state")}

    @router.post("/trade_mode")
    def set_trade_mode(body: TradeModeBody) -> Dict[str, Any]:
        """切换实盘/纸面：只改 engine_state.trade_mode，其余键原样保留。

        幂等：目标值等于当前值且文件已存在 → 不写盘（`changed=false`）。
        """
        mode = (body.trade_mode or "").strip()
        if mode not in ("live", "paper"):
            raise HTTPException(
                status_code=422,
                detail="非法 trade_mode：%s（可选 live/paper）" % (body.trade_mode,),
            )
        with _LOCK_ENGINE:
            st = _engine_state(ctx)
            changed = st.get("trade_mode") != mode
            if changed:
                # 旧端 `_load_engine_state` 的三个缺省键（文件缺失时补齐，已有则不动）
                st.setdefault("state", "READY")
                st.setdefault("mode", "READ_ONLY")
                # 2026-11-07：battle_plan_signed 镜像位随签收链删除（主人拍板），不再回填
                st["trade_mode"] = mode
                st["updated_at"] = _now()
                try:
                    ctx.state_write(STATE_ENGINE, st)
                except PermissionError as exc:
                    raise HTTPException(status_code=500,
                                        detail="engine_state 未在 data.state 声明：%s" % (exc,))
                except (ValueError, OSError) as exc:
                    raise HTTPException(status_code=503, detail="engine_state 写入失败：%s: %s"
                                        % (type(exc).__name__, exc))
        return {"ok": True, "trade_mode": mode, "engine_state": st.get("state"),
                "changed": changed}

    # ============================================================ 4. 长期预警启停/删除

    @router.post("/alert_plan/toggle")
    def alert_plan_toggle(body: AlertPlanToggleBody) -> Dict[str, Any]:
        """按 dp_id 改 enabled（保留条目，不删除）。文件缺失/无此 id → 404。"""
        with _LOCK_ALERT:
            doc = _alert_doc(ctx)
            target = next((it for it in doc["plans"]
                           if isinstance(it, dict) and it.get("dp_id") == body.dp_id), None)
            if target is None:
                raise HTTPException(status_code=404, detail="预警条目不存在：%s" % (body.dp_id,))
            target["enabled"] = bool(body.enabled)
            doc.setdefault("meta", {})["updated_at"] = _now()
            _save_alert(ctx, doc)
        return {"ok": True, "dp_id": body.dp_id, "enabled": target["enabled"]}

    @router.post("/alert_plan/delete")
    def alert_plan_delete(body: AlertPlanDeleteBody) -> Dict[str, Any]:
        """按 dp_id 真删除（不可逆，前端已二次确认）。文件缺失/无此 id → 404。"""
        with _LOCK_ALERT:
            doc = _alert_doc(ctx)
            kept = [it for it in doc["plans"]
                    if not (isinstance(it, dict) and it.get("dp_id") == body.dp_id)]
            if len(kept) == len(doc["plans"]):
                raise HTTPException(status_code=404, detail="预警条目不存在：%s" % (body.dp_id,))
            doc["plans"] = kept
            doc.setdefault("meta", {})["updated_at"] = _now()
            _save_alert(ctx, doc)
        return {"ok": True, "dp_id": body.dp_id, "remaining": len(kept)}

    # ============================================================ 5. 恐慌广度（M1 A1）

    @router.get("/panic")
    def panic():
        """冰点监控·恐慌广度（旧 `api_panic`，`engine/server.py:1268`）。

        当前值 + 状态标签 + 三项拆解（值/250 日分位）+ 近 120 日序列。
        装配在 core `panic_calc`（算法动态加载旧仓，快照走 `ctx.quotes`）。
        """
        return _panic_payload(ctx)

    # ============================================================ 6. 旧端固定动作（M1 A2~A4）

    @router.post("/amv_refresh")
    def amv_refresh():
        """按需刷新实时活跃市值（旧 `api_amv_refresh`，`engine/server.py:2235`）。

        前端：`index.html:5277` 「刷新活跃市值」按钮。`ok:false` + `reason` 仍是 **200**（旧端同）。
        """
        return _run_action(ctx, "amv_refresh")

    @router.post("/sector_track_backfill")
    def sector_track_backfill():
        """板块轨迹缺口补齐（旧 `api_sector_track_backfill`，`engine/server.py:2292`）。

        前端：`index.html:4050`。返回 `status=ok|started|busy|error`；状态查询仍走
        `compat_sector` 的 `/api/sector_track_backfill_status`（本插件不接管）。
        """
        return _run_action(ctx, "sector_backfill")

    # 2026-10-30：`POST /sector/restart` 随 8002 下线一并摘除（主人 2026-10-30 拍板）。
    # 旧口径（2026-10-26）是留一个返回 retired/410 的桩给旧调用方看；此桩已无调用方，
    # 留着只会把 8002 的指针留在代码里，故整条删除。板块数据由 sector 插件直接提供。

    # ============================================================ 7. M1 B 组（K 线周边两个根路径端点）
    #
    # 为什么挂在 ops 而不是 kline：旧前端请求的是**根路径**
    # `/api/kline-indicator-settings` 与 `/api/kline/{code}/xdxr`，而 kline 插件是
    # `mount=plugin`（/api/p/kline/*）。core 目前是「一插件一路由表、挂一个前缀」，
    # 不支持同一插件挂两前缀；`app/core/main.py` 不在本单白名单内。
    # 所以 HTTP 壳放在本插件（已是 mount=root，先例：ui_notes/trade_mode 也是根路径杂项），
    # **语义全部在 core**：除权除息取数 = `app/core/quotes.py::xdxr()`（M1 新增通道），
    # 设置读写 = `ctx.json_read` / `ctx.misc_write`（白名单 + 备份 + 原子 + 审计）。
    # 本插件不持有任何 K 线业务逻辑，迁到 kline 只需搬路由（见 HANDOFF §B 遗留）。

    @router.get("/kline-indicator-settings")
    def get_kline_indicator_settings() -> Any:
        """读指标设置（旧 `engine/server.py:719`）：无文件/坏文件 → `{}`（旧端同，不 404）。"""
        try:
            doc = ctx.json_read(JSON_KLINE_SETTINGS)
        except FileNotFoundError:
            return JSONResponse({})
        except PermissionError as exc:
            raise HTTPException(status_code=500,
                                detail="kline_indicator_settings 未在插件清单声明：%s" % (exc,))
        except (ValueError, OSError):
            # 旧端：解析失败也回 {}（前端 mergeSettings 会当无设置，不报错）
            return JSONResponse({})
        return JSONResponse(doc if isinstance(doc, dict) else {})

    @router.put("/kline-indicator-settings")
    def put_kline_indicator_settings(body: Optional[Dict[str, Any]] = Body(default=None)) -> Any:
        """存指标设置（旧 `engine/server.py:732`）：整分 dict 覆盖写，tmp+replace 原子。

        前端（`kline_lc.js:3453-3457`）是「GET 整分 → 只改 kline_lc 键 → PUT 整分」，
        所以本端点**必须保留未知顶层键**（如 tckx 预览页的 main/sub）。
        空 body / 非对象 → 落 `{}`（旧端 `body or {}` 同口径）。
        """
        try:
            ctx.misc_write(MISC_KLINE_SETTINGS, body if isinstance(body, dict) else {})
        except PermissionError as exc:
            raise HTTPException(status_code=500,
                                detail="kline_indicator_settings 未在 data.misc 声明：%s" % (exc,))
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail="设置写入失败: %s: %s"
                                % (type(exc).__name__, exc))
        return JSONResponse({"ok": True})

    @router.get("/kline/{ts_code}/xdxr")
    def kline_xdxr(ts_code: str) -> Any:
        """除权除息事件（旧 `engine/server.py:672`）：`{code, date, events[]}`。

        形状逐键对齐旧端：`date` 是**查询日**（YYYYMMDD，不是事件日），`events[]` 每项
        `{date, category, type, fenhong, songzhuangu, peigu, peigujia}`；只有 category=1
        （除权除息）带四个数字，其余为 null。取数走 `ctx.quotes.xdxr()`（core 新通道）。

        **非法代码不报 4xx**：旧端 `api_kline_xdxr` 对任何字符串都回 `200 {code, date, events:[]}`
        （`_tdx_market` 认不出 → `_fetch_xdxr` 空表）；实测 8800 对 `notacode` 就是 200。
        为同参对齐逐条一致，这里也回 200 + 空表（前端 `kline_lc.js:3131` 只需 `.ok`）。
        """
        code = str(ts_code or "").split(".")[0]
        events: List[Dict[str, Any]] = []
        try:
            events = ctx.quotes.xdxr(ts_code)
        except Exception:  # noqa: BLE001 - 行情源不可用 → 空表（旧端同降级，前端 .catch 兑 null）
            events = []
        return JSONResponse({"code": code, "date": datetime.now().strftime("%Y%m%d"),
                             "events": events})

    return router
