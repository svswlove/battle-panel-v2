# -*- coding: utf-8 -*-
"""生成「改前」版 frontend.js（同一份源码，逐段确定性回退）。

用途：
  --mode chart  只回退主人标注 41（副图：收盘归一+AMV归一 → 回退成改前），用于同票 A/B 截图
  --mode all    回退标注 41 + 标注 47（模块级缓存/并行取数），用于跑改前的打开耗时基线

为什么不用 git：这个仓工作区里有大量别人的未提交改动，stash/checkout 会误伤；
而且 HEAD 里还没有今天 21:48 的 relTime 改动，直接 checkout 会连别人的改动一起回退。
这里每段都断言命中 1 次，保证「改前」就是真改前，不是手抄。

跑法：
  python app/plugins/watchlist/tests/gen_before_frontend.py --mode all \
      --out _vis/verify_20261028/frontend_before_all.js
"""
from __future__ import annotations

import argparse
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "frontend.js"

# ---------------------------------------------------------------- 标注 41（副图）
PAIRS_CHART = [
    (
        ".wl-sub .wl-amv{stroke:var(--up);stroke-width:1.2;fill:none}\n"
        ".wl-sub .wl-amv-ma{stroke:var(--warn);stroke-width:0.9;fill:none;stroke-dasharray:2 1}\n"
        ".wl-sub .wl-amv-wh{stroke:var(--fg);stroke-width:0.8;fill:none;opacity:0.7}",
        ".wl-sub .wl-pc{stroke:var(--dim);stroke-width:0.8;fill:none}\n"
        ".wl-sub .wl-amv{stroke:var(--accent);stroke-width:1.3;fill:none}",
    ),
    (
        "        if (sub) {\n"
        "          // 副图 = 活跃市值(红) + MA10(黄虚线) + 白线(白)，三条同量纲（亿），同轴直接叠\n"
        "          sub.innerHTML = '<path class=\"wl-amv\" d=\"' + s.sub.a + '\"/><path class=\"wl-amv-ma\" d=\"'\n"
        "            + s.sub.m + '\"/><path class=\"wl-amv-wh\" d=\"' + s.sub.w + '\"/>';\n"
        "        }",
        "        if (sub) sub.innerHTML = '<path class=\"wl-pc\" d=\"' + s.sub.c + '\"/><path class=\"wl-amv\" d=\"' + s.sub.a + '\"/>';",
    ),
    (
        "            const AMV = bars.map((b) => b.amv);\n"
        "            const AMA = bars.map((b) => b.amvMa);\n"
        "            const AWH = bars.map((b) => b.amvWhite);\n"
        "            const mainExt = extentOf([C, YL, WH], 0.08, 1);\n"
        "            const subExt = extentOf([AMV, AMA, AWH], 0.08, 0.5);",
        "            const AMV = bars.map((b) => b.amv);\n"
        "            const mainExt = extentOf([C, YL, WH], 0.08, 1);\n"
        "            const base = C[0];\n"
        "            const baseAmv = AMV[0];\n"
        "            const nc = C.map((v) => (v && base ? (v / base) * 100 : null));\n"
        "            const na = AMV.map((v) => (v && baseAmv ? (v / baseAmv) * 100 : null));\n"
        "            const subExt = extentOf([nc, na], 0.12, 0.5);",
    ),
    (
        "              rec.sub = {\n"
        "                a: sparkPath(AMV, subExt.lo, subExt.hi, 20),\n"
        "                m: sparkPath(AMA, subExt.lo, subExt.hi, 20),\n"
        "                w: sparkPath(AWH, subExt.lo, subExt.hi, 20),\n"
        "              };",
        "              rec.sub = {\n"
        "                c: sparkPath(nc, subExt.lo, subExt.hi, 20),\n"
        "                a: sparkPath(na, subExt.lo, subExt.hi, 20),\n"
        "              };",
    ),
]

