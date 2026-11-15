"""插件总线契约：manifest 校验、PluginContext、加载器。

唯一权威：docs/CONTRACT.md（manifest_version=1）。
本模块只做数据与接口校验，不认识任何业务域。
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import logging
import re
import sqlite3
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter

from app.core import paths

CORE_VERSION = "0.3.1"
MANIFEST_VERSION = 1

PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CARD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# config 里出现这些键名时，对外一律打码（/_plugins 不回传敏感值）
SENSITIVE_KEY_RE = re.compile(r"(token|secret|passw|credential|api[_-]?key|private|auth)", re.I)

# CONTRACT §4：插件源码禁止直连数据库；契约测试用本常量做静态扫描
FORBIDDEN_DB_PATTERNS: tuple[str, ...] = (
    "sqlite3.connect",
    "pymysql",
    "psycopg",
    "mysql.connector",
    "create_engine",
)

# CONTRACT §8.1：口径库必须是纯函数库（import 期无副作用）；加载时静态扫描这些模式。
# 只拦「模块顶层真的去连库/开文件/发网络/起线程」的写法；函数体内的 open() 由人工审。
FORBIDDEN_LIB_IMPORT_PATTERNS: tuple[str, ...] = (
    "sqlite3.connect",
    "pymysql",
    "psycopg",
    "create_engine",
    "urllib.request.urlopen",
    "requests.get",
    "requests.post",
    "socket.socket",
    "threading.Thread",
    "subprocess.Popen",
    "subprocess.run",
)

MANIFEST_FILENAME = "plugin.json"

# manifest.data 允许出现的字段（全部已实现并被 ctx 读取）。未知字段一律拒绝加载：
# 写错字段名（如 `amv_live` 拼成 `amvlive`）会让插件静默丢能力，方向与「未声明则 PermissionError」相反。
_KNOWN_DATA_KEYS: frozenset[str] = frozenset({
    "read", "panel", "json", "lib", "quotes", "feed", "paper",
    "text", "ops_run", "amv_live", "write", "state", "misc",
})


class ContractError(Exception):
    """manifest 或插件加载违反 docs/CONTRACT.md。"""


# ---------------------------------------------------------------- 版本


def _ver_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for seg in str(value).split("."):
        m = re.match(r"\d+", seg.strip())
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def version_cmp(left: str, right: str) -> int:
    """比较点分版本；返回 -1/0/1。非数字段按 0 处理。"""
    a, b = _ver_tuple(left), _ver_tuple(right)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


# ---------------------------------------------------------------- manifest


@dataclass(frozen=True)
class BackendSpec:
    module: str = "backend"
    entry: str = "register"
    timeout_ms: int = 5000
    permissions: tuple[str, ...] = ()
    # 挂载位置："plugin"（默认，/api/p/<id>）/ "root"（/api，兼容旧前端根路径）。
    # root 仅给兼容旧面板的插件用；路径冲突启动即报错（见 main.py）。
    mount: str = "plugin"


@dataclass(frozen=True)
class CardSpec:
    id: str
    title: str
    slot: str = "main"
    order: int = 100


@dataclass(frozen=True)
class FrontendSpec:
    module: str = "frontend.js"
    cards: tuple[CardSpec, ...] = ()


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    min_core_version: str
    enabled: bool
    kind: str
    backend: BackendSpec
    frontend: FrontendSpec
    data_read: tuple[str, ...]
    panel_read: tuple[str, ...]
    json_read: tuple[str, ...]
    lib_read: tuple[str, ...]
    quotes_read: bool
    # `data.feed`：QMT 整推行情（`ctx.feed`）。默认 False —— 未声明就用不了，
    # 避免插件偷偷订阅 QMT（core 只有一个订阅者，见 docs/CONTRACT.md §4.3）。
    feed_read: bool
    # `data.paper`：纸面交易服务（`ctx.paper`，4.1.3b 新增）。默认 False —— 未声明就用不了。
    # 业务逻辑全在 `app/core/paper/`，插件只拿服务句柄做 HTTP 参数校验与 JSON 装配。
    paper_read: bool
    # `data.amv_live`：实时 AMV 逐股快照（`ctx.amv_live`，2026-10-24 新增）。默认 False。
    # 数据源是旧仓 `engine/executor/amv_live.py::stocks_snapshot()`（重模块 numpy/amv_store + 读 QMT），
    # 与 `ctx.quotes` / `ctx.feed` 同理：只允许 core 持有，插件不得自己 import。
    amv_live: bool
    # `data.text`：只读**文本目录**白名单（4.2 新增，`ctx.text_dir`）。
    # 背景：`/api/concept.json` 的数据源是 `夏天-activity/概念追踪/` 这类 Markdown 知识库目录，
    # 不是 `data/panel` 单文件。只登记目录、只给 Path、**不提供任何写方法**。
    text_read: tuple[str, ...]
    # `data.ops_run`：手动触发计划任务（`ctx.ops_run`，2026-10-24 N2 追补）。
    # 默认 False —— 这只手会起 Windows 计划任务（跨进程），必须显式声明才能用。
    ops_run: bool
    data_write: tuple[str, ...]
    # `data.state`：可写的引擎状态文件名（engine_state/kill_switch/ui_notes，`ctx.state_write`）。
    state_write: tuple[str, ...]
    # `data.misc`：可写的缓存类小文件（`ctx.misc_write`，2026-10-24 M1 新增）。
    # 与 data.write / data.state 分开：目标是 `xiaoshi_cache/` 这类工具缓存，引擎不读。
    misc_write: tuple[str, ...]
    config: dict[str, Any]
    depends: tuple[str, ...]
    directory: Path
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def panel_write(self) -> tuple[str, ...]:
        """可写的生产 JSON 名（= manifest 的 `data.write`）。

        单独开一个别名是为了在 `ctx.panel_write()` 里读起来对称：
        `panel_read` / `panel_write` 一个读一个写，都是白名单。
        """
        return self.data_write

    def public(self) -> dict[str, Any]:
        """对外清单：不含敏感值、不含原始 manifest 全量。"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "min_core_version": self.min_core_version,
            "enabled": self.enabled,
            "kind": self.kind,
            "backend": {
                "module": self.backend.module,
                "entry": self.backend.entry,
                "timeout_ms": self.backend.timeout_ms,
                "permissions": list(self.backend.permissions),
            },
            "frontend": {
                "module": self.frontend.module,
                "base": f"/plugins/{self.id}/",
                "cards": [
                    {"id": c.id, "title": c.title, "slot": c.slot, "order": c.order}
                    for c in self.frontend.cards
                ],
            },
            "data": {
                "read": list(self.data_read),
                "panel": list(self.panel_read),
                "json": list(self.json_read),
                "lib": list(self.lib_read),
                "quotes": self.quotes_read,
                "feed": self.feed_read,
                "paper": self.paper_read,
                "text": list(self.text_read),
                "ops_run": self.ops_run,
                "amv_live": self.amv_live,
                "write": list(self.data_write),
                "state": list(self.state_write),
                "misc": list(self.misc_write),
            },
            "config": scrub(self.config),
            "depends": list(self.depends),
        }


