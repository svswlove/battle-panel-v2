/**
 * 日内演化 · 模式隔离与指数叠加（N20261027-100741-29 / -30 / -31 / -32）
 *
 * 直接 import frontend/evo.js 跑纯函数（不跑旧页源码——旧页已随 8800 退役，
 * 那边的字节对齐由 tests/evo_align.mjs 单独负责，本文件只验 2026-10-27 四条标注的规格）。
 *
 * 输出：最后一行 JSON（pytest 取最后一行）。
 */
const E = await import(new URL('../frontend/evo.js', import.meta.url).href);

const IDX = ['000001.SH', '399006.SZ', '000688.SH', '000300.SH', '000852.SH', '399303.SZ'];

/** 交易日分钟 → "YYYY-MM-DD HH:MM:SS" */
function tsOf(tm, day) {
  const m = tm <= 120 ? 570 + tm : 780 + (tm - 120);
  return (day || '2026-10-27') + ' ' + String(Math.floor(m / 60)).padStart(2, '0') + ':'
    + String(m % 60).padStart(2, '0') + ':00';
}

/** 合成轨迹：每 2 交易分钟一点；idx_chg 由参数控制（null / 缺失 / 有值） */
function makeTrack(opts) {
  const o = opts || {};
  const codes = [['BK001', '半导体'], ['BK002', '光伏'], ['BK003', '军工']];
  const weights = { BK001: 4, BK002: 6, BK003: 3 };
  const snaps = [];
  for (let tm = 0; tm <= 120; tm += 2) {
    const boards = {};
    codes.forEach(([c, n], i) => {
      boards[c] = { name: n, amv_pct: 1 + i - tm / 200, chg_e: 0.5 + i, chg_w: 0.4 + i, amt_share: 4 + i };
    });
    const s = { ts: tsOf(tm, o.day), all: 1.2 - tm / 300, boards: boards };
    if (o.idx === 'full') {
      s.idx_chg = {};
      IDX.forEach((c, i) => { s.idx_chg[c] = (i % 2 ? -1 : 1) * (0.2 + i * 0.1) + tm / 500; });
    } else if (o.idx === 'nulls') {
      s.idx_chg = {};
      IDX.forEach((c) => { s.idx_chg[c] = null; });
    } else if (o.idx === 'partial') {
      s.idx_chg = { '000001.SH': tm / 400 };      // 只有一个指数有值
    }
    snaps.push(s);
  }
  return { date: (o.day || '2026-10-27').replace(/-/g, ''), weights_date: '20261026', weights: weights, snapshots: snaps, stale: false };
}

const AMV_TODAY = {
  asof: '2026-10-27 10:30:12', trade_date: '2026-10-27', tick_date: '2026-10-27',
  meta: { trade_date: '2026-10-27', generated_at: '2026-10-27 10:30:12' },
  all: { amv_pct: 0.42 },
  forecast: {
    ok: true, model: 'fc-v3', stage: '上午模型', direction: 'down', direction_basis: 'MA10 空',
    pred: -0.55, p10: -1.2, p90: 0.1, target_prob: 0.63,
    features: { open_ret_pct: -0.2, morning_high_ret_pct: 0.4, noon_1130_ret_pct: -0.1 },
  },
};

const AMV_HIST = JSON.parse(JSON.stringify(AMV_TODAY));
AMV_HIST.asof = '2026-10-23 12:24:29';
AMV_HIST.trade_date = '2026-10-23';
AMV_HIST.tick_date = '2026-10-23';
AMV_HIST.meta = { trade_date: '2026-10-23', generated_at: '2026-10-23 12:24:29' };

function state(track, amv, cfg) {
  const summary = {
    ok: true, first_all: 1.2, last_all: 0.4, all_delta: -0.8, strong: 30, weak: 60,
    all_series: [{ t: '09:30', all: 1.2 }, { t: '11:30', all: 0.4 }],
  };
  const st = E.createState(null, null, { track: () => track, amv: () => amv, summary: () => summary });
  Object.assign(st, cfg || {});
  return st;
}

