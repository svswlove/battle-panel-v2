// K 线标签（2026-11-04 · 01）：主图左下角的备注 chip，按代码存。
//
// 为什么独立成模块：存储格式、校验、上限、菜单项全是纯函数，node 直接单测；
// overlay.js 只做 DOM 与事件（与 marks.js / indicators.js 同一条分工）。
//
// 口径（2026-11-04 主人预览确认）：
//   · 按代码存（600036.SH），日线/分时/周线共用一组；不绑价位、不绑某根 K 线；
//   · localStorage，key=klm_labels_v1 = { "600036.SH": [{text,color,shape}] }；
//   · shape ∈ fill|outline|solid；颜色固定 8 选 1（不跟主题涨跌色走，否则标签之间分不开）；
//   · 上限 8 条/票：再多会挡住 K 线，也没有分页 UI；
//   · 显示开关单独存（klm_labels_on_v1），默认开。

export const KEY = 'klm_labels_v1';
export const ON_KEY = 'klm_labels_on_v1';
export const MAX_PER_CODE = 8;
export const TEXT_MAX = 24;

export const COLORS = [
  '#ff5b5b', '#ff9f45', '#ffc857', '#35c97f',
  '#35d6c9', '#4da3ff', '#a88bff', '#aebbcd',
];
export const SHAPES = ['fill', 'outline', 'solid'];
export const SHAPE_LABEL = { fill: '填充', outline: '描边', solid: '实心' };
export const DEFAULT_COLOR = COLORS[5];

/** localStorage 不可用（隐私模式/沙箱）时退回内存：功能照用，只是不持久。 */
const MEM = {};
function _store(store) {
  if (store) return store;
  try {
    if (typeof localStorage !== 'undefined') { localStorage.getItem('klm_lab_probe'); return localStorage; }
  } catch (e) { /* 下面退内存 */ }
  return {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(MEM, k) ? MEM[k] : null; },
    setItem: function (k, v) { MEM[k] = String(v); },
  };
}
function _get(store, key) {
  try { return _store(store).getItem(key); } catch (e) { return null; }
}
function _set(store, key, val) {
  try { _store(store).setItem(key, val); return true; } catch (e) { return false; }
}

function _str(v) {
  return (v === null || v === undefined) ? '' : String(v);
}

/** 文字：去换行、折叠空白、截断 TEXT_MAX；空的返回 ''（调用方按非法处理）。 */
export function cleanText(raw) {
  return _str(raw).replace(/[\r\n\t]+/g, ' ').replace(/\s{2,}/g, ' ').trim().slice(0, TEXT_MAX);
}

/** 颜色只认色板里的 6 位 hex，防止任意字符串进 style 属性；不在板内回退默认色。 */
export function cleanColor(raw) {
  const c = _str(raw).trim().toLowerCase();
  return COLORS.indexOf(c) >= 0 ? c : DEFAULT_COLOR;
}
export function cleanShape(raw) {
  const s = _str(raw).trim();
  return SHAPES.indexOf(s) >= 0 ? s : 'fill';
}

/** 单条清洗：文字为空 → null（不存空标签）。 */
export function normalizeLabel(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const text = cleanText(raw.text);
  if (!text) return null;
  return { text: text, color: cleanColor(raw.color), shape: cleanShape(raw.shape) };
}

/** 整个存储清洗：非法结构当空，单条非法就丢那一条，不影响其它代码。 */
export function readAll(store) {
  const raw = _get(store, KEY);
  if (!raw) return {};
  let obj = null;
  try { obj = JSON.parse(raw); } catch (e) { return {}; }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};
  const out = {};
  Object.keys(obj).forEach(function (code) {
    const arr = Array.isArray(obj[code]) ? obj[code] : [];
    const list = [];
    arr.forEach(function (it) {
      const n = normalizeLabel(it);
      if (n && list.length < MAX_PER_CODE) list.push(n);
    });
    if (list.length) out[String(code)] = list;
  });
  return out;
}

export function writeAll(store, map) {
  return _set(store, KEY, JSON.stringify(map || {}));
}

