# -*- coding: utf-8 -*-
"""battle_plan_rw：作战表写域根路径端点（原生写，不走转发）。

**这是写域的搬迁，不是新功能。** 语义唯一源 = 旧仓 `engine/server.py` 的同名 handler
（行号见 `docs/task_N1_bpwrite.md` §2，全部只读参考）：

| 路径 | 旧 handler | 落盘 |
|---|---|---|
| ~~`POST /api/sign`~~ | ~~`sign_plan`（7144）~~ | **2026-11-07 删除**（签收链是空壳，主人拍板：系统只有模拟盘） |
| `POST /api/kill` | `kill_switch`（7646） | battle_plan + engine_state + `engine/state/kill_switch.json` |
| `POST /api/toggle` | `toggle_item`（7232） | battle_plan |
| `POST /api/cancel` | `cancel_item`（7180） | battle_plan + engine_state |
| `POST /api/signal_watch/toggle` | `signal_watch_toggle`（7541） | battle_plan |
| `POST /api/signal_watch/cancel` | `signal_watch_cancel`（7569） | battle_plan |
| `POST /api/manual_position` | `manual_position`（7342） | battle_plan |

## 为什么单独一个插件 + `mount = "root"`

`battle_plan` 插件是 `mount=plugin`（`/api/p/battle_plan/*`）的只读影子，旧前端直调的却是
根路径 `/api/sign`。core 现状是一插件一路由表、挂一个前缀，做不到「同一插件挂两前缀」。
本批按任务书 §4 的**方案 B**：新建本插件 `mount=root` 承载根路径，`battle_plan` 原插件
一行不动，core 零改动（`main.py` 的 root 挂载与冲突检测已就绪）。

注意 `POST /api/sign` 在 core `main.py` 里还留着一份**转发到 8800 的代理**（批次 0）。
插件路由先注册（`create_app` 里先 include 插件 router，再定义 core 自带路由），
Starlette 按注册顺序匹配 → 本插件的原生 handler 生效，代理成为死代码。
**2026-11-07：签收链已整体删除，core 侧代理与前端按钮同步清掉（不再有「配置错路径」的遗留）。**

## 写通道（唯一）

- battle_plan：`ctx.panel_write("battle_plan", payload)`
- engine_state / kill_switch：`ctx.state_write(name, payload)`

两者都由 core 做白名单校验 + 写前备份 + 原子替换（tmp→os.replace）+ 审计日志；
本插件**不做任何文件 IO 写**（`tools/cutover_gate.py` 静态扫描仍然有效）。
读：battle_plan / dashboard 走 `ctx.panel_json`；两个 state 文件走 `ctx.state_json`
（core 若尚未提供该方法，退到 `paths.WRITABLE_STATE` 同一白名单**只读**兜底，
见 `_state_read` 注释——N2 并发单落地 `state_json` 后自动切回 ctx 通道）。

## 读-改-写整份 + 锁

全部端点都是「读整份 → 改内存 → 整份写回」，未知字段原样保留（顶层键、`meta`、
条目上的额外键）。同一进程内用模块级 `threading.Lock` 包住整个读改写
（`docs/CONTRACT_STATE.md` §4；旧端 `server.py` 无锁，这里补上，防连点交叉覆盖）。

## 照搬的幂等/事故细节（逐条对照旧码，不得"优化"）

1. **空 `buy_plan` 点「全部签收」不降级顶层 sign**（`_sync_plan_sign` 265 行注释里的历史
   事故：8/28、8/31 两次把已授权 sign 降成 UNSIGNED，引擎全天只读）。空买侧只回报
   「现有 sign 是否已授权」，不动 `data["sign"]`、不动 `meta.generated_at`。
2. **kill 的 on→on 不覆盖快照**（7646）：`engine_state.module_switches_snapshot` 已是 dict 时
   沿用旧快照，否则一次急停后解除会永久停机（快照里全是 false）。
   on→off→on 才重新快照（off 已恢复 + 清快照）。
3. **重复签收已签条目**：`_sign_item` 的 `sign` 子对象不覆盖（保留首次 signed_at/signer/note），
   但 `status_history` 每次追加一条（旧端行为，如实照搬）。
4. **toggle 不动 sign/status**，只改 `enabled` + 追加 `status_history`；**择机单 toggle/cancel
   不写 status_history**（S3 契约里没这个字段，不破坏 schema）。

## 与旧端的差异（逐条登记，见 `docs/battle_plan_contract.md`）

| # | 差异 | 原因 |
|---|---|---|
| 1 | `/api/sign` 响应**没有 `batch_cap` 键** | `_batch_cap_check_and_alert` 要读批次上限/单笔上限配置并推 QQ 事件，属另一个域；本批不搬（任务书 §2 明列） |
| 2 | 不重建 `dashboard.json` | 旧端 `_refresh_dashboard()` 走 `dashboard_builder`（IO+库）；v2 有独立 dashboard 域，写域不越界 |
| 3 | 不写 `PROGRESS` 日志 | 旧端 `append_progress()` 写 engine 进程日志；改记 `ctx.logger.info`（同内容、不新增写目标） |
| 4 | battle_plan.json 缺失 → 404 `battle_plan 无数据` | 旧端 `_battle_plan()` 会回退 schema 示例并**把示例写回生产文件**；v2 不拿示例冒充真数据 |
| 5 | 引擎状态文件读走 `ctx.state_json`（缺失时退 `paths.WRITABLE_STATE` 只读） | core 尚未提供 state 读方法（N2 并发单补） |
| 6 | 响应体 `engine_state` 语义不变（`ARMED`/`READY`/`FROZEN`），但不再透传 8800 的错误体 | 写在本进程内完成，没有转发层 |

写域不覆盖：模块开关（`/api/module_switch`、`/module_switches_batch`）、工单策略
（`/api/ticket_policy*`）、长期预警（`/api/alert_plan/*`）、交易模式（`/api/trade_mode`）——
各有 owner，不在本单。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import paths as core_paths

# ---------------------------------------------------------------- 常量（照旧端）

PANEL_PLAN = "battle_plan"
PANEL_DASHBOARD = "dashboard"

STATE_ENGINE = "engine_state"
STATE_KILL = "kill_switch"

# 九个执行模块键（旧端 `server.py::MODULE_KEYS` 逐字；注释里的「8 键」是历史口径，
# 实际元组 9 项，2026-09-19 加 alert）。kill 的快照/全停都按这 9 键。
MODULE_KEYS = ("entry", "exit_stop", "exit_profit", "do_t", "notify",
               "sizing", "awake", "vol_ratio_buy", "alert")

# 面板可勾选的推送类型（旧端 `engine/common/push_queue.py::NOTIFY_TYPE_KEYS`，9 类）。
#
# 为什么内置快照而不读旧仓：插件不能 import 旧仓模块（CONTRACT §8），core 也没有「读 py 常量」
# 的只读通道（READABLE_* 白名单只管数据文件）。先例：`compat/backend.py` 的 `_PUSH_TYPE_KEYS`
# 同处理。**漂移防守**：`tests/test_battle_plan_rw_contract.py::test_notify_type_keys_match_legacy`
# 直接读旧仓那个文件抽出元组逐项比对——旧仓改枚举时测试会红，不会静默漂。
NOTIFY_TYPE_KEYS = (
    "ORDER_FILLED", "ORDER_PARTIAL", "ORDER_CANCELLED", "ORDER_REJECTED",
    "SIGNAL_TRIGGERED", "STOP_TRIGGERED", "TAKE_PROFIT_TRIGGERED",
    "DO_T_TRIGGERED", "MODULE_OFF",
)

# 读-改-写互斥（模块级）：防两个请求交叉覆盖同一份文件（旧端无锁，这里补）。
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 请求体（逐字照旧端）

class CancelBody(BaseModel):
    item_type: str = "buy_plan"     # buy_plan | operations
    plan_id: Optional[str] = None   # item_type=buy_plan 时必填
    ts_code: Optional[str] = None   # item_type=operations 时必填
    reason: str = ""
    signer: str = "主人"


class ToggleBody(BaseModel):
    item_type: str = "buy_plan"
    plan_id: Optional[str] = None
    ts_code: Optional[str] = None
    enabled: bool = True
    signer: str = "主人"


class KillBody(BaseModel):
    active: bool
    reason: str = ""


class SignalWatchToggleBody(BaseModel):
    watch_id: str = Field(min_length=1)
    enabled: Optional[bool] = None   # 未传 = 保持原值（且不写盘）
    signer: str = "主人"


class SignalWatchCancelBody(BaseModel):
    watch_id: str = Field(min_length=1)
    reason: str = ""
    signer: str = "主人"


class ManualPositionBody(BaseModel):
    pct: float = Field(ge=0, le=100)   # 越界/非数字由 FastAPI 422 拦截
    signer: str = "主人"


class ManualPositionPatch(BaseModel):
    """批量提交里的可选子段（旧端 `manual_position: dict | None`）。

    旧端这里只验 `pct ∈ [0,100]`（旧 `ModuleSwitchesBatchBody.manual_position: dict | None`
    + 7420 行手写校验），不认 `signer`；这里用模型是为了拿 FastAPI 的 422，
    校验口径与旧端逐条对齐（非数字/布尔/越界 → 422）。
    """

    pct: float


class ModuleSwitchesBatchBody(BaseModel):
    """待确认批量提交（旧端 `ModuleSwitchesBatchBody`，`engine/server.py:432`）。

    · `switches`：模块开关键名 → 布尔，只更新传入键，落盘补全九键
    · `manual_position`：可选 `{pct}`，amount 按总仓位换算（旧 `_amount_from_position_cap`）
    · `notify_types`：可选推送类型白名单（9 枚举，去重保序）
    · `signer`：旧端收但本批不落盘（与其它写端点一致，只进日志）
    """

    switches: Dict[str, bool] = {}
    manual_position: Optional[Dict[str, Any]] = None
    notify_types: Optional[List[str]] = None
    signer: str = "主人"


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    """旧端 `common.tsutil.now_str()` 同格式（`YYYY-MM-DD HH:MM:SS`）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(ctx: Any, message: str) -> None:
    """操作痕迹：旧端写 PROGRESS 日志，这里只进进程日志（不新增写目标）。"""
    logger = getattr(ctx, "logger", None)
    if logger is not None:
        logger.info(message)


