# -*- coding: utf-8 -*-
"""dashboard 卡片前端静态检查（只读，不跑浏览器）。

守的是 docs/task_fe_batch1b.md §契约 与 tools/cutover_gate.py 的静态禁令：
  · 无裸 hex（颜色一律 themes.css token）
  · 零 import（不跨插件、不引依赖）；不写全局；不裸 fetch
  · 只吃 ctx.api.get；数据端点走 root 挂载的 /api/* 绝对路径
  · 刷新纪律：refreshEvery 3000 + _setHtml 同款「内容比对后再写」（防闪）
  · 账本版布局：KPI 六格 / 持仓 14 列 / 今日成交 / 活跃单 / 流水 / 三道门
  · 本批只读：撤单按钮渲染但点击给「批次 2 接」提示
"""
import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
FRONTEND_JS = PLUGIN_DIR / "frontend.js"
MANIFEST = PLUGIN_DIR / "plugin.json"

# 与 tools/cutover_gate.py::_check_no_write_code 同一份禁令（此处只守本插件前端）
BANNED = (
    ".write_text(", ".write_bytes(", ".mkdir(", ".touch(", ".unlink(", ".rmdir(", ".symlink_to(",
    "os.remove(", "os.unlink(", "os.rename(", "os.replace(", "os.makedirs(", "shutil.rmtree(",
    "shutil.move(", "shutil.copy", "json.dump(", ".to_sql(", ".to_csv(", ".to_parquet(",
    "INSERT INTO", "INSERT OR", "UPDATE ", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
    "requests.post", "requests.put", "requests.delete", "requests.patch",
    "urllib.request.urlopen", "httpx.post", "httpx.put", "subprocess.", "os.system(", "os.popen(",
    "Popen(",
)

OPEN_WRITE_RE = re.compile(r"""open\s*\([^)]{0,240}?,\s*["'](?:w|a|wb|ab|x|xb)["']""")


def _src() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 主题 / 颜色


def test_no_raw_hex_colors() -> None:
    """任务书 §契约 3：颜色一律走 token，禁裸 hex。"""
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_uses_theme_tokens() -> None:
    src = _src()
    for token in ("var(--up)", "var(--down)", "var(--warn)", "var(--danger)", "var(--dim)",
                  "var(--accent)", "var(--card2)"):
        assert token in src, "缺少主题 token %s" % token


def test_up_is_red_down_is_green() -> None:
    """任务书 §功能 3：红涨绿跌（--up 走红、--down 走绿，涨用 up 类）。"""
    src = _src()
    assert re.search(r"\.db-root \.up\{color:var\(--up\)\}", src)
    assert re.search(r"\.db-root \.down\{color:var\(--down\)\}", src)
    assert "return n > 0 ? 'up' : 'down';" in src


# ---------------------------------------------------------------- 写盘 / 依赖


def test_no_write_or_subprocess_tokens() -> None:
    src = _src()
    hits = [t for t in BANNED if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src), "frontend.js 出现 open(...,'w') 写模式"


def test_zero_imports() -> None:
    """无框架无构建：不引依赖、不跨插件 import。"""
    assert not re.search(r"^\s*import\s", _src(), re.M), "frontend.js 不应有任何 import"


def test_no_global_writes() -> None:
    """卡片只操作自己的 el，不写全局变量。"""
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _src())


# ---------------------------------------------------------------- 端点


def test_only_ctx_api_no_bare_fetch() -> None:
    src = _src()
    assert "fetch(" not in src, "应统一走 ctx.api.get，不直接 fetch"
    assert "api.get(" in src


def test_consumes_root_endpoints() -> None:
    """数据端点全部是 root 挂载的绝对 /api/ 路径（shell.js 不拼插件前缀）。"""
    src = _src()
    for path in ("/api/ledger?mode=", "/api/dashboard.json", "/api/gates", "/api/status"):
        assert "'" + path in src or '"' + path in src, "缺少端点 %s" % path
    calls = re.findall(r"""api\.get\(\s*['"]([^'"]+)['"]""", src)
    assert calls, "找不到 api.get 调用"
    for path in calls:
        assert path.startswith("/api/"), "读端点必须用绝对 /api/ 路径：%s" % path


# ---------------------------------------------------------------- 刷新纪律（防闪）


def test_refresh_every_is_3000() -> None:
    assert re.search(r"const REFRESH_MS = 3000;", _src()), "任务书 §功能 8：refreshEvery 3000"
    assert re.search(r"refreshEvery:\s*REFRESH_MS", _src())


