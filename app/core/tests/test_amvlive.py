# -*- coding: utf-8 -*-
"""`data.amv_live` / `ctx.amv_live` / `app/core/amvlive.py` 契约与门面测试（2026-10-24）。

三件事必须锁住：

1. manifest 字段解析：默认 False、显式 true 生效、非布尔 → ContractError、`data` 未知键 → ContractError、
   `public()` 透出；未声明调 `ctx.amv_live` → PermissionError；
2. 门面行为：超时/异常/空快照 → None + `last_error()` 如实给原因，**失败也写缓存**（防连点打穿 QMT），
   盘中 25s / 盘外 300s 两档 TTL；
3. `sys.path` 临时插 / 整体还原（旧仓模块 import 期自己插的条目也要被清掉）。

铁律：**任何用例都不真连 QMT** —— 一律 monkeypatch `_import_target` 或把 `paths.WS` 指到 tmp 的假
`executor` 包；`stocks_snapshot` 恒为假函数。
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import amvlive  # noqa: E402
from app.core.contract import (  # noqa: E402
    ContractError,
    PluginContext,
    validate_manifest,
)

SNAP = {
    "date": "2026-10-26",
    "asof": "2026-10-26 10:31:00",
    "state_date": "2026-10-23",
    "all_amv": 5.0e12,
    "base_all": 4.9e12,
    "stocks": {"600000": [1.0e9, 1.0e8, 1.1e8, 1.23, 4.0e8]},
}


# ---------------------------------------------------------------- 固件


def _manifest(flag: object = "absent", extra: dict | None = None) -> dict:
    data: dict = {"read": [], "panel": [], "write": []}
    if flag != "absent":
        data["amv_live"] = flag
    if extra:
        data.update(extra)
    return {
        "manifest_version": 1,
        "id": "amvprobe",
        "name": "实时 AMV 探针",
        "version": "0.0.1",
        "min_core_version": "0.1.0",
        "enabled": True,
        "kind": "domain",
        "backend": {"module": "backend", "entry": "register", "mount": "plugin"},
        "data": data,
    }


@pytest.fixture()
def pdir(tmp_path: Path) -> Path:
    d = tmp_path / "amvprobe"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后清门面缓存（模块级 _STATE 跨用例残留会让断言读到上一条结果）。"""
    amvlive.reset_cache()
    yield
    amvlive.reset_cache()


class FakeModule(types.SimpleNamespace):
    """假旧仓模块：只有 `stocks_snapshot`，调用次数记在 `calls` 里。"""

    def __init__(self, doc=None, exc=None, delay=0.0):
        self.calls = 0
        self._doc = doc
        self._exc = exc
        self._delay = delay
        super().__init__()

    def stocks_snapshot(self):
        self.calls += 1
        if self._delay:
            time.sleep(self._delay)
        if self._exc is not None:
            raise self._exc
        return self._doc


def _use_module(monkeypatch: pytest.MonkeyPatch, module) -> None:
    """把门面的懒加载换成假模块（不碰真 `executor.amv_live`、不碰 QMT）。"""
    monkeypatch.setattr(amvlive, "_import_target", lambda: module)


# ---------------------------------------------------------------- manifest 解析


def test_amv_live_defaults_false(pdir: Path) -> None:
    m = validate_manifest(_manifest(), pdir)
    assert m.amv_live is False
    assert m.public()["data"]["amv_live"] is False


def test_amv_live_true_parsed(pdir: Path) -> None:
    m = validate_manifest(_manifest(True), pdir)
    assert m.amv_live is True
    assert m.public()["data"]["amv_live"] is True


@pytest.mark.parametrize("bad", [1, 0, "true", "1", None, [], {}])
def test_amv_live_must_be_bool(pdir: Path, bad) -> None:
    """非布尔 → ContractError（不静默当 False/True）。对齐 data.feed / data.paper / data.ops_run。"""
    with pytest.raises(ContractError) as excinfo:
        validate_manifest(_manifest(bad), pdir)
    assert "data.amv_live" in str(excinfo.value)


def test_data_unknown_key_rejected(pdir: Path) -> None:
    """`data` 出现未知字段（如把 amv_live 拼错）→ 拒绝加载，不静默忽略。"""
    with pytest.raises(ContractError) as excinfo:
        validate_manifest(_manifest(extra={"amvlive": True}), pdir)
    assert "amvlive" in str(excinfo.value)


def test_public_data_keys_unchanged_for_other_plugins(pdir: Path) -> None:
    """新增键只增不改：已有键一个都不能少（旧前端/旧测试按名字取值）。"""
    keys = set(validate_manifest(_manifest(), pdir).public()["data"])
    for name in ("read", "panel", "json", "lib", "quotes", "feed", "paper",
                 "text", "ops_run", "amv_live", "write", "state", "misc"):
        assert name in keys, name


