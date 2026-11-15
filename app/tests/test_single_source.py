# -*- coding: utf-8 -*-
"""单一实现守卫（2026-11-07）。

口径：概念/复盘解析只能有一份实现，唯一实体 = panel-v2 的三个插件模块：
    app/plugins/concept/source.py
    app/plugins/concept/render.py
    app/plugins/review/source.py
旧 `engine/common/` 三份已退役删除；本测试防它们复活，也防有人把其它副本拷到别处。

检测手段（双保险）：
1. 墓碑断言：engine/common 三份路径不得再出现；
2. 全仓扫描（排除 envs/backup/归档/vendor/缓存/临时目录）：
   a. 标记函数组——同一文件定义齐一组「只有该解析器才会有」的函数名 → 命中；
   b. AST 指纹——与唯一实现里的函数体（去 docstring/注解后）完全同构，且命中 ≥2 个
      （≥8 行的函数才算）→ 命中。改名副本靠 b 抓，轻微改写的副本靠 a 抓。

白名单只允许精确到单个文件 + 写明理由；禁止整类豁免（如「compat 全免」）。
"""
from __future__ import annotations

import ast
import copy
import hashlib
import os
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]          # projects/panel-v2
WS = PROJECT_ROOT.parents[1]                                # OH-WorkSpace

CANON = {
    "concept_source": PROJECT_ROOT / "app" / "plugins" / "concept" / "source.py",
    "concept_render": PROJECT_ROOT / "app" / "plugins" / "concept" / "render.py",
    "review_source": PROJECT_ROOT / "app" / "plugins" / "review" / "source.py",
}
TOMBSTONES = [
    WS / "engine" / "common" / "concept_source.py",
    WS / "engine" / "common" / "concept_render.py",
    WS / "engine" / "common" / "review_source.py",
]

# 目录名（小写精确匹配）整棵跳过：环境/备份/归档/缓存/临时，不是现行代码
SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".pytest_cache", ".venv", "envs", "backup", "backups",
    "_vendor_src", "node_modules", "dist", "build", "downloads", "资料库",
    "oh-媒体库", "归档", "_归档", "attic", "_tmp", "tmp", "临时",
}

# 只有对应解析器才会同时定义的函数名组
MARKER_GROUPS = {
    "concept_source": ("_parse_active", "_parse_watch", "_parse_chain", "_parse_archived"),
    "concept_render": ("_linkify_seg", "linkify_stock_names", "md_to_html"),
    "review_source": ("scan_candidates", "pick_latest", "render_cached", "cache_key"),
}

MIN_LINES = 8        # 小于 8 行的函数不进 AST 指纹（太小，容易误报）
MIN_HITS = 2         # 命中 ≥2 个同构函数才判副本（单个同构可能是合理复用）

# 快速预筛：文件里一个都不出现 → 不可能包含这三份解析器（含改名副本：字符串常量通常留着）
PREFILTER_TOKENS = (
    "概念与催化总表", "概念概况缺失", "核心标的", "观察池", "产业链链位",
    "load_index", "load_detail", "linkify_stock_names", "_parse_active", "_stage_short",
    "scan_candidates", "pick_latest", "render_cached", "cache_key", "charts_version",
    "日复盘 ", "复盘 MD",
)

# 精确白名单：路径 → 理由（禁止整类豁免；当前为空）
WHITELIST: dict[str, str] = {}


def _norm_func(node: ast.AST) -> str:
    """函数节点规范化：去 docstring、去参数/返回值注解，再 ast.dump。"""
    node = copy.deepcopy(node)
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n.returns = None
            if (n.body and isinstance(n.body[0], ast.Expr)
                    and isinstance(n.body[0].value, ast.Constant)
                    and isinstance(n.body[0].value.value, str)):
                n.body = n.body[1:] or [ast.Pass()]
        elif isinstance(n, ast.arg):
            n.annotation = None
        elif isinstance(n, ast.AnnAssign):
            n.annotation = None
    return ast.dump(node, annotate_fields=False, include_attributes=False)