def scrub(value: Any) -> Any:
    """递归打码 config 中的敏感键。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, val in value.items():
            out[key] = "***" if SENSITIVE_KEY_RE.search(str(key)) else scrub(val)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ContractError(f"manifest 缺少字段: {key}")
    return data[key]


def _as_str(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"manifest 字段 {key} 必须是非空字符串")
    return value.strip()


def _as_list(value: Any, key: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"manifest 字段 {key} 必须是数组")
    return list(value)


def _parse_backend(raw: Any) -> BackendSpec:
    if not isinstance(raw, dict):
        raise ContractError("manifest 字段 backend 必须是对象")
    module = _as_str(raw.get("module", "backend"), "backend.module")
    if not MODULE_RE.match(module):
        raise ContractError(f"backend.module 非法: {module}")
    entry = _as_str(raw.get("entry", "register"), "backend.entry")
    if not MODULE_RE.match(entry):
        raise ContractError(f"backend.entry 非法: {entry}")
    timeout_ms = raw.get("timeout_ms", 5000)
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
        raise ContractError("backend.timeout_ms 必须是正整数")
    perms = _as_list(raw.get("permissions", []), "backend.permissions")
    for p in perms:
        if not isinstance(p, str):
            raise ContractError("backend.permissions 只能包含字符串")
    # mount：只有 plugin / root 两个取值。写错就报错，不静默回退。
    mount = raw.get("mount", "plugin")
    if not isinstance(mount, str) or mount.lower() not in ("plugin", "root"):
        raise ContractError("backend.mount 只支持 'plugin' / 'root'，实际: %r" % (mount,))
    return BackendSpec(module=module, entry=entry, timeout_ms=timeout_ms,
                       permissions=tuple(perms), mount=str(mount).lower())


def _parse_frontend(raw: Any) -> FrontendSpec:
    if raw is None:
        return FrontendSpec()
    if not isinstance(raw, dict):
        raise ContractError("manifest 字段 frontend 必须是对象")
    module = _as_str(raw.get("module", "frontend.js"), "frontend.module")
    if "/" in module or "\\" in module or module.startswith("."):
        raise ContractError("frontend.module 只能是插件目录内的文件名")
    cards_raw = _as_list(raw.get("cards", []), "frontend.cards")
    cards: list[CardSpec] = []
    seen: set[str] = set()
    for item in cards_raw:
        if not isinstance(item, dict):
            raise ContractError("frontend.cards 每项必须是对象")
        cid = _as_str(_require(item, "id"), "frontend.cards[].id")
        if not CARD_ID_RE.match(cid):
            raise ContractError(f"卡片 id 非法: {cid}")
        if cid in seen:
            raise ContractError(f"卡片 id 重复: {cid}")
        seen.add(cid)
        title = _as_str(_require(item, "title"), "frontend.cards[].title")
        slot = item.get("slot", "main")
        if not isinstance(slot, str) or not slot:
            raise ContractError("frontend.cards[].slot 必须是非空字符串")
        order = item.get("order", 100)
        if not isinstance(order, int) or isinstance(order, bool):
            raise ContractError("frontend.cards[].order 必须是整数")
        cards.append(CardSpec(id=cid, title=title, slot=slot, order=order))
    return FrontendSpec(module=module, cards=tuple(cards))


def validate_manifest(
    data: Any,
    directory: Path,
    core_version: str = CORE_VERSION,
) -> Manifest:
    """校验 manifest 纯数据；不合法直接抛 ContractError。"""
    if not isinstance(data, dict):
        raise ContractError("plugin.json 顶层必须是对象")

    mv = _require(data, "manifest_version")
    if mv != MANIFEST_VERSION:
        raise ContractError(f"manifest_version 不支持: {mv!r}（核心只接受 {MANIFEST_VERSION}）")

    pid = _as_str(_require(data, "id"), "id")
    if not PLUGIN_ID_RE.match(pid):
        raise ContractError(f"插件 id 非法: {pid}（只允许 [a-z][a-z0-9_]*）")
    if pid != directory.name:
        raise ContractError(f"id 与目录名不一致: id={pid} dir={directory.name}")

    name = _as_str(_require(data, "name"), "name")
    version = _as_str(_require(data, "version"), "version")

    min_core = _as_str(_require(data, "min_core_version"), "min_core_version")
    if version_cmp(min_core, core_version) > 0:
        raise ContractError(f"min_core_version={min_core} 高于核心版本 {core_version}")

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ContractError("manifest 字段 enabled 必须是布尔值")

    kind = data.get("kind", "domain")
    if not isinstance(kind, str) or not kind.strip():
        raise ContractError("manifest 字段 kind 必须是非空字符串")

    backend = _parse_backend(_require(data, "backend"))
    frontend = _parse_frontend(data.get("frontend"))

    data_block = data.get("data", {})
    if not isinstance(data_block, dict):
        raise ContractError("manifest 字段 data 必须是对象")
    data_read = _as_list(data_block.get("read", []), "data.read")
    panel_read = _as_list(data_block.get("panel", []), "data.panel")
    json_read = _as_list(data_block.get("json", []), "data.json")
    lib_read = _as_list(data_block.get("lib", []), "data.lib")
    quotes_read = data_block.get("quotes", False)
    if not isinstance(quotes_read, bool):
        raise ContractError("data.quotes 必须是布尔值")
    # data.feed：QMT 整推行情（`ctx.feed`）。默认 False，且必须显式写布尔。
    feed_read = data_block.get("feed", False)
    if not isinstance(feed_read, bool):
        raise ContractError("data.feed 必须是布尔值")
    # data.paper：纸面交易服务（`ctx.paper`，4.1.3b）。默认 False，同样必须显式写布尔。
    paper_read = data_block.get("paper", False)
    if not isinstance(paper_read, bool):
        raise ContractError("data.paper 必须是布尔值")
    # data.amv_live：实时 AMV 逐股快照（`ctx.amv_live`，2026-10-24 新增）。
    # 默认 False，同样必须显式写布尔。旧端 `/api/amv_research.json?live=1` 的数据源。
    amv_live = data_block.get("amv_live", False)
    if not isinstance(amv_live, bool):
        raise ContractError("data.amv_live 必须是布尔值")
    # data.text：只读文本目录白名单（4.2 新增）。默认空数组，每项必须字符串。
    text_read = _as_list(data_block.get("text", []), "data.text")
    # data.ops_run：手动触发计划任务（2026-10-24 N2 追补）。默认 False，且必须显式写布尔。
    ops_run = data_block.get("ops_run", False)
    if not isinstance(ops_run, bool):
        raise ContractError("data.ops_run 必须是布尔值")
    # data.amv_live：实时 AMV 逐股快照（`ctx.amv_live`）。默认 False，必须显式写布尔。
    # 与其它开关不同：它拉起的是旧仓重模块（numpy/amv_store）+ QMT 全市场 tick，
    # 输出**不进任何生产文件**，只供 `/api/amv_research.json?live=1` 当分子。
    #
    # data 未知键：写错字段名（如 `amv_live` 拼成 `amvlive`）会让插件静默失去能力，
    # 与「未声明就 PermissionError」是相反方向的失效，一律拒绕加载、不静默忽略。
    unknown_data = sorted(set(data_block) - _KNOWN_DATA_KEYS)
    if unknown_data:
        raise ContractError(
            f"data 含未知字段: {unknown_data}（允许 {sorted(_KNOWN_DATA_KEYS)}）")
    data_write = _as_list(data_block.get("write", []), "data.write")
    # data.state：引擎状态文件写白名单（2026-10-24 新增，engine_state/kill_switch/ui_notes）。
    # 与 data.write 分开：目标是 engine/state/ 下的旧系统状态文件，语义见 ctx.state_write()。
    state_write = _as_list(data_block.get("state", []), "data.state")
    # data.misc：缓存类小文件写白名单（2026-10-24 M1，`ctx.misc_write`）。
    misc_write = _as_list(data_block.get("misc", []), "data.misc")
    for item in (data_read + panel_read + json_read + lib_read + text_read
                 + data_write + state_write + misc_write):
        if not isinstance(item, str):
            raise ContractError(
                "data.read / data.panel / data.json / data.lib / data.text / data.write 只能包含字符串")
    # 2026-10-23：从「v1 必须为空」改为「写白名单」。
    #
    # 背景：迁移旧后端全部端点时，自选池增删改、签收、急停、下单这些**写操作**必须能落盘。
    # 旧的“插件零写入”保证在这个前提下不可能保留，所以改为**受控写**：
    #   • 只能写 `paths.WRITABLE_PANEL` 里登记的文件（当前只有 watchlist.json）；
    #   • 写之前强制备份、原子替换（tmp + os.replace）、记录审计日志；
    #   • 插件必须在 `data.write` 里显式声明名字，两边对不上就拒绝加载。
    # 这把“回滚零数据成本”改成“写范围受控 + 每笔可回溯”，
    # 仍然不允许插件自由写任何路径。
    unknown_write = [n for n in data_write if n not in paths.WRITABLE_PANEL]
    if unknown_write:
        raise ContractError(
            f"data.write 含白名单外的文件: {unknown_write}（允许 {sorted(paths.WRITABLE_PANEL)}）")
    unknown_state = [n for n in state_write if n not in paths.WRITABLE_STATE]
    if unknown_state:
        raise ContractError(
            f"data.state 含白名单外的状态文件: {unknown_state}（允许 {sorted(paths.WRITABLE_STATE)}）")
    unknown_misc = [n for n in misc_write if n not in paths.WRITABLE_MISC]
    if unknown_misc:
        raise ContractError(
            f"data.misc 含白名单外的缓存文件: {unknown_misc}（允许 {sorted(paths.WRITABLE_MISC)}）")
    unknown = [n for n in data_read if n not in paths.READABLE_DB]
    if unknown:
        raise ContractError(f"data.read 含白名单外的库: {unknown}（允许 {sorted(paths.READABLE_DB)}）")
    unknown_panel = [n for n in panel_read if n not in paths.READABLE_PANEL]
    if unknown_panel:
        raise ContractError(
            f"data.panel 含白名单外的 JSON: {unknown_panel}（允许 {sorted(paths.READABLE_PANEL)}）"
        )
    unknown_json = [n for n in json_read if n not in paths.READABLE_JSON]
    if unknown_json:
        raise ContractError(
            f"data.json 含白名单外的 JSON: {unknown_json}（允许 {sorted(paths.READABLE_JSON)}）"
        )
    unknown_lib = [n for n in lib_read if n not in paths.READABLE_LIB]
    if unknown_lib:
        raise ContractError(
            f"data.lib 含白名单外的口径库: {unknown_lib}（允许 {sorted(paths.READABLE_LIB)}）"
        )
    # data.text：只读文本目录白名单（4.2 新增）。未登记 → 拒绝加载，不静默降级。
    unknown_text = [n for n in text_read if n not in paths.READABLE_TEXT_DIR]
    if unknown_text:
        raise ContractError(
            f"data.text 含白名单外的文本目录: {unknown_text}"
            f"（允许 {sorted(paths.READABLE_TEXT_DIR)}）"
        )

    config = data.get("config", {})
    if not isinstance(config, dict):
        raise ContractError("manifest 字段 config 必须是对象")

    depends = _as_list(data.get("depends", []), "depends")
    if depends:
        raise ContractError(f"v1 不支持 depends: {depends}")

    return Manifest(
        id=pid,
        name=name,
        version=version,
        min_core_version=min_core,
        enabled=enabled,
        kind=kind.strip(),
        backend=backend,
        frontend=frontend,
        data_read=tuple(data_read),
        panel_read=tuple(panel_read),
        json_read=tuple(json_read),
        lib_read=tuple(lib_read),
        quotes_read=quotes_read,
        feed_read=feed_read,
        paper_read=paper_read,
        text_read=tuple(text_read),
        ops_run=ops_run,
        amv_live=amv_live,
        data_write=tuple(data_write),
        state_write=tuple(state_write),
        misc_write=tuple(misc_write),
        config=copy.deepcopy(config),
        depends=tuple(depends),
        directory=directory,
        raw=copy.deepcopy(data),
    )


def load_manifest(plugin_dir: Path, core_version: str = CORE_VERSION) -> Manifest:
    """读并校验 <plugin_dir>/plugin.json。"""
    plugin_dir = Path(plugin_dir)
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ContractError(f"缺少 {MANIFEST_FILENAME}: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"plugin.json 不是合法 JSON: {exc}") from exc
    return validate_manifest(raw, plugin_dir, core_version=core_version)


# ---------------------------------------------------------------- ctx


class PluginContext:
    """插件唯一入口：id / config / data_dir / db_read / panel_json / json_read / lib / logger。

    v1 只给只读数据库与只读 JSON；写数据一律落 data_dir（data/panel_v2/<id>/）。
    口径库（v1.2）：只给已登记 + 已声明的纯函数库。
    """

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._logger = logging.getLogger(f"panel_v2.plugin.{manifest.id}")
        self._data_dir: Path | None = None
        self._libs: dict[str, Any] = {}

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def config(self) -> dict[str, Any]:
        """manifest.config 的副本；改它不影响清单。"""
        return copy.deepcopy(self.manifest.config)

    @property
    def data_dir(self) -> Path:
        if self._data_dir is None:
            self._data_dir = paths.plugin_out_dir(self.manifest.id)
        return self._data_dir

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def panel_json(self, name: str) -> dict[str, Any]:
        """只读加载白名单 panel JSON；名字必须同时出现在 manifest.data.panel 与全局白名单。"""
        if name not in self.manifest.panel_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.panel 声明 {name}")
        path = paths.panel_path(name)
        if path is None:
            raise PermissionError(f"{name} 不在核心只读 panel 白名单内")
        if not path.is_file():
            raise FileNotFoundError(f"panel JSON 不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"panel JSON 顶层必须是对象: {path}")
        return data

    def json_read(self, name: str) -> Any:
        """只读加载白名单生产 JSON（非 data/panel）；名字必须同时出现在 manifest.data.json 与全局白名单。

        与 panel_json 的区别：panel_json 只服务 data/panel/*.json（dict）；
        本方法服务其他已有生产产物（如 hcl_latest / stock_names），返回原始 JSON（dict 或 list）。
        """
        if name not in self.manifest.json_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.json 声明 {name}")
        path = paths.json_path(name)
        if path is None:
            raise PermissionError(f"{name} 不在核心只读 JSON 白名单内")
        if not path.is_file():
            raise FileNotFoundError(f"JSON 不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def text_dir(self, name: str) -> Path:
        """只读**文本目录**白名单（4.2 新增）：名字必须同时出现在 manifest.data.text 与全局白名单。

        用途：给 `/api/concept.json` 这类「数据源是一整个 Markdown 知识库目录」的插件开一条
        **目录级只读**通道（`READABLE_DB` 是库、`READABLE_JSON` 是单文件，都不适用）。

        安全边界：
        • **只读**：只返回 `Path`，核心不提供任何写方法；插件仍然禁止 `open(..., "w")`
          （`tools/cutover_gate.py` 的「插件无写代码」门禁继续有效）。
        • 三重校验：未在 `manifest.data.text` 声明 → `PermissionError`；
          未登记在 `paths.READABLE_TEXT_DIR` → `PermissionError`；
          登记了但磁盘上不存在 → `FileNotFoundError`（不静默返回空目录）。
        """
        if name not in self.manifest.text_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.text 声明 {name}")
        path = paths.text_dir(name)
        if path is None:
            raise PermissionError(f"{name} 不在核心只读文本目录白名单内")
        if not path.is_dir():
            raise FileNotFoundError(f"文本目录不存在: {path}")
        return path

    def db_read(self, name: str) -> sqlite3.Connection:
        if name not in self.manifest.data_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.read 声明 {name}")
        path = paths.db_path(name)
        if path is None:
            raise PermissionError(f"{name} 不在核心只读白名单内")
        if not path.is_file():
            raise FileNotFoundError(f"数据库不存在: {path}")
        uri = "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    # ------------------------------------------------------------ 交易日时钟（2026-11-07）

    @property
    def trade_clock(self) -> Any:
        """交易日时钟门面（只读）：`is_trading_day` / `expected_data_date8` / `postclose`。

        为什么放 core：盘后落盘、预热、页面“数据日”三处都要判“该看哪一天”；
        此前 compat / compat_kline / kline 各写一份 16:10 时钟，只看周末不判节假日。
        插件**不得**再自建时段常量；需要注入时间时直接传 `now=` 参数。
        """
        from app.core import trade_clock as _tc  # noqa: PLC0415 - 避开循环导入

        return _tc

    # ------------------------------------------------------------ 行情源（v1.3 新增，2026-10-23）

    @property
    def quotes(self) -> Any:
        """只读行情源门面（CONTRACT §4.1）。

        分时/五档/逐笔都走 eltdx 按需 socket，没有常驻进程。全进程单例连接 + 短 TTL 缓存
        + 断线重建一次，全部在 core 里，插件不得自己 `from eltdx import TdxClient`
        （否则 N 个插件 = N 个连接、N 套重连与降级分支）。

        用法：
            glb = ctx.quotes.normalize("600519.SH")   # -> "sh600519"
            rec = ctx.quotes.depth(glb)                # 五档快照记录，不可用 -> None
            ticks = ctx.quotes.today_ticks(glb)        # 当日逐笔，不可用 -> []
            bars = ctx.quotes.minute_bars(glb, 241)    # 当日 1m，不可用 -> []

        不可用一律返回 None / 空，**不抛** —— 调用方据此降级，不编造数据。
        """
        if not self.manifest.quotes_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.quotes 声明 true")
        from app.core import quotes as _q  # noqa: PLC0415 - 避开循环导入

        return _q

    # ------------------------------------------------------------ QMT 整推（4.1.3a-2 新增）

    @property
    def feed(self) -> Any:
        """QMT 整推行情门面（`app/core/feed.py::QuoteFeed` 进程单例）。

        与 `ctx.quotes`（eltdx 按需拉）互补：这里是**推**，用于下单行情快照的秒级覆盖。
        理由同 `ctx.quotes`：QMT 只允许 core 订阅一次，插件不得自己 `import xtquant`
        （`FORBIDDEN_QUOTE_PATTERNS` 静态扫描会拦）。

        用法：
            ctx.feed.ensure(["600519.SH"])        # 增量登记订阅（只增不减）
            q = ctx.feed.quote("600519.SH")       # 最近一条 tick，太旧 -> None
            q = ctx.feed.order_quote("600519.SH") # eltdx 打底 + 5s 内 QMT 覆盖

        是否真的连上由 `PANEL_V2_FEED=1` 决定；未启动时 `quote()` 一律 None，
        调用方据此降级到 `ctx.quotes`，**不抛、不编造**。
        """
        if not self.manifest.feed_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.feed 声明 true")
        from app.core.feed import get_feed  # noqa: PLC0415 - 避开循环导入

        return get_feed()

    # ------------------------------------------------------------ 纸面交易（4.1.3b 新增）

    @property
    def paper(self) -> Any:
        """纸面交易服务门面（`app/core/paper/service.py::get_service` 进程单例）。

        返回的是 **PaperOrderService 实例本身**，不是包装层：下单 / 撤单 / 条件单 /
        账户视图 / 账本字段都由 core 提供，插件只做 HTTP 参数校验与 JSON 装配。

        为什么放在 core：账本写入需要跨请求串行、文件锁、原子替换、备份与审计
        （`app/core/paper/state.py`），属基础设施而非插件业务；而且 `cutover_gate.py`
        的「插件无写代码」门禁必须保持有效。

        用法：
            svc = ctx.paper
            svc.place(code, "buy", 100, price=10.0, quote=svc.quote_for(code))
            svc.account_view() / svc.list_orders() / svc.list_conditions()

        **本门面绝不提供任何真实下单路径**：`trade_mode()` 恒 `"paper"`、
        `live_order_enabled()` 恒 `False`（见 `app/core/paper/orders.py` 模块头）。
        """
        if not self.manifest.paper_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.paper 声明 true")
        from app.core.paper.service import get_service  # noqa: PLC0415 - 避开循环导入

        return get_service()

    # ------------------------------------------------------------ 实时 AMV 快照（2026-10-24 新增）

    @property
    def amv_live(self) -> Any:
        """实时 AMV 逐股快照门面（`app/core/amvlive.py`）。

        数据源是旧仓 `engine/executor/amv_live.py::stocks_snapshot()`（重模块：numpy /
        amv_store / QMT）。理由同 `ctx.quotes` / `ctx.feed`：只允许 core 持有，
        插件不得自己 `import executor.amv_live`（`cutover_gate` 静态扫描会拦）。

        用法：
            snap = ctx.amv_live.snapshot()      # dict | None（不可用一律 None）
            err = ctx.amv_live.last_error()     # 失败原因，供上层填 live_err

        可用性：`stocks_snapshot()` 需要 QMT 在线且处于交易时段；盘外/链路不通 →
        None，**不招摘、不编造**，调用方回退收盘口径。
        """
        if not self.manifest.amv_live:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.amv_live 声明 true")
        from app.core import amvlive as _al  # noqa: PLC0415 - 避开循环导入

        return _al

    def panel_write(self, name: str, payload: dict[str, Any]) -> Path:
        """原子写一个**可写白名单**里的生产 JSON（CONTRACT §4.2）。

        与 `panel_json` 对称，但只对 `paths.WRITABLE_PANEL` 里的名字开放，
        且插件必须在 `manifest.data.write` 里声明。

        安全措施（改生产文件必须可回溯）：
        1. 名字双白名单校验（manifest + 全局）
        2. 写前把当前文件备份到 `data/panel_v2/backups/<name>/<时间戳>.json`
        3. 先写同目录 `.tmp` 再 `os.replace` → 不会产生半个文件
        4. 每次写追加一行审计日志（`backups/<name>/audit.log`）

        返回写入后的文件路径。
        """
        if name not in self.manifest.panel_write:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.write 声明 {name}")
        path = paths.panel_writable(name)
        if path is None:
            raise PermissionError(f"{name} 不在可写白名单（WRITABLE_PANEL）内")
        if not isinstance(payload, dict):
            raise ValueError("panel_write 的 payload 必须是 dict")

        import os as _os
        from datetime import datetime as _dt

        path.parent.mkdir(parents=True, exist_ok=True)
        backup_dir = paths.OUT_DIR / "backups" / name
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.now().strftime("%Y%m%d_%H%M%S_%f")

        # 1) 备份当前文件（存在才备）
        if path.is_file():
            try:
                (backup_dir / f"{stamp}.json").write_bytes(path.read_bytes())
            except Exception:  # noqa: BLE001 - 备份失败不阻断写入，但下面会记审计
                pass

        # 2) 原子写
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, path)

        # 3) 审计
        try:
            size = path.stat().st_size
            with open(backup_dir / "audit.log", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\tplugin={self.id}\tfile={name}\tbytes={size}\n")
        except Exception:  # noqa: BLE001
            pass
        self._logger.info("panel_write %s by %s (%d bytes)", name, self.id, size)
        return path

    # ------------------------------------------------------------ 手动触发计划任务（N2 追补）

    def ops_run(self, name: str, timeout_s: float = 30.0) -> dict[str, Any]:
        """手动触发一个**数据类**计划任务（`POST /api/ops/run` 的实现面）。

        必须先在 `manifest.data.ops_run` 声明 `true`，否则 `PermissionError`。

        为什么在 core 而不是插件：底层是 `schtasks /run`（跨进程系统操作），插件侧静态禁
        子进程。实现与语义单源都在 `app/core/ops_run.py` + 旧仓
        `engine/ops/ops_status.py::run_manual`（白名单/冷却/文件锁全在旧仓，不复制）。

        返回旧仓原样 dict（`{ok, reason, task, ts, message}`），**异常与超时也返回 dict**
        （`ok=false` + `reason`）——HTTP 层据此如实回 200/5xx，不静默吞。
        旧仓缺失/导入失败 → `RuntimeError`（不降级成 `ok:false`，避免「看着触发其实没跑」）。
        """
        if not self.manifest.ops_run:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.ops_run 声明 true")
        from app.core import ops_run as _ops_run  # noqa: PLC0415 - 避开循环导入

        try:
            return _ops_run.run(name, timeout_s=timeout_s)
        except _ops_run.OpsRunError as exc:
            raise RuntimeError(str(exc)) from exc

    def ops_action(self, name: str) -> dict[str, Any]:
        """执行一个**固定动作**（`amv_refresh` / `sector_backfill` / `ops_snapshot`，2026-10-24 M1 新增）。

        与 `ops_run()` 的区别：那个触发**计划任务名**（白名单在旧仓 `run_manual`）；
        这个只认 `app/core/ops_run.py::OPS_ACTIONS` 里登记的名字，对应旧 8800
        固定 handler（`POST /api/amv_refresh`、`/api/sector_track_backfill`）。

        `sector_restart` 已于 2026-10-30 随 8002 下线整条摘除（不再是可调名字；
        旧口径的 retired/410 桩也一并删，代码里不再留 8002 指针）。

        为什么也在 core：三条都是**起线程/杀进程/拉进程**（插件侧静态禁子进程/线程），
        而语义单源原本就写在旧 server.py 的处理函数体里（锁、超时、结果形状）。

        声明：同一把门 `manifest.data.ops_run: true`（它守的是「这只手能起后台动作」，
        不区分是 schtasks 还是固定动作）；不另开字段，免得两份声明漂。

        返回值一律是 dict，带旧端形状；需要翻译成 HTTP 状态的额外带 `http` 键（插件层负责）。
        未知动作名 → ValueError（调用方 400）。
        """
        if not self.manifest.ops_run:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.ops_run 声明 true")
        from app.core import ops_run as _ops_run  # noqa: PLC0415 - 避开循环导入

        return _ops_run.run_action(name)

    def state_json(self, name: str) -> dict[str, Any]:
        """只读加载白名单**引擎状态文件**（engine_state / kill_switch / ui_notes，2026-10-24 新增）。

        与 `panel_json` 的模式一致，但读的是 `engine/state/` 下的旧系统状态文件：
        双白名单校验（manifest `data.state` + `paths.WRITABLE_STATE`）+ 顶层必须是对象。

        为什么复用**写**白名单做读：一个状态文件只该被声明一次，读和写用同一份名单，
        不会出现「能读不能写」或「能写不能读」的半套声明；插件读自己负责的状态，
        不该顺带读别人的（急停/交易模式互不越界由 manifest 声明约束）。
        """
        if name not in self.manifest.state_write:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.state 声明 {name}")
        path = paths.state_writable(name)
        if path is None:
            raise PermissionError(f"{name} 不在可写状态白名单（WRITABLE_STATE）内")
        if not path.is_file():
            raise FileNotFoundError(f"状态文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"状态文件顶层必须是对象: {path}")
        return data

    def state_write(self, name: str, payload: dict[str, Any]) -> Path:
        """原子写一个**引擎状态文件**（engine_state / kill_switch / ui_notes，2026-10-24 新增）。

        与 `panel_write` 同一套安全措施（双白名单 + 备份 + 原子替换 + 审计），
        目标是 `engine/state/` 下的旧系统状态文件：旧执行器与旧壳都直接读这些文件，
        插件原生写它们 = 旧执行链照常生效，不需要旧 server.py 参与写。
        manifest 声明字段为 `data.state`（与 data.write 分开，避免读 manifest 时混淆两类写目标）。
        """
        if name not in self.manifest.state_write:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.state 声明 {name}")
        path = paths.state_writable(name)
        if path is None:
            raise PermissionError(f"{name} 不在可写状态白名单（WRITABLE_STATE）内")
        if not isinstance(payload, dict):
            raise ValueError("state_write 的 payload 必须是 dict")

        import os as _os
        from datetime import datetime as _dt

        path.parent.mkdir(parents=True, exist_ok=True)
        backup_dir = paths.OUT_DIR / "backups" / name
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.now().strftime("%Y%m%d_%H%M%S_%f")

        if path.is_file():
            try:
                (backup_dir / f"{stamp}.json").write_bytes(path.read_bytes())
            except Exception:  # noqa: BLE001
                pass

        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, path)

        try:
            size = path.stat().st_size
            with open(backup_dir / "audit.log", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\tplugin={self.id}\tfile={name}\tbytes={size}\n")
        except Exception:  # noqa: BLE001
            pass
        self._logger.info("state_write %s by %s (%d bytes)", name, self.id, size)
        return path

    def misc_write(self, name: str, payload: dict[str, Any]) -> Path:
        """原子写一个**缓存类小文件**（2026-10-24 M1 新增，`data.misc` 白名单）。

        与 `panel_write` / `state_write` 同一套安全措施（双白名单 + 备份 + 原子替换 + 审计），
        目标是 `夏天-activity/工具/xiaoshi_cache/` 这类**工具缓存/界面设置**：
        • 不是 data/panel 生产 JSON（那走 panel_write）；
        • 不是 engine/state 引擎状态（那走 state_write）；
        • 引擎与执行器都不读，消费方是前端（跨会话保留设置）⇒ 写坏了不影响交易链。

        先例：旧 8800 `PUT /api/kline-indicator-settings`（`engine/server.py:732`，tmp+replace 硬编码）
        直接写 `xiaoshi_cache/kline_indicator_settings.json`。v2 不把硬编码搬进插件，统一收在这里。

        manifest 声明字段为 `data.misc`（三张写白名单互相独立，读 manifest 时一眼能分清写目标）。
        """
        if name not in self.manifest.misc_write:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.misc 声明 {name}")
        path = paths.misc_writable(name)
        if path is None:
            raise PermissionError(f"{name} 不在可写缓存白名单（WRITABLE_MISC）内")
        if not isinstance(payload, dict):
            raise ValueError("misc_write 的 payload 必须是 dict")

        import os as _os
        from datetime import datetime as _dt

        path.parent.mkdir(parents=True, exist_ok=True)
        backup_dir = paths.OUT_DIR / "backups" / name
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.now().strftime("%Y%m%d_%H%M%S_%f")

        if path.is_file():
            try:
                (backup_dir / f"{stamp}.json").write_bytes(path.read_bytes())
            except Exception:  # noqa: BLE001
                pass

        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            # 旧端 `json.dump(body, f, ensure_ascii=False)` 无缩进；这里与旧端**逐字节对齐**
            # （这个文件是前端 setttings 快照，缩进会放大体积且旧端消费方按原样读）。
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, path)

        try:
            size = path.stat().st_size
            with open(backup_dir / "audit.log", "a", encoding="utf-8") as f:
                f.write(f"{stamp}\tplugin={self.id}\tfile={name}\tbytes={size}\n")
        except Exception:  # noqa: BLE001
            pass
        self._logger.info("misc_write %s by %s (%d bytes)", name, self.id, size)
        return path

    def cache_read(self, name: str) -> dict[str, Any]:
        """读本插件自己的落盘缓存（`data/panel_v2/_cache/<id>/<name>.json`）。

        2026-11-07 新增：盘后结果落盘，重启/机器重开后不用重算（主人：
        「收盘后的数据就已经固定下来了，没必要每次都重拉重算」）。
        边界：只给插件自己的目录；版本校验由调用方做（把数据戳放进 payload）。
        文件不存在 → `FileNotFoundError`（调用方重算）；损坏/非对象 → `ValueError`。

        **三条铁律（主人 2026-11-07 提醒，调用方必须自己守）**：
        1. 空数据不写：结果为空（榜单/序列 0 条）不得写入，否则会把“无数据”固定成“有数据”。
        2. 失败不写：取数失败/超时/降级回上一交易日的结果不得写入。
        3. 日期对齐：读时必须校验 payload 里的数据日 == 当前应有的交易日；
           不相等就当作 miss，绝不把昨天的缓存当今天返回。
        core 只提供存储，不做业务判断。
        """
        path = paths.plugin_cache_path(self.id, name)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("缓存顶层必须是对象: %s" % path)
        return data

    def cache_write(self, name: str, payload: dict[str, Any]) -> Path:
        """原子写本插件落盘缓存（tmp → os.replace）。无备份/审计：缓存可重算。"""
        if not isinstance(payload, dict):
            raise ValueError("cache_write 的 payload 必须是 dict")
        import os as _os

        path = paths.plugin_cache_path(self.id, name)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            _os.fsync(f.fileno())
        _os.replace(tmp, path)
        return path

    def output_read(self, plugin_id: str, name: str, default: Any = None) -> Any:
        """只读**另一个插件**的输出 JSON（跨插件产销分离，2026-11-07）。

        场景：某个指标由 A 插件产出（如全A 日线序列）、B 插件只消费；让消费方重算就是第二份口径。
        这里只读、不算、不写。
        名字白名单同缓存接口；文件不存在/损坏 → 返回 `default`（调用方回退）。
        """
        path = paths.plugin_output_file(plugin_id, name)
        if not path.is_file():
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - 坏文件当缺失，不拖垮请求
            return default

    def lib(self, name: str) -> Any:
        """加载白名单口径库（纯函数模块），**只返回 `LibSpec.exports` 里声明的符号**。

        CONTRACT §8.1：口径库通常是“纯函数 + 自带数据加载”混在一起的旧模块。
        只把声明的符号包成 `SimpleNamespace` 交给插件，其余（如 `market_regime_payload`
        内部自己 `sqlite3.connect` 的那类）一律不可见 —— 否则就是绕过 `READABLE_DB` 的后门。
        加载前做 AST 扫描，命中 import 期副作用即拒。按 (插件, 名) 缓存。
        """
        if name not in self.manifest.lib_read:
            raise PermissionError(f"插件 {self.id} 未在 manifest.data.lib 声明 {name}")
        cached = self._libs.get(name)
        if cached is not None:
            return cached
        spec_info = paths.lib_spec(name)
        if spec_info is None:
            raise PermissionError(f"{name} 不在核心口径库白名单内")
        path = spec_info.path
        if not path.is_file():
            raise FileNotFoundError(f"口径库不存在: {path}")
        source = path.read_text(encoding="utf-8", errors="replace")
        banned = find_forbidden_lib_usage(source)
        if banned:
            raise ContractError(f"口径库 {name} 含 import 期副作用（禁止）：{banned}")
        module_name = f"_panel_v2_lib_{name}"
        module = sys.modules.get(module_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ContractError(f"无法为口径库 {name} 建立 import spec")
            module = importlib.util.module_from_spec(spec)
            # 口径库自身可能 import 同目录兄弟模块（如 amv_contrib -> bj_scope）。
            # **关键**：口径库自己往往在 import 期执行 `sys.path.insert(0, 它的目录)`，
            # 而它不会清理。如果不恢复，那个目录就永久留在 sys.path[0]，
            # 任何插件都能直接 `import amv_regime` 拿到完整模块（含 `_load_total` 那类
            # 自己连库的函数）—— facade 就成了摆设。
            # 因此：导入前快照 sys.path，导入后**整体恢复**，丢掉它插的条目。
            path_before = list(sys.path)
            parent = str(path.parent)
            inserted = parent not in sys.path
            if inserted:
                sys.path.append(parent)
            try:
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(module_name, None)
                raise ContractError(f"口径库 {name} 导入失败: {type(exc).__name__}: {exc}") from exc
            finally:
                # 无条件恢复：属于该库目录的条目一并清掉，不依赖它有没有乱插
                sys.path[:] = [p for p in path_before if p != parent]
        missing = [sym for sym in spec_info.exports if not hasattr(module, sym)]
        if missing:
            raise ContractError(f"口径库 {name} 缺少声明导出的符号: {missing}")
        facade = types.SimpleNamespace(
            **{sym: getattr(module, sym) for sym in spec_info.exports}
        )
        self._libs[name] = facade
        return facade


# ---------------------------------------------------------------- loader


@dataclass
class LoadedPlugin:
    manifest: Manifest
    ctx: PluginContext
    router: APIRouter
    load_ms: float


def load_plugin(plugin_dir: Path, core_version: str = CORE_VERSION) -> LoadedPlugin:
    """validate → import backend → register(ctx) → APIRouter 校验。

    任一步失败抛 ContractError（import 期原始异常也会被包成 ContractError）。
    """
    plugin_dir = Path(plugin_dir).resolve()
    started = time.perf_counter()
    manifest = load_manifest(plugin_dir, core_version=core_version)

    backend_path = plugin_dir / f"{manifest.backend.module}.py"
    if not backend_path.is_file():
        raise ContractError(f"缺少后端模块: {backend_path.name}")

    module_name = f"_panel_v2_plugin_{manifest.id}_{manifest.backend.module}"
    spec = importlib.util.spec_from_file_location(module_name, backend_path)
    if spec is None or spec.loader is None:
        raise ContractError(f"无法为 {backend_path} 建立 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ContractError(f"backend 导入失败: {type(exc).__name__}: {exc}") from exc

    entry = getattr(module, manifest.backend.entry, None)
    if not callable(entry):
        sys.modules.pop(module_name, None)
        raise ContractError(f"backend 缺少入口函数 {manifest.backend.entry}(ctx)")

    ctx = PluginContext(manifest)
    try:
        router = entry(ctx)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise ContractError(f"{manifest.backend.entry}(ctx) 执行失败: {type(exc).__name__}: {exc}") from exc

    if not isinstance(router, APIRouter):
        sys.modules.pop(module_name, None)
        raise ContractError(
            f"{manifest.backend.entry}(ctx) 必须返回 fastapi.APIRouter，实际 {type(router).__name__}"
        )

    return LoadedPlugin(
        manifest=manifest,
        ctx=ctx,
        router=router,
        load_ms=round((time.perf_counter() - started) * 1000, 2),
    )


def find_forbidden_db_usage(source: str) -> list[str]:
    """静态扫描插件源码里的直连数据库写法（供插件契约测试复用）。"""
    return [pat for pat in FORBIDDEN_DB_PATTERNS if pat in source]


# CONTRACT §4.1：插件不得自己连行情源，一律走 `ctx.quotes`。
# 理由：若每个插件各自 `TdxClient()`，就是 N 个连接、N 套重连与降级分支；
# 行情源换地址/换协议时要改 N 处。
FORBIDDEN_QUOTE_PATTERNS: tuple[str, ...] = (
    "from eltdx",
    "import eltdx",
    "TdxClient",
    "xtquant",
    "xtdata",
)


def find_forbidden_quote_usage(source: str) -> list[str]:
    """静态扫描插件源码里自己连行情源的写法（供插件契约测试复用）。"""
    return [pat for pat in FORBIDDEN_QUOTE_PATTERNS if pat in source]


def find_forbidden_lib_usage(source: str) -> list[str]:
    """静态扫描口径库的**import 期**副作用（CONTRACT §8.1）。

    只看模块顶层语句（函数/类体内的调用不算副作用），且跳过 `if __name__ == "__main__":`
    自检块——那段代码只在直接跑脚本时执行，import 时不会。
    解析失败（语法错）时退回整文本扫描，宁可误报也不漏。
    """
    import ast

    def dotted(node: ast.AST) -> str:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [pat for pat in FORBIDDEN_LIB_IMPORT_PATTERNS if pat in source]

    hits: list[str] = []
    for stmt in tree.body:
        # 跳过 __main__ 自检块
        if isinstance(stmt, ast.If):
            test = stmt.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                continue
        # 函数/类定义体内的调用不是 import 期副作用 → 整块跳过
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                name = dotted(node.func)
                if name in FORBIDDEN_LIB_IMPORT_PATTERNS and name not in hits:
                    hits.append(name)
    return hits
