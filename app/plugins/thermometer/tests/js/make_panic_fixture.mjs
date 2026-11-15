// 生成 2026-10-29 收盘口径的 /api/panic 回放夹具（测试与截图用）。
//   node app/plugins/thermometer/tests/js/make_panic_fixture.mjs
// 做法：取当前实例的 panic 序列（库内到 10-23）+ 把 10-29 那一条收盘读数补上。
// 10-29 的四项值与分位是 2026-10-29 23:3x 从活实例记录的真实响应（mode=close、四项口径），
// 不是估算值；夹具里 `_fixture` 字段写明来源与用途。
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.argv[2] || 'http://127.0.0.1:8810';
const OUT = path.join(HERE, 'fixtures', 'panic_20261029.json');

const AMOUNT = 1556524171264.0;
const PREV_AMOUNT = 1628487560384.0;

// 2026-10-29 收盘实测（原样记录，勿改）
const RECORDED = {
  value: 23.1,
  state: '冰点',
  items: [
    { key: 'limit_down', label: '跌停占比', unit: '%', value: 0.7624, count: 42, percentile: 85.2 },
    { key: 'up_ratio', label: '上涨占比', unit: '%', value: 14.1768, count: 781, percentile: 6.0 },
    { key: 'median', label: '全A中位涨跌', unit: '%', value: -2.5969, count: null, percentile: 5.2 },
    { key: 'amount', label: '量能（成交额/昨日）', unit: '×', value: 0.9558, count: null,
      percentile: 33.6, kind: 'actual', active: true, state: 'in',
      amount: AMOUNT, prev_amount: PREV_AMOUNT, ratio_same_time: 0.9558 },
  ],
  volume: { amount: AMOUNT, kind: 'actual', prev_day_amount: PREV_AMOUNT, ratio: 0.9558,
    ratio_same_time: 0.9558, percentile: 33.6, state: 'in',
    source: 'intraday:total_amount|prev=intraday:prev_same_time_amount' },
};

const live = await fetch(BASE + '/api/panic').then((r) => r.json());
const series = (live.series || []).filter((p) => p.date !== '20261029');
series.push({ date: '20261029', value: RECORDED.value, q_limit_down_count: null, up: 781 });

const doc = {
  _fixture: {
    what: 'GET /api/panic 的 2026-10-29 收盘口径回放（mode=close，四项含量能）',
    why: ('页面是收盘后日度工具；零点之后 /api/panic 按设计回落到库内最后一行（mode=closed），'
      + '为了让测试与截图稳定复现 10-29 收盘读数，用本夹具替换该请求，其余接口仍然真实。'),
    recorded_at: '2026-10-29 23:3x（活实例真实响应）',
    series_source: '库内既有序列（至 ' + (((live.series || []).slice(-1)[0] || {}).date || '?') + '）+ 10-29 记录点',
  },
  ok: true,
  asof: '2026-10-29 23:35:00',
  trade_date: '20261029',
  mode: 'close',
  live: false,
  source: 'eltdx:880005+880006+880009',
  window: live.window || 250,
  value: RECORDED.value,
  state: RECORDED.state,
  items: RECORDED.items,
  series,
  stage: { date: '20261029', label: '退潮' },
  volume: RECORDED.volume,
  note: '回放夹具：恐慌广度=四项（跌停占比/上涨占比/全A中位/量能）250 日滚动分位反向均值',
};

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(doc, null, 1), 'utf8');
console.log('夹具已写入 ' + OUT + '（series ' + series.length + ' 点，含 10-29）');