def test_sethtml_cache_compare() -> None:
    """任务书 §功能 8：照搬旧版 _setHtml —— 内容比对后才写 DOM，避免 3s 重建闪烁。"""
    src = _src()
    m = re.search(r"const setHtml = \(el, html, key\) => \{(.+?)\n      \};", src, re.S)
    assert m, "找不到 setHtml 实现"
    body = m.group(1)
    assert "S.html[key] === html" in body, "必须与上次写入的 HTML 比对"
    assert "return" in body, "命中缓存必须直接返回，不写 DOM"
    assert "el.innerHTML = html" in body


def test_shell_built_once() -> None:
    """骨架只在 mount 里建一次；refresh 只走 load → render（不重建 root）。"""
    src = _src()
    mount_start = src.index("async mount(root, ctx)")
    refresh_start = src.index("async refresh()")
    mount_body = src[mount_start:refresh_start]
    refresh_body = src[refresh_start:]
    assert "root.append(wrap)" in mount_body, "骨架应在 mount 里搭一次"
    assert "innerHTML" not in refresh_body, "refresh 不应整块重建 DOM"
    assert "S.load()" in refresh_body, "refresh 只应重拉数据并走比对写入"


def test_refresh_skips_inflight() -> None:
    """上一轮没回来不叠请求（3s tick 防堆积）。"""
    src = _src()
    assert "if (S.loading) return;" in src


# ---------------------------------------------------------------- 布局 / 功能


def test_kpi_six_cells() -> None:
    src = _src()
    for label in ("总资产", "可用", "市值", "当日盈亏", "持仓盈亏", "现金占比"):
        assert "'" + label + "'" in src, "KPI 缺格：%s" % label


def test_position_table_fourteen_columns() -> None:
    src = _src()
    m = re.search(r"const POS_HEADS = \[(.+?)\];", src, re.S)
    assert m, "找不到 POS_HEADS"
    heads = re.findall(r"'([^']+)'", m.group(1))
    assert heads == ["代码 / 名称", "余额", "可用", "冻结", "成本价", "现价", "参考盈亏", "盈亏%",
                     "当日盈亏", "当日%", "市值", "仓位", "今买", "今卖"], heads
    assert "cost_fallback" in src, "price_source=cost_fallback 需灰显「成本」"
    assert "src-tag" in src


def test_empty_states() -> None:
    """任务书 §功能 9：paper 空账本必须正常渲染，各自 muted 文案，不塌。"""
    src = _src()
    assert "无持仓" in src
    assert "今日无成交" in src
    assert "无活跃单" in src
    assert "今日无流水" in src


def test_ledger_mode_switch_persisted() -> None:
    """任务书 §功能 2：实盘/纸面切换，localStorage 记忆（沿用旧版键名）。"""
    src = _src()
    assert "panel_ledger_mode" in src
    assert "localStorage.getItem" in src and "localStorage.setItem" in src
    assert "switchMode('live')" in src and "switchMode('paper')" in src


def test_cancel_button_is_readonly_this_batch() -> None:
    """任务书 §功能 6：撤单按钮照旧渲染，点击只提示「批次 2 接」，不发写请求。"""
    src = _src()
    assert "data-cancel=\"" in src and "data-ccancel=\"" in src
    assert "批次 2" in src
    assert "api.post(" not in src, "本批只读：不应有任何写请求"


def test_gates_strip_rendered() -> None:
    """任务书 §功能 7：三道门灯条（状态色走 token，判定全在服务端）。"""
    src = _src()
    assert "gate-lamp" in src
    for cls in ("g-GREEN", "g-YELLOW", "g-RED", "g-UNKNOWN"):
        assert cls in src
    for gate in ("gate1", "gate2", "gate3"):
        assert gate in src


def test_engine_badges() -> None:
    """任务书 §功能 1：引擎/执行器/看门狗/急停/签收/心跳 徽章同款齐全。"""
    src = _src()
    for token in ("执行器 ", "看门狗 ", "已急停", "已签收", "心跳", "纸面 [PAPER]", "实盘"):
        assert token in src, "引擎徽章缺：%s" % token
    assert "heartbeat_age_sec" in src and "90" in src, "执行器心跳超时阈值要保留"


def test_open_kline_attribute_reserved() -> None:
    """任务书 §不做：个股点击本批只用 data-open-kline 属性预留。"""
    src = _src()
    assert 'data-open-kline="' in src


# ---------------------------------------------------------------- 卡片声明


def test_manifest_card_matches_frontend() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards = data["frontend"]["cards"]
    ids = [c["id"] for c in cards]
    assert ids == ["dashboard.main"]
    assert len(ids) == len(set(ids)), "卡片 id 必须唯一"
    src = _src()
    assert "export default" in src
    assert re.search(r"id: 'dashboard\.main'", src), "frontend.js 卡片 id 与 manifest 不一致"
    assert "mount(" in src and "unmount(" in src and "refresh(" in src
