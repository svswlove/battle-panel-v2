# -*- coding: utf-8 -*-
"""watchlist 卡片前端静态检查（只读，不跑浏览器）。

守的是 docs/task_fe_batch1a.md §契约 与 tools/cutover_gate.py 的静态禁令：
  · 无裸 hex（颜色一律 themes.css token）
  · 零 import（不跨插件、不引依赖）
  · 无写盘 / 无子进程（门禁 banned tokens）
  · 只吃 ctx.api.get/post；写端点走 /api/watchlist/* 绝对路径（compat 层既定消费方式）
  · 刷新纪律：refreshEvery 5000 + 三种跳过条件
  · 卡片 id 与 manifest 一致
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
    """任务书 §契约 2：颜色一律走 token，禁裸 hex（#d33 / #178a4c 那批必须换掉）。"""
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", _src())
    assert not found, "frontend.js 出现裸 hex 色值：%s" % found[:8]


def test_uses_theme_tokens() -> None:
    src = _src()
    for token in ("var(--up)", "var(--down)", "var(--dim)", "var(--accent)"):
        assert token in src, "缺少主题 token %s" % token


# ---------------------------------------------------------------- 写盘 / 依赖


def test_no_write_or_subprocess_tokens() -> None:
    src = _src()
    hits = [t for t in BANNED if t in src]
    assert not hits, "frontend.js 命中门禁禁令：%s" % hits
    assert not OPEN_WRITE_RE.search(src), "frontend.js 出现 open(...,'w') 写模式"


def test_zero_imports() -> None:
    """无框架无构建：不引依赖、不跨插件 import（任务书 §契约 5）。"""
    assert not re.search(r"^\s*import\s", _src(), re.M), "frontend.js 不应有任何 import"


def test_no_global_writes() -> None:
    """卡片只操作自己的 el，不写全局变量（任务书 §契约 4）。"""
    assert not re.search(r"(^|\s)(?:window|globalThis)\.[A-Za-z_$]+\s*=", _src())


# ---------------------------------------------------------------- 端点


def test_only_allowed_endpoints() -> None:
    """只吃 ctx.api（不裸 fetch），写端点全部走 /api/watchlist/* 绝对路径。"""
    src = _src()
    assert "fetch(" not in src, "应统一走 ctx.api.get/post，不直接 fetch"
    assert "api.get(" in src and "api.post(" in src
    for path in ("/api/watchlist/add", "/api/watchlist/remove", "/api/watchlist/move",
                 "/api/watchlist/reorder", "/api/watchlist/group/add",
                 "/api/watchlist/group/rename", "/api/watchlist/group/remove",
                 "/api/watchlist/search", "/api/watchlist.json"):
        assert "'" + path in src or '"' + path in src, "缺少端点 %s" % path


def test_write_endpoints_all_absolute_api_path() -> None:
    """写操作必须带 /api/ 前缀 —— shell 的 makeCtx 对 /api/ 开头不拼插件前缀。"""
    src = _src()
    calls = re.findall(r"""api\.post\(\s*['"]([^'"]+)['"]""", src)
    assert calls, "找不到任何 api.post 调用"
    for path in calls:
        assert path.startswith("/api/"), "写端点必须用绝对 /api/ 路径：%s" % path


# ---------------------------------------------------------------- 刷新纪律


def test_refresh_every_is_5000() -> None:
    assert re.search(r"refreshEvery:\s*5000", _src()), "任务书 §功能 10：refreshEvery 必须是 5000"


def test_level_type_rendered_in_chinese() -> None:
    """2026-11-06 主人：自选池卡的关键位不许显英文类型（YELLOW/EK_OPEN…）。

    数据源 focus_levels.json 的 level_type 是枚举英文；页面必须走 LV_CN 映射。
    新增类型时回退原值（不吞信息），但存量四类必须中文。
    """
    src = _src()
    assert "const LV_CN" in src, "缺关键位中文映射表"
    for k, v in (("YELLOW", "黄线"), ("WHITE", "白线"),
                 ("EK_OPEN", "动能K下沿"), ("EK_CLOSE", "动能K上沿"),
                 ("N_LOW", "N型低点")):
        assert ("%s: '%s'" % (k, v)) in src, "LV_CN 缺 %s→%s" % (k, v)
    assert "esc(LV_CN[lv.level_type]" in src, "关键位渲染必须走 LV_CN 映射"
    assert "esc(lv.level_type || " not in src, "不许直接渲染英文 level_type"


def test_refresh_skips_three_cases() -> None:
    """任务书 §契约 6：搜索框聚焦/非空、拖拽中、分组名编辑中 三种情况跳过刷新。"""
    src = _src()
    m = re.search(r"async refresh\(\)\s*\{(.+?)\n    \},", src, re.S)
    assert m, "找不到 refresh() 实现"
    body = m.group(1)
    assert "S.drag" in body, "拖拽中必须跳过刷新"
    assert "S.editing" in body, "分组名编辑中必须跳过刷新"
    assert "activeElement" in body and ".trim()" in body, "搜索框聚焦/非空必须跳过刷新"
    assert "S.reload()" in body, "通过时应走同一份 loadAll"


# ---------------------------------------------------------------- 卡片声明


def test_manifest_card_matches_frontend() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cards = data["frontend"]["cards"]
    ids = [c["id"] for c in cards]
    assert ids == ["watchlist.main"]
    assert len(ids) == len(set(ids)), "卡片 id 必须唯一"
    src = _src()
    assert "export default" in src
    assert re.search(r"id:\s*['\"]watchlist\.main['\"]", src), "frontend.js 卡片 id 与 manifest 不一致"
    assert "mount(" in src and "unmount(" in src and "refresh(" in src


