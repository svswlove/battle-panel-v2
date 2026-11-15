/**
 * intraday 卡 · 板块轨迹口径层（纯函数，零状态零 DOM）
 *
 * 行为基准 = 旧页 engine/web/index.html 的「盘中」Tab：
 *   _stSnaps(4782) / _stLatest(4783) / _stVal(4784) / _stSeries(4792) / _stUnit(4793)
 *   _stLabel(4794) / _stChgVal(4778) / _gapBreak(4066) / _lineD(4073)
 *   _evoShare(4211) / _evoContrib(4218)
 *
 * **口径禁改**（旧页注释原话）：
 *   · `_stSnaps` 只取 09:30–15:00 —— 剔盘前 08:xx 极端值 + 收盘后点，
 *     防纵轴被撑爆/末尾假跳；
 *   · `rel` 超额 = 板块 amv_pct − 同快照 all；
 *   · `contrib` 增量贡献 = (当前占比 − EOD 基线占比) × 10，单位 ‰。
 *     占比(t) = 基线 × (1+amv_pct/100) / (1+all/100) —— 这里只有**一份**实现，
 *     旧页 `_stGridHtml` 里另写了一个内联 `shareAt`，搬过来时并入本函数。
 *
 * 所有函数第一个参数都是`板块轨迹文档`（/api/sector_track.json 原样），
 * 不读全局、不缓存，便于单测直接喂快照。
 */

export const SESSION_OPEN = '09:30';
export const SESSION_CLOSE = '15:00';

/** 旧页 4782：只取盘中 09:30–15:00 */
export function snapsOf(track) {
  return ((track && track.snapshots) || []).filter((s) => {
    const t = (s.ts || '').slice(11, 16);
    return t >= SESSION_OPEN && t <= SESSION_CLOSE;
  });
}

/** 旧页 4783 */
export function latestOf(track) {
  const s = snapsOf(track);
  return s.length ? s[s.length - 1] : null;
}

/** 旧页 4778：板块真实涨幅（chg_w 加权 / chg_e 等权，均剔首日新股） */
export function chgValOf(snap, code, chg) {
  const b = snap && snap.boards && snap.boards[code];
  if (!b) return null;
  return chg === 'e' ? b.chg_e : b.chg_w;
}

/** 旧页 4910 _evoShare：当前成交额占比 %（= 基线 × (1+amv)/(1+all)） */
export function shareOf(track, code, snap) {
  const wmap = (track && track.weights) || {};
  const base = wmap[code];
  const b = (snap && snap.boards && snap.boards[code]) || {};
  if (base == null || b.amv_pct == null || snap.all == null) return null;
  const v = base * (1 + b.amv_pct / 100) / (1 + snap.all / 100);
  return isFinite(v) ? v : null;
}

/** 旧页 4917 _evoContrib：增量贡献（Δ占比 % → ‰），唯一实现 */
export function contribOf(track, code, snap) {
  const sh = shareOf(track, code, snap);
  const base = ((track && track.weights) || {})[code];
  if (sh == null || base == null) return null;
  const v = (sh - base) * 10;
  return isFinite(v) ? v : null;
}

/** 旧页 4784 _stVal：四档口径 rel / abs / contrib / amt */
export function valOf(track, snap, code, cal) {
  if (!snap || !snap.boards || !snap.boards[code]) return null;
  const b = snap.boards[code];
  if (cal === 'rel') return (b.amv_pct == null || snap.all == null) ? null : b.amv_pct - snap.all;
  if (cal === 'abs') return b.amv_pct;
  if (cal === 'contrib') return contribOf(track, code, snap);
  return b.amt_share;
}

/** 旧页 4792 */
export function seriesOf(track, code, cal) {
  return snapsOf(track).map((s) => valOf(track, s, code, cal));
}

/** 旧页 4793 */
export function unitOf(cal) {
  return cal === 'rel' ? 'pp' : (cal === 'contrib' ? '‰' : '%');
}

/** 旧页 4794 */
export function labelOf(v, cal) {
  const u = unitOf(cal);
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + Number(v).toFixed(2) + u;
}

/**
 * 旧页 4066 _gapBreak：相邻采样点间隔 > 3 分钟即认为断更。
 * 断更处必须断线（否则 10:38→15:07 会被连成假跳变）。
 */
export function gapBreak(snaps, i) {
  if (i <= 0) return false;
  const a = (snaps[i - 1] || {}).ts || '';
  const b = (snaps[i] || {}).ts || '';
  if (a.length < 19 || b.length < 19) return false;
  const ta = Date.parse(a.slice(0, 19).replace(' ', 'T'));
  const tb = Date.parse(b.slice(0, 19).replace(' ', 'T'));
  return (!isNaN(ta) && !isNaN(tb)) ? (tb - ta) > 3 * 60 * 1000 : false;
}

/** 旧页 4073 _lineD：断点感知折线路径 */
export function lineD(snaps, vals, X, Y) {
  let d = '';
  let prev = false;
  for (let k = 0; k < vals.length; k++) {
    const v = vals[k];
    if (v == null || !isFinite(v)) { prev = false; continue; }
    d += ((!prev || gapBreak(snaps, k)) ? 'M' : 'L') + X(k).toFixed(1) + ',' + Y(v).toFixed(1) + ' ';
    prev = true;
  }
  return d.trim();
}

/** 旧页 4783 的"最近有效值"（_evoLastOf 4073 同口径） */
export function lastOf(arr) {
  for (let i = arr.length - 1; i >= 0; i--) {
    const v = arr[i];
    if (v != null && isFinite(v)) return v;
  }
  return null;
}
