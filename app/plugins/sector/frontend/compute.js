// ============================================================================
// 板块卡片 · 计算层（纯函数，零 DOM）
//
// 来源：8002 工作台 `夏天-activity/工具/dashboard/web/app.js` 的榜单/榜位/评分/在榜
// 一组函数，逐段搬入并把隐式全局（state.level / state.sortKey / _flowMap / _divMap）
// 改成显式入参，便于单测。
//
// 为什么单独一层：这些函数只吃「权重矩阵 + 截面快照」，不碰 DOM，可以独立验证算得对不对；
// 渲染层（frontend.js）只管把它们的结果画出来。改动面清晰，出错容易定位。
//
// 口径约定（与 8002 逐字一致，禁止自行调整）：
//   · weight 矩阵单位 = 1e6 整数（share），total 亦为 1e6 整数；
//   · 权重变化 d = w_now - w_base（单位同 weight，显示时 /1e6*100 得 pp）；
//   · 活跃涨跌 amv_pct 在逐日排序里用权重比 rc/rb（当日全市场 total 对所有板块是同常数，
//     比值序与活跃涨跌序严格一致）；
//   · TOP 数量 = clamp(round(n * 0.10), 10, 30)；
//   · 评分 = 1/3/5/10/20 日权重变化的横截面排名百分位加权 0.15/0.20/0.25/0.25/0.15。
// ============================================================================

export const LEVEL_KEY = { L2: 'l2', L3: 'l3', CONCEPT: 'concept', INDEX: 'index' };
export const LEVEL_NAME = { L2: '二级行业', L3: '三级行业', CONCEPT: '概念板块', INDEX: '各大指数' };

export const TOP_PCT = 0.10;
export const TOP_MIN = 10;
export const TOP_MAX = 30;
export const REIN_N = 3;                       // 重入判定窗口（交易日）
export const SCORE_WINS = [1, 3, 5, 10, 20];
export const SCORE_W = [0.15, 0.20, 0.25, 0.25, 0.15];
export const HIT_WIN = 30;                     // 在榜统计窗口
export const BADGE_SORT_KEYS = new Set(['d', 'weight', 'rank', 'amv_pct', 'score']);

/**
 * TOP 数量：按占比取，夹在 [10, 30]，再夹 ⌊n/2⌋。
 *
 * 最后一层 ⌊n/2⌋ 不能省，也不能自己加“下限”——小样本层（如钻取单个一级行业）
 * 板块数不到 20 时，10 的固定下限会让前 10% 吃满全池，后 10% 被 inSet 排空。
 * n<20 时返回小于 10 的值是**对的**（8002 同口径）。
 */
export function topCount(n) {
  return Math.max(1, Math.min(
    Math.min(TOP_MAX, Math.max(TOP_MIN, Math.round(n * TOP_PCT))),
    Math.floor(n / 2),
  ));
}

/**
 * 建立一次计算上下文。
 *
 * @param {object} opts
 * @param {object} opts.catalog      /catalog 结果（含 l2/l3/concept/index 数组）
 * @param {object} opts.rankRecent   /rank_recent?level= 结果（dates/total/codes/weight）
 * @param {string} opts.level        L2 | L3 | CONCEPT | INDEX
 * @param {string} opts.sortKey      d | weight | rank | amv_pct | real_pct | main | score
 * @param {number} opts.winN         比较周期（1/3/5/10/20）
 * @param {string|null} opts.drillL1 一级行业钻取（null = 全部）
 * @param {object} opts.flowMap      code -> {main, amv_pct, real_pct, diverge, bull}
 * @param {object} opts.divMap       code -> {real_pct, diverge, amv_pct}
 * @param {number} opts.dayB         当前日索引（0-based，指向 rankRecent.dates）
 * @returns {object} 绑定好的计算函数集合
 */