# ---------------------------------------------------------------- 读（只经 ctx）


def _read_plan_for_write(ctx: Any) -> Dict[str, Any]:
    """写操作第一步：读整份 battle_plan。

    与只读路径的差别：**读失败就不写**（否则一次读错会拿空文档覆盖生产文件）。
    文件确实不存在才 404（旧端语义：无作战表 → `battle_plan 无数据`）。
    """
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
    try:
        doc = getter(PANEL_PLAN)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="battle_plan 无数据")
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="battle_plan 未在插件清单声明：%s" % (exc,))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail="battle_plan 读取失败，拒绝写入（防覆盖）：%s: %s" % (type(exc).__name__, exc),
        )
    if not isinstance(doc, dict):
        raise HTTPException(status_code=503, detail="battle_plan 顶层不是对象，拒绝写入")
    # 旧端 `_battle_plan()` 的兜底三件套（缺键时补默认，写回即补全）
    doc.setdefault("buy_plan", [])
    doc.setdefault("operations", [])
    return doc


def _state_read(ctx: Any, name: str) -> Dict[str, Any]:
    """只读一个引擎状态文件（engine_state / kill_switch）。

    优先 `ctx.state_json(name)`（core 提供时走正式 ctx 通道）。core 当前只有
    `state_write`（2026-10-24 白名单机制，见 `docs/CONTRACT_STATE.md`），读方法由 N2
    并发单补；在那之前退到 `paths.WRITABLE_STATE` **同一份白名单**做只读兜底——
    路径不写死、不绕过白名单，也不产生任何写动作。
    """
    getter = getattr(ctx, "state_json", None)
    if callable(getter):
        try:
            data = getter(name)
        except FileNotFoundError:
            return {}
        except PermissionError as exc:
            raise HTTPException(status_code=500, detail="%s 未在插件清单声明：%s" % (name, exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail="%s 读取失败，拒绝写入（防覆盖）：%s: %s" % (name, type(exc).__name__, exc),
            )
        return data if isinstance(data, dict) else {}

    manifest = getattr(ctx, "manifest", None)
    declared = getattr(manifest, "state_write", ()) or ()
    if name not in declared:
        raise HTTPException(status_code=500, detail="%s 未在 manifest.data.state 声明" % (name,))
    path = core_paths.state_writable(name)
    if path is None:
        raise HTTPException(status_code=500, detail="%s 不在状态文件白名单内" % (name,))
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001 - 损坏文件按空文档起（旧端 `_read_json` 同口径）
        return {}
    return data if isinstance(data, dict) else {}


def _load_engine_state(ctx: Any) -> Dict[str, Any]:
    """旧端 `_load_engine_state()`：缺键补默认（trade_mode 缺省=live，与
    `executor.auto_buy.load_trade_mode` 同一口径；非法值不静默切纸面）。"""
    st = _state_read(ctx, STATE_ENGINE)
    st.setdefault("state", "READY")
    st.setdefault("mode", "READ_ONLY")
    st.setdefault("trade_mode", "live")
    st.setdefault("version", "v0.1.0-dryrun")
    return st


def _load_kill_switch(ctx: Any) -> Dict[str, Any]:
    ks = _state_read(ctx, STATE_KILL)
    ks.setdefault("active", False)
    return ks


def _dashboard_total_asset(ctx: Any) -> Optional[float]:
    """`dashboard.json` 的 `account.total_asset`（manual_position 换算的兜底基准）。"""
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        return None
    try:
        dash = getter(PANEL_DASHBOARD)
    except Exception:  # noqa: BLE001 - 仪表盘缺失/未声明 → 兜底不可用（不猜）
        return None
    if not isinstance(dash, dict):
        return None
    account = dash.get("account")
    if not isinstance(account, dict):
        return None
    ta = account.get("total_asset")
    return float(ta) if isinstance(ta, (int, float)) and not isinstance(ta, bool) else None


# ---------------------------------------------------------------- 写（只经 ctx）


def _write_plan(ctx: Any, doc: Dict[str, Any]) -> None:
    writer = getattr(ctx, "panel_write", None)
    if not callable(writer):
        raise HTTPException(status_code=500, detail="ctx.panel_write 不可用")
    try:
        writer(PANEL_PLAN, doc)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="battle_plan 未在 data.write 声明：%s" % (exc,))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="battle_plan 写入失败：%s" % (exc,))


