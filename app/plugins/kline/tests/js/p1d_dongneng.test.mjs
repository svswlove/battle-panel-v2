// P1d 单测：动能K 染色算子 `indicators.js::dongneng` 与 Python 独立实现对拍。
//
// 为什么这样测：JS 算子是照通达信原文（主人 2026-11-02 提供）转写的，Python 侧
// （.tmp_dn/gen_fixture.py，已随本文件删除；口径同 jrx_momentum.py::calc_jrx + 原公式扩展）
// 是另一个语言、另一条实现路径。两边都从公式文本出发、互不复用代码，逐值对上才算移植正确。
// fixture 一旦生成即与数据源解耦（静态 JSON），不依赖 QMT / track_pool 是否在线。
//
// 对拍范围：base（基础动能）、xd（X动能）比数值（容差 1e-9）；sig（染色）与 bg（背景）比布尔。
// 输入用 fixture 全量 300 根（含预热段），只比对末 240 根 —— 前端实际取 240 根。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

globalThis.window = { devicePixelRatio: 1 };
const IND = await import(new URL('../../frontend/indicators.js', import.meta.url).href);

const FIXTURE = new URL('./_fixtures/dongneng.json', import.meta.url);
const data = JSON.parse(readFileSync(FIXTURE, 'utf8'));

let n = 0;
const it = (name, fn) => { fn(); n += 1; console.log('ok ' + n + ' - ' + name); };

const TOL = 1e-9;

function near(a, b) {
  if (a === null || b === null) return a === b;
  return Math.abs(a - b) <= TOL * Math.max(1, Math.abs(a), Math.abs(b));
}

function tail(arr, k) {
  return arr.slice(Math.max(0, arr.length - k));
}

it('fixture 形状完整', () => {
  assert.ok(Array.isArray(data.cases) && data.cases.length >= 2, '用例数应 ≥2');
  for (const cs of data.cases) {
    assert.equal(cs.bars.length, cs.n, cs.code + ' bars 长度应等于 n');
    for (const k of ['base', 'xd', 'sig']) {
      assert.ok(Array.isArray(cs.expect[k]), cs.code + ' 缺 ' + k);
    }
    assert.equal(cs.expect.sig.length, Math.min(240, cs.n), cs.code + ' 期望序列应为末 240 根');
  }
});

for (const cs of data.cases) {
  const out = IND.dongneng(cs.bars);
  const k = cs.expect.sig.length;

  it(cs.code + ' 输出长度与输入等长', () => {
    assert.equal(out.base.length, cs.bars.length);
    assert.equal(out.sig.length, cs.bars.length);
  });

  it(cs.code + ' base 逐值一致（容差 1e-9）', () => {
    const g = tail(out.base, k);
    let worst = 0, worstAt = -1;
    for (let i = 0; i < k; i++) {
      const a = g[i], b = cs.expect.base[i];
      if (!near(a, b)) {
        const d = (a === null || b === null) ? Infinity : Math.abs(a - b);
        if (d > worst) { worst = d; worstAt = i; }
      }
    }
    assert.equal(worstAt, -1,
      cs.code + ' base 第 ' + worstAt + ' 根不一致：JS=' + g[worstAt] + ' PY=' + cs.expect.base[worstAt]);
  });

  it(cs.code + ' xd 逐值一致（容差 1e-9）', () => {
    const g = tail(out.xd, k);
    for (let i = 0; i < k; i++) {
      assert.ok(near(g[i], cs.expect.xd[i]),
        cs.code + ' xd 第 ' + i + ' 根不一致：JS=' + g[i] + ' PY=' + cs.expect.xd[i]);
    }
  });

  it(cs.code + ' sig（染色）逐根一致', () => {
    const g = tail(out.sig, k);
    for (let i = 0; i < k; i++) {
      assert.equal(g[i] ? 1 : 0, cs.expect.sig[i],
        cs.code + ' sig 第 ' + i + ' 根不一致（JS=' + g[i] + ' PY=' + cs.expect.sig[i] + '）');
    }
    assert.ok(cs.expect.sig.some((x) => x === 1), cs.code + ' 样本里应有可染色的日子');
  });

}

it('空输入返回空序列（不抛）', () => {
  const o = IND.dongneng([]);
  assert.deepEqual(o.sig, []);
});

it('单根输入不崩（预热全缺值，无信号）', () => {
  const o = IND.dongneng([{ open: 1, high: 1, low: 1, close: 1, volume: 1 }]);
  assert.equal(o.sig.length, 1);
  assert.equal(o.sig[0], false);
  assert.equal(o.base[0], null);
});

it('X动能只在真阳线且动能为正增量时非零', () => {
  const cs = data.cases[0];
  const out = IND.dongneng(cs.bars);
  const k = cs.expect.sig.length;
  const zero = tail(out.xd, k).filter((x) => x === 0).length;
  assert.ok(zero > 0, '样本里应有大量 X动能为 0 的日子');
});

console.log('\nP1d 通过 ' + n + ' 项');
