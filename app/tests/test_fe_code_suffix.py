# -*- coding: utf-8 -*-
"""壳层代码后缀口径（管线对齐旧端 `app.js::_suf6`，2026-10-30 补）

## 为什么补这条

旧仓 `engine/tests/test_kline_code_suffix.py` 守的是「6 位代码 → 交易所后缀」：北交所 920 段
曾被 `^[659]→SH` 规则误判成沪市，导致 `/api/kline/920179.SH` 空图并拖慢服务。那条测试连同
8002 工作台一起退役（2026-10-30），但**这条口径本身还活着** —— v2 壳在 `shell.js`
`normalizeStockCode()` 里重建了同一张表（K 线标题、列表上下文、F10 判定共用）。没有测试接住
就是覆盖缺口，本文件接住。

## 做法

从 `app/web/shell.js` 抽出 `normalizeStockCode` 函数体，用 node 真跑（不是文本断言），
零网络、零 IO。
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[2]
SHELL_JS = PANEL / "app" / "web" / "shell.js"
NODE = shutil.which("node")

# 6 位代码 → 期望后缀（后缀表口径，与旧端 to_ts_code 一致）
CASES = {
    "920002": "BJ", "920179": "BJ", "920819": "BJ",   # 北交所 920 段（回归点）
    "430047": "BJ", "830799": "BJ", "870199": "BJ",   # 北交所 43x/83x/87x
    "900901": "SH",                                    # 沪 B 股（不得被 920 规则误伤）
    "600519": "SH", "688981": "SH", "510300": "SH",    # 沪主板 / 科创板 / 沪基金
    "000001": "SZ", "002594": "SZ", "300750": "SZ", "301236": "SZ",
    "159583": "SZ",                                    # 深基金（1 段归深）
}


PASSTHRU = ["600519.SH", "920179.BJ", "000001.SZ", "881166.TI", "abc", "", ".SH"]


def _extract_fn(src: str) -> str:
    m = re.search(r"function normalizeStockCode\(code\)\s*\{[\s\S]*?\n\}", src)
    assert m, "shell.js 里找不到 normalizeStockCode（函数被改名/删除 → 壳层口径断链）"
    return m.group(0)


@pytest.fixture(scope="module")
def run_suffix():
    if not NODE:
        pytest.fail("需要 node 才能真跑前端后缀表（环境缺 node）")
    fn = _extract_fn(SHELL_JS.read_text(encoding="utf-8"))
    driver = fn + "\nconst out = {};\n"
    driver += "for (const c of %s) out[c] = normalizeStockCode(c);\n" % json.dumps(list(CASES))
    driver += "for (const c of %s) out['passthru:'+c] = normalizeStockCode(c);\n" % json.dumps(
        PASSTHRU)
    driver += "console.log(JSON.stringify(out));\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "suffix.mjs"
        path.write_text(driver, encoding="utf-8")
        res = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.parametrize("code,expect", sorted(CASES.items()))
def test_suffix_table(run_suffix, code, expect):
    got = run_suffix[code]
    assert got == code + "." + expect, f"{code} 判成 {got}，期望 {code}.{expect}"


@pytest.mark.parametrize("code", [c for c in PASSTHRU if "." in c])
def test_already_suffixed_passthrough(run_suffix, code):
    assert run_suffix["passthru:" + code] == code, "已带后缀的代码必须原样返回，不得二次拼接"


def test_invalid_code_untouched(run_suffix):
    assert run_suffix["passthru:abc"] == "abc"
    assert run_suffix["passthru:"] == ""
    assert run_suffix["passthru:.SH"] == ".SH"


def test_920_not_swallowed_by_9_to_sh():
    """顺序回归：920 判定必须在 `9 → SH` 之前，否则 920xxx 又变沪市。"""
    body = _extract_fn(SHELL_JS.read_text(encoding="utf-8"))
    i920 = body.index("92")
    i9 = body.index("head === '9'")
    assert i920 < i9, "920 分支被挪到 9→SH 之后（920xxx 会重新误判成沪市）"