export function readCode(store, code) {
  const all = readAll(store);
  return all[_str(code).trim()] || [];
}

function _put(store, code, list) {
  const all = readAll(store);
  const key = _str(code).trim();
  if (list.length) all[key] = list; else delete all[key];
  const ok = writeAll(store, all);
  // 2026-11-05（主人：重启后标签要留着）：写成功后通知持久化层（kline_lc 把它并进
  //   /api/kline-indicator-settings 的 annotations.labels）。浏览器外（node 单测）自动跳过。
  if (ok && typeof window !== 'undefined' && typeof window.__KLINE_LABEL_CHANGED === 'function') {
    try { window.__KLINE_LABEL_CHANGED(key, list); } catch (e) { /* 同步失败不影响本地 */ }
  }
  return { ok: ok, labels: list };
}

/**
 * 新增一条：满 8 条 / 文字为空 / 存不下都返回 ok:false + 人话原因，不静默丢弃。
 * @returns {{ok:boolean, reason:string, labels:Array}}
 */
export function addLabel(store, code, input) {
  const list = readCode(store, code).slice();
  const n = normalizeLabel(input);
  if (!n) return { ok: false, reason: '标签文字不能为空', labels: list };
  if (list.length >= MAX_PER_CODE) {
    return { ok: false, reason: '每只票最多 ' + MAX_PER_CODE + ' 条标签，先删一条', labels: list };
  }
  list.push(n);
  const r = _put(store, code, list);
  if (!r.ok) return { ok: false, reason: '存不下（浏览器存储被禁用或已满）', labels: r.labels };
  return { ok: true, reason: '', labels: r.labels };
}

/** 改第 index 条：只改传进来的字段；文字改空按非法拒绝。 */
export function updateLabel(store, code, index, patch) {
  const list = readCode(store, code).slice();
  const i = Number(index);
  if (!(i >= 0 && i < list.length)) return { ok: false, reason: '这条标签已经不在了', labels: list };
  const cur = list[i];
  const next = normalizeLabel({
    text: (patch && patch.text !== undefined) ? patch.text : cur.text,
    color: (patch && patch.color !== undefined) ? patch.color : cur.color,
    shape: (patch && patch.shape !== undefined) ? patch.shape : cur.shape,
  });
  if (!next) return { ok: false, reason: '标签文字不能为空', labels: list };
  list[i] = next;
  const r = _put(store, code, list);
  if (!r.ok) return { ok: false, reason: '存不下（浏览器存储被禁用或已满）', labels: r.labels };
  return { ok: true, reason: '', labels: r.labels };
}

export function removeLabel(store, code, index) {
  const list = readCode(store, code).slice();
  const i = Number(index);
  if (!(i >= 0 && i < list.length)) return { ok: false, reason: '这条标签已经不在了', labels: list };
  const gone = list.splice(i, 1)[0];
  const r = _put(store, code, list);
  return { ok: r.ok, reason: r.ok ? '' : '存不下（浏览器存储被禁用或已满）', labels: r.labels, removed: gone };
}

export function clearCode(store, code) {
  return _put(store, code, []);
}

export function readOn(store) {
  return _get(store, ON_KEY) !== '0';
}
export function writeOn(store, on) {
  return _set(store, ON_KEY, on ? '1' : '0');
}

/** 右键菜单的「标签」子项（结构同 marks.js::buildMenuModel 的 sub）。 */
export function menuItems(count, on) {
  return [
    { id: 'lab_new', label: '新建标签…' },
    { id: 'lab_show', label: '显示标签', checked: !!on },
    { kind: 'sep' },
    { id: 'lab_clear', label: '清空本图标签', disabled: !count },
  ];
}

export default {
  KEY, ON_KEY, MAX_PER_CODE, TEXT_MAX, COLORS, SHAPES, SHAPE_LABEL, DEFAULT_COLOR,
  cleanText, cleanColor, cleanShape, normalizeLabel,
  readAll, writeAll, readCode, addLabel, updateLabel, removeLabel, clearCode,
  readOn, writeOn, menuItems,
};
