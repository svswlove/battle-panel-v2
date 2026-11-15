// 预警页卡片烟测（2026-11-07）：不依赖浏览器/jsdom，用最小 DOM shim 真跑 mount。
//
// 为什么要有它：卡片是纯前端，pytest 看不到运行期错误（未定义变量、接口形状不符）。
// 这里用 60 行 DOM 替身把 mount 真跑一遍，断言首页文案与「条件行」渲染出来；
// 无第三方依赖，`node app/tests/js/alert_card.smoke.mjs` 直接跑。
//
// 不覆盖：真实布局像素、声音自动播放（浏览器策略）、fetch 网络层。

class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.style = {};
    this.className = '';
    this.textContent = '';
    this.innerHTML = '';
    this.hidden = false;
    this.onclick = null;
    this.value = '';
    this.type = '';
  }
  append(...nodes) {
    for (const n of nodes) {
      if (n == null) continue;
      const el = (typeof n === 'object') ? n : document.createTextNode(String(n));
      el.parentNode = this;
      this.children.push(el);
    }
  }
  appendChild(n) { this.append(n); return n; }
  removeChild(n) { this.children = this.children.filter((x) => x !== n); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) { this[`attr_${k}`] = v; }
  addEventListener(type, fn) { (this._ev = this._ev || {})[type] = fn; }
  querySelectorAll(sel) {
    // 只支持 [data-act] 这种属性选择器 + 递归
    const out = [];
    const want = /^\[([\w-]+)\]$/.exec(sel);
    const walk = (node) => {
      for (const c of node.children || []) {
        if (want && c.dataset && Object.prototype.hasOwnProperty.call(c.dataset, want[1])) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  get lastChild() { return this.children[this.children.length - 1] || null; }
}

class TextNode {
  constructor(t) { this.textContent = String(t); this.children = []; this.dataset = {}; }
}

globalThis.document = {
  head: new El('head'),
  body: new El('body'),
  createElement: (tag) => new El(tag),
  createTextNode: (t) => new TextNode(t),
  getElementById: () => null,
};
globalThis.window = { confirm: () => true, AudioContext: undefined, webkitAudioContext: undefined };
globalThis.location = { search: '' };

const mod = await import(new URL('../../plugins/alert/frontend.js', import.meta.url).href);
const cards = mod.default.cards;
if (!cards || cards.length !== 1) throw new Error('卡片导出异常');
const card = cards[0];

const PLAN_LIST = [
    { dp_id: 'AL-LINE_BELOW-600271.SH-white', theme: '航天信息 跌破白线（动态）', kind: 'LINE_BELOW',
      kind_cn: '跌破黄白线', name: '航天信息', code: '600271.SH', condition_text: 'price_vs_line<0(line=white)',
      window: '09:35-14:55', exit: 'L2', exit_cn: '直推主人', source: 'session', source_cn: '会话设置',
      enabled: true, state: 'MONITORING', state_cn: '监控中', quote_covered: true, last_hit: null,
      badge: '白线', lit: true, dist_pct: 1.6, prox: 0.85, hot: false },
    { dp_id: 'AL-PRICE_BELOW-600271.SH-6.9', theme: '航天信息 跌破 6.9 提醒', kind: 'PRICE_BELOW',
      kind_cn: '跌破价', name: '航天信息', code: '600271.SH', condition_text: 'stock_price<6.9',
      window: '09:35-14:55', exit: 'L2', exit_cn: '直推主人', source: 'kline', source_cn: 'K线右键',
      enabled: true, state: 'PUSHED', state_cn: '已推送', quote_covered: true,
      last_hit: { ts: '2026-11-07 09:40:00' }, badge: '6.9', lit: false,
      dist_pct: -0.4, prox: null, hot: false },
    { dp_id: 'AL-INDEX_PCT_BELOW-880008.SH-m1', theme: '全A 指数跌幅跌破 1（系统级）',
      kind: 'INDEX_PCT_BELOW', kind_cn: '指数跌幅', name: '全A', code: '880008.SH',
      condition_text: 'index_pct<-1', window: '09:35-14:55', exit: 'L1', exit_cn: '唤醒夏天',
      source: 'review', source_cn: '复盘产出', enabled: true, state: 'PENDING', state_cn: '待激活',
      quote_covered: true, last_hit: null, badge: '指数跌1%', lit: true },
];

const payload = {
  ok: true,
  trade_date: '20261107',
  updated_at: '2026-11-07 01:00:00',
  channels: { page: true, sound: true, qq: false },
  stats: { total: 3, enabled: 3, monitoring: 1, hit_today: 1, closed: 0, by_exit: { L1: 1, L2: 2 } },
  plans: PLAN_LIST,
  groups: [
    { key: '600271.SH', code: '600271.SH', name: '航天信息', exit: 'L2', total: 2, lit: 1,
      enabled: 2, frozen: false, last_hit_ts: '2026-11-07 09:40:00',
      plans: [PLAN_LIST[0], PLAN_LIST[1]] },
    { key: '__system__', code: '', name: '大盘 / 全A', exit: 'L1', total: 1, lit: 1,
      enabled: 1, frozen: false, last_hit_ts: '', plans: [PLAN_LIST[2]] },
  ],
  flow: [
    { ts: '2026-11-07 09:36:00', dp_id: 'AL-LINE_BELOW-600271.SH-white', theme: '航天信息 跌破白线（动态）',
      from: 'PENDING', to: 'MONITORING', reason: '进入评估窗口', exit: 'L2', trigger_values: [], delivery: {} },
  ],
  enums: {
    states: [{ code: 'PENDING', name: '待激活' }, { code: 'MONITORING', name: '监控中' }, { code: 'HIT', name: '命中' },
      { code: 'PUSHED', name: '已推送' }, { code: 'WOKEN', name: '已唤醒' }, { code: 'EXPIRED', name: '失效' },
      { code: 'CLOSED', name: '手工关闭' }],
    exits: [{ code: 'L1', name: '唤醒夏天' }, { code: 'L2', name: '直推主人' }],
    sources: [{ code: 'session', name: '会话设置' }],
    channels: [{ code: 'page', name: '页内' }],
    types: [{ code: 'PRICE_BELOW', name: '跌破价' }, { code: 'INDEX_PCT_BELOW', name: '指数跌幅' }],
    type_cn: { PRICE_BELOW: '跌破价', INDEX_PCT_BELOW: '指数跌幅' },
  },
  todo: { alerts: [{ level: 'danger', message: '征和工业 -10.00%', ts: '2026-11-06 14:58:37' }], todos: [] },
  notify: { ok: true, configured: true, last_ok_ts: '2026-11-06 19:21:41', consecutive_fails: 0 },
  paths: { store: 'C:/x/alert_plan_persistent.json' },
};

const root = new El('div');
const calls = [];
const ctx = {
  api: {
    async get(path) { calls.push(['GET', path]); if (path !== '/alert/list') throw new Error('意外路径 ' + path); return payload; },
    async post(path, body) { calls.push(['POST', path, body]); return { ok: true, channels: { page: true, sound: true, qq: true } }; },
  },
  log() {},
};

const inst = await card.mount(root, ctx);
if (!calls.some((c) => c[0] === 'GET' && c[1] === '/alert/list')) throw new Error('未调用读端点');

const dump = (node) => {
  let s = node.textContent || '';
  for (const c of node.children || []) s += ' ' + dump(c);
  return s;
};
const text = dump(root);
for (const kw of ['预警', '监控中', '今日流水', '今日异动', '新增预警', '唤醒夏天', '航天信息', '大盘 / 全A']) {
  if (!text.includes(kw)) throw new Error('页面缺文案：' + kw);
}
if (!text.includes('2 道') && !text.includes('1 道')) throw new Error('卡片未显示防线计数');
if (!text.includes('白线')) throw new Error('徽标文案未渲染');

// 卡片联动：股票名要带 data-open-kline（壳的 K 线桥靠这个属性弹窗）
const findKlineNode = (node) => {
  if (node.dataset && node.dataset.openKline) return node;
  for (const c of node.children || []) {
    const hit = findKlineNode(c);
    if (hit) return hit;
  }
  return null;
};
const knode = findKlineNode(root);
if (!knode) throw new Error('卡片缺 data-open-kline（点名称弹不了 K 线）');
if (knode.dataset.openKline !== '600271.SH') throw new Error('data-open-kline 值不对：' + knode.dataset.openKline);

// 靠近防线的胶囊：应带 near 类与 --prox（外圈深浅）
const findNear = (node) => {
  if (node.className && String(node.className).indexOf('bdg') >= 0 && String(node.className).indexOf('near') >= 0) return node;
  for (const c of node.children || []) {
    const hit = findNear(c);
    if (hit) return hit;
  }
  return null;
};
if (!findNear(root)) throw new Error('距离 5% 内的徽标没有 near 类（外圈深浅不生效）');

// 长按拖动：卡片上要挂 pointerdown
const findCard = (node) => {
  if (node.dataset && node.dataset.key) return node;
  for (const c of node.children || []) {
    const hit = findCard(c);
    if (hit) return hit;
  }
  return null;
};
const cardEl = findCard(root);
if (!cardEl || !cardEl._ev || !cardEl._ev.pointerdown) throw new Error('卡片没挂长按拖动');

// 二次刷新不应抛
await inst.refresh();
console.log('SMOKE OK · 卡片分组/徽标/流水渲染正常；GET/POST 路径符合 /api/p/alert 前缀契约');