# ---------------------------------------------------------------- 白名单闸


def test_ctx_amv_live_permission(pdir: Path) -> None:
    """未声明 `data.amv_live` → PermissionError；声明 true → 拿到 core 模块句柄。"""
    denied = PluginContext(validate_manifest(_manifest(), pdir))
    with pytest.raises(PermissionError) as excinfo:
        _ = denied.amv_live
    assert "data.amv_live" in str(excinfo.value)

    allowed = PluginContext(validate_manifest(_manifest(True), pdir))
    assert allowed.amv_live is amvlive


def test_ctx_amv_live_is_module_handle_not_snapshot(pdir: Path) -> None:
    """门面返回**模块句柄**（`snapshot()` / `last_error()`），不是一次快照结果。

    理由：调用方要能先拿快照、失败后再取原因，一次调用回两个值会把「失败」和「没数据」搅在一起。
    """
    ctx = PluginContext(validate_manifest(_manifest(True), pdir))
    handle = ctx.amv_live
    assert callable(handle.snapshot) and callable(handle.last_error)


# ---------------------------------------------------------------- 门面行为


def test_snapshot_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = FakeModule(doc=SNAP)
    _use_module(monkeypatch, mod)
    assert amvlive.snapshot() == SNAP
    assert amvlive.last_error() is None
    assert mod.calls == 1


def test_snapshot_none_records_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧仓返回 None（QMT 未开 / 空快照）→ None + `last_error()=="empty"`（旧端同文案）。"""
    _use_module(monkeypatch, FakeModule(doc=None))
    assert amvlive.snapshot() is None
    assert amvlive.last_error() == "empty"


def test_snapshot_exception_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """异常 → None + `"<ExcType>: <msg>"`，**不抛**（面板不能因为 QMT 挂了整卡 500）。"""
    _use_module(monkeypatch, FakeModule(exc=RuntimeError("qmt:connect-fail")))
    assert amvlive.snapshot() is None
    err = amvlive.last_error()
    assert err and err.startswith("RuntimeError:") and "qmt:connect-fail" in err


def test_snapshot_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时 → None + `"timeout"`；**不 kill** 后台线程（旧端同范式）。"""
    monkeypatch.setattr(amvlive, "JOIN_TIMEOUT_S", 0.05)
    _use_module(monkeypatch, FakeModule(doc=SNAP, delay=0.4))
    t0 = time.time()
    assert amvlive.snapshot() is None
    assert time.time() - t0 < 0.35, "join 上限必须真的生效，不能等慢线程跑完"
    assert amvlive.last_error() == "timeout"


def test_snapshot_bad_shape_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """形状不符（缺 date / stocks / all_amv）→ 当坏数据，不用半截 doc 充实时。"""
    for bad in ({"date": "2026-10-26"}, {"stocks": {}, "all_amv": 1.0},
                {"date": "2026-10-26", "stocks": [], "all_amv": 1.0},
                {"date": "2026-10-26", "stocks": {}, "all_amv": "1.0"}):
        amvlive.reset_cache()
        _use_module(monkeypatch, FakeModule(doc=bad))
        assert amvlive.snapshot() is None, bad
        assert amvlive.last_error() == "bad_snapshot", bad


