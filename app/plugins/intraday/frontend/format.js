/**
 * intraday 卡 · 格式化层（纯函数，零状态零 DOM）
 *
 * 口径源 = 旧页 engine/web/index.html:2784-2880 / 4622，逐字照搬，不改语义：
 *   esc / num / pct / pp / code6 / fmtAmount / dShort
 *
 * 涨跌色有两个方言，**不要合并**：
 *   clsUp(v)  → 'up' / 'down' / ''      旧页口径，配合旧页 CSS（.st-cell-v.up / .evo-card-v.up）
 *   clsIt(v)  → 'it-up' / 'it-down' / '' 本卡 v0.1.0 起的自有类名（.it-up/.it-down）
 * 两套同时存在是因为搬进来的旧页 DOM 结构与类名照抄，不能改类名去迁就 v2 命名。
 */

export const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

export const isNil = (v) => v == null || v === '' || (typeof v === 'number' && !isFinite(v));

export const num = (v, d) => (isNil(v) ? '—' : Number(v).toFixed(d == null ? 2 : d));

export const pct = (v, d) => (isNil(v) ? '—' : (Number(v) > 0 ? '+' : '') + num(v, d) + '%');

export const pp = (v, d) => (isNil(v) ? '—' : (Number(v) > 0 ? '+' : '') + num(v, d) + 'pp');

/** 旧页 cls：涨跌 → up / down（旧页 CSS 类名） */
export const clsUp = (v) => (isNil(v) ? '' : (Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : '')));

/** 本卡 v0.1.0 起的自有类名：it-up / it-down（不动旧页那段 DOM，只给卡片自己的块用） */
export const clsIt = (v) => (isNil(v) ? '' : (Number(v) > 0 ? 'it-up' : (Number(v) < 0 ? 'it-down' : '')));

export const code6 = (ts) => String(ts == null ? '' : ts).split('.')[0];

export const errText = (e) => (e && e.message ? e.message : String(e));

export const fmtAmount = (v) => {
  if (isNil(v)) return '—';
  const n = Number(v);
  if (n >= 1e12) return num(n / 1e12, 2) + ' 万亿';
  if (n >= 1e8) return num(n / 1e8, 1) + ' 亿';
  return num(n, 0);
};

/** 20261023 → 10/23（旧页 4622 _d） */
export const dShort = (s) => (String(s || '').length === 8
  ? String(s).slice(4, 6) + '/' + String(s).slice(6, 8)
  : String(s || ''));
