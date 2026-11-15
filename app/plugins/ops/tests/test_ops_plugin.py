# -*- coding: utf-8 -*-
"""ops 插件契约测试（N2，2026-10-24）。

覆盖方式：**真实 core**（`validate_manifest` + `load_plugin` 的 `PluginContext`），
只把 `paths.WRITABLE_STATE` / `paths.READABLE_PANEL` / `paths.OUT_DIR` 指到 `tmp_path`，
因此测的是实际的白名单校验 + 备份 + 原子替换 + 审计，而不是假 ctx 的复刻。

覆盖点：
1. manifest：mount=root、data.state / data.write / data.panel / data.ops_run 声明齐全、版本 0.2.0；
2. 路由表：10 条路径齐全、不带 `/api` 前缀（core 负责拼）、写端点只在这些路径上；
3. ui_notes 四件套语义：id 规则、html/style 截断、部分更新、resolve 流转、422/404 分支；
4. `GET /ops.json`：ops_status.json 原样透传、缺失 404、`refresh` 参数兼容；
5. `POST /ops/run`：透传 `key`/`task`、缺名 400、白名单闸未声明 500、旧仓缺失 503
   （**全程 mock `run_manual`，绝不真跑 schtasks**）；
6. trade_mode：缺省 live、非法值 422、写保留其他键、同值不写盘（幂等）；
7. alert_plan toggle/delete：404 分支、保留未知键、`meta.updated_at` 刷新；
8. 静态禁令：无直连库 / 自连行情源 / 子进程 / 自开写句柄（插件侧）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import contract as core_contract  # noqa: E402
from app.core import paths as core_paths  # noqa: E402
from app.core.contract import (  # noqa: E402
    PluginContext,
    find_forbidden_db_usage,
    find_forbidden_quote_usage,
    load_manifest,
)
from app.plugins.ops import backend  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parents[1]
BACKEND_PY = PLUGIN_DIR / "backend.py"
MANIFEST = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))

EXPECTED_PATHS = {
    "/ui_notes/list", "/ui_notes/add", "/ui_notes/update", "/ui_notes/resolve",
    # 2026-10-27 标注生命周期：阶段流转 + history（契约见 test_ops_ui_notes_stage.py）
    "/ui_notes/stage",
    # 2026-11-06 标注可删：单条 delete + 按阶段批量 purge（契约见 test_ops_ui_notes_delete.py）
    "/ui_notes/delete", "/ui_notes/purge",
    "/ops.json", "/ops/run",
    "/trade_mode",
    "/alert_plan/toggle", "/alert_plan/delete",
    # 2026-10-24 M1 补缺（A1~A4 + B1/B2 的 HTTP 壳）；A4 `/sector/restart` 2026-10-30 随 8002 下线摘除
    "/panic", "/amv_refresh", "/sector_track_backfill",
    "/kline-indicator-settings", "/kline/{ts_code}/xdxr",
}


# ---------------------------------------------------------------- 夹具


class Env:
    """把三份被写文件与两份只读文件都指到 tmp，返回 (ctx, client, 路径字典)。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        state_dir = tmp_path / "engine_state"
        panel_dir = tmp_path / "panel"
        state_dir.mkdir(parents=True)
        panel_dir.mkdir(parents=True)
        self.state = {
            "ui_notes": state_dir / "ui_notes.json",
            "engine_state": state_dir / "engine_state.json",
            "kill_switch": state_dir / "kill_switch.json",
        }
        self.panel = {
            "ops": panel_dir / "ops_status.json",
            "alert_plan_persistent": panel_dir / "alert_plan_persistent.json",
        }
        self.write_panel = {"alert_plan": self.panel["alert_plan_persistent"]}
        for name, path in self.state.items():
            monkeypatch.setitem(core_paths.WRITABLE_STATE, name, path)
        for name, path in self.panel.items():
            monkeypatch.setitem(core_paths.READABLE_PANEL, name, path)
        for name, path in self.write_panel.items():
            monkeypatch.setitem(core_paths.WRITABLE_PANEL, name, path)
        monkeypatch.setattr(core_paths, "OUT_DIR", tmp_path / "out")

        self.manifest = load_manifest(PLUGIN_DIR)
        self.ctx = PluginContext(self.manifest)
        app = FastAPI()
        app.include_router(backend.register(self.ctx), prefix="/api")
        self.client = TestClient(app)

    # ---- 造数据 / 读数据

    def put_state(self, name: str, obj: object) -> Path:
        path = self.state[name]
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return path

    def put_panel(self, name: str, obj: object) -> Path:
        path = self.panel[name]
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return path

    def read_state(self, name: str) -> object:
        return json.loads(self.state[name].read_text(encoding="utf-8"))

    def read_panel(self, name: str) -> object:
        return json.loads(self.panel[name].read_text(encoding="utf-8"))

    def backups(self, name: str) -> list[Path]:
        bdir = core_paths.OUT_DIR / "backups" / name
        return sorted(bdir.glob("*.json")) if bdir.is_dir() else []


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


