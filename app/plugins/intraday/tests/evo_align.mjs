/**
 * 日内演化对齐运行器（给 tests/test_evo.py 调用；也可单独 node 跑）
 *
 * 做什么：把旧页 engine/web/index.html 的 `_evo*` 段（`let _evoDir` 声明起，到 `_amvEvoHtml` 收尾）
 * 用 node:vm 原样跑起来，
 * 与 frontend/evo.js 的 html() 在**同一份轨迹 + 同一份 amv_live** 上对比：
 *   ① 结构事实（svg 数 / data-evo / data-n / data-off / 榜单行 / 图例开关 / 段控文案）
 *   ② 曲线数据（每张图的 path `d` 逐字、末值标签文本、坐标轴刻度文本）
 *   ③ 预测浮层文案
 *   ④ 归一化后的整段 HTML：**去掉曲线坐标与榜单条数后**逐字节相同
 *      （2026-10-26 主人改了两条规格，这两处不再对齐旧页，单独验：
 *        · 「这两张图表的滚轮缩放功能取消。固定时间轴，盘中不再拉伸K线」→ X 从索引映射改成交易日分钟轴；
 *        · 「板块由8个增加为10个」→ 榜单 8 行 → 10 行）
 *      所以本文件额外输出 `sameStruct`（刨掉 path d 与榜单块的整段），
 *      以及 `axis`（首/末点的 X 像素，用来直接验「11:30 只走到一半」）与 `yEqual`（Y 值未走形）。
 *
 * 为什么单独成文件：脚本要读旧页源码片段并灌一份 30×5 的合成轨迹，
 * 内联进 pytest 命令行会撞 Windows 命令长度上限（test_caliber.py 踩过 WinError 206）。
 *
 * 输出：最后一行 JSON（pytest 取最后一行）。
 */
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeSeg, UTIL_LINES } from './legacy_anchor.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../..');            // projects/panel-v2
const REPO = path.resolve(PANEL, '../..');                 // OH-WorkSpace
const LEGACY = path.join(REPO, 'engine', 'web', 'index.html');

const E = await import(new URL('../frontend/evo.js', import.meta.url).href);
const C = await import(new URL('../frontend/caliber.js', import.meta.url).href);

