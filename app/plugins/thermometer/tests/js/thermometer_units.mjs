// 情绪温度计 · 纯函数单测（node，无浏览器、无网络）
//   node app/plugins/thermometer/tests/js/thermometer_units.mjs
// 守的是展示层最容易算错的四件事：分位口径、炸板率、断点分段、三态动作。
import { pctRank, burstRate, splitSegments, decide, bandOf, num } from '../../frontend.js';

let failed = 0;
function eq(name, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g === w) { console.log('  ok   ' + name); return; }
  failed++;
  console.log('  FAIL ' + name + ' -> got ' + g + ', want ' + w);
}
function near(name, got, want, eps) {
  const e = eps === undefined ? 1e-9 : eps;
  if (got !== null && got !== undefined && Math.abs(got - want) <= e) { console.log('  ok   ' + name); return; }
  failed++;
  console.log('  FAIL ' + name + ' -> got ' + got + ', want ' + want);
}

// ---------------------------------------------------------------- num
eq('num(null) -> null', num(null), null);
eq('num("") -> null', num(''), null);
eq('num("abc") -> null', num('abc'), null);
eq('num(0) -> 0（0 不当中性值丢掉）', num(0), 0);

// ---------------------------------------------------------------- 分位（与 emotion.engine.pct_rank 同式）
// 窗口内 1..10，value=10 → (9 个严格小于 + 0.5×1) / 10 = 0.95
near('分位：最大值 0.95（并列按半分）', pctRank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 10), 0.95);
near('分位：最小值 0.05', pctRank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 1), 0.05);
near('分位：并列 3 个 5 → (4 + 1.5)/10', pctRank([1, 2, 3, 4, 5, 5, 5, 8, 9, 10], 5), 0.55);
eq('分位：样本 <5 → null（页面显示「缺」）', pctRank([1, 2, 3, 4], 4), null);
eq('分位：缺值不参与但不报错', pctRank([1, null, 3, 4, 5, 6], 6), 0.9);
eq('分位：当日缺值 → null', pctRank([1, 2, 3, 4, 5], null), null);
// 窗口截断：只取最近 60 个样本 —— 100 个旧 0 + 10/20/30/40/50，最后 60 个 = 55 个 0 + 5 个新值
{
  const values = new Array(100).fill(0).concat([1, 2, 3, 4, 5].map((x) => x * 10));
  const p = pctRank(values, 50, 60, 5);
  near('分位：窗口只取最近 60 个（旧样本不参与）', p, (55 + 4 + 0.5) / 60);
}

// ---------------------------------------------------------------- 炸板率
near('炸板率 = (曾涨停 − 涨停) / 曾涨停', burstRate({ zt_total: 39, zt_ever: 64 }), 25 / 64);
eq('炸板率：曾涨停 0 → null（不是 0）', burstRate({ zt_total: 0, zt_ever: 0 }), null);
eq('炸板率：缺字段 → null', burstRate({ zt_total: 39 }), null);
eq('炸板率：空行 → null', burstRate(null), null);

// ---------------------------------------------------------------- 断点分段
{
  const pts = [
    { date: '20261020', value: 50 },
    { date: '20261021', value: 60 },
    { date: '20261022', value: null },   // 缺口
    { date: '20261023', value: 70 },
    { date: '20261029', value: 23.1 },   // 断点日（四项口径）
    { date: '20261030', value: 30 },
  ];
  const segs = splitSegments(pts, '20261029');
  eq('分段：缺口与断点都要断开', segs.map((s) => s.map((p) => p.date)), [
    ['20261020', '20261021'],
    ['20261023'],
    ['20261029', '20261030'],
  ]);
  eq('分段：不给断点日 → 只按缺口断', splitSegments(pts, null).length, 2);
  eq('分段：全缺 → 0 段', splitSegments([{ date: '20261029', value: null }], '20261029').length, 0);
}

// ---------------------------------------------------------------- 三态动作
eq('动作：冰点共振 → 等信号', decide(8.26, 23.1).label, '等信号');
eq('动作：情绪冰点但恐慌不冰 → 不加仓', decide(20, 60).label, '不加仓');
eq('动作：中性区间 → 做结构', decide(45, 30).label, '做结构');
eq('动作：偏热 → 做结构（持股为主）', decide(75, 60).label, '做结构');
eq('动作：恐慌过热压过偏热 → 不加仓', decide(75, 85).label, '不加仓');
eq('动作：情绪缺失 → 不出判定', decide(null, 23.1).label, '缺读数');

// ---------------------------------------------------------------- 档位（与 emotion.BANDS 同值）
eq('档位：8.26 → 冰点', bandOf(8.26).label, '冰点');
eq('档位：25 → 偏冷（下界含）', bandOf(25).label, '偏冷');
eq('档位：40 → 中性', bandOf(40).label, '中性');
eq('档位：55 → 偏热', bandOf(55).label, '偏热');
eq('档位：70 → 亢奋', bandOf(70).label, '亢奋');
eq('档位：缺值 → null', bandOf(null), null);

console.log(failed ? 'FAILED ' + failed : 'ALL OK');
process.exit(failed ? 1 : 0);
