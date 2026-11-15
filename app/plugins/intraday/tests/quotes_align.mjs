/**
 * 实时行情 KPI · 旧页对齐运行器（给 tests/test_quotes.py 调用；也可单独 node 跑）
 *
 * 比什么：
 *   ① mqCard / mqHtml：逐卡逐字节比，只归一化两处强制差异
 *      （全A 行的 regime-mark 占位本卡不接、path 的 d 已填 —— 后者正是本单的改动目的）
 *   ② _amvAlias（旧页 5270-5271）：880008.SH → ALL
 *   ③ sparkCodes（旧页 5132-5133 的 data-idx 集合）：去重保序
 *   ④ kCode 占位（旧页 2920-2927）：行情卡的名字列可点
 *   ⑤ 空行情降级文案（旧页 4685）
 *
 * **spark 回填是本单唯一「不照抄」的行为**（任务书点名）：旧页先渲空 path、异步回填 d；
 * 卡片改成同一拍写入。故比对时给旧页那一份也灌同样数据（模拟回填完成态），
 * 再断言卡片**首绘**就是满的 —— 后者是 audit_dom.mjs 的真 DOM 断言，不在本文件。
 *
 * 输出：最后一行 JSON（pytest 取最后一行）。
 */
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeSeg, UTIL_LINES } from './legacy_anchor.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PANEL = path.resolve(HERE, '../../../..');
const REPO = path.resolve(PANEL, '../..');
const LEGACY = path.join(REPO, 'engine', 'web', 'index.html');
const FIXTURE = path.join(HERE, 'fixtures/intraday_payload.json');

const Q = await import(new URL('../frontend/quotes.js', import.meta.url).href);

/** 旧页切片：按锚点定位（2026-10-30 起不再写行号，旧页加行也不会切错） */
const seg = makeSeg(LEGACY);