def test_cross_card_events_declared() -> None:
    """任务书 §不做：K 线/板块详情本批只派发 CustomEvent，无监听方时 console.debug。"""
    src = _src()
    assert "panel:open-kline" in src, "个股名称/代码需派发 panel:open-kline"
    assert "panel:open-sector" in src, "板块 chip 需派发 panel:open-sector"
    assert "console.debug" in src, "无监听方时应降级为 console.debug"


def test_mode_state_in_localstorage() -> None:
    """任务书 §功能 2：盘后/实时档状态存 localStorage 键 wl_mode_v1。"""
    src = _src()
    assert "wl_mode_v1" in src
    assert "localStorage.getItem" in src and "localStorage.setItem" in src


def test_meta_uses_relative_time() -> None:
    """主人标注 44：刷新时间不标到秒，用「刚刚 / N分钟前 / N小时前 / N天前」。"""
    src = _src()
    assert "const relTime = (s) =>" in src, "缺相对时间格式化"
    for word in ("刚刚", "分钟前", "小时前", "天前", "今天", "昨天"):
        assert word in src, "相对时间缺 %s 档" % word
    assert "relTime(d.quote_asof)" in src, "卡头行情时间未走 relTime"


# ---------------------------------------------------------------- 主人标注回归


def test_stock_subchart_is_amv_ma10_white() -> None:
    """主人标注 41：个股卡副图 = 活跃市值 + MA10 + 白线（同量纲同轴，不再画「收盘归一」）。

    口径源：/api/kline 的 bars[].amv / amvMa / amvWhite（都是「亿」，后端算好下发，前端零计算）。
    改前是「收盘归一 + AMV 归一」两条不同量纲的序列各自归一到 100 —— 只能看形状，看不出绝对量。
    """
    src = _src()
    assert "b.amvMa" in src and "b.amvWhite" in src, "副图缺 amvMa / amvWhite 取数"
    for cls in ("wl-amv", "wl-amv-ma", "wl-amv-wh"):
        assert 'class="' + cls + '"' in src, "副图缺 %s 的 path" % cls
    assert "wl-pc" not in src, "旧的「收盘归一」副图线应已移除"
    assert "na = AMV.map" not in src and "baseAmv" not in src, "旧的 AMV 归一到 100 的写法应已移除"
    # 三条线共用同一个 subExt（同轴），不能各自归一
    assert "extentOf([AMV, AMA, AWH]" in src, "副图三条线未共用同一个纵轴范围"


def test_open_speed_module_cache() -> None:
    """主人标注 47：切 Tab 回来不再整页重拉。

    shell 切 Tab 会 unmount 并删宿主节点，插件内的 S 全部销毁；
    缓存必须放模块作用域（MOD）才活得过 unmount，重开时先画缓存再后台刷新。
    """
    src = _src()
    assert "const MOD = {" in src, "缺模块级缓存"
    for key in ("eod: MOD.eod", "live: MOD.live", "secEod: MOD.secEod", "secLive: MOD.secLive"):
        assert key in src, "缓存表未指向模块作用域：%s" % key
    assert "PAYLOAD_CACHE_MS" in src and "applyData(pc.data)" in src, "重开未走缓存首帧"
    # 缓存不能把旧价当现价：超过窗口就等新数据
    assert "(Date.now() - pc.at) < PAYLOAD_CACHE_MS" in src, "缓存窗口未做新鲜度判断"
    # 并行取数：条目与关键位一起发，不再串行等两个 RTT
    assert "Promise.all([" in src and "levelsP" in src, "条目/关键位未并行取"
    # 盘后档数据没变就不重建 DOM；但日线缓存过期必须重建（否则图停在旧数据上）
    assert "eodStale()" in src and "lastSig" in src, "缺「无变化不重绘」或日线过期重绘"



def test_yellow_line_has_enough_history() -> None:
    """主人标注 52：黄线 = (MA14+MA28+MA57+MA114)/4，前 113 根必然为空。

    原来只拉 limit=60 根，画出来的 45 个点里只有末尾 7 个有黄线（实测）；
    改 160 根后 45 个点全部有值，与收盘线同长。
    """
    src = _src()
    assert "limit=160" in src, "日线请求根数不足（黄线算不出全区间）"
    assert "bars.slice(-45)" in src, "显示窗口应保持最近 45 根"


def test_open_uses_batch_sparks() -> None:
    """2026-11-04：盘后档一次批量取整组 spark，不再逐票拉 /api/kline。

    取证：37 票逐票拉时服务端票均 112.8 ms（mf 43.8 / AMV 31.9 / 行业 4.9 / 流通股 3.9，
    副图一个都不用），合计 4.2 s 串行、1.5 MB 载荷；批量后 64 KB、冷取 ~0.9 s、20 s 内热取 ~5 ms。
    """
    src = _src()
    assert "/api/kline_sparks" in src, "关键：盘后档未走批量 spark 端点"
    assert "loadStockSparks" in src and "buildEodRec" in src
    assert "tail=45" in src and "limit=160" in src, "批量端点窗口应与单票口径一致"
    # 批量端点挂了不能整卡空图：必须保留逐票兜底
    assert "hydrateEodOneByOne" in src, "缺逐票兜底（批量端点故障时整组卡会空图）"
    m = re.search(r"const hydrateEodStocks = \(stocks\) => \{(.+?)\n      \};", src, re.S)
    assert m, "找不到 hydrateEodStocks 实现"
    body = m.group(1)
    assert "loadStockSparks" in body, "盘后档主路径必须是批量"
    assert "hydrateEodOneByOne" in body, "批量失败时要退逐票"