# ---------------------------------------------------------------- 1. manifest / 路由


def test_manifest_declarations() -> None:
    assert MANIFEST["id"] == "ops"
    assert MANIFEST["version"] == "0.3.4"
    assert MANIFEST["backend"]["mount"] == "root"
    data = MANIFEST["data"]
    assert set(data["state"]) == {"ui_notes", "engine_state"}
    assert data["write"] == ["alert_plan"]
    assert data["ops_run"] is True
    assert set(data["panel"]) == {"ops", "alert_plan_persistent", "panic_breadth"}
    # M1 B1：设置文件读写双侧声明（读 READABLE_JSON、写 WRITABLE_MISC）
    assert data["json"] == ["kline_indicator_settings"]
    assert data["misc"] == ["kline_indicator_settings"]
    # M1 A1：恐慌广度要 eltdx 快照
    assert data["quotes"] is True


def test_manifest_in_whitelists() -> None:
    """声明名字必须同时存在于 core 全局白名单（加载期两边都校验）。"""
    for name in MANIFEST["data"]["state"]:
        assert name in core_paths.WRITABLE_STATE, name
    for name in MANIFEST["data"]["write"]:
        assert name in core_paths.WRITABLE_PANEL, name
    for name in MANIFEST["data"]["panel"]:
        assert name in core_paths.READABLE_PANEL, name
    for name in MANIFEST["data"].get("json", []):
        assert name in core_paths.READABLE_JSON, name
    for name in MANIFEST["data"].get("misc", []):
        assert name in core_paths.WRITABLE_MISC, name


def test_routes_complete(env: Env) -> None:
    router = backend.register(env.ctx)
    paths = {r.path for r in router.routes}
    assert paths == EXPECTED_PATHS
    assert all(not p.startswith("/api") for p in paths), "前缀由 core 拼，插件不自带 /api"
    methods: dict[str, set[str]] = {}
    for route in router.routes:
        methods.setdefault(route.path, set()).update(set(route.methods) - {"HEAD"})
    assert methods["/ui_notes/list"] == {"GET"}
    assert methods["/ops.json"] == {"GET"}
    # POST /trade_mode 与 GET /trade_mode 共用同一路径（两个路由条目）
    assert methods["/trade_mode"] == {"GET", "POST"}
    trade = [r for r in router.routes if r.path == "/trade_mode"]
    assert len(trade) == 2
    for p in ("/ui_notes/add", "/ui_notes/update", "/ui_notes/resolve",
              "/ui_notes/delete", "/ui_notes/purge",
              "/ops/run", "/alert_plan/toggle", "/alert_plan/delete"):
        assert methods[p] == {"POST"}, p


# ---------------------------------------------------------------- 2. ui_notes 四件套


def test_ui_notes_list_empty_when_file_missing(env: Env) -> None:
    body = env.client.get("/api/ui_notes/list").json()
    assert body["ok"] is True and body["notes"] == [] and body["open"] == 0
    # 2026-10-27：列表响应增 stats（全量阶段/页面统计）；空库也要给零值形状，前端不用判空
    assert body["stats"]["total"] == 0
    assert set(body["stats"]["by_stage"]) == {"open", "doing", "fixed", "verified", "rejected"}
    assert body["stats"]["by_page"] == {}