/** 旧页 mqHtml 真身（`const mqHtml = mq.length` 起整段）+ 它依赖的 esc/num/pct/cls/kTsPlayable/kCode/_amvAlias */
export function legacyBox() {
  const box = { console: { info() {}, warn() {}, log() {} } };
  vm.createContext(box);
  const src = [
    seg.each(UTIL_LINES),                                    // esc / num / pct / cls 四行
    seg.block(/^function kTsPlayable\(/),
    seg.block(/^function kCode\(/),
    seg.line(/^const AMV_CODE_ALIAS = /) + '\n' + seg.block(/^function _amvAlias\(/),
    // mqHtml（const mq / const mqHtml）是 renderIntraday 的局部声明，包一层函数再跑
    'globalThis.__mqHtml = function(d){ var mq = d.market_quotes||[];',
    seg.stmt(/const mqHtml = mq\.length/),
    'return mqHtml; };',
    'globalThis.__amvAlias = _amvAlias;',
    'globalThis.__kCode = kCode;',
    'globalThis.__ALIAS = AMV_CODE_ALIAS;',
  ].join('\n');
  vm.runInContext(src, box, { filename: 'legacy-quotes.js' });
  return box;
}

/** 归一化：只抹掉「本单明确改的两处」，其余必须逐字节相同 */
export function normalize(h) {
  return h
    // ① 全A 行的 regime-mark 占位：本卡不取 amv_regime / battle_plan / real_positions 三个端点，
    //    留个空 span 等于给用户一个永远不亮的灯（见 quotes.js 文件头）
    .replace(/ <span class="regime-mark" data-regime><\/span>/g, '')
    .replace(/>\s+</g, '><')
    .replace(/\s+/g, ' ')
    .trim();
}

/** 从整块 HTML 里按 data-idx 切出单卡（旧页没有单卡函数，只有这段内联 map） */
export function pickCard(html, idx) {
  const MARK = '<div class="kpi mq-kpi" data-idx="' + idx + '">';
  const at = html.indexOf(MARK);
  if (at < 0) return '';
  const next = html.indexOf('<div class="kpi mq-kpi"', at + MARK.length);
  if (next >= 0) return html.slice(at, next);
  // 最后一张卡后面跟着 </div>（关 .grid），丢掉它再比
  return html.slice(at).replace(/<\/div>$/, '');
}

/** 把旧页的空 d 填成桩数据（= _sparkInject 回填完成后的 DOM 状态） */
export function inject(card, sp) {
  return card
    .replace('class="mq-spark-amv" d=""', 'class="mq-spark-amv" d="' + ((sp || {}).amv || '') + '"')
    .replace('class="mq-spark-ma" d=""', 'class="mq-spark-ma" d="' + ((sp || {}).ma10 || '') + '"');
}

export function run() {
  const payload = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
  const quotes = payload.market_quotes || [];
  const codes = Q.sparkCodes(quotes);
  // 桩一份 spark（真实端点返回 {code:{amv,ma10}}，形状同旧页 _sparkData）
  const spark = {};
  codes.forEach((c, i) => {
    spark[c] = { amv: 'M 0.0,' + i + ' L 100.0,' + (i + 1), ma10: 'M 0.0,' + (i + 2) };
  });

  const box = legacyBox();
  const out = {};

  const legacyRaw = box.__mqHtml({ market_quotes: quotes });
  out.rawHasEmptyPath = (legacyRaw.match(/d=""/g) || []).length;
  out.curHasEmptyPath = (Q.mqHtml(quotes, spark).match(/d=""/g) || []).length;

  // ① 逐卡比：旧页单卡（回填后）vs 卡片首绘
  out.cards = quotes.map((q) => {
    const idx = Q.amvAlias(q.code);
    const legacyCard = inject(pickCard(legacyRaw, idx), spark[idx]);
    const curCard = Q.mqCard(q, spark);
    const a = normalize(legacyCard);
    const b = normalize(curCard);
    let at = -1;
    if (a !== b) { at = 0; while (at < a.length && a[at] === b[at]) at += 1; }
    return {
      code: q.code, idx, equal: a === b, at,
      legacy: a.slice(Math.max(0, at - 60), at + 140),
      cur: b.slice(Math.max(0, at - 60), at + 140),
    };
  });

  // ② 别名表
  out.alias = ['880008.SH', '000001.SH', '399001.SZ', '399303.SZ', ''].map((c) => ({
    c, legacy: box.__amvAlias(c), cur: Q.amvAlias(c),
  }));
  out.aliasTable = { legacy: Object.assign({}, box.__ALIAS), cur: Object.assign({}, Q.AMV_CODE_ALIAS) };

  // ③ 码集合（旧页 5132-5133 的 data-idx 去重）
  const legacyIdx = Array.from(legacyRaw.matchAll(/data-idx="([^"]+)"/g)).map((m) => m[1]);
  const legacyCodes = legacyIdx.filter((c, i) => legacyIdx.indexOf(c) === i);
  out.codes = { cur: codes, legacy: legacyCodes, nQuotes: quotes.length };

  // ④ kCode 占位（行情卡的名字列可点）
  out.kCode = quotes.slice(0, 3).map((q) => ({
    code: q.code,
    hasOpen: Q.mqCard(q, spark).indexOf('data-open-kline="' + q.code + '"') >= 0,
  }));

  // ⑤ 空行情降级文案（旧页 4685）
  out.empty = { legacy: box.__mqHtml({ market_quotes: [] }), cur: Q.mqHtml([], null) };

  // ⑥ 无 spark：卡片照常出全部小卡，只是 d 为空（背景线缺失不拖住行情）
  const noSpark = Q.mqHtml(quotes, null);
  out.noSpark = {
    cards: (noSpark.match(/class="kpi mq-kpi"/g) || []).length,
    emptyPaths: (noSpark.match(/d=""/g) || []).length,
  };

  out.meta = { quotes: quotes.length, cardsEqual: out.cards.every((c) => c.equal) };
  return out;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  console.log(JSON.stringify(run()));
}