// 旧页片段（按锚点定位，2026-10-30 起不写行号）：
//   · 工具函数四行 esc/num/pct/cls（evo 段依赖它们）
//   · 整段日内演化：从 `let _evoDir = 'pos', …` （17 个模块级变量的声明）到 _amvEvoHtml 收尾，
//     少了声明 vm 里会 ReferenceError
const seg = makeSeg(LEGACY);
const EVO_DECLS = /^let _evoDir = /;
const EVO_HTML = /^function _amvEvoHtml\(/;

function legacySource() {
  return [seg.each(UTIL_LINES), seg.upto(EVO_DECLS, EVO_HTML)].join('\n');
}

/** 合成一份「形状与真数据一致」的轨迹：30 个采样点 × 5 个板块 + 六指数 + 权重 */
export function makeTrack() {
  const codes = [
    ['BK001', '半导体'], ['BK002', '光伏'], ['BK003', '军工'], ['BK004', '白酒'], ['BK005', '煤炭'],
  ];
  const idxCodes = ['000001.SH', '399006.SZ', '000688.SH', '000300.SH', '000852.SH', '399303.SZ'];
  const snaps = [];
  const weights = {};
  codes.forEach(([c], i) => { weights[c] = 3 + i * 2.5; });
  for (let k = 0; k < 30; k++) {
    const mm = 9 * 60 + 30 + k * 2;           // 09:30 起每 2 分钟一点（不触发 3 分钟断线）
    const t = String(Math.floor(mm / 60)).padStart(2, '0') + ':' + String(mm % 60).padStart(2, '0') + ':00';
    const all = Number((Math.sin(k / 5) * 1.2 - 0.4).toFixed(2));
    const boards = {};
    codes.forEach(([c], i) => {
      boards[c] = {
        name: codes[i][1],
        amv_pct: Number((all + Math.cos(k / (4 + i)) * (1.5 + i * 0.4) + i * 0.3).toFixed(2)),
        chg_e: Number((Math.sin(k / (3 + i)) * 2 + i * 0.2).toFixed(2)),
        chg_w: Number((Math.sin(k / (3.5 + i)) * 1.8 + i * 0.18).toFixed(2)),
        amt_share: Number((weights[c] + Math.sin(k / 6) * 0.2).toFixed(3)),
      };
    });
    const idx_chg = {};
    idxCodes.forEach((c, i) => {
      idx_chg[c] = Number((Math.sin(k / (4 + i * 0.5)) * (1.1 + i * 0.2) + i * 0.1).toFixed(2));
    });
    snaps.push({ ts: '2026-10-23 ' + t, all: all, boards: boards, idx_chg: idx_chg });
  }
  return { date: '20261023', weights_date: '20261022', weights: weights, snapshots: snaps, stale: false };
}

/** 合成 amv_live（预测浮层）：形状照 /api/amv_live.json */
export function makeAmv() {
  return {
    asof: '14:30:00',
    all: { amv_pct: -0.42 },
    forecast: {
      ok: true, model: 'fc-v3', stage: '14:30模型', direction: 'down', direction_basis: 'MA10 空头',
      pred: -0.55, p10: -1.2, p90: 0.1, target_prob: 0.63,
      features: { open_ret_pct: -0.2, morning_high_ret_pct: 0.4, noon_1130_ret_pct: -0.1,
        ret_1330_pct: -0.3, ret_1400_pct: -0.35, ret_1430_pct: -0.42 },
    },
  };
}

/** 在 vm 里跑旧页 evo 段，返回 _amvEvoHtml() */
export function legacyPaint(src, track, amv, cfg) {
  const box = {
    console: { info() {}, warn() {}, log() {} },
    document: { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] },
    window: { addEventListener() {} },
    setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, clearInterval: () => {},
    // 旧页 4778-4782 的切片函数在 sector 段里，这里按 caliber.js 同口径补上
    _stSnaps: () => C.snapsOf(track),
    _stAt: 0,
  };
  box.globalThis = box;
  vm.createContext(box);
  vm.runInContext(src + '\n' + [
    // evo 段依赖的模块级变量（在 sector / amv 段里声明，本片段没包含）
    'var _sectorTrack = null, _amvLive = null, _stAt2 = 0;',
    'function loadSectorTrack(){ return null; }',
    'globalThis.__evo = function(track, amv, dir, mode, srt, idxOn, bfState, sel){',
    '  _sectorTrack = track; _amvLive = amv;',
    '  _evoDir = dir; _evoMode = mode; _evoSrt = srt; _evoIdxOn = idxOn || {};',
    '  _evoBfState = bfState || ""; _evoSel = sel == null ? null : sel;',
    '  return _amvEvoHtml();',
    '};',
  ].join('\n'), box, { filename: 'legacy-evo.js' });
  return box.__evo(track, amv, cfg.dir, cfg.mode, cfg.srt, cfg.idxOn, cfg.bfState, cfg.sel);
}

/** 卡片侧：同一个 track/amv，同一个配置 */
export function cardHtml(track, amv, cfg) {
  const st = E.createState(null, null, { track: () => track, amv: () => amv });
  st.dir = cfg.dir; st.mode = cfg.mode; st.srt = cfg.srt;
  st.idxOn = cfg.idxOn || {};
  st.bfState = cfg.bfState || '';
  st.sel = cfg.sel == null ? null : cfg.sel;
  // 2026-10-28（N20261028-140428-39）：行情图新增全A 基准线（旧页没有）——这是主人新加的规格，
  // 不属于「曲线数据走形」，故比对前先剔掉这条基准线；它本身由 test_quote_mode_has_all_baseline 单独盯。
  // 同一规格还把全A 计入 Y 轴范围（quoteChart 的 allv），所以 quote 模式的像素 Y 必然与旧页不同：
  // 该模式的数据判定改用「同一仿射变换下逐点吻合」（见 yAffineEqual）。
  return E.html(st).replace(/<path class="evo-allbase"[^>]*><\/path>|<path class="evo-allbase"[^>]*\/>/g, '');
}

/**
 * 归一化：只抹掉「任务书四条约束强制造成的差异」——内联 onclick → data-act、三个 id。
 * 其余（含空白、属性顺序、数字精度）必须逐字节相同。
 */
