# -*- coding: utf-8 -*-
"""M4 切流门禁：每天观察期直接跑这一条。

只读检查 + 子进程调用现有 diff 工具；不写生产数据、不修改任何插件。
用法：
  python tools/cutover_gate.py
  python tools/cutover_gate.py --skip-pytest
  python tools/cutover_gate.py --skip-shadow
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PY = os.environ.get("PANEL_V2_PYTHON") or sys.executable
NEW = os.environ.get("PANEL_V2_NEW_BASE", "http://127.0.0.1:8810")


def _request(url: str, method: str = "GET", timeout: int = 20) -> tuple[int, Any]:
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    started = time.perf_counter()
    # 子进程统一 UTF-8：计划任务会话没有 PYTHONUTF8/PYTHONIOENCODING，默认用 CP936 解码，
    # 遇子进程 UTF-8 输出会在 _readerthread 抛 UnicodeDecodeError（10-23 08:50 日检假红根因）。
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()[-1200:]
    except subprocess.TimeoutExpired:
        return 124, "timeout after %ss" % timeout


def _check_health(rows: list[dict]) -> None:
    code, body = _request(NEW + "/api/health")
    ok = code == 200 and isinstance(body, dict) and body.get("ok") is True
    rows.append({"item": "8810 /api/health", "ok": ok, "detail": f"HTTP {code} ok={body.get('ok') if isinstance(body, dict) else body}"})

    code, body = _request(NEW + "/_plugins/health")
    plugins = body.get("plugins", []) if isinstance(body, dict) else []
    ok = code == 200 and isinstance(body, dict) and body.get("ok") is True and len(plugins) >= 4
    bad = [p.get("id") for p in plugins if not p.get("ok")]
    rows.append({"item": "全部插件健康", "ok": ok, "detail": f"HTTP {code} n={len(plugins)} bad={bad}"})


def _check_shadow(rows: list[dict]) -> None:
    """回归守门：与 8810 自身基线比**接口结构**（2026-10-26 旧端口下线后改口径）。

    改前：跑 `tools/shadow_check.py --once`，它同时打旧端 8800/8002 与新端 8810 逐值比对。
    改后：旧端已退役，旧的那条判据无法再计算（会天天 error）。改为跑
    `tools/capture_baseline.py --check`：拿当前 8810 响应与已落盘的基线比结构，
    抓「字段增删 / 类型改变」这类无意破坏。
    数值不参与判定：盘中实时值、时间戳本来就每笔在变。
    逐值比对工具 shadow_check 保留，需要重做迁移时仍可手动跑。
    """
    code, out = _run([PY, "tools/capture_baseline.py", "--check"], timeout=180)
    rows.append({"item": "8810 接口结构回归", "ok": code == 0,
                 "detail": f"exit={code} {out[-300:]}"})


def _check_amv(rows: list[dict]) -> None:
    """AMV 三份快照：校验**文件与 8810 端点逐值一致**（2026-10-26 旧端口下线后改口径）。

    改前：`tools/diff_amv.py` 比对 8800 与 8810。但 8800 本身也只是读
    `data/panel/amv_live.json` / `amv_regime_latest.json` / `position_cap_latest.json`
    再原样返回（生产者分别是 `executor/amv_live.py`、`夏天-activity/工具/amv_regime_export.py`、
    `夏天-activity/工具/position_cap_amv.py`），所以那个比对实质是「文件 == 端点」。
    旧端退役后直接比文件与端点，反而更直接、且不再依赖任何旧进程。
    """
    code, out = _run([PY, "tools/check_amv_parity.py"], timeout=60)
    rows.append({"item": "AMV 快照 文件↔端点", "ok": code == 0, "detail": f"exit={code} {out[-300:]}"})


def _check_no_write(rows: list[dict]) -> None:
    # 2026-10-24 周末冲刺：battle_plan 升级为原生写域（sign/toggle 等受控写 battle_plan.json），
    # 写域实现在 battle_plan_rw（mount=root，根路径 /api/sign 等），原 /api/p/battle_plan/plan
    # 保持只读。探针改为：根路径写域存在（POST /api/sign 空 body 应回 4xx 而非 404）+ 原插件读路径
    # 仍拒绝盲写（POST /api/p/battle_plan/plan 仍应 405）。
    try:
        code_rw, _ = _request(NEW + "/api/sign", method="POST")
        code_ro, _ = _request(NEW + "/api/p/battle_plan/plan", method="POST")
        ok = code_rw != 404 and code_ro == 405
        rows.append({"item": "battle_plan 写域接管", "ok": ok,
                     "detail": f"POST /api/sign -> HTTP {code_rw}（非 404=写域在）; POST /api/p/.../plan -> {code_ro} (期望 405)"})
    except Exception as exc:  # noqa: BLE001
        rows.append({"item": "battle_plan 写域接管", "ok": False, "detail": f"POST 异常: {type(exc).__name__}: {exc}"})


def _check_kline(rows: list[dict]) -> None:
    code, body = _request(NEW + "/api/p/kline/bars/000001.SH?limit=3")
    ok = code == 200 and isinstance(body, dict) and body.get("intraday") is False and bool(body.get("last_bar_date")) and bool(body.get("notice"))
    rows.append({
        "item": "kline 边界声明",
        "ok": ok,
        "detail": f"HTTP {code} intraday={body.get('intraday') if isinstance(body, dict) else None} last={body.get('last_bar_date') if isinstance(body, dict) else None}",
    })


def _check_no_write_code(rows: list[dict]) -> None:
    """静态扫描：插件源码不得出现任何写操作（切流安全/回滚零数据的前提）。

    扫 backend.py **与 frontend.js**；覆盖 open 写模式全谱、Path 写方法、JSON/SQL 写、
    删改移、以及 `ctx.data_dir`（允许写本地输出目录，但不能拿来当生产写入通道）。
    """
    banned = [
        # 2026-10-23 修正：不再用 `, "w")` 这类裸子串 —— 实测 `p not in ("d", "w")`
        # 这种普通元组也会被当成 open 写模式误报（compat_kline 撞上过）。
        # 写模式改由下面 OPEN_WRITE_RE 在 `open(` 上下文里判。
        # Path / shutil / os 写与删
        ".write_text(", ".write_bytes(", ".mkdir(", ".touch(", ".unlink(",
        ".rmdir(", ".symlink_to(",
        "os.remove(", "os.unlink(", "os.rename(", "os.replace(",
        "os.makedirs(", "shutil.rmtree(", "shutil.move(", "shutil.copy",
        # JSON / SQL / 序列化写入
        "json.dump(", ".to_sql(", ".to_csv(", ".to_parquet(",
        "INSERT INTO", "INSERT OR", "UPDATE ", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
        # 网络与子进程（2026-10-23 复审：requests.post / subprocess 之前全漏）
        "requests.post", "requests.put", "requests.delete", "requests.patch",
        "urllib.request.urlopen", "httpx.post", "httpx.put",
        "subprocess.", "os.system(", "os.popen(", "Popen(",
        # 输出目录：允许存在，但写成敏感动作时必须人工看
        # （ctx.data_dir 本身不算违规；这行只防范“拿 data_dir 当路径变量写生产”的隐式写）
    ]
    # open 的写/追加模式：必须在 `open(` 之后的参数里，不能是普通字符串/元组。
    open_write_re = re.compile(r"open\s*\([^)]{0,240}?,\s*[\"'](?:w|a|wb|ab|x|xb)[\"']")
    open_kw_write_re = re.compile(r"open\s*\([^)]{0,240}?mode\s*=\s*[\"'](?:w|a|wb|ab|x|xb)[\"']")
    hits: list[str] = []
    plugins_root = ROOT / "app" / "plugins"
    # 2026-10-23 实测：只扫 backend.py / frontend.js 时，把写代码放进 `helpers.py` 就能绕过
    # （探针 `_probe_write.py` 实测报 clean）。改为扫插件目录下所有 .py/.js，排除 tests/
    # （测试合法地写 tmp_path）。
    files = sorted(
        p for p in list(plugins_root.glob("*/**/*.py")) + list(plugins_root.glob("*/**/*.js"))
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in banned:
            if token in text:
                hits.append(f"{path.parent.name}/{path.name}:{token.strip()}")
        if open_write_re.search(text) or open_kw_write_re.search(text):
            m = (open_write_re.search(text) or open_kw_write_re.search(text))
            hits.append(f"{path.parent.name}/{path.name}:open-write:{m.group(0)[:40]}")
    rows.append({"item": "插件无写代码", "ok": not hits,
                 "detail": "clean (%d files)" % len(files) if not hits else "; ".join(hits[:6])})


def _check_pytest(rows: list[dict]) -> None:
    # 2026-10-24 嗅嗅审 P1-1：补 compat / core 两个目录——批次 0 起壳层端点与 shell 路由都在
    # 这两处，漏掉会让门禁对自家改动的测试盲区（当时门禁 pytest 只跑 244 项，全量 721）。
    # 2026-10-24 S2 补：compat_kline 68 例此前不在门禁清单。
    # 2026-10-24 收尾（01）：**改成扫整个 app/**。手工枚举目录的毛病是“新插件一开张就漏”
    # —— 此前 compat_kline(68) / selection(81) / intraday / core/paper(111) 四个目录
    # 全都在清单外，门禁一直打印「pytest 全套=OK」却少跑 260+ 例。与其每周补一个名字，
    # 不如让覆盖范围自然跟随目录结构；将来加插件不需要再改这里。
    code, out = _run([PY, "-m", "pytest", "app", "tools/test_shadow_comparators.py", "-q"],
                     timeout=420)
    rows.append({"item": "pytest 全套", "ok": code == 0, "detail": f"exit={code} {out[-300:]}"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--skip-shadow", action="store_true")
    args = ap.parse_args()

    rows: list[dict] = []
    started = time.perf_counter()
    _check_health(rows)
    if not args.skip_shadow:
        _check_shadow(rows)
    _check_amv(rows)
    _check_no_write(rows)
    _check_no_write_code(rows)
    _check_kline(rows)
    if not args.skip_pytest:
        _check_pytest(rows)

    ok = all(r["ok"] for r in rows)
    elapsed = round(time.perf_counter() - started, 1)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# M4 切流门禁最新结果",
        "",
        f"- 时间：{now}",
        f"- 结论：**{'PASS' if ok else 'FAIL'}**",
        f"- 耗时：{elapsed}s",
        f"- 端点：{NEW}",
        "",
        "| 项 | 结果 | 证据 |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['item']} | {'PASS' if r['ok'] else 'FAIL'} | {str(r['detail']).replace('|', '/')[:300]} |")
    lines += ["", "## 说明", "", "任一项 FAIL 即退出码 1；本脚本不写生产数据、不改旧端口。"]
    (ROOT / "docs" / "cutover_gate_latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(("PASS" if ok else "FAIL") + f"  {elapsed}s  " + "  ".join(f"{r['item']}={'OK' if r['ok'] else 'FAIL'}" for r in rows))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