const count = (h, re) => (h.match(re) || []).length;
const quoteSvg = (h) => (h.match(/<svg[^>]*data-evo="quote"[\s\S]*?<\/svg>/) || [''])[0];
const amvSvg = (h) => (h.match(/<svg[^>]*data-evo="amv"[\s\S]*?<\/svg>/) || [''])[0];
// 2026-10-28（N20261028-140428-39）：行情图新增全A 基准线。它是**基准**、不是主人勾选的指数，
// 故 pathsOf 默认剔除它（勾选几条就数几条），基准线用 allbaseOf 单独验「在不在、有没有值」。
const isBase = (tag) => /class="evo-allbase"/.test(tag);
const pathsOf = (svg) => (svg.match(/<path[^>]*\sd="([^"]*)"/g) || [])
  .filter((s) => !isBase(s))
  .map((s) => (s.match(/d="([^"]*)"/) || [])[1]);
const allbaseOf = (svg) => (svg.match(/<path[^>]*class="evo-allbase"[^>]*\sd="([^"]*)"/g) || []).length;
/** 末值标签只数指数（全A 基准那行不计入「与曲线数一致」的口径） */
const idxLabels = (h) => (h.match(/<text class="st-lab"[^>]*>[^<]*</g) || [])
  .filter((s) => !/>全A /.test(s)).length;
const idxButtons = (h) => (h.match(/class="evo-idx[^"]*"[^>]*data-code="([^"]+)"/g) || [])
  .map((s) => (s.match(/data-code="([^"]+)"/) || [])[1]);

export function run() {
  const out = { cases: {} };

  // ---- ① 模式隔离：quote 不显示预测 / 不显示全A 信息 / 标题改行情
  {
    const tr = makeTrack({ idx: 'full' });
    const q = E.html(state(tr, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 } }));
    const a = E.html(state(tr, AMV_TODAY, { mode: 'amv', idxOn: {} }));
    out.cases.modeIsolation = {
      quoteHasInfo: count(q, /class="evo-info"/g),
      quoteTitle: (q.match(/<h3 class="evo-h">([\s\S]*?)<\/h3>/) || ['', ''])[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
      quoteHasAmvSub: /class="evo-sub">[^<]*%/.test(q),
      quoteSvg: count(q, /data-evo="quote"/g),
      amvSvg: count(q, /data-evo="amv"/g),
      amvHasInfo: count(a, /class="evo-info"/g),
      amvTitle: (a.match(/<h3 class="evo-h">([\s\S]*?)<\/h3>/) || ['', ''])[1].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
    };
  }

  // ---- ② 预测新鲜度（此 case 已于 2026-11-05 随预测浮层删除一并剥除）

  // ---- ③ 六指数：勾选即出线，逐个 on/off 实际改变 path
  {
    const tr = makeTrack({ idx: 'full' });
    const one = E.html(state(tr, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 } }));
    const two = E.html(state(tr, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1, '399006.SZ': 1 } }));
    const none = E.html(state(tr, AMV_TODAY, { mode: 'quote', idxOn: {} }));
    const p1 = pathsOf(quoteSvg(one));
    const p2 = pathsOf(quoteSvg(two));
    const pn = pathsOf(quoteSvg(none));
    out.cases.idxToggle = {
      buttons: idxButtons(one),
      pathOne: p1.length,
      pathTwo: p2.length,
      pathNone: pn.length,
      changed: JSON.stringify(p1) !== JSON.stringify(p2),
      noneNotBlank: pn.length > 0 && pn.every((d) => d.length > 0),
      labelOne: idxLabels(one),
      labelTwo: idxLabels(two),
      // 全A 基准线：勾一个 / 不勾时都在（基准不该因为没勾指数就消失）
      allbaseOne: allbaseOf(quoteSvg(one)),
      allbaseNone: allbaseOf(quoteSvg(none)),
      // 切回 AMV 模式：六指数仍在（叠加），活跃指数也在
      amvWithIdx: pathsOf(amvSvg(E.html(state(tr, AMV_TODAY, { mode: 'amv', idxOn: { '000001.SH': 1 } })))).length,
      amvNoIdx: pathsOf(amvSvg(E.html(state(tr, AMV_TODAY, { mode: 'amv', idxOn: {} })))).length,
    };
  }

  // ---- ④ 缺数据如实说明：idx_chg 全缺 / 全 null / 历史日 三种话不一样，且都不补 0
  {
    const miss = makeTrack({ idx: 'none' });
    const nulls = makeTrack({ idx: 'nulls' });
    const partial = makeTrack({ idx: 'partial' });
    const hist = makeTrack({ idx: 'none', day: '2026-10-23' });      // 历史交易日的轨迹
    const now = new Date(2026, 9, 27, 10, 45, 0);
    const txt = (h) => {
      const m = h.match(/<p class="muted">([^<]*)<\/p>/);
      return m ? m[1] : '';
    };
    // 这三个用例与 histMsg 同批，"今天"必须用注入的 now（否则跑到第二天就把 10-27 的 fixture 判成历史日，
    // 断言随机器日期漂——2026-10-28 实测已漂）。产品行为未动，只补上注入。
    out.cases.missing = {
      missingMsg: txt(E.html(state(miss, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 }, now: now }))),
      nullMsg: txt(E.html(state(nulls, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 }, now: now }))),
      histMsg: txt(E.html(state(hist, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 }, now: now }))),
      partialSvg: pathsOf(quoteSvg(E.html(state(partial, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 }, now: now })))).length,
      partialMsg: txt(E.html(state(partial, AMV_TODAY, { mode: 'quote', idxOn: { '399006.SZ': 1 }, now: now }))),
      // 缺 idx_chg 时 quote 图不能把 0 当值画
      missPathZero: pathsOf(quoteSvg(E.html(state(miss, AMV_TODAY, { mode: 'quote', idxOn: { '000001.SH': 1 }, now: now })))).length,
    };
  }

  return out;
}

if (process.argv[1] && process.argv[1].endsWith('evo_mode.mjs')) {
  const r = run();
  console.log(JSON.stringify(r));
}