def test_ui_notes_add_semantics(env: Env) -> None:
    env.put_state("ui_notes", {"notes": [{"id": "old", "status": "resolved"}]})
    payload = {
        "selector": "#panel-dashboard .kpi:nth-child(1)", "elem": "DIV 总资产",
        "text": "  总资产字号再大一号  ", "page": "#dashboard",
        "x": 100.4, "y": 200.6,
        "html": "H" * 900, "style": "S" * 500,
    }
    body = env.client.post("/api/ui_notes/add", json=payload).json()
    assert body["ok"] is True
    note = body["note"]
    # id 规则：N + %Y%m%d-%H%M%S + "-" + (现有条数 % 1000)
    assert note["id"].startswith("N")
    assert note["id"].endswith("-1"), note["id"]
    assert len(note["id"]) == 1 + 8 + 1 + 6 + 1 + 1
    assert note["text"] == "总资产字号再大一号"          # strip
    assert note["x"] == 100 and note["y"] == 201         # int(round())
    assert len(note["html"]) == backend.HTML_MAX         # 截断 800
    assert len(note["style"]) == backend.STYLE_MAX       # 截断 400
    assert note["status"] == "open" and note["resolve_note"] == ""
    assert body["open"] == 1

    doc = env.read_state("ui_notes")
    assert len(doc["notes"]) == 2 and doc["notes"][1]["id"] == note["id"]
    assert doc["notes"][0] == {"id": "old", "status": "resolved"}, "未知条目原样保留"
    # 备份 + 审计（走的是真实 ctx.state_write）
    assert env.backups("ui_notes"), "写前必须备份"


def test_ui_notes_add_rejects_blank_text(env: Env) -> None:
    res = env.client.post("/api/ui_notes/add", json={"text": "   "})
    assert res.status_code == 422 and res.json()["detail"] == "标注内容不能为空"
    assert not env.state["ui_notes"].exists(), "校验不过不许写"


def test_ui_notes_add_keeps_other_top_keys(env: Env) -> None:
    env.put_state("ui_notes", {"notes": [], "schema": "x", "meta": {"a": 1}})
    env.client.post("/api/ui_notes/add", json={"text": "abc"})
    doc = env.read_state("ui_notes")
    assert doc["schema"] == "x" and doc["meta"] == {"a": 1}


def test_ui_notes_update_partial(env: Env) -> None:
    env.put_state("ui_notes", {"notes": [{
        "id": "N1", "selector": "#a", "page": "/", "text": "旧文案", "status": "open",
        "x": 1, "y": 2,
    }]})
    body = env.client.post("/api/ui_notes/update", json={"id": "N1", "text": " 新文案 "}).json()
    assert body["ok"] is True
    note = body["note"]
    assert note["text"] == "新文案"
    assert "edited_at" in note
    # selector / page / 位置不动（位置变了算新标注）
    assert note["selector"] == "#a" and note["page"] == "/" and note["x"] == 1
    assert note["status"] == "open"


def test_ui_notes_update_404_and_422(env: Env) -> None:
    env.put_state("ui_notes", {"notes": []})
    res = env.client.post("/api/ui_notes/update", json={"id": "nope", "text": "x"})
    assert res.status_code == 404 and "nope" in res.json()["detail"]
    res = env.client.post("/api/ui_notes/update", json={"id": "nope", "text": ""})
    assert res.status_code == 422


def test_ui_notes_resolve(env: Env) -> None:
    env.put_state("ui_notes", {"notes": [{"id": "N9", "text": "t", "status": "open",
                                          "resolve_note": ""}]})
    body = env.client.post("/api/ui_notes/resolve",
                           json={"id": "N9", "note": "已改字号"}).json()
    assert body["note"]["status"] == "resolved"
    assert body["note"]["resolve_note"] == "已改字号"
    assert "resolved_at" in body["note"]
    assert body["note"]["text"] == "t", "resolve 不动正文"
    # 列表按 status 过滤 + open 计数
    assert env.client.get("/api/ui_notes/list").json()["open"] == 0
    assert env.client.get("/api/ui_notes/list?status=open").json()["notes"] == []
    assert len(env.client.get("/api/ui_notes/list?status=resolved").json()["notes"]) == 1


def test_ui_notes_resolve_404(env: Env) -> None:
    env.put_state("ui_notes", {"notes": []})
    res = env.client.post("/api/ui_notes/resolve", json={"id": "gone"})
    assert res.status_code == 404


