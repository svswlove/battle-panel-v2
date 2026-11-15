# -*- coding: utf-8 -*-
"""把外部代码依赖同步进 panel-v2（vendoring），并记录来源哈希。

**为什么要有这个脚本**：口径库（`app/lib/*.py`）必须落在项目内，但源文件是**口径唯一源**，
直接复制就有「两份实现会漂移」的风险。所以：

- 复制时把源文件的 sha256 记进 `app/lib/VENDOR.json`
- `--check` 比对源与副本：源变了 / 副本被改 → 报「副本已过期」
- 门禁落在 `app/tests/test_web_single_source.py`，由 `pytest app`（含 `cutover_gate.py`）带上

前端内容页**不走** vendoring：8810 直接零拷贝挂载 `engine/web`（`/legacy/*` 与根静态），
`app/web/legacy` 只保留 `shell.html` 这一份打了补丁的派生文件。
2026-11-04 之前那里有 10 个内容页死副本（1.52 MiB，改它们不生效），已删；别再塞回来。

数据文件（`data/*.db`、`data/panel/*.json`）**不复制**：它们体积大且每日更新，
属于「外部输入」而不是代码。路径集中在 `app/core/paths.py`，将来整体搬迁只改那里。

用法：
    python tools/sync_vendor.py          # 同步并写 VENDOR.json
    python tools/sync_vendor.py --check  # 只检查，不写（门禁用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # panel-v2
WS = ROOT.parents[1]                                    # OH-WorkSpace
LIB_DIR = ROOT / "app" / "lib"
WEB_LEGACY = ROOT / "app" / "web" / "legacy"
MANIFEST = LIB_DIR / "VENDOR.json"

# 口径库：源文件 → 目标名
LIBS = {
    "amv_contrib": WS / "夏天-activity" / "工具" / "amv_contrib.py",
    "amv_regime": WS / "夏天-activity" / "工具" / "amv_regime.py",
    "amv_state_tree": WS / "夏天-activity" / "工具" / "amv_state_tree.py",
    "amv_struct_tag": WS / "夏天-activity" / "工具" / "amv_struct_tag.py",
    "panel_scores": WS / "夏天-activity" / "工具" / "panel_scores.py",
    "indicators": WS / "engine" / "common" / "indicators.py",
}

# 口径库的本地补丁（同 SHELL_PATCHES）：源文件写死本机绝对路径，副本要能独立跑。
# 同步后自动重施，所以 `do_sync` 不会把这些改动覆盖掉。
#
# **冻结项（LIBS_FROZEN）**：副本已与源分叉，不能再从源覆盖——否则会静默丢掉副本上的修复。
#   amv_regime：2026-11-09 副本加了「缺失值不当 0、不删位」的 None 语义（守门测试
#   `app/lib/tests/test_amv_regime_null_semantics.py`），源文件仍是旧逻辑。
#   合并需人工把修复回写源，再解冻。—— 2026-11-15 记
LIBS_FROZEN: dict[str, str] = {
    "amv_regime": "2026-11-09 None 语义修复只在副本上，源未合入",
}

LIB_PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    "amv_regime": ((
        "WS = r'C:\\Users\\65442\\Desktop\\OH-WorkSpace'",
        "# 2026-11-15 公开化：路径不再写死本机。PANEL_V2_WS 优先，否则按本文件位置推导\n"
        "# （app/lib/amv_regime.py → 上溯三层 = 项目根）。生产环境仍指向 OH-WorkSpace 根。\n"
        "WS = os.environ.get(\"PANEL_V2_WS\") or os.path.dirname(\n"
        "    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))",
    ),),
    "amv_contrib": ((
        '    W = r"C:\\Users\\65442\\Desktop\\OH-WorkSpace"',
        "    # 2026-11-15 公开化：不再写死本机路径；PANEL_V2_WS 优先，否则按本文件位置推导（app/lib → 项目根）。\n"
        "    W = os.environ.get(\"PANEL_V2_WS\") or os.path.dirname(\n"
        "        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))",
    ),),
}

# 旧前端：源目录 → 目标目录。
# 只同步 shell.html：其余内容页由 8810 零拷贝挂载 engine/web（app/core/main.py L488/L543/L546），
# 复制过来的副本没有任何路由指向，改了也不生效（2026-11-04 前的真实陷阱）。
# 结构守卫见 app/tests/test_web_single_source.py，别把内容页副本加回来。
WEB_SRC = WS / "engine" / "web"
WEB_FILES = ("shell.html",)


# 同步后必须重施的本地补丁（源文件里没有）。
# shell.html 的 ENGINE 改写：本项目要求自包含 + 同源，不能写死 8800。
SHELL_PATCHES = (
    (
        "const ENGINE = 'http://127.0.0.1:8800';",
        "// 2026-10-23 panel-v2 自包含托管：外壳与内容页同源加载，ENGINE 不再写死 8800。\n"
        "// 旧仓原版是 'http://127.0.0.1:8800'；本项目要求自包含，所以改成同源 origin：\n"
        "// 页面从 8810 打开就请求 8810，从 8800 打开就请求 8800（同一份代码两边通用）。\n"
        "// ⚠ 本文件由 `tools/sync_vendor.py` 从 `engine/web/shell.html` 同步而来，\n"
        "//   补丁由该脚本自动重施（见 SHELL_PATCHES），不要手改。\n"
        "const ENGINE = window.location.origin;\n"
        "// 内容页（index.html）被托管在 `/legacy/` 下：新版 `/` 是六卡壳，不是旧界面。\n"
        "const PAGE_BASE = /\\/legacy(\\/|$)/.test(window.location.pathname)\n"
        "  ? window.location.origin + '/legacy'\n"
        "  : window.location.origin;",
    ),
    (
        "f.src = ENGINE + '/?shell=1&theme='",
        "f.src = PAGE_BASE + '/?shell=1&theme='",
    ),
)


def _read_text_bytes(p: Path) -> tuple[str, str]:
    """读文本并返回 (内容, 行尾)，**不做行尾归一化**。

    2026-11-15：原先用 read_text/write_text，LF 文件会被写回成 CRLF，
    git 里整文件全是「190/190」看不出真实改动。这里按字节读写，行尾不变。
    """
    raw = p.read_bytes()
    return raw.decode("utf-8"), ("\r\n" if b"\r\n" in raw else "\n")


def _apply_patches(text: str, patches: tuple, nl: str, tag: str,
                   applied: list[str]) -> str:
    """把补丁字符串里的 \n 按文件实际行尾展开后再匹配/替换。"""
    for old, new in patches:
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if n.split(nl)[-1] in text and o not in text:
            continue                      # 已打过
        if o in text:
            text = text.replace(o, n, 1)
            applied.append(f"{tag} {o.strip()[:40]}")
    return text


def apply_web_patches() -> list[str]:
    """对同步后的前端文件重施本地补丁；返回应用了的补丁说明。"""
    applied: list[str] = []
    shell = WEB_LEGACY / "shell.html"
    if not shell.is_file():
        return applied
    src, nl = _read_text_bytes(shell)
    src = _apply_patches(src, SHELL_PATCHES, nl, "web", applied)
    shell.write_bytes(src.encode("utf-8"))
    return applied


def apply_lib_patches() -> list[str]:
    """对同步后的口径库副本重施本地补丁（路径不写死本机）；返回已应用的说明。"""
    applied: list[str] = []
    for name, patches in LIB_PATCHES.items():
        dst = LIB_DIR / f"{name}.py"
        if not dst.is_file():
            continue
        src, nl = _read_text_bytes(dst)
        new_src = _apply_patches(src, patches, nl, f"lib:{name}", applied)
        if new_src != src:
            dst.write_bytes(new_src.encode("utf-8"))
    return applied


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest() -> dict:
    if MANIFEST.is_file():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"libs": {}, "web": {}}


def plan() -> tuple[list[tuple[str, Path, Path]], list[str]]:
    """返回 (要同步的文件, 缺失的源文件)。"""
    jobs: list[tuple[str, Path, Path]] = []
    missing: list[str] = []
    for name, src in LIBS.items():
        if name in LIBS_FROZEN:
            continue                      # 已冻结：不从源覆盖（见 LIBS_FROZEN 注释）
        if not src.is_file():
            missing.append(f"lib:{name} <- {src}")
            continue
        jobs.append((f"lib:{name}", src, LIB_DIR / f"{name}.py"))
    for fname in WEB_FILES:
        src = WEB_SRC / fname
        if not src.is_file():
            missing.append(f"web:{fname} <- {src}")
            continue
        jobs.append((f"web:{fname}", src, WEB_LEGACY / fname))
    return jobs, missing


def do_check() -> int:
    jobs, missing = plan()
    mf = load_manifest()
    stale, ok = [], 0
    for key, src, dst in jobs:
        bucket, name = ("libs", key.split(":", 1)[1]) if key.startswith("lib:") \
            else ("web", key.split(":", 1)[1])
        rec = (mf.get(bucket) or {}).get(name) or {}
        if not dst.is_file():
            stale.append(f"{key}: 副本不存在 {dst}")
            continue
        src_hash = sha256(src)
        local_hash = sha256(dst)
        # 1) 源变了？（上游口径库/前端更新过）
        if rec.get("source_sha256") != src_hash:
            stale.append(f"{key}: 源文件已更新（{rec.get('source_sha256', '?')[:8]} → {src_hash[:8]}）"
                         f"—— 重跑 sync 再复验口径")
            continue
        # 2) 副本被改过？（除已知本地补丁外，不应与记录不符）
        expect = rec.get("local_sha256") or src_hash
        if local_hash != expect:
            stale.append(f"{key}: 副本与记录不符（期望 {expect[:8]} / 实际 {local_hash[:8]}）")
            continue
        ok += 1
    if missing:
        print("缺失的源文件（外部已被删除或改名）：")
        for m in missing:
            print("  -", m)
    if stale:
        print("副本过期：")
        for s in stale:
            print("  -", s)
        print("\n修复：python tools/sync_vendor.py")
        return 1
    print(f"vendor 一致：{ok} 个文件与源相同")
    return 0


def do_sync() -> int:
    jobs, missing = plan()
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    WEB_LEGACY.mkdir(parents=True, exist_ok=True)
    mf = {"libs": {}, "web": {}}
    for key, src, dst in jobs:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        digest = sha256(src)
        bucket = "libs" if key.startswith("lib:") else "web"
        mf[bucket][key.split(":", 1)[1]] = {
            "source": str(src.relative_to(WS)).replace("\\", "/"),
            "source_sha256": digest,
            "bytes": src.stat().st_size,
        }
        print(f"  synced {key:22s} {src.stat().st_size/1024:8.1f} KB")
    # 先写一次（不含 local_sha256），打补丁后再回填
    MANIFEST.write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    patched = apply_web_patches()
    patched += apply_lib_patches()
    if patched:
        print("\n已重施本地补丁：")
        for x in patched:
            print("  ~", x)
    # 回填本地哈希（补丁后的真实字节）
    for key, src, dst in jobs:
        bucket = "libs" if key.startswith("lib:") else "web"
        name = key.split(":", 1)[1]
        if dst.is_file():
            mf[bucket][name]["local_sha256"] = sha256(dst)
    MANIFEST.write_text(json.dumps(mf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写入 {MANIFEST.relative_to(ROOT)}")
    if missing:
        print("\n以下源文件不存在，已跳过：")
        for m in missing:
            print("  -", m)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查副本是否与源一致")
    args = ap.parse_args()
    return do_check() if args.check else do_sync()


if __name__ == "__main__":
    sys.exit(main())