export function createCompute(opts) {
  const o = opts || {};
  const catalog = o.catalog || {};
  const rr = o.rankRecent || { dates: [], total: [], codes: [], weight: [] };
  const level = o.level || 'L2';
  const sortKey = o.sortKey || 'd';
  const winN = Number(o.winN) || 1;
  const drillL1 = o.drillL1 || null;
  const flowMap = o.flowMap || {};
  const divMap = o.divMap || {};
  const dayB = Number.isInteger(o.dayB) ? o.dayB : Math.max(0, (rr.dates || []).length - 1);

  const meta = catalog[LEVEL_KEY[level]] || [];
  // 索引对齐校验：rankRecent.codes 与 catalog[level] 必须同序（已实测 L2/L3/CONCEPT/INDEX 四层全等）。
  // 不一致时退回按位取值，并在控制台留一条警告——宁可显示可疑，也不要静默错位。
  const codes = rr.codes || [];
  let aligned = codes.length === meta.length;
  if (aligned) {
    for (let i = 0; i < codes.length; i++) {
      if (codes[i] !== meta[i].code) { aligned = false; break; }
    }
  }
  if (!aligned && typeof console !== 'undefined') {
    console.warn('[sector] catalog 与 rank_recent 的代码顺序不一致，按位取值可能有偏移',
      { level: level, metaLen: meta.length, rrLen: codes.length });
  }

  const weight = rr.weight || [];
  const total = rr.total || [];
  const dates = rr.dates || [];

  function skipMeta(m) {
    return !!(m && m.exclude);
  }

  /** 当天权重排名：j -> 名次（1 起，按权重降序）。 */
  function rankMap(dayIndex) {
    const out = {};
    const row = weight[dayIndex];
    if (!row) return out;
    const pairs = [];
    for (let j = 0; j < meta.length; j++) {
      if (skipMeta(meta[j])) continue;
      const w = row[j];
      if (w == null || isNaN(w)) continue;
      pairs.push([j, w]);
    }
    pairs.sort((a, b) => b[1] - a[1]);
    pairs.forEach((x, i) => { out[x[0]] = i + 1; });
    return out;
  }

  /** 按「当前排序键 + 比较周期」排出当天名次：j -> 名次。 */
  function rankMapBy(dayIndex, nBack, l1) {
    const out = {};
    const base = dayIndex - nBack;
    if (base < 0 || !weight[dayIndex] || !weight[base]) return out;
    const k = sortKey;
    const rowCur = weight[dayIndex];
    const rowBase = weight[base];
    const rk = (k === 'rank') ? rankMap(dayIndex) : null;
    const rkPrev = (k === 'rank') ? rankMap(dayIndex - 1) : null;
    const vals = [];
    for (let j = 0; j < meta.length; j++) {
      if (skipMeta(meta[j])) continue;
      if (l1 && meta[j].l1 !== l1) continue;
      const rc = rowCur[j], rb = rowBase[j];
      if (rc == null || rb == null) continue;
      let v;
      if (k === 'weight') v = rc;
      else if (k === 'amv_pct') v = (rb === 0) ? null : rc / rb;
      else if (k === 'rank') v = (rk[j] == null || rkPrev[j] == null) ? null : rkPrev[j] - rk[j];
      else if (k === 'score') { const scm = scoreMapOf(dayIndex, l1); v = scm.has(j) ? scm.get(j) : null; }
      else v = rc - rb;
      if (v == null || isNaN(v)) continue;
      vals.push([j, v]);
    }
    vals.sort((a, b) => b[1] - a[1]);
    vals.forEach((x, i) => { out[x[0]] = i + 1; });
    return out;
  }

  const _boardCache = new Map();

  /** 榜位集合（带缓存）：{inSet, outSet, n}；无逐日序列的排序键返回 null。 */
  function boardSets(dayIndex, nBack, l1) {
    const ck = level + '|' + sortKey + '|' + dayIndex + '|' + nBack + '|' + (l1 || '');
    if (_boardCache.has(ck)) return _boardCache.get(ck);
    if (_boardCache.size > 4000) _boardCache.clear();
    const res = boardSetsRaw(dayIndex, nBack, l1);
    _boardCache.set(ck, res);
    return res;
  }

  function boardSetsRaw(dayIndex, nBack, l1) {
    const base = dayIndex - nBack;
    if (base < 0 || !weight[dayIndex] || !weight[base]) return null;
    const k = sortKey;
    if (!BADGE_SORT_KEYS.has(k)) return null;
    if (dayIndex < 0) return null;
    const rowCur = weight[dayIndex];
    const rowBase = weight[base];
    const rk = (k === 'rank') ? rankMap(dayIndex) : null;
    const rkPrev = (k === 'rank') ? rankMap(dayIndex - 1) : null;
    const pool = [];
    for (let j = 0; j < meta.length; j++) {
      if (skipMeta(meta[j])) continue;
      if (l1 && meta[j].l1 !== l1) continue;
      const rc = rowCur[j], rb = rowBase[j];
      if (rc == null || rb == null) continue;
      let v;
      if (k === 'weight') v = rc;
      else if (k === 'amv_pct') v = (rb === 0) ? null : rc / rb;
      else if (k === 'rank') v = (rk[j] == null || rkPrev[j] == null) ? null : rkPrev[j] - rk[j];
      else if (k === 'score') { const scm = scoreMapOf(dayIndex, l1); v = scm.has(j) ? scm.get(j) : null; }
      else v = rc - rb;
      if (v == null || isNaN(v)) continue;
      pool.push({ j, v });
    }
    pool.sort((a, b) => b.v - a.v);
    const kk = topCount(pool.length);
    const inTop = pool.slice(0, kk);
    const inSet = new Set(inTop.map((x) => x.j));
    const outTop = pool.slice(-kk).reverse().filter((x) => !inSet.has(x.j));
    return { inSet: new Set(inTop.map((x) => x.j)), outSet: new Set(outTop.map((x) => x.j)), n: pool.length };
  }

  /**
   * 榜位状态：streak（连续在榜天数）/ isNew / isReentry / flipped。
   *
   * 换边不算 NEW：昨天在跌幅榜、今天翻上涨幅榜 → flipped=true 且不标 NEW
   * （主人 2026-10-22 报过「明明昨天还在榜，为什么也 NEW」）。
   */
  function boardStatus(dayIndex, nBack, l1, key, side) {
    const setOf = (d, sd) => {
      if (d < 0) return null;
      const s = boardSets(d, nBack, l1);
      if (!s) return null;
      return (sd === 'in' ? s.inSet : s.outSet).has(key);
    };
    const inOf = (d) => setOf(d, side);
    let streak = 0;
    for (let d = dayIndex; d >= 0; d--) {
      if (inOf(d) !== true) break;
      streak++;
    }
    if (streak > 1) return { streak, isReentry: false, isNew: false };
    if (inOf(dayIndex - 1) !== false) return { streak, isReentry: false, isNew: false };
    if (setOf(dayIndex - 1, side === 'in' ? 'out' : 'in') === true) {
      return { streak, isReentry: false, isNew: false, flipped: true };
    }
    let last = null;
    for (let d = dayIndex - 2; d >= Math.max(0, dayIndex - REIN_N - 2); d--) {
      if (inOf(d) === true) { last = d; break; }
    }
    if (last != null && (dayIndex - 1 - last) <= REIN_N) {
      return { streak, isReentry: true, isNew: false };
    }
    return { streak, isReentry: false, isNew: true };
  }

  /**
   * 候选池（某日、某周期）：返回带 amv/weight/rank/d 的行数组；数据不足返回 null。
   * 行的 amv 用「当日权重 × 当日全市场 total」还原，与 8002 一致。
   */
  function poolAt(dayIndex, nBack, l1) {
    const base = dayIndex - nBack;
    if (base < 0) return null;
    const rowCur = weight[dayIndex];
    const rowBase = weight[base];
    if (!rowCur || !rowBase) return null;
    const tCur = total[dayIndex];
    const tBase = total[base];
    const rk = rankMap(dayIndex);
    const pool = [];
    for (let j = 0; j < meta.length; j++) {
      const m = meta[j];
      if (skipMeta(m)) continue;
      if (l1 && m.l1 !== l1) continue;
      const rc = rowCur[j], rb = rowBase[j];
      if (rc == null || rb == null) continue;
      pool.push({
        j,
        code: m.code,
        name: m.name,
        l1: m.l1,
        weight: rc,
        weightPrev: rb,
        amv: (tCur != null && rc != null) ? Math.round(rc / 1e6 * tCur) : null,
        d: rc - rb,
        rank: rk[j] == null ? null : rk[j],
        rankPrev: null,
        score: null,
      });
    }
    return pool;
  }

  const _scoreCache = new Map();

  /** 加权评分：j -> 0..100。数据不足返回空 Map。 */
  function scoreMapOf(dayIndex, l1) {
    const ck = 'sc|' + level + '|' + dayIndex + '|' + (l1 || '');
    if (_scoreCache.has(ck)) return _scoreCache.get(ck);
    if (_scoreCache.size > 400) _scoreCache.clear();
    const acc = new Map();
    const empty = () => { _scoreCache.set(ck, acc); return acc; };
    if (!weight[dayIndex]) return empty();
    for (let w = 0; w < SCORE_WINS.length; w++) {
      const base = dayIndex - SCORE_WINS[w];
      if (base < 0) return empty();
      const rowCur = weight[dayIndex], rowBase = weight[base];
      if (!rowCur || !rowBase) return empty();
      const rows = [];
      for (let j = 0; j < meta.length; j++) {
        if (skipMeta(meta[j])) continue;
        if (l1 && meta[j].l1 !== l1) continue;
        const rc = rowCur[j], rb = rowBase[j];
        if (rc == null || rb == null) continue;
        rows.push([j, rc - rb]);
      }
      rows.sort((a, b) => b[1] - a[1]);
      const n = rows.length;
      for (let i = 0; i < n; i++) {
        const p = n > 1 ? (1 - i / (n - 1)) * 100 : 50;
        const j = rows[i][0];
        acc.set(j, (acc.get(j) || 0) + p * SCORE_W[w]);
      }
    }
    _scoreCache.set(ck, acc);
    return acc;
  }

  /** 排序取值：按 sortKey 从行里挑字段；null = 排在最后。 */
  function sortValOf(r, k) {
    if (k === 'rank') {
      if (r.rank == null || r.rankPrev == null) return null;
      return r.rankPrev - r.rank;
    }
    if (k === 'weight') return r.weight;
    if (k === 'amv_pct') {
      const f = flowMap[r.code] || {};
      const e = divMap[r.code] || {};
      return (f.amv_pct != null) ? f.amv_pct : e.amv_pct;
    }
    if (k === 'real_pct') { const d2 = divMap[r.code] || {}; return d2.real_pct; }
    if (k === 'main') { const f2 = flowMap[r.code]; return f2 == null ? null : f2.main; }
    if (k === 'score') return (r.score == null ? null : r.score);
    return r.d;
  }

  /** 固定降序（值大在前）——前 k = 最强的 k 个。 */
  function sortByKey(pool, k) {
    pool.sort((a, b) => {
      const va = sortValOf(a, k), vb = sortValOf(b, k);
      const na = (va == null || isNaN(va)) ? -Infinity : va;
      const nb = (vb == null || isNaN(vb)) ? -Infinity : vb;
      return nb - na;
    });
    return pool;
  }

  function sortPool(pool) {
    return sortByKey(pool, sortKey);
  }

  /** 昨日同口径 TOP 榜（用于 NEW 标记与退出项判定）。 */
  function prevBoard(dayIndex) {
    const pool = poolAt(dayIndex - 1, winN, drillL1);
    if (!pool) return null;
    sortPool(pool);
    const k = topCount(pool.length);
    const inList = pool.slice(0, k);
    const inSet = new Set(inList.map((x) => x.j));
    const outList = pool.slice(-k).reverse().filter((x) => !inSet.has(x.j));
    return { inSet, outSet: new Set(outList.map((x) => x.j)), inList, outList };
  }

  /**
   * 主榜计算：前 10% / 后 10% + 各自退出项 + 徽标。
   * 数据不足（warmup）时返回空榜并标 warmup=true。
   */
  function computeBoardLists(dayIndex) {
    const pool = poolAt(dayIndex, winN, drillL1);
    if (!pool) return { warmup: true, topIn: [], topOut: [], exitsIn: [], exitsOut: [] };
    const scNow = scoreMapOf(dayIndex, drillL1);
    const rkNow = rankMapBy(dayIndex, winN, drillL1);
    const rkPre = rankMapBy(dayIndex - 1, winN, drillL1);
    for (const r of pool) {
      r.score = scNow.has(r.j) ? Math.round(scNow.get(r.j)) : null;
      if (rkNow[r.j] != null) r.rank = rkNow[r.j];
      if (rkPre[r.j] != null) r.rankPrev = rkPre[r.j];
    }
    sortPool(pool);
    const k = topCount(pool.length);
    const topIn = pool.slice(0, k);
    const inSet = new Set(topIn.map((x) => x.j));
    const topOut = pool.slice(-k).reverse().filter((x) => !inSet.has(x.j));

    const prev = prevBoard(dayIndex);
    const badgeable = BADGE_SORT_KEYS.has(sortKey);
    const mark = (list, side) => {
      for (const r of list) {
        if (!badgeable) { r.noBadge = true; continue; }
        const st = boardStatus(dayIndex, winN, drillL1, r.j, side);
        r.streak = st.streak;
        r.isReentry = st.isReentry;
        r.isNew = st.isNew;
        r.flipped = st.flipped;
      }
    };
    const exits = (prevList, todayList, side) => {
      if (!prevList) return [];
      const today = new Set(todayList.map((x) => x.j));
      return prevList.filter((x) => !today.has(x.j)).map((x) => Object.assign({}, x, {
        streak: boardStatus(dayIndex - 1, winN, drillL1, x.j, side).streak,
        isNew: false, isReentry: false,
        score: scNow.has(x.j) ? Math.round(scNow.get(x.j)) : null,
        isExit: true,
      }));
    };
    mark(topIn, 'in');
    mark(topOut, 'out');
    return {
      topIn, topOut,
      exitsIn: prev ? exits(prev.inList, topIn, 'in') : [],
      exitsOut: prev ? exits(prev.outList, topOut, 'out') : [],
      warmup: false,
    };
  }

  /**
   * 指数层全量行（照 8002 `idxPoolRows`）：**不切 10%**，整层一次返回。
   *
   * 为什么单独一个函数：指数只有 12 个，工作台本来就是「左栏全列指数、右栏走势与数据」；
   * 复用 computeBoardLists 会按 topCount 切出前/后 10%，12 个指数只剩 6 个（标注 N20261027-011947-26）。
   * 其他层级仍走 computeBoardLists，10% 口径一个字不改。
   *
   * @returns {{rows: Array, warmup: boolean}}
   */
  function computeAllRows(dayIndex) {
    const pool = poolAt(dayIndex, winN, drillL1);
    if (!pool) return { rows: [], warmup: true };
    const scNow = scoreMapOf(dayIndex, drillL1);
    const rkNow = rankMapBy(dayIndex, winN, drillL1);
    const rkPre = rankMapBy(dayIndex - 1, winN, drillL1);
    for (const r of pool) {
      r.score = scNow.has(r.j) ? Math.round(scNow.get(r.j)) : null;
      if (rkNow[r.j] != null) r.rank = rkNow[r.j];
      if (rkPre[r.j] != null) r.rankPrev = rkPre[r.j];
      r.noBadge = true;   // 指数层不出连N/NEW 徽标（8002 idxPoolRows 同口径）
    }
    sortPool(pool);
    // 8002 同款：全A 固定首位（左栏顺序 = 全A、上证、深证…）
    pool.sort((a, b) => (a.code === 'ALL' ? -1 : 0) - (b.code === 'ALL' ? -1 : 0));
    return { rows: pool, warmup: false };
  }

  /** 在榜：近 win 个交易日里进过该侧榜的天数（{days: n, today: bool}）。 */
  function boardHits(dayIndex, nBack, l1, key, side, win) {
    const days = [];
    for (let hd = dayIndex; hd >= Math.max(0, dayIndex - (win || HIT_WIN) + 1); hd--) {
      const s = boardSets(hd, nBack, l1);
      if (!s) continue;
      days.push({ d: hd, in: (side === 'in' ? s.inSet : s.outSet).has(key) });
    }
    const n = days.filter((x) => x.in).length;
    return { days: n, total: days.length, today: days.length ? days[0].in : false, trail: days };
  }

  return {
    // 元信息
    meta, codes, dates, total, weight, aligned,
    level, sortKey, winN, drillL1, dayB,
    // 计算
    skipMeta, topCount,
    rankMap, rankMapBy, boardSets, boardSetsRaw, boardStatus,
    poolAt, scoreMapOf, sortValOf, sortByKey, sortPool,
    computeBoardLists, computeAllRows, prevBoard, boardHits,
  };
}

/** 名次变化徽标（8002 `mvHtml`）。 */
export function mvHtml(cur, prev) {
  if (cur == null) return '';
  if (prev == null) return '<span class="sc-mv up">NEW</span>';
  if (prev > cur) return '<span class="sc-mv up">▲' + (prev - cur) + '</span>';
  if (prev < cur) return '<span class="sc-mv dn">▼' + (cur - prev) + '</span>';
  return '<span class="sc-mv flat">—</span>';
}
