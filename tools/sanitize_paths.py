"""docs 与代码里的本机路径脱敏：先统计，再替换。

2026-11-15 公开前准备。规则：
  C:\\Users\\65442\\Desktop\\OH-WorkSpace  ->  OH-WorkSpace
  C:\\Users\\65442                        ->  <HOME>
反斜杠/正斜杠、大小写都覆盖；只动文本类文件，不动 data/、__pycache__。

用法：
  python _sanitize_paths.py            # 只看统计，不写
  python _sanitize_paths.py --apply     # 真替换
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # projects/panel-v2
EXTS = {".md", ".py", ".js", ".mjs", ".json", ".ps1", ".html", ".css", ".txt", ".cs", ".csproj"}
SKIP_DIRS = {"data", "__pycache__", "node_modules", ".git", "_vis", "_tmp", ".pytest_cache", "bin", "obj", "publish"}

HOME = re.compile(r"C:[\\/]Users[\\/]65442", re.I)
WS_PATH = re.compile(r"C:[\\/]Users[\\/]65442[\\/]Desktop[\\/]OH-WorkSpace", re.I)


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if p.stat().st_size > 4 * 1024 * 1024:
            continue
        yield p


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    files = list(iter_files())
    hit_files = 0
    total_ws = total_home = 0
    tails = collections.Counter()

    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        ws_n = len(WS_PATH.findall(text))
        home_n = len(HOME.findall(text))
        if not ws_n and not home_n:
            continue
        hit_files += 1
        total_ws += ws_n
        total_home += home_n
        # 记录剩余 <HOME> 后面跟的目录名，人工确认没有漏网的个人目录
        for m in HOME.finditer(text):
            nxt = text[m.end():m.end() + 40].lstrip("\\/").split("\\")[0].split("/")[0]
            tails[nxt.split()[0] if nxt.split() else "(空)"] += 1
        if apply:
            new = WS_PATH.sub("OH-WorkSpace", text)
            new = HOME.sub("<HOME>", new)
            if new != text:
                p.write_text(new, encoding="utf-8")

    print("扫描 %d 个文本文件；命中 %d 个" % (len(files), hit_files))
    print("  工作区绝对路径 %d 处 → OH-WorkSpace" % total_ws)
    print("  其余用户目录   %d 处 → <HOME>" % total_home)
    print("\n<HOME> 之后的名字分布（核对是否有个人目录暴露）：")
    for k, v in tails.most_common(15):
        print("  %-24s %d" % (k or "(空)", v))
    print("\n" + ("已写盘" if apply else "仅统计，未写盘（加 --apply 执行）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