export function normalize(h) {
  return h
    .replace(/ onclick="evoSetMode\('([a-z]+)'\)"/g, ' data-act="evo-mode" data-v="$1"')
    .replace(/ onclick="evoSet\('([a-z]+)'\)"/g, ' data-act="evo-dir" data-v="$1"')
    .replace(/ onclick="evoSetSrt\('([a-z_]+)'\)"/g, ' data-act="evo-srt" data-v="$1"')
    .replace(/ onclick="evoToggleIdx\('([^']+)'\)"/g, ' data-act="evo-idx" data-code="$1"')
    .replace(/ onclick="evoBackfill\(\)"/g, ' data-act="evo-bf"')
    .replace(/ onclick="evoClearRange\(\)"/g, ' data-act="evo-clear"')
    .replace(/ id="evoBfBtn"/g, '')
    .replace(/ id="evoPaneL"/g, '')
    .replace(/ id="evoPaneR"/g, '')
    .replace(/ id="evoCard"/g, '')
    .replace(/>\s+</g, '><')
    .replace(/\s+/g, ' ')
    .trim();
}

const grab = (h, re) => (h.match(re) || []);
const paths = (h) => grab(h, /<path[^>]*\sd="([^"]*)"/g).map((s) => (s.match(/d="([^"]*)"/) || [])[1]);
const pairs = (d) => (d.match(/-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?/g) || [])
  .map((s) => s.split(',').map(Number));
const ys = (d) => pairs(d).map((p) => p[1]);

/**
 * 刨掉「主人改规格」的两处：曲线 path d、右侧榜单块。其余必须与旧页一致。
 *
 * 另有两类「主人 2026-10-27 有意改规格」的偏离也在此抹平（各自有单独用例把关）：
 *   · quote 模式标题改「行情涨跌 + 已选指数名」而非「活跃指数 + AMV 读数」（-32）。
 * 2026-11-05（标注 73）：AMV 盘中预测浮层整体删除，两种模式都不再出 `.amv-fc-ov`，
 * 原「quote 模式不画浮层」「历史预测标注」两条偏离同时失效，归一化里不再需要处理。
 */