def test_failure_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败也进缓存：连点两次只打一次旧仓（防 QMT 未开时被面板连点打穿）。"""
    mod = FakeModule(exc=RuntimeError("boom"))
    _use_module(monkeypatch, mod)
    assert amvlive.snapshot() is None
    assert amvlive.snapshot() is None
    assert mod.calls == 1


def test_success_cached_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = FakeModule(doc=SNAP)
    _use_module(monkeypatch, mod)
    amvlive.snapshot()
    amvlive.snapshot()
    assert mod.calls == 1
    # 手动把缓存时间往前推过 TTL → 重算
    amvlive._STATE["ts"] = time.time() - 10_000.0
    amvlive.snapshot()
    assert mod.calls == 2


def test_ttl_two_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    """盘中 60s / 盘外 300s（2026-11-19 主人：盘中改 60s 与 tick_feed 快照同频，避免双取数相撞）。"""
    assert amvlive.TTL_IN_SESSION == 60.0
    assert amvlive.TTL_OFF_SESSION == 300.0
    assert amvlive.JOIN_TIMEOUT_S == 6.0

    mod = FakeModule(doc=SNAP)
    _use_module(monkeypatch, mod)

    monkeypatch.setattr(amvlive, "in_session", lambda: True)
    amvlive.snapshot()
    amvlive._STATE["ts"] = time.time() - 70.0     # 超过 60s → 盘中重算
    amvlive.snapshot()
    assert mod.calls == 2

    monkeypatch.setattr(amvlive, "in_session", lambda: False)
    amvlive._STATE["ts"] = time.time() - 30.0     # 不到 300s → 盘外沿用旧帧
    amvlive.snapshot()
    assert mod.calls == 2


def test_in_session_falls_back_to_weekday_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """日历不可用 → 工作日 09:30–15:00 兜底（任务书指定口径），不抛。"""

    def _boom() -> bool:
        raise RuntimeError("日历炸了")

    monkeypatch.setattr(amvlive, "_probe_in_session", _boom)
    # 兜底值取决于当前时刻，只断言「返回 bool 且不抛」
    assert isinstance(amvlive.in_session(), bool)

    monkeypatch.setattr(amvlive, "_fallback_in_session", lambda: True)
    monkeypatch.setattr(amvlive, "SESSION_TTL_S", 0.0)   # 禁用 20s 缓存，强制重新探测
    assert amvlive.in_session() is True

    monkeypatch.setattr(amvlive, "_fallback_in_session", lambda: False)
    assert amvlive.in_session() is False


def test_session_probe_timeout_uses_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """日历探测超过上限 → 按兜底口径立刻给答案，不把请求线程卡在 eltdx 上。"""
    engine = tmp_path / "engine"
    (engine / "common").mkdir(parents=True)
    (engine / "common" / "__init__.py").write_text("", encoding="utf-8")
    (engine / "common" / "trading_calendar.py").write_text(
        "import time\n"
        "def in_trading_session(now=None):\n"
        "    time.sleep(0.5)\n"
        "    return True\n",
        encoding="utf-8")
    monkeypatch.setattr(amvlive.paths, "WS", tmp_path)
    monkeypatch.setattr(amvlive, "SESSION_PROBE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(amvlive, "_fallback_in_session", lambda: False)
    for name in list(sys.modules):
        if name == "common" or name.startswith("common."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    t0 = time.time()
    assert amvlive._probe_in_session() is False, "超时 → 走兜底，不能等到日历返回 True"
    assert time.time() - t0 < 0.4


def test_session_probe_uses_calendar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """日历可用 → 采信日历结果（不是恒用兜底）。"""
    engine = tmp_path / "engine"
    (engine / "common").mkdir(parents=True)
    (engine / "common" / "__init__.py").write_text("", encoding="utf-8")
    (engine / "common" / "trading_calendar.py").write_text(
        "def in_trading_session(now=None):\n"
        "    return True\n",
        encoding="utf-8")
    monkeypatch.setattr(amvlive.paths, "WS", tmp_path)
    monkeypatch.setattr(amvlive, "_fallback_in_session", lambda: False)
    for name in list(sys.modules):
        if name == "common" or name.startswith("common."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    before = list(sys.path)
    assert amvlive._probe_in_session() is True
    assert sys.path == before, "探测也要还原 sys.path"


def test_sys_path_restored_after_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """真实 import 路径：临时插 `<WS>/engine`，用完**只删本次新增条目**（含旧仓自己插的）。"""
    engine = tmp_path / "engine"
    (engine / "executor").mkdir(parents=True)
    (engine / "executor" / "__init__.py").write_text("", encoding="utf-8")
    (engine / "executor" / "amv_live.py").write_text(
        "import sys\n"
        "sys.path.insert(0, 'C:/definitely/not/here')\n"
        "def stocks_snapshot():\n"
        "    return {'date': '2026-10-26', 'asof': 'x', 'all_amv': 1.0, 'stocks': {}}\n",
        encoding="utf-8")
    monkeypatch.setattr(amvlive.paths, "WS", tmp_path)
    for name in list(sys.modules):
        if name == "executor" or name.startswith("executor."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    before = list(sys.path)
    doc = amvlive.snapshot()
    assert doc and doc["date"] == "2026-10-26"
    assert sys.path == before, "sys.path 必须还原成原样（含旧仓模块自己插的条目）"
    assert "C:/definitely/not/here" not in sys.path
    assert str(engine) not in sys.path


def test_import_target_reports_import_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """旧仓不可 import（目录不存在）→ 门面照样不抛，`last_error()` 里能看到原因。

    2026-11-09：本测试在**全量**跑时才红（单跑绿）——前序测试把真实 engine 目录
    留在了 sys.path，于是 worker 真去 import 旧仓并卡到 6s 超时，错误变成 "timeout"。
    这里显式把「真实 engine 目录」从 sys.path 摘掉（monkeypatch 负责还原），
    让本测试稳定处于「旧仓不存在」的状态，不被测试顺序影响。
    """
    try:
        engine_dir = (Path(amvlive.paths.WS) / "engine").resolve()
    except Exception:  # noqa: BLE001 - WS 不可解析就不做这项清理
        engine_dir = None
    if engine_dir is not None:
        filtered = [p for p in sys.path
                    if Path(p or ".").resolve() != engine_dir]
        monkeypatch.setattr(sys, "path", filtered)
    monkeypatch.setattr(amvlive.paths, "WS", tmp_path)   # tmp 下没有 engine/executor
    for name in list(sys.modules):
        if name == "executor" or name.startswith("executor."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    assert amvlive.snapshot() is None
    err = amvlive.last_error()
    assert err and "ModuleNotFoundError" in err


def test_concurrent_callers_use_stale_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """上一轮还在算（inflight）→ 直接返回旧帧，不排队、不重复打旧仓。

    2026-11-16：以前用 `_LOCK.acquire()` 表达“在飞”，但 join 超时后锁被立即还回去，
    而后台 worker 还在跑 → 下一拍又起一个（N 个并发全量快照互抢，盘初全超时）。
    现改为独立的 `inflight` 标记，本用例直接置位模拟“还在算”。
    """
    mod = FakeModule(doc=SNAP)
    _use_module(monkeypatch, mod)
    amvlive._STATE["inflight"] = True
    amvlive._STATE["inflight_at"] = time.time()
    try:
        assert amvlive.snapshot() is None      # 旧帧为空
        assert mod.calls == 0
    finally:
        amvlive._STATE["inflight"] = False


def test_snapshot_timeout_keeps_stale_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时**不清**已有实时帧，且后台算完自己落到缓存（2026-11-16 根因固定）。

    旧行为：join 超时后 `_STATE["doc"] = None`，把上一帧成功的实时快照一起冲掉——
    盘初数据源一慢（>6s），面板就从实时掉回收盘口径，且下一拍从零重来，爬不回来。
    """
    _use_module(monkeypatch, FakeModule(doc=SNAP))
    assert amvlive.snapshot() == SNAP
    amvlive._STATE["ts"] = time.time() - 10_000.0        # 越过 TTL

    fresh = dict(SNAP, asof="2026-10-26 10:32:00")
    slow = FakeModule(doc=fresh, delay=0.5)
    _use_module(monkeypatch, slow)
    monkeypatch.setattr(amvlive, "JOIN_TIMEOUT_S", 0.05)

    t0 = time.time()
    assert amvlive.snapshot() is SNAP, "超时只能返回旧帧，不能清空"
    assert time.time() - t0 < 0.4, "join 上限必须真的生效"
    assert amvlive.last_error() == "timeout"

    deadline = time.time() + 3.0                          # 等后台跑完
    while amvlive._STATE["inflight"] and time.time() < deadline:
        time.sleep(0.02)
    assert amvlive._STATE["inflight"] is False
    assert slow.calls == 1
    assert amvlive.snapshot() is fresh, "后台结果已在缓存里，下一拍直接命中"
    assert slow.calls == 1, "命中缓存，不该再打一次旧仓"
    assert amvlive.last_error() is None