def test_ui_notes_corrupt_file_refuses_write(env: Env) -> None:
    env.state["ui_notes"].write_text("{not json", encoding="utf-8")
    res = env.client.post("/api/ui_notes/add", json={"text": "abc"})
    assert res.status_code == 503 and "拒绝" in res.json()["detail"]
    assert env.state["ui_notes"].read_text(encoding="utf-8") == "{not json", "坏文件不得被覆盖"


# ---------------------------------------------------------------- 3. /ops.json


_OPS_SAMPLE = {
    "meta": {"updated_at": "2026-10-23 20:00:13", "today": "2026-10-23", "counts": {"tasks": 22, "daemons": 16}},
    "summary": {"tasks_total": 22, "tasks_fail": 0},
    "tasks": [{"id": "t1", "name": "盘后链", "status": "OK"}],
    "daemons": [{"id": "d1", "name": "8800", "status": "UP"}],
    "freshness": [{"key": "amv_live", "label": "AMV", "state": "新鲜"}],
}


def test_ops_json_passthrough(env: Env) -> None:
    env.put_panel("ops", _OPS_SAMPLE)
    body = env.client.get("/api/ops.json").json()
    assert body == _OPS_SAMPLE, "只读透传，不改形状不补键"


def test_ops_json_refresh_param_is_compatible(env: Env) -> None:
    env.put_panel("ops", _OPS_SAMPLE)
    assert env.client.get("/api/ops.json?refresh=1").json() == _OPS_SAMPLE


def test_ops_json_missing_404(env: Env) -> None:
    res = env.client.get("/api/ops.json")
    assert res.status_code == 404
    assert "ops_status.json" in res.json()["detail"]


# ---------------------------------------------------------------- 4. /ops/run（core 门面，全程 mock）
#
# 铁律：测试**绝不**真跑 schtasks。这里把 core `ops_run` 模块的 `_load_legacy` 换成假模块，
# 连真旧仓文件都不碰。


def _fake_legacy(calls: list[str], result: dict | None = None):
    """造一个假旧仓模块（只有 run_manual）。"""
    import types

    def run_manual(name: str) -> dict:
        calls.append(name)
        return result if result is not None else {
            "ok": True, "reason": "ok", "task": name,
            "ts": "2026-10-24 13:30:00",
            "message": "已触发 %s（任务已提交，稍后刷新看状态）" % name,
        }

    return types.SimpleNamespace(run_manual=run_manual)