def _write_state(ctx: Any, name: str, doc: Dict[str, Any]) -> None:
    writer = getattr(ctx, "state_write", None)
    if not callable(writer):
        raise HTTPException(status_code=500, detail="ctx.state_write 不可用")
    try:
        writer(name, doc)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="%s 未在 data.state 声明：%s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="%s 写入失败：%s" % (name, exc))


def _save_engine_state(ctx: Any, st: Dict[str, Any]) -> None:
    st["updated_at"] = _now()
    _write_state(ctx, STATE_ENGINE, st)


def _save_kill_switch(ctx: Any, ks: Dict[str, Any]) -> None:
    ks["updated_at"] = _now()
    _write_state(ctx, STATE_KILL, ks)


# ---------------------------------------------------------------- 纯函数（照旧端）


def _signal_watch_orders(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """读/初始化 signal_watch.orders（缺段向后兼容：缺失或非列表时补空列表）。"""
    sw = data.get("signal_watch")
    if not isinstance(sw, dict):
        sw = data["signal_watch"] = {}
    orders = sw.get("orders")
    if not isinstance(orders, list):
        orders = sw["orders"] = []
    return orders


def _amount_from_position_cap(ctx: Any, data: Dict[str, Any], pct: float) -> float:
    """amount = pct% × 总仓位（battle_plan.position_cap，主人 2026-09-01 统一口径：
    金额基准一律用总仓位，不用总资产/可用仓位这类随盘面变动的量）；
    position_cap 缺失/负 → 回退总资产（dashboard.json，标注宽限）；仍不可得 → 0.0。
    0 = 合法值（AMV 极端档禁开新仓）→ amount 落 0，不回退总资产。
    """
    cap = data.get("position_cap")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap >= 0:
        return round(pct / 100.0 * float(cap), 2)
    ta = _dashboard_total_asset(ctx)
    if ta is not None and ta > 0:
        return round(pct / 100.0 * ta, 2)
    return 0.0


def _sync_kelly_to_cap(data: Dict[str, Any], total_cap: float) -> None:
    """总仓位变化时同步 buy_plan 每个预案的 kelly.size_amount（单票上限）。
    凯利服从总仓位（主人 2026-08-24 定）：单票目标 = 总仓位 × 50%，保留 kelly 其他字段。
    total_cap <= 0 → 不联动（总资产未就绪时 amount=0，等下次有效设置）。
    """
    if not total_cap or total_cap <= 0:
        return
    for it in data.get("buy_plan") or []:
        if not isinstance(it, dict):
            continue
        kelly = it.setdefault("kelly", {})
        kelly["size_amount"] = round(total_cap * 0.50, 2)
        kelly["basis_n"] = kelly.get("basis_n") or 0


def _find(items: Any, key: str, value: Any) -> Optional[Dict[str, Any]]:
    for it in items or []:
        if isinstance(it, dict) and it.get(key) == value:
            return it
    return None


# ---------------------------------------------------------------- 端点实现


def kill_switch(ctx: Any, body: KillBody) -> Dict[str, Any]:
    """急停升格总停（2026-08-28 主人裁决，第⑤件）。

    active=true：①快照 9 键 module_switches → engine_state.module_switches_snapshot
    ②battle_plan.module_switches 全置 false（producers 5s 重读现成链路生效）。
    active=false：从快照恢复各模块原状态 + 快照清除。
    面板/watchdog/QMT 保持运行（解除入口必须活着）；不撤真实挂单（纸面模拟）。

    **on→on 重复按：沿用既有快照**（防把全 false 快照进去致解除后永久停机）；
    on→off→on：off 已恢复+清快照，再 on 重新快照。
    """
    with _LOCK:
        now = _now()
        ks = _load_kill_switch(ctx)
        ks["active"] = bool(body.active)
        ks["triggered_at"] = now if body.active else None
        ks["reason"] = body.reason or ("主人急停" if body.active else "解除急停")
        st = _load_engine_state(ctx)
        snapshot_note = "（无作战表，未动 module_switches）"
        try:
            data: Optional[Dict[str, Any]] = _read_plan_for_write(ctx)
        except HTTPException as exc:
            # 急停不能被作战表读取问题挡住：读不到（缺失/损坏/未声明）就只写两个状态文件，
            # **绝不**拿空文档覆盖 battle_plan。如实记下没动 module_switches 的原因。
            _log(ctx, "battle_plan_rw /api/kill：作战表不可写（%s），跳过 module_switches"
                    % (exc.detail,))
            snapshot_note = "（作战表不可读：%s；未动 module_switches）" % (exc.detail,)
            data = None
        if data is not None:
            sw = data.get("module_switches")
            if not isinstance(sw, dict):
                sw = {}
            for k in MODULE_KEYS:      # 补全九键，与 /api/module_switch 同口径
                sw.setdefault(k, True)
            if body.active:
                if not isinstance(st.get("module_switches_snapshot"), dict):
                    # ①快照原状态（仅首次：on→on 不覆盖，防全 false 快照致解除后永久停机）
                    st["module_switches_snapshot"] = {k: bool(sw.get(k, True)) for k in MODULE_KEYS}
                    snapshot_note = "快照 8 键原状态"
                else:
                    snapshot_note = "沿用既有快照（防全 false 覆盖）"
                # ②全停九键（exit_stop/notify 含内：主人裁决总停语义）
                for k in MODULE_KEYS:
                    sw[k] = False
            else:
                snap = st.get("module_switches_snapshot")
                if isinstance(snap, dict):
                    # ④解除：逐键恢复原状态 + 快照清除
                    for k in MODULE_KEYS:
                        sw[k] = bool(snap.get(k, True))
                    st["module_switches_snapshot"] = None
                    snapshot_note = "从快照恢复 8 键原状态，快照已清除"
            data["module_switches"] = sw
            _write_plan(ctx, data)
        st["kill_switch_active"] = bool(body.active)
        st["state"] = "FROZEN" if body.active else "READY"
        _save_engine_state(ctx, st)
        ks.setdefault("history", []).append({   # 留痕（kill_switch.json 是事实源）
            "ts": now, "active": bool(body.active), "reason": ks["reason"],
            "snapshot": snapshot_note,
        })
        _save_kill_switch(ctx, ks)
        _log(ctx, "battle_plan_rw /api/kill：active=%s reason=%s %s"
                % (body.active, body.reason, snapshot_note))
    return {
        "ok": True,
        "kill_switch": ks,
        "engine_state": st["state"],
        "module_switches_snapshot": st.get("module_switches_snapshot"),
    }


def toggle_item(ctx: Any, body: ToggleBody) -> Dict[str, Any]:
    """逐预案/逐操作启停开关（JSON 是唯一事实源，纯本地落盘）。

    enabled=false = 该条已停止（不执行/不触发），保留签收与状态记录（非取消）；
    **不动 sign/status**，仅追加 status_history 留痕。
    """
    with _LOCK:
        data = _read_plan_for_write(ctx)
        now = _now()
        if body.item_type == "operations":
            if not body.ts_code:
                raise HTTPException(status_code=422, detail="operations 启停需提供 ts_code")
            target = _find(data.get("operations"), "ts_code", body.ts_code)
            if target is None:
                raise HTTPException(status_code=404, detail="操作条目不存在：%s" % (body.ts_code,))
            item_key = body.ts_code
        else:
            if not body.plan_id:
                raise HTTPException(status_code=422, detail="buy_plan 启停需提供 plan_id")
            target = _find(data.get("buy_plan"), "plan_id", body.plan_id)
            if target is None:
                raise HTTPException(status_code=404, detail="预案不存在：%s" % (body.plan_id,))
            item_key = body.plan_id
        target["enabled"] = bool(body.enabled)
        target.setdefault("status_history", []).append({
            "status": target.get("status") or target.get("execution_status") or "EXECUTING",
            "ts": now,
            "by": body.signer,
            "note": "主人启用" if body.enabled else "主人停止（enabled=false，不执行/不触发）",
        })
        _write_plan(ctx, data)
        _log(ctx, "battle_plan_rw /api/toggle：%s %s %s"
                % (body.item_type, item_key, "启用" if body.enabled else "停止"))
    return {
        "ok": True,
        "item_type": body.item_type,
        "item": item_key,
        "enabled": target["enabled"],
        "status": target.get("status") or target.get("execution_status"),
    }


def cancel_item(ctx: Any, body: CancelBody) -> Dict[str, Any]:
    """作战表条目取消（前端二次确认后调用）。

    buy_plan：按 plan_id → status=CANCELLED；
    operations：按 ts_code → execution_status=CANCELLED（同时写 status）。
    均追加 status_history 留痕。

    2026-11-07：签收链整体删除（主人拍板）——取消后不再重刷顶层 sign、不再联动
    engine_state（那套「未签收=引擎只读」的语义已不存在）。
    """
    with _LOCK:
        data = _read_plan_for_write(ctx)
        now = _now()
        if body.item_type == "operations":
            if not body.ts_code:
                raise HTTPException(status_code=422, detail="operations 取消需提供 ts_code")
            target = _find(data.get("operations"), "ts_code", body.ts_code)
            if target is None:
                raise HTTPException(status_code=404, detail="操作条目不存在：%s" % (body.ts_code,))
            target["execution_status"] = "CANCELLED"
            target["status"] = "CANCELLED"
            item_key = body.ts_code
        else:
            if not body.plan_id:
                raise HTTPException(status_code=422, detail="buy_plan 取消需提供 plan_id")
            target = _find(data.get("buy_plan"), "plan_id", body.plan_id)
            if target is None:
                raise HTTPException(status_code=404, detail="预案不存在：%s" % (body.plan_id,))
            target["status"] = "CANCELLED"
            item_key = body.plan_id
        target.setdefault("status_history", []).append({
            "status": "CANCELLED", "ts": now, "by": body.signer,
            "note": body.reason or "主人取消（二次确认）",
        })
        _write_plan(ctx, data)
        st = _load_engine_state(ctx)   # 2026-11-07：签收链已删，取消不再联动引擎状态
        _log(ctx, "battle_plan_rw /api/cancel：%s %s 已取消（reason=%s）"
                % (body.item_type, item_key, body.reason))
    return {
        "ok": True,
        "item_type": body.item_type,
        "item": item_key,
        "status": target.get("status") or target.get("execution_status"),
        "engine_state": st["state"],
    }


def signal_watch_toggle(ctx: Any, body: SignalWatchToggleBody) -> Dict[str, Any]:
    """择机单启停（只改 enabled 不动 status；无 status_history —— S3 契约字段不含）。

    `enabled` 未传 → 保持原值，**且不写盘**（旧端语义：只更新传入键）。
    """
    with _LOCK:
        data = _read_plan_for_write(ctx)
        orders = _signal_watch_orders(data)
        target = _find(orders, "watch_id", body.watch_id)
        if target is None:
            raise HTTPException(status_code=404, detail="择机单不存在：%s" % (body.watch_id,))
        if body.enabled is not None:
            target["enabled"] = bool(body.enabled)
            _write_plan(ctx, data)
            _log(ctx, "battle_plan_rw /api/signal_watch/toggle：%s %s"
                    % (body.watch_id, "启用" if body.enabled else "停止"))
    return {
        "ok": True,
        "watch_id": body.watch_id,
        "enabled": target.get("enabled", True),   # 缺省语义：未设置视为启用
        "status": target.get("status") or "WATCHING",
    }


def signal_watch_cancel(ctx: Any, body: SignalWatchCancelBody) -> Dict[str, Any]:
    """择机单取消：status→CANCELLED（不可逆；保留记录，展示=已取消灰）。不写 status_history。"""
    with _LOCK:
        data = _read_plan_for_write(ctx)
        orders = _signal_watch_orders(data)
        target = _find(orders, "watch_id", body.watch_id)
        if target is None:
            raise HTTPException(status_code=404, detail="择机单不存在：%s" % (body.watch_id,))
        target["status"] = "CANCELLED"
        _write_plan(ctx, data)
        _log(ctx, "battle_plan_rw /api/signal_watch/cancel：%s 已取消（reason=%s）"
                % (body.watch_id, body.reason))
    return {"ok": True, "watch_id": body.watch_id, "status": target["status"]}


def manual_position(ctx: Any, body: ManualPositionBody) -> Dict[str, Any]:
    """主人手动仓位（sizing=false 时生效，纯本地落盘）。

    amount = pct% × 总仓位（battle_plan.position_cap）；总仓位缺失 → 回退总资产，
    仍不可得 → 0（前端显示 ¥— 并提示「总仓位未就绪」）。换算基准只在服务端算，
    不让前端传，防双源不一致。
    """
    with _LOCK:
        data = _read_plan_for_write(ctx)
        mp = {
            "pct": round(body.pct, 2),
            "amount": round(_amount_from_position_cap(ctx, data, body.pct), 2),
            "updated_at": _now(),
        }
        data["manual_position"] = mp
        _sync_kelly_to_cap(data, mp["amount"])
        _write_plan(ctx, data)
        _log(ctx, "battle_plan_rw /api/manual_position：pct=%s%% amount=%.2f（sizing=false 手动仓位）"
                % (mp["pct"], mp["amount"]))
    return {"ok": True, "manual_position": mp}


# ---------------------------------------------------------------- module_switches_batch（M1 C1）


def module_switches_batch(ctx: Any, body: ModuleSwitchesBatchBody) -> Dict[str, Any]:
    """待确认批量提交（旧 `module_switches_batch`，`engine/server.py:7400` 逐条照搬）。

    前端：`index.html:9071` `confirmBatch()`（编辑态→确定：一次原子落盘）。

    语义（与旧端逐条对齐，不得“优化”）：
    1. `switches` 键名必须 ∈ `MODULE_KEYS`，非法 → 422（旧端 `invalid` 列表同文案）；
       只更新传入键，**未传保持原值**，落盘补全九键（缺省 true）。
    2. `manual_position.pct` 存在且 ∈[0,100]（bool/非数字/越界 → 422）；
       amount = pct% × position_cap（缺失/负 → 回退总资产，见 `_amount_from_position_cap`），
       并联动 `kelly.size_amount`（单票上限 = 总仓位×50%，同 `/manual_position`）。
    3. `notify_types` 每项 ∈ 9 枚举（非法 → 422）；**去重保序**落盘顶层。
    4. **空提交（无 switches / 无 manual_position / 无 notify_types）：不写盘**，
       直接返回现状（旧端 7426 行）。这是幂等保证：重复点「确定」不刷 `updated_at`。
    5. 一次写盘（`ctx.panel_write` = 备份+tmp+replace+审计），返回最新全量状态。

    与旧端的已知差异（见 HANDOFF §C1）：
    · 不写 `append_progress` 进度日志（那是旧进程内函数，本批统一改 `ctx.logger`）；
    · 不重建 `dashboard.json`（旧端本端点也不重建，防死循环铁律）；
    · 响应键与旧端逐键一致（`ok/module_switches/manual_position/notify_types`），
      不额外加 `changed`（旧端没有）。
    """
    invalid = [k for k in body.switches if k not in MODULE_KEYS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail="非法模块键：%s（可选 %s）" % (",".join(sorted(invalid)), "/".join(MODULE_KEYS)))
    mp = body.manual_position
    pct: Optional[float] = None
    if mp is not None:
        raw_pct = mp.get("pct") if isinstance(mp, dict) else None
        if isinstance(raw_pct, bool) or not isinstance(raw_pct, (int, float)) \
                or not (0 <= raw_pct <= 100):
            raise HTTPException(status_code=422,
                                detail="manual_position.pct 非法：%r（需 ∈[0,100]）" % (raw_pct,))
        pct = float(raw_pct)
    if body.notify_types is not None:
        unknown = [t for t in body.notify_types if t not in NOTIFY_TYPE_KEYS]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail="非法推送类型：%s（可选 %s）"
                       % (",".join(sorted(unknown)), "/".join(sorted(NOTIFY_TYPE_KEYS))))
    with _LOCK:
        data = _read_plan_for_write(ctx)
        # 4. 空提交：不写盘，只回现状（旧端 7426-7431）
        if not body.switches and mp is None and body.notify_types is None:
            return {"ok": True, "module_switches": data.get("module_switches"),
                    "manual_position": data.get("manual_position"),
                    "notify_types": data.get("notify_types")}
        sw = data.get("module_switches")
        if not isinstance(sw, dict):
            sw = {}
        for k in MODULE_KEYS:      # 补全九键（JSON 一致，缺省 true）
            sw.setdefault(k, True)
        sw.update({k: bool(v) for k, v in body.switches.items()})
        data["module_switches"] = sw
        if pct is not None:
            data["manual_position"] = {
                "pct": round(pct, 2),
                "amount": round(_amount_from_position_cap(ctx, data, pct), 2),
                "updated_at": _now(),
            }
            _sync_kelly_to_cap(data, data["manual_position"]["amount"])
        if body.notify_types is not None:
            data["notify_types"] = list(dict.fromkeys(body.notify_types))   # 去重保序
        _write_plan(ctx, data)
        changed = "、".join("%s=%s" % (k, "开" if v else "关") for k, v in body.switches.items())
        note = "battle_plan_rw /api/module_switches_batch：%s" % (changed or "仅仓位")
        if pct is not None:
            note += "；手动仓位 pct=%s%% amount=¥%.2f" % (
                data["manual_position"]["pct"], data["manual_position"]["amount"])
        if body.notify_types is not None:
            note += "；推送类型 %d/%d" % (len(data["notify_types"]), len(NOTIFY_TYPE_KEYS))
        _log(ctx, note)
    return {"ok": True, "module_switches": sw, "manual_position": data.get("manual_position"),
            "notify_types": data.get("notify_types")}