def test_timeout_does_not_stack_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时后 worker 仍在跑 → 下一拍不能再起一个（防并发叠乘）。"""
    mod = FakeModule(doc=SNAP, delay=0.4)
    _use_module(monkeypatch, mod)
    monkeypatch.setattr(amvlive, "JOIN_TIMEOUT_S", 0.05)
    assert amvlive.snapshot() is None
    assert mod.calls == 1
    assert amvlive.snapshot() is None                     # 仍在飞 → 旧帧，不再起 worker
    assert mod.calls == 1
    deadline = time.time() + 3.0
    while amvlive._STATE["inflight"] and time.time() < deadline:
        time.sleep(0.02)
    assert amvlive.snapshot() == SNAP
    assert mod.calls == 1


def test_inflight_stale_released(monkeypatch: pytest.MonkeyPatch) -> None:
    """inflight 超上限（worker 卡死）→ 允许重起，不把面板永久冻在旧帧上。"""
    mod = FakeModule(doc=SNAP)
    _use_module(monkeypatch, mod)
    amvlive._STATE["inflight"] = True
    amvlive._STATE["inflight_at"] = time.time() - (amvlive.INFLIGHT_MAX_S + 1.0)
    assert amvlive.snapshot() == SNAP
    assert mod.calls == 1
