// labels.js 单测（2026-11-04）：纯函数层，不需要 DOM。
//   node app/plugins/kline/tests/js/p1e_labels.test.mjs
import assert from 'node:assert/strict';

const L = await import(new URL('../../frontend/labels.js', import.meta.url).href);

let n = 0;
function it(name, fn) {
  fn();
  n++;
  console.log('ok ' + n + ' - ' + name);
}

function fakeStore(seed) {
  const m = Object.assign({}, seed || {});
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null),
    setItem: (k, v) => { m[k] = String(v); },
    _dump: () => m,
  };
}
const CODE = '600036.SH';

it('空存储：没有标签，显示开关默认开', () => {
  const s = fakeStore();
  assert.deepEqual(L.readCode(s, CODE), []);
  assert.equal(L.readOn(s), true);
});

it('新增一条能读回，文字去空白、截断到 24 字', () => {
  const s = fakeStore();
  const r = L.addLabel(s, CODE, { text: '  竞价异动\n高开3.2%  ', color: '#ffc857', shape: 'fill' });
  assert.equal(r.ok, true);
  assert.deepEqual(L.readCode(s, CODE), [{ text: '竞价异动 高开3.2%', color: '#ffc857', shape: 'fill' }]);
  const long = '一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十一二三四五六';
  L.addLabel(s, CODE, { text: long });
  assert.equal(L.readCode(s, CODE)[1].text.length, L.TEXT_MAX);
});

it('空文字拒绝，且不落盘', () => {
  const s = fakeStore();
  const r = L.addLabel(s, CODE, { text: '   ' });
  assert.equal(r.ok, false);
  assert.match(r.reason, /不能为空/);
  assert.deepEqual(L.readCode(s, CODE), []);
});

it('满 8 条后拒绝第 9 条', () => {
  const s = fakeStore();
  for (let i = 1; i <= L.MAX_PER_CODE; i++) assert.equal(L.addLabel(s, CODE, { text: 't' + i }).ok, true);
  const r = L.addLabel(s, CODE, { text: 'overflow' });
  assert.equal(r.ok, false);
  assert.match(r.reason, /最多 8 条/);
  assert.equal(L.readCode(s, CODE).length, L.MAX_PER_CODE);
});

it('颜色/形态非法时回退默认，不接受任意字符串', () => {
  const s = fakeStore();
  L.addLabel(s, CODE, { text: 'x', color: 'red;background:url(x)', shape: 'blink' });
  const got = L.readCode(s, CODE)[0];
  assert.equal(got.color, L.DEFAULT_COLOR);
  assert.equal(got.shape, 'fill');
});

it('坏 JSON 不崩，按空处理', () => {
  const s = fakeStore({ [L.KEY]: '{not json' });
  assert.deepEqual(L.readAll(s), {});
  assert.deepEqual(L.readCode(s, CODE), []);
});

it('单条非法只丢那一条，其它代码不受影响', () => {
  const s = fakeStore({
    [L.KEY]: JSON.stringify({
      '600036.SH': [{ text: '好' }, { text: '' }, null, { text: '也好', color: '#4da3ff' }],
      '000001.SZ': [{ text: '另一只' }],
    }),
  });
  assert.deepEqual(L.readCode(s, CODE).map((x) => x.text), ['好', '也好']);
  assert.deepEqual(L.readCode(s, '000001.SZ').map((x) => x.text), ['另一只']);
});

it('改文字/颜色/形态，越界与改空都拒绝', () => {
  const s = fakeStore();
  L.addLabel(s, CODE, { text: '旧' });
  assert.equal(L.updateLabel(s, CODE, 0, { text: '新', color: '#35c97f', shape: 'solid' }).ok, true);
  assert.deepEqual(L.readCode(s, CODE)[0], { text: '新', color: '#35c97f', shape: 'solid' });
  assert.equal(L.updateLabel(s, CODE, 0, { text: '  ' }).ok, false);
  assert.equal(L.updateLabel(s, CODE, 5, { text: 'x' }).ok, false);
});

it('删除返回被删的那条，越界返回 ok:false', () => {
  const s = fakeStore();
  L.addLabel(s, CODE, { text: 'A' });
  L.addLabel(s, CODE, { text: 'B' });
  const r = L.removeLabel(s, CODE, 0);
  assert.equal(r.ok, true);
  assert.equal(r.removed.text, 'A');
  assert.deepEqual(L.readCode(s, CODE).map((x) => x.text), ['B']);
  assert.equal(L.removeLabel(s, CODE, 9).ok, false);
});

it('清空后该代码从存储里消失', () => {
  const s = fakeStore();
  L.addLabel(s, CODE, { text: 'A' });
  L.clearCode(s, CODE);
  assert.deepEqual(L.readCode(s, CODE), []);
  assert.deepEqual(L.readAll(s), {});
});

it('显示开关能写能读', () => {
  const s = fakeStore();
  L.writeOn(s, false);
  assert.equal(L.readOn(s), false);
  L.writeOn(s, true);
  assert.equal(L.readOn(s), true);
});

it('存储写失败（配额/禁用）返回 ok:false 且带原因', () => {
  const s = { getItem: () => null, setItem: () => { throw new Error('QuotaExceeded'); } };
  const r = L.addLabel(s, CODE, { text: 'x' });
  assert.equal(r.ok, false);
  assert.match(r.reason, /存不下/);
});

it('菜单项：清空项在没有标签时禁用', () => {
  const items = L.menuItems(0, true);
  const clear = items.find((x) => x.id === 'lab_clear');
  assert.equal(clear.disabled, true);
  const show = items.find((x) => x.id === 'lab_show');
  assert.equal(show.checked, true);
  assert.equal(L.menuItems(2, false).find((x) => x.id === 'lab_clear').disabled, false);
});

console.log('\n' + n + ' tests passed');
