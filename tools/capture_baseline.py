# -*- coding: utf-8 -*-
"""旧端口退役前，抓 8810 响应的真值基线（一次性工具，2026-10-26）。

为什么必须在停 8800/8002 之前跑：
  `tools/shadow_check.py` 与 `tools/diff_amv.py` 的判据是「新端 == 旧端」。旧端停掉后，
  这条判据无法再计算，日检（PanelV2_DailyCheck → cutover_gate）会天天报 error。
  基线落盘后，日检改跑本工具的 `--check` 模式，不再依赖旧端活着。

基线模式的判据是**结构守门**，不是数值比对：
  停旧端后要守的不是「数没变」（盘中实时值本来就每笔在变、时间戳必变），
  而是「接口形状没意外变」——字段增删、类型改、列表元素形状变。
  所以比对的是「键路径集合 + 叶子类型」，数值不参与判定。
  旧端在役时的逐值比对（shadow_check）不删除，改天需要重做迁移仍可 `--old/--new` 用。

口径（与 shadow_check 一致）：
- 只发 HTTP GET，不连库、不写生产数据。
- 基线整份 JSON 原样存（留证用），但比对只走结构。

用法：
    python tools/capture_baseline.py            # 抓一轮，写 data/panel_v2/baseline/
    python tools/capture_baseline.py --check    # 与已有基线比结构（只读，exit 0/1）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shadow_check as sc  # noqa: E402

BASE_DIR = sc.ROOT / "data" / "panel_v2" / "baseline"
BASE_FILE = BASE_DIR / "baseline.json"


def _shape(obj, prefix="", out=None, max_depth=12) -> dict:
    """展平成 {键路径: 叶子类型}，用于结构比对。

    规则（刻意保守，只抓真结构变化）：
    - dict：递归到每个键；键集合变化 → 结构变
    - list：只取第一个元素的形状（用 `[]` 表示“数组元素形状”），列表长度不算结构
      （盘中序列长度天然随数据量变，拿长度判会天天红）
    - 空列表/空字典：记为 `empty`，不当错误（数据未就绪是常态）
    - 叶子：记类型名（str/int/float/bool/null），不记值
    时间戳/实时数值因此天然不影响判定。
    """
    if out is None:
        out = {}
    if max_depth <= 0:
        out[prefix or "$"] = "depth_limit"
        return out
    if isinstance(obj, dict):
        if not obj:
            out[prefix or "$"] = "empty"
            return out
        for k in sorted(obj, key=str):
            _shape(obj[k], f"{prefix}.{k}" if prefix else str(k), out, max_depth - 1)
    elif isinstance(obj, list):
        if not obj:
            out[prefix or "$"] = "empty_list"
            return out
        _shape(obj[0], f"{prefix}[]", out, max_depth - 1)
    else:
        out[prefix or "$"] = type(obj).__name__
    return out


def _shape_diff(base: dict, cur: dict) -> list[str]:
    """结构差异：新增键 / 缺失键 / 类型改变。"""
    out = []
    for k in sorted(set(base) - set(cur)):
        out.append(f"缺失 {k}（原 {base[k]}）")
    for k in sorted(set(cur) - set(base)):
        out.append(f"新增 {k}（{cur[k]}）")
    for k in sorted(set(base) & set(cur)):
        if base[k] != cur[k]:
            out.append(f"类型变 {k}：{base[k]} → {cur[k]}")
    return out


def _fetch_new(session, case) -> dict:
    return sc.fetch(session, sc.NEW_8810, case.new_path, case.new_params)


def capture(out: Path) -> int:
    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": "panel-v2-baseline/1", "Accept": "application/json"})
    doc = {"captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "new_base": sc.NEW_8810, "cases": {}}
    bad = []
    for case in sc.CASES:
        key = f"{case.domain}.{case.name}"
        r = _fetch_new(session, case)
        if not r.get("ok"):
            bad.append(f"{key}: {r.get('error')}")
            continue
        doc["cases"][key] = {"domain": case.domain, "name": case.name,
                             "new_path": case.new_path, "data": r["data"]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"captured {len(doc['cases'])} cases -> {out}")
    for b in bad:
        print(f"  MISS {b}")
    return 0 if not bad else 1


def check(base_file: Path) -> int:
    import requests
    if not base_file.is_file():
        print(f"基线不存在：{base_file}（先跑一次 capture）", file=sys.stderr)
        return 2
    doc = json.loads(base_file.read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers.update({"User-Agent": "panel-v2-baseline/1", "Accept": "application/json"})

    drift, errors, skipped = [], [], []
    for case in sc.CASES:
        key = f"{case.domain}.{case.name}"
        base = doc["cases"].get(key)
        if base is None:
            skipped.append(f"{key}: 基线缺此项")
            continue
        r = _fetch_new(session, case)
        if not r.get("ok"):
            errors.append(f"{key}: {r.get('error')}")
            continue
        diffs = _shape_diff(_shape(base["data"]), _shape(r["data"]))
        if diffs:
            drift.append(f"{key}: {len(diffs)} 处结构变化，例 {diffs[:3]}")

    print(f"baseline={base_file.name} captured_at={doc.get('captured_at')}")
    print(f"  structure drift={len(drift)} error={len(errors)} skipped={len(skipped)}")
    for line in drift:
        print(f"  DRIFT {line}")
    for line in errors:
        print(f"  ERROR {line}")
    return 1 if (drift or errors) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="8810 响应真值基线（旧端口退役后的回归锚点）")
    ap.add_argument("--check", action="store_true", help="与已有基线比对，不重写")
    ap.add_argument("--out", default="", help="基线文件路径（默认 data/panel_v2/baseline/baseline.json）")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else BASE_FILE
    return check(out) if args.check else capture(out)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.exit(main())