def _canon_hashes() -> dict[str, tuple[str, str, int]]:
    """{函数体指纹: (模块名, 函数名, 行数)}（只收 ≥ MIN_LINES 行的）。"""
    out: dict[str, tuple[str, str, int]] = {}
    for mod_name, path in CANON.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lines = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
            if lines < MIN_LINES:
                continue
            h = hashlib.sha1(_norm_func(node).encode("utf-8")).hexdigest()
            out.setdefault(h, (mod_name, node.name, lines))
    return out


def _iter_py_files():
    for root, dirs, files in os.walk(WS):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIR_NAMES]
        for name in files:
            if not name.endswith(".py"):
                continue
            p = Path(root) / name
            if p in CANON.values():
                continue
            try:
                if p.stat().st_size > 300_000:
                    continue
            except OSError:
                continue
            yield p


def _scan_file(path: Path, canon: dict[str, tuple[str, str, int]]) -> dict | None:
    """单文件扫描 → 命中描述或 None。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # a. 标记函数组
    for group, names in MARKER_GROUPS.items():
        if all(("def %s(" % n) in text for n in names):
            return {"path": path, "kind": "marker", "group": group,
                    "detail": "、".join(names)}
    # b. AST 指纹（先过快速预筛，避免解析全仓 4800+ 文件）
    if not any(tok in text for tok in PREFILTER_TOKENS):
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
    except SyntaxError:
        return None
    matched = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lines = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
        if lines < MIN_LINES:
            continue
        hit = canon.get(hashlib.sha1(_norm_func(node).encode("utf-8")).hexdigest())
        if hit:
            matched.append("%s.%s" % (hit[0], node.name))
    if len(matched) >= MIN_HITS:
        return {"path": path, "kind": "ast", "detail": "、".join(sorted(set(matched)))}
    return None


def scan() -> list[dict]:
    """全仓扫描，返回命中列表（已剔除精确白名单）。"""
    canon = _canon_hashes()
    hits = []
    for path in _iter_py_files():
        if str(path) in WHITELIST:
            continue
        hit = _scan_file(path, canon)
        if hit:
            hits.append(hit)
    return hits


def test_engine_common_copies_are_gone():
    alive = [str(p) for p in TOMBSTONES if p.exists()]
    assert not alive, "旧 engine/common 副本复活（唯一实现见 app/plugins/）：\n" + "\n".join(alive)


def test_no_duplicate_implementations_in_workspace():
    hits = scan()
    assert not hits, "发现疑似概念/复盘解析副本：\n" + "\n".join(
        f"{h['path']}（{h['kind']}：{h['detail']}）" for h in hits)


def test_whitelist_is_small_and_explicit():
    assert len(WHITELIST) <= 10, "白名单超过 10 条：AST 指纹定得太松，回头调"
    for path, reason in WHITELIST.items():
        assert reason.strip(), f"白名单缺理由：{path}"


def test_scanner_catches_a_real_copy(tmp_path: Path):
    """守卫自检：把唯一实现里的两个大函数原样拷到临时文件，必须被抓到。"""
    source = CANON["concept_source"].read_text(encoding="utf-8")
    tree = ast.parse(source)
    picked = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            lines = (node.end_lineno or node.lineno) - node.lineno + 1
            if lines >= MIN_LINES and node.name in ("_parse_active", "_parse_watch", "load_index"):
                seg = ast.get_source_segment(source, node)
                if seg:
                    picked.append(seg)
    assert len(picked) >= 2, "自检取样不足：唯一实现结构变了？"
    copy_file = tmp_path / "copied_parser.py"
    copy_file.write_text("\n\n".join(picked) + "\n", encoding="utf-8")
    canon = _canon_hashes()
    hit = _scan_file(copy_file, canon)
    assert hit is not None and hit["kind"] == "ast", f"副本未被识别：{hit}"


if __name__ == "__main__":   # 手动全仓实跑：python -m app.tests.test_single_source
    _files = list(_iter_py_files())
    _canon = _canon_hashes()
    _hits = scan()
    print(f"scanned_py={len(_files)} canonical_funcs={len(_canon)} "
          f"whitelist={len(WHITELIST)} hits={len(_hits)}")
    for _h in _hits:
        print(f"HIT {_h['path']} ({_h['kind']}: {_h['detail']})")

