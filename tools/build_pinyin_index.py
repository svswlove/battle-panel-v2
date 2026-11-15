"""生成拼音索引：名称 → 全拼 + 首字母（一次性构建，运行时不依赖 pypinyin）。

用法（需装 pypinyin 的 Python）：
    python tools/build_pinyin_index.py

数据源
  · 股票名：`data/stock_names.json::names`（eltdx 全量表 6 位码 → 名称）
  · 板块名：`data/amv_board.db::sector_meta(level, code, name)`
产物
  `engine/state/pinyin_index.json` = {
      "generated_at": "YYYY-MM-DD HH:MM:SS",
      "count": N,
      "items": { "<name>": {"f": "<全拼小写>", "i": "<首字母小写>"} }
  }

口径
  · 只对名称里**非 ASCII 字符**转拼音，ASCII 原样保留并转小写（ST/*ST/N/C/XD 等前缀仍可搜）；
  · 多音字按 pypinyin 默认（不加载自定义词典），只用作召回，不参与展示；
  · 名称重复（同名不同码）只存一份。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

WS = Path(__file__).resolve().parents[3]          # OH-WorkSpace
STOCK_NAMES = WS / "data" / "stock_names.json"
BOARD_DB = WS / "data" / "amv_board.db"
OUT = WS / "engine" / "state" / "pinyin_index.json"


def _pinyin(name: str) -> dict:
    from pypinyin import Style, lazy_pinyin  # noqa: PLC0415 - 仅构建期依赖

    txt = unicodedata.normalize("NFKC", name)   # 全角→半角（Ａ→A）
    full: list[str] = []
    ini: list[str] = []
    for ch in txt:
        if ch.isspace():                        # 「万 科Ａ」的中间空格不参与拼音
            continue
        if ch.isascii():
            low = ch.lower()
            full.append(low)
            ini.append(low)
            continue
        f = "".join(lazy_pinyin(ch, style=Style.NORMAL, errors="ignore"))
        i = "".join(lazy_pinyin(ch, style=Style.FIRST_LETTER, errors="ignore"))
        full.append(f)
        ini.append(i or (f[:1] if f else ""))
    return {"f": "".join(full), "i": "".join(ini)}


def main() -> int:
    names: dict[str, str] = {}
    if STOCK_NAMES.is_file():
        doc = json.loads(STOCK_NAMES.read_text(encoding="utf-8"))
        names.update({str(k): str(v) for k, v in (doc.get("names") or {}).items()})
    if BOARD_DB.is_file():
        try:
            conn = sqlite3.connect(f"file:{BOARD_DB}?mode=ro", uri=True)
            try:
                for _lv, _code, nm in conn.execute("SELECT level, code, name FROM sector_meta"):
                    if nm:
                        names[str(nm)] = str(nm)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print("board db skipped:", exc)
    if not names:
        print("no names found; abort")
        return 2

    items: dict[str, dict] = {}
    for nm in sorted(set(names.values())):
        nm = nm.strip()
        if not nm or nm in items:
            continue
        try:
            items[nm] = _pinyin(nm)
        except Exception as exc:  # noqa: BLE001 - 单个失败不阻断
            print("skip", nm.encode("unicode_escape").decode(), exc)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "count": len(items), "items": items}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("ok", len(items), "->", OUT)
    sample = items.get("贵州茅台") or {}
    print("sample guizhoumaotai:", sample.get("f"), sample.get("i"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