# ---------------------------------------------------------------- 路由（root 挂载）


def register(ctx: Any) -> APIRouter:
    """注册 7 条根路径路由（manifest `backend.mount = "root"` → 最终 `/api/<path>`）。"""
    router = APIRouter()

    @router.post("/kill")
    def _kill(body: KillBody) -> Dict[str, Any]:
        return kill_switch(ctx, body)

    @router.post("/toggle")
    def _toggle(body: ToggleBody) -> Dict[str, Any]:
        return toggle_item(ctx, body)

    @router.post("/cancel")
    def _cancel(body: CancelBody) -> Dict[str, Any]:
        return cancel_item(ctx, body)

    @router.post("/signal_watch/toggle")
    def _sw_toggle(body: SignalWatchToggleBody) -> Dict[str, Any]:
        return signal_watch_toggle(ctx, body)

    @router.post("/signal_watch/cancel")
    def _sw_cancel(body: SignalWatchCancelBody) -> Dict[str, Any]:
        return signal_watch_cancel(ctx, body)

    @router.post("/manual_position")
    def _manual_position(body: ManualPositionBody) -> Dict[str, Any]:
        return manual_position(ctx, body)

    @router.post("/module_switches_batch")
    def _module_switches_batch(body: ModuleSwitchesBatchBody) -> Dict[str, Any]:
        """待确认批量提交（旧 `engine/server.py:7400`）。前端 `index.html:9071`。"""
        return module_switches_batch(ctx, body)

    return router