export function stripChanged(h, mode) {
  let s = normalize(h)
    .replace(/<div class="evo-rank">(?:<div class="evo-rank-row[\s\S]*?<\/div>)*<\/div>/g, '<RANK/>')
    .replace(/ d="[^"]*"/g, ' d="<D>"')
    .replace(/<text class="st-xtx"[^>]*>[^<]*<\/text>/g, '<XTX/>')
    // 2026-11-05（标注 73）：旧页有、新卡已删的 AMV 预测浮层（内部只有 span，无嵌套 div）
    .replace(/<div class="amv-fc-ov"[\s\S]*?<\/div>/g, '');
  if (mode === 'quote') {
    s = s
      // 数据源前缀：quote 模式取指数分钟线，旧页恒写「真实存档」。
      // 只归一首词，后面的时间/点数/回填按钮仍然逐字比。
      .replace(/<div class="src">指数分钟线 /g, '<div class="src">真实存档 ')
      .replace(/<h3 class="evo-h">[^<]*<span class="evo-sub">[\s\S]*?<\/span><\/h3>/g, '<H3/>')
      // 全A 计入 Y 轴范围后纵轴刻度值也整体变了（同一规格，见 cardHtml 上方）
      .replace(/<text class="st-ytx"[^>]*>[^<]*<\/text>/g, '<YTX/>')
      // 0 基准线（st-base）的 y 跟着 Y 域走，同样属于该规格
      .replace(/<line class="st-base"[^>]*>/g, '<BASE/>')
      // 全A 基准线的末值标签（基准线本体已在 cardHtml 剔过，标签一并剔）
      .replace(/<text class="st-lab"[^>]*fill="var\(--accent\)">全A [^<]*<\/text>/g, '')
      // 指数末值标签：位置随 Y 域变，但**标签文本（名字 + 末值）不剔**——那个必须逐字相同
      .replace(/<text class="st-lab"[^>]*>/g, '<text class="st-lab">')
      // 图例里的「活跃指数」色块：AMV 专属（-29 模式隔离，quote 模式不挂 AMV 元素）
      .replace(/<span class="evo-lg-i"><span class="evo-lg-d" style="background:var\(--accent\)"><\/span>活跃指数<\/span>/g, '');
  }
  return s
    .replace(/<span class="fc-src" data-stale="1"[^>]*>历史预测 [^<]*<\/span>/g, '')
    .replace(/历史预测 [^：]*：非今日快照，不是实时预测 ?/g, '');
}

/**
 * 两条线是否「同一组数据」：存在常数 a、b 使 yCard = a*yLegacy + b，逐点误差 < 0.25px。
 * 阀值为何是 0.25：两侧 Y 各保留一位小数（±0.05），a/b 由首末两点拟合，误差随跨度放大；
 * 实测四组报价曲线最大残差 0.21px，而一个数值真变了至少差 1px。
 *
 * 为什么不用「像素相等」：2026-10-28 把全A 基线计入 Y 轴范围（N20261028-140428-39），
 * 行情图的 Y 刻度变了，像素必然不同；Y 值走形仍然会被这个判定拦住。
 */
function sameSeries(yl, yc) {
  const n = yl.length;
  if (n !== yc.length || !n) return false;
  const span = yl[n - 1] - yl[0];
  if (Math.abs(span) < 1e-9) {
    // 平线：两侧各自必须是常量（比不了斜率，就比「很平」这一件事）
    return yl.every((v) => Math.abs(v - yl[0]) < 1e-9)
      && yc.every((v) => Math.abs(v - yc[0]) < 1e-9);
  }
  const a = (yc[n - 1] - yc[0]) / span;
  const b = yc[0] - a * yl[0];
  if (!isFinite(a) || !isFinite(b)) return false;
  return yl.every((v, i) => Math.abs(a * v + b - yc[i]) < 0.25);
}

function yAffineEqual(hLegacy, hCard) {
  const pl = paths(hLegacy).map(pairs);
  const pc = paths(hCard).map(pairs);
  if (pl.length !== pc.length) return false;
  return pl.every((p, i) => sameSeries(p.map((q) => q[1]), (pc[i] || []).map((q) => q[1])));
}

/** 取指定 kind 的图，里面点数最多那条 path 的首/末 X（验证固定时间轴用） */
function axisOf(h, kind) {
  const svg = (h.match(new RegExp('<svg[^>]*data-evo="' + kind + '"[\\s\\S]*?</svg>')) || [''])[0];
  const ds = paths(svg);
  let best = null;
  ds.forEach((d) => {
    const p = pairs(d);
    if (p.length && (!best || p.length > best.length)) best = p;
  });
  if (!best) return null;
  const head = (svg.match(/data-ml="(\d+)"/) || ['', '0'])[1];
  const mr = (svg.match(/data-mr="(\d+)"/) || ['', '0'])[1];
  const w = (svg.match(/data-w="(\d+)"/) || ['', '0'])[1];
  return {
    kind: kind, pts: best.length,
    ml: +head, mr: +mr, w: +w, plotW: (+w) - (+head) - (+mr),
    x0: best[0][0], x1: best[best.length - 1][0],
  };
}

export function run() {
  const src = legacySource();
  const track = makeTrack();
  const amv = makeAmv();
  const cases = [
    ['默认 amv/正/贡献', { dir: 'pos', mode: 'amv', srt: 'contrib', idxOn: {}, bfState: '' }],
    ['行情涨跌+两指数', { dir: 'pos', mode: 'quote', srt: 'contrib', idxOn: { '000001.SH': 1, '399006.SZ': 1 }, bfState: 'checking' }],
    ['负向+AMV排序', { dir: 'neg', mode: 'amv', srt: 'amv', idxOn: { '000300.SH': 1 }, bfState: 'done:12' }],
    ['正负对比+钉住', { dir: 'all', mode: 'amv', srt: 'contrib', idxOn: {}, sel: 'BK002', bfState: '' }],
  ];

  const out = { cases: [], total: 0, equal: 0 };
  for (const [name, cfg] of cases) {
    const lg = legacyPaint(src, track, amv, cfg);
    const cu = cardHtml(track, amv, cfg);
    const nlg = normalize(lg);
    const ncu = normalize(cu);
    const same = nlg === ncu;
    let firstDiff = null;
    if (!same) {
      let i = 0;
      while (i < nlg.length && i < ncu.length && nlg[i] === ncu[i]) i++;
      firstDiff = { at: i, legacy: nlg.slice(i, i + 120), card: ncu.slice(i, i + 120) };
    }
    const slg = stripChanged(lg, cfg.mode); const scu = stripChanged(cu, cfg.mode);
    let structDiff = null;
    if (slg !== scu) {
      let i = 0;
      while (i < slg.length && i < scu.length && slg[i] === scu[i]) i++;
      structDiff = { at: i, legacy: slg.slice(i, i + 120), card: scu.slice(i, i + 120) };
    }
    const yModern = paths(cu).map(ys); const yLegacy = paths(lg).map(ys);
    const yEqual = JSON.stringify(yModern) === JSON.stringify(yLegacy);
    // quote 模式：全A 计入 Y 轴范围后像素 Y 必然不同（见 cardHtml 上方注释）→ 改看「同一仿射下逐点吻合」
    const yAffine = cfg.mode === 'quote' ? yAffineEqual(lg, cu) : yEqual;
    out.cases.push({
      name: name, cfg: cfg,
      svgCount: grab(cu, /<svg /g).length,
      svgCountLegacy: grab(lg, /<svg /g).length,
      evoKinds: grab(cu, /data-evo="([a-z]+)"/g).map((s) => (s.match(/="([a-z]+)"/) || [])[1]),
      dataN: grab(cu, /data-n="(\d+)"/g).map((s) => (s.match(/="(\d+)"/) || [])[1]),
      dataNAndOffLegacy: grab(lg, /data-n="(\d+)"[^>]*data-off="(\d+)"/g),
      dataNAndOffCard: grab(cu, /data-n="(\d+)"[^>]*data-off="(\d+)"/g),
      pathModern: paths(cu).length,
      pathLegacy: paths(lg).length,
      dEqual: JSON.stringify(paths(cu)) === JSON.stringify(paths(lg)),
      rankRows: grab(cu, /class="evo-rank-row/g).length,
      rankRowsLegacy: grab(lg, /class="evo-rank-row/g).length,
      rankText: grab(cu, /<span class="evo-rank-name">([^<]*)<\/span>/g).map((s) => s.replace(/<[^>]+>/g, '')),
      rankTextLegacy: grab(lg, /<span class="evo-rank-name">([^<]*)<\/span>/g).map((s) => s.replace(/<[^>]+>/g, '')),
      idxButtons: grab(cu, /class="evo-idx/g).length,
      idxButtonsLegacy: grab(lg, /class="evo-idx/g).length,
      segLabels: grab(cu, /data-act="evo-(?:mode|dir|srt)"[^>]*>([^<]+)</g).map((s) => s.replace(/<[^>]+>$/g, '').replace(/^[^>]*>/, '')),
      segLabelsLegacy: grab(lg, /onclick="evo(?:SetMode|Set|SetSrt)\('[a-z_]+'\)"[^>]*>([^<]+)</g).map((s) => s.replace(/<[^>]+>$/g, '').replace(/^[^>]*>/, '')),
      amvSub: (cu.match(/class="evo-sub">([\s\S]*?)<\/span>/) || ['', ''])[1].replace(/<[^>]+>/g, ''),
      amvSubLegacy: (lg.match(/class="evo-sub">([\s\S]*?)<\/span>/) || ['', ''])[1].replace(/<[^>]+>/g, ''),
      // 标题正文（两种模式语义不同：amv=活跃指数+读数；quote=行情涨跌+已选指数名）
      title: (cu.match(/<h3 class="evo-h">([^<]*)/) || ['', ''])[1],
      titleLegacy: (lg.match(/<h3 class="evo-h">([^<]*)/) || ['', ''])[1],
      mode: cfg.mode,
      idxOnNames: (cfg.idxOn ? Object.keys(cfg.idxOn) : [])
        .map((c) => (E.EVO_IDX.filter((p) => p[0] === c)[0] || ['', c])[1]),
      same: same,
      firstDiff: firstDiff,
      sameStruct: slg === scu,
      structDiff: structDiff,
      yEqual: yEqual,
      yAffine: yAffine,
      axis: axisOf(cu, cfg.mode === 'quote' ? 'quote' : 'amv'),
      axisLegacy: axisOf(lg, cfg.mode === 'quote' ? 'quote' : 'amv'),
    });
    out.total++;
    if (same) out.equal++;
  }
  // 一份 30 点轨迹：末值 / 区间标签（人读用）
  out.meta = { snaps: 30, boards: 5 };
  return out;
}

if (process.argv[1] && process.argv[1].endsWith('evo_align.mjs')) {
  const r = run();
  for (const c of r.cases) {
    console.log(c.name, '| struct=' + c.sameStruct, '| y=' + c.yEqual, '| yAffine=' + c.yAffine, '| 旧页整段=' + c.same,
      '| svg=' + c.svgCount, '| rank=' + c.rankRows, '| idxBtn=' + c.idxButtons,
      '| x0=' + (c.axis ? c.axis.x0.toFixed(1) : '—') + ' x1=' + (c.axis ? c.axis.x1.toFixed(1) : '—'));
  }
  console.log(JSON.stringify(r));
}