# ---------------------------------------------------------------- 标注 47（打开速度）
PAIRS_PERF = [
    (
        "\n\n// ---- 跨挂载缓存（2026-10-28 主人标注 47「自选池打开速度太慢」）\n"
        "// shell 切 Tab 时 unmount 会删掉宿主节点，插件里的 S 随之销毁 —— 原来每次切回都重新拉\n"
        "// /api/watchlist.json（实测约 520ms）+ 24 条 /api/kline（约 700ms）。把取数结果放模块作用域，\n"
        "// 重开时先按缓存画一帧，再照常刷新（stale-while-revalidate），不改任何数据口径。\n"
        "const MOD = {\n"
        "  payload: { at: 0, data: null },      // /api/watchlist.json（含行情/多空/徽标）\n"
        "  levels: { at: 0, data: null },       // 本插件 /levels（关键位）\n"
        "  eod: new Map(), live: new Map(), secEod: new Map(), secLive: new Map(),\n"
        "};\n"
        "const PAYLOAD_CACHE_MS = 15000;        // 重开时可直接画的窗口；超过就等新数据，不显示旧价\n"
        "const LEVELS_CACHE_MS = 60000;",
        "",
    ),
    (
        "        // 下面四张表走模块作用域（MOD）：切 Tab 回来时命中缓存，不重新拉取\n"
        "        eod: MOD.eod,              // code -> 日线序列路径\n"
        "        live: MOD.live,            // code -> 当日分时点\n"
        "        secEod: MOD.secEod,        // 'L:<level>:<codes>' -> sector_card map\n"
        "        secLive: MOD.secLive,      // 'S:<codes>' / code -> 当日轨迹",
        "        eod: new Map(),            // code -> 日线序列路径\n"
        "        live: new Map(),           // code -> 当日分时点\n"
        "        secEod: new Map(),         // 'L:<level>:<codes>' -> sector_card map\n"
        "        secLive: new Map(),        // 'S:<codes>' / code -> 当日轨迹",
    ),
    (
        "      // ---- 渲染（骨架不动，只换内容）\n"
        "      // 盘后档数据没变化就不重建卡片：5s 轮询每次都全量重写 innerHTML 会白闪 + 白重绘。\n"
        "      // 实时档不跳（当日分时点位每秒在变，必须重画）；日线缓存过期也不跳（否则图会停在旧数据上）。\n"
        "      let lastSig = '';\n"
        "      const eodStale = () => {\n"
        "        for (const it of groupItems()) {\n"
        "          if (it.kind === 'stock' && !fresh(S.eod.get(it.code), EOD_TTL)) return true;\n"
        "        }\n"
        "        return false;\n"
        "      };\n"
        "      const dataSig = (d) => {\n"
        "        const items = (d && d.items) || [];\n"
        "        let s = String((d && d.quote_asof) || '') + '#' + S.mode + '#' + S.group + '#' + items.length;\n"
        "        for (const it of items) {\n"
        "          const q = it.quote || {};\n"
        "          s += '|' + it.code + ',' + it.group + ',' + it.order + ',' + q.price + ',' + q.change_pct\n"
        "            + ',' + it.regime + ',' + it.level + ',' + it.pct + ',' + it.div + ',' + it.flow\n"
        "            + ',' + ((it.badges || []).map((b) => (b && b.label) || '').join('+'));\n"
        "        }\n"
        "        return s;\n"
        "      };\n\n"
        "      const render = () => {",
        "      // ---- 渲染（骨架不动，只换内容）\n"
        "      const render = () => {",
    ),
    (
        "        renderMode();\n"
        "        const sig = dataSig(d) + '#' + (MOD.levels.at || 0);\n"
        "        if (S.mode === 'eod' && sig === lastSig && !eodStale()) return;   // 无变化 → 保留现有 DOM\n"
        "        lastSig = sig;\n"
        "        renderBody();\n"
        "        hydrate();",
        "        renderMode();\n"
        "        renderBody();\n"
        "        hydrate();",
    ),
    (
        "      // ---- 主加载（mount 与 5s 刷新共用）\n"
        "      const applyData = (data) => {\n"
        "        S.data = data || {};\n"
        "        const gs = groupsOf();\n"
        "        if (!gs.some((g) => g.id === S.group)) {\n"
        "          // 当前组不存在 → 回退同类型默认组（同旧 Tab renderWatchlist）\n"
        "          const fallback = String(S.group || '').indexOf('sector') === 0 ? 'sector_default' : 'stock_default';\n"
        "          S.group = gs.some((g) => g.id === fallback) ? fallback : ((gs[0] && gs[0].id) || 'stock_default');\n"
        "        }\n"
        "        render();\n"
        "      };\n\n"
        "      const fetchLevels = async () => {\n"
        "        try {\n"
        "          return buildLevelIndex(await api.get('/levels?effective_only=0'));\n"
        "        } catch (e) {\n"
        "          return null;          // 关键位缺失不影响主表（旧卡同口径）\n"
        "        }\n"
        "      };\n\n"
        "      const loadAll = async () => {\n"
        "        if (!S.alive) return;\n"
        "        // 条目与关键位并行取（原来串行 await，白等一个 RTT）；关键位 60s 内复用模块缓存\n"
        "        const lc = MOD.levels;\n"
        "        const levelsP = (lc.data && (Date.now() - lc.at) < LEVELS_CACHE_MS)\n"
        "          ? Promise.resolve(lc.data)\n"
        "          : fetchLevels().then((v) => {\n"
        "            if (v) MOD.levels = { at: Date.now(), data: v };\n"
        "            return v;\n"
        "          });\n"
        "        let d = null;\n"
        "        try {\n"
        "          const r = await Promise.all([\n"
        "            api.get('/api/watchlist.json' + (S.mode === 'live' ? '?mode=live' : '')),\n"
        "            levelsP,\n"
        "          ]);\n"
        "          d = r[0];\n"
        "          if (r[1]) S.levels = r[1];\n"
        "        } catch (e) {\n"
        "          showMsg('加载失败：' + errText(e), true);\n"
        "          return;\n"
        "        }\n"
        "        if (!S.alive) return;\n"
        "        MOD.payload = { at: Date.now(), data: d || {} };\n"
        "        applyData(d);\n"
        "        if (!S.el.msg.classList.contains('is-err')) showMsg('', false);\n"
        "      };",
        "      // ---- 主加载（mount 与 5s 刷新共用）\n"
        "      const loadAll = async () => {\n"
        "        if (!S.alive) return;\n"
        "        let d = null;\n"
        "        try {\n"
        "          d = await api.get('/api/watchlist.json' + (S.mode === 'live' ? '?mode=live' : ''));\n"
        "        } catch (e) {\n"
        "          showMsg('加载失败：' + errText(e), true);\n"
        "          return;\n"
        "        }\n"
        "        if (!S.alive) return;\n"
        "        S.data = d || {};\n"
        "        try {\n"
        "          S.levels = buildLevelIndex(await api.get('/levels?effective_only=0'));\n"
        "        } catch (e) {\n"
        "          S.levels = {};        // 关键位缺失不影响主表（旧卡同口径）\n"
        "        }\n"
        "        if (!S.alive) return;\n"
        "        const gs = groupsOf();\n"
        "        if (!gs.some((g) => g.id === S.group)) {\n"
        "          // 当前组不存在 → 回退同类型默认组（同旧 Tab renderWatchlist）\n"
        "          const fallback = String(S.group || '').indexOf('sector') === 0 ? 'sector_default' : 'stock_default';\n"
        "          S.group = gs.some((g) => g.id === fallback) ? fallback : ((gs[0] && gs[0].id) || 'stock_default');\n"
        "        }\n"
        "        render();\n"
        "        if (!S.el.msg.classList.contains('is-err')) showMsg('', false);\n"
        "      };",
    ),
    (
        "      // 切 Tab 回来：模块缓存里还有上一帧的条目与日线，先画出来（不等接口），再走正常刷新。\n"
        "      // 缓存超过 PAYLOAD_CACHE_MS 就不用（宁可等新数据，也不把旧价当成现价）。\n"
        "      const pc = MOD.payload;\n"
        "      if (pc.data && (Date.now() - pc.at) < PAYLOAD_CACHE_MS) {\n"
        "        const lc = MOD.levels;\n"
        "        if (lc.data && (Date.now() - lc.at) < LEVELS_CACHE_MS) S.levels = lc.data;\n"
        "        applyData(pc.data);\n"
        "      }\n"
        "      await loadAll();",
        "      await loadAll();",
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["chart", "all"], default="chart")
    a = ap.parse_args()
    src = SRC.read_text(encoding="utf-8")
    pairs = PAIRS_CHART + (PAIRS_PERF if a.mode == "all" else [])
    for i, (new, old) in enumerate(pairs, 1):
        n = src.count(new)
        assert n == 1, "第 %d 段回退锚点命中 %d 次（应为 1）" % (i, n)
        src = src.replace(new, old)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(src, encoding="utf-8")
    print("wrote %s (%d bytes, mode=%s)" % (out, len(src.encode("utf-8")), a.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
