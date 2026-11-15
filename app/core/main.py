"""核心应用：启动、插件扫描、路由挂载、健康检查、静态壳。

只做总线，不认识任何业务域。契约见 docs/CONTRACT.md。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import paths
from app.core.contract import (
    CORE_VERSION,
    ContractError,
    Manifest,
    load_manifest,
    load_plugin,
)

logger = logging.getLogger("panel_v2.core")

# 插件前端只允许暴露这些路径（其余如 backend.py / plugin.json / tests 不外发）
ASSET_PREFIXES: tuple[str, ...] = ("frontend/", "assets/")

# ---------------------------------------------------------------- 旧引擎外壳端点（批次 0，2026-10-24）
#
# 背景：旧壳（`app/web/legacy/shell.html`）在 8801 宿主下由 `engine/shell_server.py` 提供
# 急停代理 / 重启引擎 / 搜索 / 签收四个端点。8810 托管旧壳后这些请求落到 8810 自己身上，
# 实测 404，于是急停/重启按钮与搜索面板全哑。本批补齐，**语义照旧端，禁复制逻辑**：
#
#   语义唯一源 = `engine/shell_server.py`（外壳宿主）与 `engine/server.py`（引擎本体）。
#   急停真正的后果（模块开关快照、作战表重写、引擎状态落盘）只在 8800 里发生，
#   本进程只做**转发**；重启则是「拉起 8800」，口径唯一源 = `engine/executor/run_daemons.py`。
#
# ⚠ 目标进程差异（任务书 §2 硬约束）：旧端 `/api/shell/restart` 会杀掉命令行里含
#   `engine\server.py` 的进程再拉起（8800）。v2 里**绝不能**在请求线程里直接杀 uvicorn/8810——
#   本进程就是 8810，同步杀自己会把响应湖死。2026-10-29 改为「分离 helper + 先回响应」自重启：
#   core 只负责起 `tools/restart_8810.ps1`（延迟 2s → 杀端口监听进程 → ensure 拉起）。
#
# 2026-10-26 旧端口下线后语义收口（docs/HANDOFF_20261026_old_ports_retire.md）：
#   8800 已退役，旧的四条转发（急停代理 / 重启引擎 / 签收 / 搜索）现在这样落：
#     急停  `/api/shell/kill`  → 转发到**本进程** `/api/kill`（rw 插件原生）
#     重启  `/api/shell/restart` → 2026-10-29 主人裁定改为**重启 8810 自身**
#                                （分离 helper 杀进程 + ensure 拉起；不再拉起 8800）
#     签收  `/api/sign`         → rw 插件原生（core 的代理已删）
#     搜索  compat 插件原生
#
#   /  首页       → v2 壳（2026-10-26 收口；旧页改挂 /legacy/ 作回滚）
#   /v2/           → 同一份 v2 壳（保留旧书签可用）
#   /legacy/       → 旧仓前端原样（engine/web 只读直挂）
QMT312_DIR = paths.WS / "envs" / "qmt312"
PROBE_TIMEOUT_S = 0.8      # 探活超时：够本机握手，不拖慢外壳（同 engine/shell_server.py）
WRITE_PROXY_TIMEOUT_S = 8.0  # 急停/签收在 8800 侧要落盘，留足余量（同 engine/shell_server.py）


def _http_json(url: str, method: str = "GET", body: dict[str, Any] | None = None,
               timeout: float = PROBE_TIMEOUT_S) -> Any | None:
    """极简 HTTP JSON 调用；**连接类失败**返回 None（调用方据此判不可达，不抛穿）。

    `engine/shell_server.py::_http_json` 的同口径复刻（同样的异常集合、同样的空体处理）。

    2026-10-24 验收修：`urllib.error.HTTPError` 是 `URLError` 的子类，原实现把 8800 的
    422（参数校验）/ 500 也当成「不可达」吞成 None → 代理层误报「引擎未启动」。
    现单独捕获 HTTPError 并把响应体（JSON）透传出去，由调用方区分「引擎不在」与
    「引擎在、请求不合法」：前者 None，后者原样返回 8800 的错误体。
    """
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:          # 引擎在，但 4xx/5xx：透传其响应体
        try:
            raw = exc.read().decode("utf-8", "replace")
            parsed = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError):
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {"detail": str(parsed)[:300]}
        return {**parsed, "_http_status": exc.code}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _launcher_python() -> str:
    """拉起用解释器（qmt312 venv）。"""
    for cand in (QMT312_DIR / "Scripts" / "python.exe", QMT312_DIR / "python.exe"):
        if cand.exists():
            return str(cand)
    return sys.executable


# 2026-10-26 旧端口下线：以下符号已删（无调用者，纯 8800 遗留）：
#   _proxy_engine_write()      —— 把写类请求透传给 8800（急停/签收）；两者现已原生，
#                                  急停走本进程 /api/kill（见 shell_kill），签收由 rw 插件直接提供。
#   build_engine_restart()     —— 杀 engine/server.py 再 run_daemons --only server 拉起 8800；
#                                  8800 退役后该动作已无对象（shell_restart 现直接回 retired）。
#   _list_python_procs()       —— 只服务于上两者（枚举进程找 8800）。
#   ENGINE_SERVER_SCRIPT / ENGINE_BASE / PANEL_DOWN_DETAIL —— 同上，仅 8800 转发用。
#   RUN_DAEMONS / RESTART_TIMEOUT_S —— 同上（拉起 8800 的超时与脚本路径）。
# _http_json 保留：shell_kill 仍用它转发到本进程 /api/kill。


@dataclass
class PluginRecord:
    """一个插件的加载结果；ok=False 时只禁用该插件。"""

    id: str
    ok: bool
    error: str | None = None
    load_ms: float = 0.0
    manifest: Manifest | None = None
    timeout_ms: int = 5000
    enabled: bool = True
    router: APIRouter | None = None

    def health(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ok": self.ok,
            "error": self.error,
            "load_ms": self.load_ms,
            "enabled": self.enabled,
        }

    def listing(self) -> dict[str, Any] | None:
        if self.manifest is None:
            return None
        payload = self.manifest.public()
        payload["ok"] = self.ok
        payload["error"] = self.error
        return payload


def discover_plugin_dirs(plugins_dir: Path) -> list[Path]:
    """扫描 <plugins_dir>/*/plugin.json；按目录名排序，保证加载顺序稳定。"""
    if not plugins_dir.is_dir():
        return []
    found = [
        child
        for child in sorted(plugins_dir.iterdir(), key=lambda p: p.name)
        if child.is_dir() and (child / "plugin.json").is_file()
    ]
    return found


def _load_all(plugins_dir: Path) -> list[PluginRecord]:
    records: list[PluginRecord] = []
    for directory in discover_plugin_dirs(plugins_dir):
        started = time.perf_counter()
        try:
            manifest = load_manifest(directory)
        except ContractError as exc:
            logger.warning("插件 %s manifest 校验失败: %s", directory.name, exc)
            records.append(
                PluginRecord(
                    id=directory.name,
                    ok=False,
                    error=str(exc),
                    load_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            )
            continue

        if not manifest.enabled:
            records.append(
                PluginRecord(
                    id=manifest.id,
                    ok=True,
                    error=None,
                    load_ms=round((time.perf_counter() - started) * 1000, 2),
                    manifest=manifest,
                    timeout_ms=manifest.backend.timeout_ms,
                    enabled=False,
                )
            )
            continue

        try:
            loaded = load_plugin(directory)
        except ContractError as exc:
            logger.warning("插件 %s 加载失败: %s", manifest.id, exc)
            records.append(
                PluginRecord(
                    id=manifest.id,
                    ok=False,
                    error=str(exc),
                    load_ms=round((time.perf_counter() - started) * 1000, 2),
                    manifest=manifest,
                    timeout_ms=manifest.backend.timeout_ms,
                )
            )
            continue

        records.append(
            PluginRecord(
                id=manifest.id,
                ok=True,
                load_ms=loaded.load_ms,
                manifest=loaded.manifest,
                timeout_ms=loaded.manifest.backend.timeout_ms,
                router=loaded.router,
            )
        )

    return records


# 2026-10-26 首页收口：`_v2_owned_tabs()` 已删。它只为「往旧页 <head> 注入 __V2_OWNED」
#   存在（告诉旧页哪些 Tab 已由 v2 接管，免得 boot 阶段去拉 8002）。首页改成 v2 壳后
#   不再有「旧页 boot」这一步，注入与守卫都失去对象；旧页在 /legacy/ 下自行完整渲染。

def create_app(
    plugins_dir: Path | str | None = None,
    web_dir: Path | str | None = None,
) -> FastAPI:
    """构造应用；plugins_dir / web_dir 可覆盖（测试用）。"""
    pdir = Path(plugins_dir) if plugins_dir is not None else paths.PLUGINS_DIR
    wdir = Path(web_dir) if web_dir is not None else paths.WEB_DIR

    app = FastAPI(title="作战面板 v2", version=CORE_VERSION, docs_url="/api/docs", redoc_url=None)

    records = _load_all(pdir)
    by_id: dict[str, PluginRecord] = {r.id: r for r in records}

    # 插件挂载前缀：默认 `/api/p/<id>`；`backend.mount = "root"` 时挂到 `/api`。
    #
    # 为什么要 root（2026-10-23）：旧面板前端直接请求根路径 `/api/<名>`，
    # 而不是 `/api/p/<id>/<名>`。迁移策略是「复用旧前端、换数据源」，
    # 所以需要让兼容插件能注册到根。
    # 冲突保护：同一路径被两个插件注册就直接报错（启动即暴露，不静默覆盖）。
    claimed: dict[str, str] = {}
    for record in records:
        if not (record.ok and record.enabled and record.router is not None):
            continue
        mount = (record.manifest.backend.mount or "plugin").lower()
        if mount == "root":
            for route in record.router.routes:
                path = getattr(route, "path", "")
                if not path:
                    continue
                full = "/api" + path
                prev = claimed.get(full)
                if prev and prev != record.id:
                    raise RuntimeError(
                        "根路径冲突：%s 已被插件 %s 注册（%s）" % (full, prev, record.id))
                claimed[full] = record.id
            app.include_router(record.router, prefix="/api")
            logger.info("已挂载插件 %s -> /api（根路径，%d 个路由）", record.id,
                        len(record.router.routes))
        else:
            app.include_router(record.router, prefix=f"/api/p/{record.id}")
            logger.info("已挂载插件 %s -> /api/p/%s", record.id, record.id)

    loaded_ids = [r.id for r in records if r.ok and r.enabled]

    # ------------------------------------------------------------ 健康/清单
    @app.exception_handler(FileNotFoundError)
    async def _file_not_found(request: Request, exc: FileNotFoundError):
        """数据/依赖文件缺失 = 503（不是“服务器内部错”）。

        2026-11-15：未挂 `/api/p/` 前缀的兼容端点（compat 挂在 root）不走 plugin_guard，
        数据文件缺失原先直接变成 500。统一在这里转成 503，与其它插件“缺数据”同语义，
        前端按「数据不可用」展示而不是弹故障。
        """
        logger.warning("缺数据 %s: %s", request.url.path, exc)
        return JSONResponse(
            {"error": "resource_unavailable", "detail": f"数据不可用: {exc}"[:500]},
            status_code=503,
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": CORE_VERSION, "plugins": loaded_ids}

    @app.get("/api/prewarm/status")
    def prewarm_status() -> dict[str, Any]:
        """盘后预热状态（只读）：交易日/期望数据日、本轮进度、清单项数。

        给后台页与验收用；不触发预热、不改任何文件。
        """
        from app.core import prewarm as _prewarm  # noqa: PLC0415

        return {"ok": True, **_prewarm.status_snapshot()}

    @app.get("/_plugins")
    def list_plugins() -> dict[str, Any]:
        return {
            "core_version": CORE_VERSION,
            "plugins": [r.listing() for r in records if r.listing() is not None],
            "errors": [
                {"id": r.id, "error": r.error}
                for r in records
                if not r.ok
            ],
        }

    @app.get("/_plugins/health")
    def plugins_health() -> dict[str, Any]:
        return {
            "ok": all(r.ok for r in records),
            "core_version": CORE_VERSION,
            "plugins": [r.health() for r in records],
        }

    # ------------------------------------------------------------ 预警推送（2026-10-28）
    #
    # 口径：docs/RESEARCH_20261028_cond_push.md §5。通道 = QQ 官方 bot；发送在 core/notify 的
    # sender 线程里异步做，行情线程/条件单评估只 enqueue（不能同步 HTTP，10s 超时拖死 tick）。
    @app.get("/api/notify/status")
    def notify_status() -> dict[str, Any]:
        from app.core import notify  # noqa: PLC0415 - 懒引入，不扩大 core 启动面
        return notify.status()

    @app.get("/api/notify/list")
    def notify_list(n: int = 50) -> dict[str, Any]:
        from app.core import notify  # noqa: PLC0415
        return {"ok": True, "items": notify.recent(n)}

    @app.post("/api/notify/enqueue")
    async def notify_enqueue(request: Request) -> JSONResponse:
        """预警出口（L2）专用入队口（2026-11-07 预警页重建）。

        为什么在 core 而不在插件：真正的 QQ 发送器是 `core/notify` 的常驻线程，插件
        （预警页）只负责展示与写条件；引擎侧的预警内核要推主人时，唯一活通道就是这里。
        参数只收标题/正文/去重键，**不暴露凭据、不提供开关**（开关在页面 UI）。

        `wait`（秒，可选）：入队后最多等这么久拿落地回执。**入队 ≠ 送达**（2026-11-07）：
        不传 wait 时返回只代表「已入队」；传了并拿到 msg_id 才是「已确认送达」。
        """
        from app.core import notify  # noqa: PLC0415
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 非 JSON 一律 400
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "detail": "需要 JSON 体"}, status_code=400)
        title = str(body.get("title") or "").strip()
        if not title:
            return JSONResponse({"ok": False, "detail": "缺 title"}, status_code=400)
        res = notify.enqueue(
            str(body.get("event") or "ALERT"),
            title,
            str(body.get("body") or ""),
            dedup_key=(str(body["dedup_key"]) if body.get("dedup_key") else None),
            level=str(body.get("level") or "info"),
        )
        # 入队即止：调用方自己决定要不要等回执（行情线程不能等，预警内核可以等）
        try:
            wait_s = float(body.get("wait") or 0)
        except (TypeError, ValueError):
            wait_s = 0.0
        key = res.get("key")
        if res.get("ok") and key and wait_s > 0:
            deadline = time.time() + min(wait_s, 5.0)
            while time.time() < deadline:
                rc = notify.receipt(key)
                if rc:
                    res.update({"status": rc.get("status"),
                                "msg_id": rc.get("msg_id"),
                                "msg_ts": rc.get("msg_ts"),
                                "sent_at": rc.get("sent_at")})
                    break
                await asyncio.sleep(0.1)
        if "status" not in res:
            res["status"] = "queued" if res.get("ok") else "failed"
        return JSONResponse(res)

    @app.get("/api/notify/receipt")
    def notify_receipt(key: str = "") -> dict[str, Any]:
        """查一条投递回执（落地 msg_id / 时间戳）。没投递过或还没处理到 → status=unknown。"""
        from app.core import notify  # noqa: PLC0415
        rc = notify.receipt(key) if key else None
        return {"ok": True, "key": key, "status": "unknown", **(rc or {})}


    @app.post("/api/notify/test")
    async def notify_test(request: Request) -> JSONResponse:
        """唯一会真发消息的入口，必须 {"confirm": true} 二次确认。"""
        from app.core import notify  # noqa: PLC0415
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 空体/非 JSON 均视为未确认
            body = {}
        if not (isinstance(body, dict) and body.get("confirm") is True):
            return JSONResponse({"ok": False, "detail": "需要 {\"confirm\": true} 二次确认"},
                                status_code=400)
        return JSONResponse(notify.test_send())

    # ------------------------------------------------------------ 旧引擎外壳端点（批次 0）
    #
    # 三条都在 core 而不是 compat 插件，理由如下（任务书 §2 的裁决原则）：
    #   · /api/shell/restart 要拉进程（subprocess / taskkill），插件侧有静态扫描禁令
    #     （tools/cutover_gate.py 的 banned tokens：subprocess. / Popen( / os.system(），
    #     任务书明确「若插件契约禁止 subprocess，则 restart 放 core」；
    #   · /api/shell/kill、/api/sign 是**写类透传**（要 urllib.request.urlopen 才能代理 8800），
    #     同一个 urlopen 禁令让它们也不能进插件；而急停/签收的口径唯一源在 8800，
    #     这里只转发 body、不复制任何写逻辑，放 core 不新增业务域。
    #     （插件里唯一新增的写类路由是 POST /api/shell/search，它同源直调 build_search，零 IO。）
    @app.post("/api/shell/kill")
    async def shell_kill(request: Request) -> JSONResponse:
        """急停 / 解除急停：**转发到本进程原生的 `/api/kill`**（body 透传）。

        2026-10-26：目标从 8800 改为本进程自身。理由：原始写域（kill_switch / engine_state /
        作战表）已由 mount=root 的 rw 插件原生接管（`/api/kill` 已在路由表里），
        而 8800 已退役 —— 再往 8800 转就是永远失败。本进程仍**不做**任何状态改写，只转发，
        口径唯一源还是插件。

        （QMT 关停后急停不再影响真实下单；它现在约束的是纸面仓与条件单评估，
         按钮仍有意义，故不改语义、只换目标。）
        """
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - 空体/非 JSON：按空对象转
            body = {}
        target = str(request.base_url).rstrip("/") + "/api/kill"
        return JSONResponse(_http_json(target, method="POST", body=body,
                                       timeout=WRITE_PROXY_TIMEOUT_S))

    # 2026-10-24：`POST /api/sign` 的 core 代理已删——mount=root 的 rw 插件
    # 原生实现了同名端点（受控写作战表文件，语义含空 buy_plan 不降级/幂等快照），
    # 插件路由先注册先匹配，core 这条是死代码。旧壳 `/api/sign` 请求现在直达插件。

    @app.post("/api/shell/run-script")
    def shell_run_script(body: dict) -> JSONResponse:
        """按需跑一个白名单脚本（2026-11-04：估值页顶部「刷新数据」）。

        语义源在旧仓：`ops_status.MANUAL_SCRIPTS`（名字 → 脚本路径），本端点只按名字取路径，
        不知道也不关心具体是哪个业务脚本。
        为什么放 core：插件侧有 subprocess 静态禁令（tools/cutover_gate.py），
        「跑一个脚本」与 `/api/shell/restart` 同属进程生命周期职责。

        返回：`{ok:true, started:true}`；已在跑 → `{ok:false, reason:"busy"}`（仍 200，不是错误）。
        未知名字 → 400；白名单/脚本缺失 → 503。
        """
        from app.core import ops_run  # noqa: PLC0415 - 懒引入，不扩大 core 启动面

        name = str((body or {}).get("name") or "").strip()
        try:
            res = ops_run.run_script(name)
        except ValueError as exc:
            return JSONResponse({"ok": False, "detail": str(exc)}, status_code=400)
        except ops_run.OpsRunError as exc:
            return JSONResponse({"ok": False, "detail": str(exc)}, status_code=503)
        return JSONResponse(res)

    @app.post("/api/shell/restart")
    def shell_restart() -> JSONResponse:
        """重启**本面板进程**（8810）—— 2026-10-29 主人裁定。

        历史：2026-10-26 旧端口下线时本端点改成固定回 `engine:"retired"`（不再拉 8800），
        但设置面板的「重启服务」按钮没删、文案还写着拉 8800，点下去必失败；
        而面板自身改完后端/插件后**没有任何重启入口**，只能人工杀进程。

        现在的语义（自重启，不在请求线程里杀自己）：
          1. 起一个分离的 `tools/restart_8810.ps1`：先等 2 秒（让本请求把 {ok:true} 回完）
             → 按端口杀监听进程及其 uvicorn 父进程 → 调 `ensure_8810.ps1 -Once` 拉起；
          2. 前端收到 ok 后轮询 `/api/health`，恢复即 `location.reload()`；
          3. helper 自身挂了有 `PanelV2_KeepAlive` 计划任务兜底（每 5 分钟 ensure）。

        为什么放 core 而不是插件：插件侧有 subprocess 静态禁令（tools/cutover_gate.py），
        且进程生命周期属 core 职责。
        """
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        script = paths.PANEL_V2_DIR / "tools" / "restart_8810.ps1"
        if not script.is_file():
            return JSONResponse({
                "ok": False, "engine": "panel_v2", "ts": ts, "helper_pid": None,
                "detail": "重启脚本缺失：%s" % script,
            }, status_code=503)

        ps = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(ps):
            ps = "powershell.exe"
        # CREATE_NO_WINDOW(0x08000000) | CREATE_NEW_PROCESS_GROUP(0x200)：
        #   · 实测（2026-10-29）：DETACHED_PROCESS(0x8) 下 powershell.exe 不放控制台会立即退出，
        #     脚本一行都跑不到（日志空、进程已死）；CREATE_NO_WINDOW 正常。
        #   · 子进程不随父进程退（Windows 默认），且新进程组不会收到父控制台的 Ctrl 事件。
        flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
        log_dir = paths.OUT_DIR / "logs"
        proc = None
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "_restart_helper.out.log").open("ab") as fo, \
                    (log_dir / "_restart_helper.err.log").open("ab") as fe:
                proc = subprocess.Popen(  # noqa: S603 - 固定脚本路径，无用户输入拼接
                    [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    cwd=str(paths.PANEL_V2_DIR), stdin=subprocess.DEVNULL,
                    stdout=fo, stderr=fe,
                    creationflags=flags, close_fds=True,
                )
        except Exception as exc:  # noqa: BLE001 - 起不来就如实报错，不谎报成功
            return JSONResponse({
                "ok": False, "engine": "panel_v2", "ts": ts, "helper_pid": None,
                "detail": "起重启 helper 失败：%s: %s" % (type(exc).__name__, exc),
            }, status_code=503)

        return JSONResponse({
            "ok": True, "engine": "panel_v2", "ts": ts, "helper_pid": int(proc.pid),
            "detail": "8810 将在约 2 秒后重启，就绪后本页自动刷新（不可用窗口约 3–6 秒）",
        })

    # ------------------------------------------------------------ 插件静态文件
    @app.get("/plugins/{plugin_id}/{asset_path:path}")
    def plugin_asset(plugin_id: str, asset_path: str) -> FileResponse:
        record = by_id.get(plugin_id)
        if record is None or record.manifest is None or not record.enabled:
            raise StarletteHTTPException(status_code=404, detail=f"plugin not found: {plugin_id}")
        allowed = asset_path == record.manifest.frontend.module or asset_path.startswith(ASSET_PREFIXES)
        if not allowed:
            raise StarletteHTTPException(status_code=404, detail="asset not exposed")
        base = record.manifest.directory.resolve()
        target = (base / asset_path).resolve()
        if base not in target.parents and target != base:
            raise StarletteHTTPException(status_code=404, detail="asset outside plugin dir")
        if not target.is_file():
            raise StarletteHTTPException(status_code=404, detail="asset not found")
        media_type, _ = mimetypes.guess_type(target.name)
        # 2026-10-27 修复「改了前端但页面不更新」：
        # 以前 FileResponse 走默认缓存策略，浏览器会拿着旧 detail.js 不放，
        # 导致后端已返回新字段、界面上却看不到（实测：drv 徽标已在 DOM、但旧 JS 不渲染）。
        # 改用 no-cache（而非 no-store）：仍允许缓存，但每次强校验 ETag/Last-Modified，
        # 文件变了立即追上，没变只回 304——比禁缓存便宜，也不会再出现“改了不生效”。
        return FileResponse(
            target, media_type=media_type or "application/octet-stream",
            headers={"Cache-Control": "no-cache, must-revalidate"})

    # ------------------------------------------------------------ 静态壳
    if wdir.is_dir():
        # 2026-11-04：静态资源强制回源校验。
        #   原来直接 `StaticFiles(...)`，响应不带 Cache-Control，浏览器按启发式自己缓存 js；
        #   改了 v2_note.js / shell.js 后用户刷新页面仍拿旧文件（2026-11-02 已出过同样的事）。
        #   这些文件名不带 hash，又要求改完立即生效，只能每次都回源（未变化时只回 304，代价很小）。
        class _RevalidateStatic(StaticFiles):
            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                resp.headers["Cache-Control"] = "no-cache, must-revalidate"
                return resp

        app.mount("/static", _RevalidateStatic(directory=str(wdir)), name="static")

        # 2026-10-24 主人裁定 + 知夏审查订正：用户日常用的是 8800 直开 index.html 的完整页面
        # （自带侧栏/抽屉，非外壳形态）。首页必须与现用形态一致：直接原样服务旧仓 index.html，
        # 数据请求打到 8810 插件。外壳壳子保留在 /legacy/shell.html（其 postMessage 桥写死
        # SHELL_ORIGIN=8801，在 8810 下静默失效，属已知限制）；v2 新壳在 /v2/。
        legacy_dir = wdir / "legacy"
        legacy_web = paths.WS / "engine" / "web"
        legacy_ready = legacy_dir.is_dir() and (legacy_dir / "shell.html").is_file()
        # 旧页文件仍在（`engine/web/index.html`）：首页已不再服务它，只作 `/legacy/` 回滚入口。
        # 回滚 = 把下面 index() 改成 `FileResponse(legacy_web / "index.html", media_type="text/html; charset=utf-8")`。

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            """首页 = v2 壳（2026-10-26 主人拍板收口）。

            改前：首页服务旧仓 index.html，并在 `<head>` 注入 `__V2_OWNED`、在 `<body>` 前注入
            `v2bridge.js`；桥脚本再往旧页侧栏加一个「v2 插件」Tab，用 iframe 把 `/v2/` 套进来。
            那是绞杀者迁移的中间态：旧页当壳、v2 卡片当内容。

            为什么收口：旧端口（8800/8801/8002）已停，旧页不再有独立宿主；中间态的代价是实打实的
            ——首页要读 669KB 旧页、每张卡多一层 iframe、主题靠 postMessage 推、标注要跨 iframe 生效。
            覆盖核对（切换前实测）：旧页 9 个 Tab（仪表盘/作战表/盘中/板块/概念/复盘/自选池/选股/后台）
            在 v2 里都有对应卡，另多 K 线 / 活跃市值 / HCL / 纸面 4 张。

            回滚：`/legacy/` 仍完整托管旧仓前端（`engine/web` 只读直挂，见下方 mount），
            把本函数改回返回 `legacy_web / "index.html"` 即为旧首页。
            """
            return FileResponse(wdir / "shell.html", media_type="text/html; charset=utf-8")

        @app.get("/v2/", include_in_schema=False)
        @app.get("/v2", include_in_schema=False)
        def v2_shell() -> FileResponse:
            return FileResponse(wdir / "shell.html", media_type="text/html; charset=utf-8")

        # ------------------------------------------------------------ 旧前端托管（2026-10-23；2026-10-24 起为首页）
        #
        # 背景：旧面板前端（`engine/web/index.html`，750KB、10 个 Tab）引用了 85 个
        # `/api/...` 路径。迁移策略是**复用旧前端、换数据源**，不是重做一个界面。
        #
        # 做法：
        # • `shell.html` 单独一份（`app/web/legacy/shell.html`）—— 只改了两处：
        #   `ENGINE` 从写死的 8800 改成同源 origin；新增 `PAGE_BASE` 把 iframe 指到 `/legacy/`。
        # • 其余前端文件**不复制**，直接挂载旧仓 `engine/web`（只读），避免双源：
        #   旧仓前端一更新，8810 立即生效。
        # • 首页直开 index.html 后，页面里的根相对引用（旧 K 线模块 js、/vendor/*.js、
        #   /themes.css 等）也落在根路径 —— 所以旧仓 web 目录除了挂 /legacy/ 前缀，
        #   **还挂在根静态区**（html=True 不用：根的 / 已被手写路由占用，StaticFiles
        #   只服务文件不抢 / 路由）。手写路由与插件 /api 路由优先于 mount，不受影响。
        #
        # 手写路由优先于 mount，所以 `/legacy/shell.html` 会先命中下面这一个，
        # 其余路径落到旧仓目录。
        #
        # 入口：首页 `/`（=旧 index.html）；旧外壳 /legacy/shell.html；v2 新壳 /v2/。
        if legacy_ready:
            @app.get("/legacy/shell.html", include_in_schema=False)
            def legacy_shell() -> FileResponse:
                return FileResponse(legacy_dir / "shell.html",
                                    media_type="text/html; charset=utf-8")
            if legacy_web.is_dir():
                # html=True：否则 `/legacy/` 不会落到 index.html（会 404），
                # 而旧外壳的 iframe 正是请求 `PAGE_BASE + '/?shell=1...'`。
                app.mount("/legacy", StaticFiles(directory=str(legacy_web), html=True), name="legacy")
                # 根静态：旧 index.html 的根相对引用（旧 K 线模块 js 等）由此服务。
                # mount 在路由表尾部，不会抢 / 、/api/*、/plugins/* 等手写路由。
                app.mount("/", StaticFiles(directory=str(legacy_web)), name="legacy_root")
                logger.info("旧前端已托管: /legacy/shell.html 与根静态（内容页读 %s）", legacy_web)
            else:
                logger.warning("旧仓前端目录不存在: %s", legacy_web)
        else:
            logger.warning("旧外壳不存在，未托管: %s", legacy_dir)
    else:
        logger.warning("前端目录不存在，静态壳未挂载: %s", wdir)

        @app.get("/", include_in_schema=False)
        def index_missing() -> JSONResponse:
            return JSONResponse({"error": "shell missing", "web_dir": str(wdir)}, status_code=503)

    # ------------------------------------------------------------ 插件异常包装
    @app.middleware("http")
    async def plugin_guard(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/p/"):
            return await call_next(request)
        parts = path.split("/")
        plugin_id = parts[3] if len(parts) > 3 else "unknown"
        record = by_id.get(plugin_id)
        timeout = (record.timeout_ms if record else 5000) / 1000
        try:
            return await asyncio.wait_for(call_next(request), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("插件 %s 超时 %.0fms", plugin_id, timeout * 1000)
            return JSONResponse(
                {"error": f"plugin:{plugin_id}", "detail": f"timeout after {int(timeout * 1000)}ms"},
                status_code=504,
            )
        except FileNotFoundError as exc:
            # 2026-11-15：数据文件/库缺失不是代码错，是“数据不可用”——统一 503。
            # 与各插件自己 raise HTTPException(503) 的语义对齐（前端按“缺数据”展示，不弹故障）。
            logger.warning("插件 %s 缺数据: %s", plugin_id, exc)
            return JSONResponse(
                {"error": f"plugin:{plugin_id}", "detail": f"数据不可用: {exc}"[:500]},
                status_code=503,
            )
        except Exception as exc:  # noqa: BLE001 - 插件错误统一包装成 500
            logger.exception("插件 %s 处理失败", plugin_id)
            return JSONResponse(
                {"error": f"plugin:{plugin_id}", "detail": f"{type(exc).__name__}: {exc}"[:500]},
                status_code=500,
            )

    app.state.panel_v2 = {
        "core_version": CORE_VERSION,
        "plugins_dir": str(pdir),
        "web_dir": str(wdir),
        "records": {r.id: r for r in records},
    }

    # ------------------------------------------------------------ QMT feed 生命周期（4.1.3a-2）
    #
    # 默认**不启动**：测试环境没有 QMT，一旦 import 就连接会让跑测变慢变脏。
    # 打开方式只有 `PANEL_V2_FEED=1`（`run.ps1` 已设），即生产 8810 常驻。
    # start() 幂等（见 feed.QuoteFeed.start），所以 create_app() 被测试多次调用也不会多起线程。
    @app.on_event("startup")
    def _start_feed() -> None:
        # 预警推送 sender 线程（幂等；没消息时阻塞在 queue.get，无 IO）
        from app.core import notify as _notify  # noqa: PLC0415
        _notify.start()
        # 盘后预热线程（2026-11-07）：读后台看板判断数据就绪，空闲时把首屏结果先算一遍。
        # 没清单/没就绪就空转，不产生请求；daemon 线程随进程退出。
        from app.core import prewarm as _prewarm  # noqa: PLC0415
        _prewarm.start(app)
        if os.environ.get("PANEL_V2_FEED", "0") != "1":
            return
        from app.core.feed import get_feed  # noqa: PLC0415 - 环境变量关着就不 import

        feed = get_feed()
        # 把已声明的订阅标的登记进去：目前没有插件声明，先留接口；
        # 前端/后端后续调 ctx.feed.ensure() 增量补。
        feed.start()
        logger.info("PANEL_V2_FEED=1，QMT feed 已启动")

    @app.on_event("shutdown")
    def _stop_feed() -> None:
        # 停机只 stop，不 reset：进程要退了，保留单例让状态可读（幂等 + 不抛）。
        from app.core import prewarm as _prewarm  # noqa: PLC0415
        _prewarm.stop()
        from app.core.feed import get_feed  # noqa: PLC0415

        get_feed().stop()

    return app


app = create_app()