def test_ops_run_passes_name_through(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import ops_run as core_ops_run

    calls: list[str] = []
    monkeypatch.setattr(core_ops_run, "_load_legacy", lambda: _fake_legacy(calls))
    body = env.client.post("/api/ops/run", json={"key": "OH_AMV_Board_Update"}).json()
    assert calls == ["OH_AMV_Board_Update"]
    assert body["ok"] is True and body["task"] == "OH_AMV_Board_Update"
    # 旧仓返回体原样透传（不补键、不改名）
    assert set(body) == {"ok", "reason", "task", "ts", "message"}


def test_ops_run_accepts_task_alias(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧端 body 收 `key` 或 `task`（`key or task`），两个都认。"""
    from app.core import ops_run as core_ops_run

    calls: list[str] = []
    monkeypatch.setattr(core_ops_run, "_load_legacy", lambda: _fake_legacy(calls))
    env.client.post("/api/ops/run", json={"task": "OH_Fupan"})
    assert calls == ["OH_Fupan"]


def test_ops_run_missing_name_400(env: Env) -> None:
    for payload in ({}, {"key": "   "}, {"key": "", "task": ""}):
        res = env.client.post("/api/ops/run", json=payload)
        assert res.status_code == 400, payload
        assert "缺少 key" in res.json()["detail"]


def test_ops_run_whitelist_gate(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """manifest 没声明 `data.ops_run: true` → PermissionError → 500（不落到底层）。"""
    from app.core import ops_run as core_ops_run
    from app.core.contract import PluginContext, validate_manifest

    calls: list[str] = []
    monkeypatch.setattr(core_ops_run, "_load_legacy", lambda: _fake_legacy(calls))
    raw = json.loads(json.dumps(MANIFEST))
    raw["data"].pop("ops_run", None)
    ctx = PluginContext(validate_manifest(raw, PLUGIN_DIR))
    with pytest.raises(PermissionError):
        ctx.ops_run("OH_Fupan")
    assert calls == [], "未声明就不许碰底层"


def test_ops_run_gate_through_http(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 层同一条闸：声明缺失 → 500 + detail 点出 data.ops_run。"""
    from app.core import ops_run as core_ops_run
    from app.core.contract import PluginContext, validate_manifest

    calls: list[str] = []
    monkeypatch.setattr(core_ops_run, "_load_legacy", lambda: _fake_legacy(calls))
    raw = json.loads(json.dumps(MANIFEST))
    raw["data"].pop("ops_run", None)
    ctx = PluginContext(validate_manifest(raw, PLUGIN_DIR))
    app = FastAPI()
    app.include_router(backend.register(ctx), prefix="/api")
    res = TestClient(app).post("/api/ops/run", json={"key": "OH_Fupan"})
    assert res.status_code == 500
    assert "data.ops_run" in res.json()["detail"]
    assert calls == []


def test_ops_run_legacy_missing_503(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧仓聚合器不存在 → 503 + 明确路径（不谎报已触发）。"""
    from app.core import ops_run as core_ops_run

    monkeypatch.setattr(core_ops_run, "LEGACY_OPS_STATUS",
                        Path("Z:/nope/engine/ops/ops_status.py"))
    core_ops_run.reset_cache()
    res = env.client.post("/api/ops/run", json={"key": "OH_Fupan"})
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "旧仓聚合器不存在" in detail and "ops_status.py" in detail


def test_ops_run_legacy_import_error_503(env: Env, monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    """旧仓文件存在但导入失败（语法错）→ 503 且带异常类型。"""
    from app.core import ops_run as core_ops_run

    bad = tmp_path / "ops_status.py"
    bad.write_text("def run_manual(\n", encoding="utf-8")
    monkeypatch.setattr(core_ops_run, "LEGACY_OPS_STATUS", bad)
    core_ops_run.reset_cache()
    res = env.client.post("/api/ops/run", json={"key": "OH_Fupan"})
    assert res.status_code == 503
    assert "导入失败" in res.json()["detail"]


def test_ops_run_legacy_without_run_manual_503(env: Env, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
    from app.core import ops_run as core_ops_run

    bad = tmp_path / "ops_status.py"
    bad.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(core_ops_run, "LEGACY_OPS_STATUS", bad)
    core_ops_run.reset_cache()
    res = env.client.post("/api/ops/run", json={"key": "OH_Fupan"})
    assert res.status_code == 503
    assert "缺少 run_manual" in res.json()["detail"]


def test_ops_run_reports_refusal_verbatim(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧仓拒绝（白名单外/冷却/运行中）→ 原样透传 ok:false + reason，HTTP 仍 200。

    白名单判定单源在旧仓（不复制）；这里锁住「不擅自改写为 4xx/5xx」这一层。
    """
    from app.core import ops_run as core_ops_run

    denied = {"ok": False, "reason": "not_whitelisted",
              "message": "不可手动触发：QMT_AutoBuy_Morning（仅数据类白名单；交易链红线严禁）"}
    calls: list[str] = []
    monkeypatch.setattr(core_ops_run, "_load_legacy",
                        lambda: _fake_legacy(calls, result=denied))
    res = env.client.post("/api/ops/run", json={"key": "QMT_AutoBuy_Morning"})
    assert res.status_code == 200
    assert res.json() == denied
    assert calls == ["QMT_AutoBuy_Morning"], "拒绝判定由旧仓做，新端不预筛"


def test_ops_run_timeout_returns_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时 → `{ok:false, reason:'timeout'}`，且不杀已经在跑的那次调用。"""
    import time as _time

    from app.core import ops_run as core_ops_run

    class _Slow:
        def run_manual(self, name: str) -> dict:
            _time.sleep(0.3)
            return {"ok": True, "task": name}

    monkeypatch.setattr(core_ops_run, "_load_legacy", lambda: _Slow())
    out = core_ops_run.run("OH_Fupan", timeout_s=0.05)
    assert out["ok"] is False and out["reason"] == "timeout"
    assert out["task"] == "OH_Fupan"
    _time.sleep(0.4)   # 让慢调用自己跑完，确认没被强杀（无异常抛出）


def test_ops_run_rejects_blank_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import ops_run as core_ops_run

    monkeypatch.setattr(core_ops_run, "_load_legacy", lambda: _fake_legacy([]))
    with pytest.raises(ValueError):
        core_ops_run.run("   ")


def test_ops_run_module_never_shadows_subprocess() -> None:
    """core 门面不得把「任意子进程」能力暴露给插件。

    口径演变：M1 之前断言全文无 `subprocess`；2026-10-30 8002 下线回归严格口径；
    2026-11-04 主人要「选股页刷新」按钮，新增 `run_script`（命令全由旧仓 `MANUAL_SCRIPTS`
    白名单决定）→ 严格口径收窄为：
      · 计划任务路径仍只能经旧仓 `run_manual`，不得自带白名单；
      · `subprocess` 只允许在 `run_script` 内出现，不得出现在其它函数；
      · 不得再出现 `run_daemons` / `amv_dashboard`（那一整套只为 8002 存在）。
    """
    import ast

    src = (ROOT / "app" / "core" / "ops_run.py").read_text(encoding="utf-8")
    assert "run_manual" in src
    assert "MANUAL_WHITELIST =" not in src and "_MANUAL_COOLDOWN_S =" not in src
    assert "run_daemons" not in src and "amv_dashboard" not in src, \
        "8002 拉起链路已删除，不应回潮"
    tree = ast.parse(src)
    # 子进程调用只允许落在 `run_script` 的源码区间内（含它内部的线程体）；
    # 文档/注释里提到 subprocess 不算（它们在解释「插件看不见它」）
    rs = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "run_script")
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            if not (rs.lineno <= node.lineno <= (rs.end_lineno or rs.lineno)):
                offenders.append(node.lineno)
    assert not offenders, \
        "subprocess 只允许出现在 run_script 内（含其内部线程体），越界行：%s" % offenders


# ---------------------------------------------------------------- 5. trade_mode


def test_trade_mode_default_live(env: Env) -> None:
    assert env.client.get("/api/trade_mode").json()["trade_mode"] == "live"


def test_trade_mode_illegal_value_defaults_live(env: Env) -> None:
    env.put_state("engine_state", {"trade_mode": "BOGUS", "state": "READY"})
    body = env.client.get("/api/trade_mode").json()
    assert body["trade_mode"] == "live" and body["engine_state"] == "READY"


def test_trade_mode_write_preserves_other_keys(env: Env) -> None:
    env.put_state("engine_state", {
        "state": "ARMED", "mode": "ARMED", "battle_plan_signed": True,
        "kill_switch_active": False, "version": "v0.1.0-dryrun",
        "trade_mode": "live", "updated_at": "2026-10-20 10:00:00",
    })
    body = env.client.post("/api/trade_mode", json={"trade_mode": "paper"}).json()
    assert body["ok"] is True and body["trade_mode"] == "paper" and body["changed"] is True
    doc = env.read_state("engine_state")
    assert doc["trade_mode"] == "paper"
    assert doc["state"] == "ARMED" and doc["mode"] == "ARMED"
    assert doc["battle_plan_signed"] is True and doc["version"] == "v0.1.0-dryrun"
    assert doc["updated_at"] != "2026-10-20 10:00:00", "updated_at 要刷新"
    assert env.backups("engine_state"), "写前必须备份"
    # GET 与写后一致
    assert env.client.get("/api/trade_mode").json()["trade_mode"] == "paper"


def test_trade_mode_rejects_illegal_value(env: Env) -> None:
    env.put_state("engine_state", {"trade_mode": "paper"})
    res = env.client.post("/api/trade_mode", json={"trade_mode": "模拟"})
    assert res.status_code == 422 and "非法 trade_mode" in res.json()["detail"]
    assert env.read_state("engine_state")["trade_mode"] == "paper", "非法值不许落盘"


def test_trade_mode_same_value_is_noop(env: Env) -> None:
    path = env.put_state("engine_state", {"trade_mode": "paper", "updated_at": "2026-10-20 10:00:00"})
    before_text = path.read_text(encoding="utf-8")
    before_mtime = path.stat().st_mtime_ns
    body = env.client.post("/api/trade_mode", json={"trade_mode": "paper"}).json()
    assert body["changed"] is False
    assert path.read_text(encoding="utf-8") == before_text
    assert path.stat().st_mtime_ns == before_mtime, "同值重放不得改 mtime"
    assert env.backups("engine_state") == [], "同值重放不得产生备份"


# ---------------------------------------------------------------- 6. alert_plan


def _alert_doc() -> dict:
    return {
        "meta": {"persistent": True, "updated_at": "2026-10-20 13:49:44", "source": "add_alert.py"},
        "plans": [
            {"dp_id": "AL-1", "enabled": True, "name": "破位预警", "switch": "keep"},
            {"dp_id": "AL-2", "enabled": False, "name": "黄线预警"},
        ],
    }


def test_alert_toggle(env: Env) -> None:
    env.put_panel("alert_plan_persistent", _alert_doc())
    body = env.client.post("/api/alert_plan/toggle",
                           json={"dp_id": "AL-1", "enabled": False}).json()
    assert body == {"ok": True, "dp_id": "AL-1", "enabled": False}
    doc = env.read_panel("alert_plan_persistent")
    assert doc["plans"][0]["enabled"] is False
    assert doc["plans"][0]["switch"] == "keep", "未知键原样保留"
    assert doc["plans"][1]["enabled"] is False, "不碰其他条目"
    assert doc["meta"]["updated_at"] != "2026-10-20 13:49:44"
    assert doc["meta"]["persistent"] is True
    assert env.backups("alert_plan"), "写前必须备份（WRITABLE_PANEL 名 alert_plan）"


def test_alert_delete(env: Env) -> None:
    env.put_panel("alert_plan_persistent", _alert_doc())
    body = env.client.post("/api/alert_plan/delete", json={"dp_id": "AL-2"}).json()
    assert body == {"ok": True, "dp_id": "AL-2", "remaining": 1}
    doc = env.read_panel("alert_plan_persistent")
    assert [p["dp_id"] for p in doc["plans"]] == ["AL-1"]


def test_alert_unknown_dp_id_404(env: Env) -> None:
    env.put_panel("alert_plan_persistent", _alert_doc())
    for path in ("/api/alert_plan/toggle", "/api/alert_plan/delete"):
        res = env.client.post(path, json={"dp_id": "NOPE"})
        assert res.status_code == 404, path
        assert "NOPE" in res.json()["detail"]
    assert env.read_panel("alert_plan_persistent") == _alert_doc(), "404 分支不许写盘"


def test_alert_file_missing_404(env: Env) -> None:
    for path in ("/api/alert_plan/toggle", "/api/alert_plan/delete"):
        res = env.client.post(path, json={"dp_id": "AL-1"})
        assert res.status_code == 404
        assert "长期预警文件不存在" in res.json()["detail"]


def test_alert_dp_id_required(env: Env) -> None:
    env.put_panel("alert_plan_persistent", _alert_doc())
    assert env.client.post("/api/alert_plan/toggle", json={}).status_code == 422


# ---------------------------------------------------------------- 7. state_json 契约（core 新方法）


def test_state_json_requires_manifest_declaration(env: Env) -> None:
    m = core_contract.validate_manifest({
        "manifest_version": 1, "id": "ops", "name": "x", "version": "0.1.0",
        "min_core_version": "0.1.0", "enabled": True, "kind": "domain",
        "backend": {"module": "backend", "entry": "register", "mount": "root"},
        "data": {"state": ["engine_state"]},
    }, PLUGIN_DIR)
    ctx = PluginContext(m)
    with pytest.raises(PermissionError):
        ctx.state_json("ui_notes")


def test_state_json_missing_file_and_bad_top(env: Env) -> None:
    with pytest.raises(FileNotFoundError):
        env.ctx.state_json("ui_notes")
    env.state["ui_notes"].write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        env.ctx.state_json("ui_notes")


# ---------------------------------------------------------------- 8. 静态禁令


def test_no_forbidden_sources() -> None:
    source = BACKEND_PY.read_text(encoding="utf-8")
    assert find_forbidden_db_usage(source) == []
    assert find_forbidden_quote_usage(source) == []
    for token in ("subprocess.", "Popen(", "os.system(", "urllib.request.urlopen",
                  "requests.post", "requests.put", "json.dump(", ".write_text(",
                  ".write_bytes(", "os.replace(", "os.remove(", ".mkdir("):
        assert token not in source, "插件不得自带写/进程/网络通道: %s" % token
    # 写只走两条受控通道
    assert "ctx.state_write(" in source and "ctx.panel_write(" in source
