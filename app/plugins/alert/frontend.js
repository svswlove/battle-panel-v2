// 预警页卡片（2026-11-07 预警页重建任务书 §二；01:50 按主人要求改卡片式）
//
// 布局口径（主人原话）：
//   · 按股票一张卡，防线做成徽标——还活着的点亮，已经破了的熄灭；
//   · 启用的卡正常显示，被临时冻结的卡整卡变灰。
//
// 只做提醒：页面里**没有**任何下单、持仓、账户、仓位入口（§七 禁止事项）。
// 数据全部来自一个读端点 `/api/p/alert/alert/list`；写全部走 `/api/p/alert/alert/write`。
// 状态/出口/来源/类型的中文名一律后端下发（唯一源 = engine/common/alert_schema.py + add_alert.TYPE_CN），
// 徽标文案也由后端拼好（`badge` 字段），前端只负责画。

const CSS = `
.al-card{display:flex;flex-direction:column;gap:10px}
.al-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.al-kpis{display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
.al-kpi{font-size:12px;color:var(--dim)}
.al-kpi b{font-size:18px;color:var(--fg-strong);font-variant-numeric:tabular-nums;margin-right:2px}
.al-spacer{flex:1}
.al-ch{display:flex;gap:6px;align-items:center}
.al-chbtn{font:inherit;font-size:12px;padding:3px 9px;border-radius:var(--radius-sm);
  border:1px solid var(--line);background:transparent;color:var(--dim);cursor:pointer}
.al-chbtn.on{border-color:var(--accent);color:var(--accent);background:var(--card2)}
.al-btn{font:inherit;font-size:12px;padding:4px 10px;border-radius:var(--radius-sm);
  border:1px solid var(--line);background:var(--card2);color:var(--fg);cursor:pointer}
.al-btn:hover{border-color:var(--accent)}
.al-btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-on)}
.al-btn.danger:hover{border-color:var(--down);color:var(--down-ink)}
.al-sec{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--dim);margin-top:4px}
.al-sec:after{content:"";flex:1;height:1px;background:var(--line-soft)}
.al-form{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:8px;
  background:var(--card2);border:1px solid var(--line-soft);border-radius:var(--radius-sm);padding:10px}
.al-form[hidden]{display:none}
.al-f{display:flex;flex-direction:column;gap:3px;font-size:11px;color:var(--dim)}
.al-f input,.al-f select{font:inherit;font-size:12px;padding:4px 6px;border-radius:var(--radius-sm);
  border:1px solid var(--line);background:var(--card);color:var(--fg)}
.al-foot{grid-column:1/-1;display:flex;gap:8px;align-items:center}
.al-msg{font-size:12px;color:var(--dim)}
.al-msg.err{color:var(--down-ink)}

/* ---- 卡片网格：一只票一张卡 ---- */
.al-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:10px}
.wc{background:var(--card2);border:1px solid var(--line-soft);border-radius:var(--radius-md,10px);
  padding:9px 11px 8px;display:flex;flex-direction:column;gap:7px;
  transition:border-color .15s ease,opacity .15s ease}
.wc:hover{border-color:var(--line)}
.wc.frozen{opacity:.45;filter:grayscale(1)}
.wc.sys{border-left:3px solid var(--warn-solid,var(--warn))}
.wc.armed{border-left:3px solid var(--accent)}
.wc-hd{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;cursor:pointer}
.wc-name{font-size:13.5px;font-weight:650;color:var(--fg-strong);cursor:pointer}
.wc.dragging{opacity:.35}
.wc.pressing{border-color:var(--accent);box-shadow:0 6px 18px color-mix(in srgb, var(--fg) 18%, transparent)}
.wc.drop{border-color:var(--accent);box-shadow:inset 2px 0 0 var(--accent)}
.wc-ghost{position:fixed;z-index:9998;pointer-events:none;opacity:.85;transform:scale(.98)}
.wc-name:hover{color:var(--accent);text-decoration:underline dotted}
.al-menu{position:fixed;z-index:9999;min-width:150px;padding:4px;border-radius:8px;
  background:var(--card2);border:1px solid var(--line);box-shadow:0 10px 28px color-mix(in srgb, var(--fg) 22%, transparent)}
.al-mi{display:block;width:100%;text-align:left;font:inherit;font-size:12.5px;padding:6px 10px;
  border:none;background:transparent;color:var(--fg);cursor:pointer;border-radius:6px}
.al-mi:hover{background:var(--line-soft)}
.al-mi.danger{color:var(--down-ink)}
.wc-code{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}
.wc-count{font-size:11px;color:var(--dim);margin-left:auto;font-variant-numeric:tabular-nums}
.wc-freeze{font:inherit;font-size:11px;padding:2px 8px;border-radius:var(--radius-sm);
  border:1px solid var(--line);background:transparent;color:var(--dim);cursor:pointer}
.wc-freeze:hover{border-color:var(--accent);color:var(--accent)}
.wc-tag{font-size:10px;padding:1px 6px;border-radius:var(--radius-sm);
  background:var(--line-soft);color:var(--dim)}
.wc-badges{display:flex;flex-wrap:wrap;gap:5px}
.bdg{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;line-height:1.2;
  padding:3px 8px;border-radius:999px;border:1px solid var(--line);color:var(--dim);
  background:transparent;cursor:pointer;font-variant-numeric:tabular-nums;white-space:nowrap;position:relative}
/* 靠近防线：5% 内越近外圈越深，1% 内转红（主人 2026-11-07） */
.bdg.near::after{content:"";position:absolute;inset:-3px;border-radius:999px;
  border:2px solid var(--danger);opacity:calc(var(--prox, 0) * 0.9);pointer-events:none}
.bdg.hot{border-color:var(--danger);color:var(--danger-ink)}
.bdg.hot::after{opacity:.85}
/* 活着的防线：点亮 */
.bdg.lit{border-color:var(--accent);color:var(--accent);background:color-mix(in srgb, var(--accent) 12%, transparent)}
.bdg.lit .dot{background:var(--accent)}
/* 已经破了的防线：熄灭 */
.bdg.dead{opacity:.5;text-decoration:line-through;border-style:dashed}
.bdg .dot{width:6px;height:6px;border-radius:50%;background:var(--line);flex:none}
.bdg.dead .dot{background:transparent;border:1px solid var(--dim)}
/* 跌破（关注池已判破）：红但不断句 */
.bdg.broken{border-color:var(--danger);color:var(--danger-ink);border-style:dashed;opacity:.9}
.bdg.broken .dot{background:var(--danger)}
/* 来源 auto（自选池批量派生）：只落盘、不推送 —— 点线边 + 透明度略降，和会推的区分开 */
.bdg.au{border-style:dotted}
.bdg.au:not(.dead){opacity:.82}
.bdg.au .dot{background:transparent;border:1px dotted var(--dim)}
.bdg.lit.au{opacity:.92;border-color:var(--accent)}
.org{font-size:10px;padding:0 5px;border-radius:var(--radius-sm);border:1px solid var(--line);color:var(--dim);white-space:nowrap}
.org.o-manual{border-color:var(--accent);color:var(--accent)}
.org.o-review{border-color:var(--warn);color:var(--warn)}
.org.o-auto{opacity:.85}
.wc-foot{font-size:11px;color:var(--dim);display:flex;gap:10px;flex-wrap:wrap}
.wc-rows{display:flex;flex-direction:column;gap:4px;border-top:1px dashed var(--line-soft);padding-top:6px}
.wc-row{display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;
  font-size:11.5px;color:var(--fg);padding:2px 0}
.wc-row .st{color:var(--dim)}
.wc-row .ops{display:flex;gap:4px}
.wc-row .ops .al-btn{font-size:10.5px;padding:1px 6px}
.wc-row.dead{opacity:.65}

.al-flow{display:flex;flex-direction:column;gap:3px;font-size:12px;font-variant-numeric:tabular-nums}
.al-fr{display:grid;grid-template-columns:120px 1fr auto;gap:8px;padding:3px 0;border-bottom:1px dashed var(--line-soft)}
.al-fr .t{color:var(--dim)}
.al-fr .s{color:var(--fg)}
.al-fr .d{font-size:11px;color:var(--dim)}
.al-todo{display:flex;flex-direction:column;gap:3px;font-size:12px}
.al-td{display:flex;gap:8px;color:var(--fg)}
.al-td.danger{color:var(--down-ink)}
.al-empty{font-size:12px;color:var(--dim);padding:6px 0}
.al-note{font-size:11px;color:var(--dim);line-height:1.7;border-top:1px solid var(--line-soft);padding-top:8px}

/* ---- 决策点分区（2026-11-16 主人 20:47：并进预警卡，不新开页） ---- */
.al-dp{display:flex;flex-direction:column;gap:8px;border:1px solid var(--line-soft);
  border-radius:var(--radius-md,10px);padding:9px 11px 10px;background:var(--card)}
.al-dp-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--dim)}
.al-dp-ttl{font-size:13px;font-weight:650;color:var(--fg-strong)}
.al-dp-pill{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:1px 8px;
  font-size:11px;border:1px solid currentColor;white-space:nowrap}
.al-dp-pill .dot{width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.al-dp-pill.run{color:var(--accent)}
.al-dp-pill.stale{color:var(--warn)}
.al-dp-pill.exit{color:var(--dim)}
.al-dp-pill.unknown{color:var(--warn)}
.al-dp-kpis{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--dim);
  font-variant-numeric:tabular-nums}
.al-dp-kpis b{font-size:16px;color:var(--fg-strong);font-variant-numeric:tabular-nums;margin-right:2px}
.al-dp-kpis b.zero{color:var(--dim);font-weight:600}
.al-dpw{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:12.5px;line-height:1.6;
  border:1.5px solid var(--down);border-radius:var(--radius-sm);padding:7px 10px;
  background:color-mix(in srgb, var(--down) 14%, transparent);color:var(--down-ink)}
.al-dpw b{color:var(--down-ink)}
.al-dpw.warn{border-color:var(--warn);background:color-mix(in srgb, var(--warn) 14%, transparent);color:var(--warn)}
.al-dpw.warn b{color:var(--warn)}
.al-dp-on{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--dim);flex-wrap:wrap}
.al-dp-on b{color:var(--fg)}
.al-dpi{border-top:1px dashed var(--line-soft);padding:2px 0}
.al-dpr{display:grid;grid-template-columns:132px minmax(0,1fr) 46px 84px 62px minmax(0,1.2fr) 94px;
  gap:8px;align-items:center;cursor:pointer;font-size:12px;padding:4px 0;list-style:none}
.al-dpr::-webkit-details-marker{display:none}
.al-dpr .i{font-family:var(--font-mono,monospace);font-size:11px;color:var(--fg-strong);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.al-dpr .t{color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.al-dpr .sev{font-size:10.5px;text-align:center;border-radius:3px;border:1px solid currentColor;white-space:nowrap}
.al-dpr .sev.alert{color:var(--down-ink)}
.al-dpr .sev.action{color:var(--warn)}
.al-dpr .sev.other{color:var(--dim)}
.al-dpr .w,.al-dpr .c{font-size:11px;color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.al-dpr .cd{font-size:11.5px;color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.al-dpr .h{text-align:right;font-size:11px;color:var(--dim);white-space:nowrap;font-variant-numeric:tabular-nums}
.al-dpr .h.on{color:var(--down-ink);font-weight:650}
.al-dpd{padding:3px 0 7px;font-size:11.5px;line-height:1.75;color:var(--fg)}
.al-dpd .k{color:var(--dim)}
.al-dpd .raw{font-family:var(--font-mono,monospace);font-size:10.5px;color:var(--dim);
  word-break:break-all;margin-top:3px}
.al-dp-note{font-size:11px;color:var(--dim);line-height:1.7}
@media(max-width:760px){.al-form{grid-template-columns:repeat(2,1fr)}.al-grid{grid-template-columns:1fr}
  .al-dpr{grid-template-columns:minmax(0,1fr) 78px}
  .al-dpr .sev,.al-dpr .w,.al-dpr .c{display:none}}
`;

const STYLE_ID = 'alert-page-css';
function ensureCss() {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID; s.textContent = CSS;
  document.head.appendChild(s);
}

const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d) => (v == null || v === '' || !isFinite(Number(v)) ? '—' : Number(v).toFixed(d == null ? 2 : d));
const div = (cls, txt) => { const d = document.createElement('div'); if (cls) d.className = cls; if (txt != null) d.textContent = txt; return d; };
const btn = (label, cls, act, data) => {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'al-btn' + (cls ? ' ' + cls : '');
  b.textContent = label;
  Object.entries(data || {}).forEach(([k, v]) => { b.dataset[k] = String(v); });
  if (act) b.dataset.act = act;
  return b;
};

let ac = null;                      // AudioContext（首次点声音开关时解锁）
function unlockAudio() {
  try {
    if (!ac) {
      const C = window.AudioContext || window.webkitAudioContext;
      if (!C) return false;
      ac = new C();
    }
    if (ac.state === 'suspended') ac.resume();
    return true;
  } catch (e) { return false; }
}
function beep() {
  if (!ac || ac.state !== 'running') return;
  try {
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = 'sine'; o.frequency.value = 880;
    g.gain.setValueAtTime(0.0001, ac.currentTime);
    g.gain.exponentialRampToValueAtTime(0.18, ac.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + 0.28);
    o.connect(g); g.connect(ac.destination);
    o.start(); o.stop(ac.currentTime + 0.3);
  } catch (e) { /* 声音失败不影响页面 */ }
}

export default {
  cards: [{
    id: 'alert.main',
    title: '预警',
    slot: 'main',
    order: 40,
    refreshEvery: 15000,
    async mount(root, ctx) {
      ensureCss();
      root.textContent = '';
      const wrap = div('al-card');
      root.append(wrap);

      const st = {
        data: null, formOpen: false, lastFlowTs: null, booted: false,
        soundOn: false, msg: '', msgErr: false, open: {},
        dp: null, dpErr: '', dpOpen: {},
        form: { type: 'PRICE_BELOW', code: '', name: '', value: '', line: 'white', ma: 'ma5',
                dir: 'down', target: 'auto', window: '09:35-14:55', source: 'session' },
      };

      const post = (body) => ctx.api.post('/alert/write', body);

      function renderForm(S) {
        const types = (S.enums && S.enums.types) || [];
        const t = st.form.type;
        const isLine = t === 'LINE_BELOW';
        const isMa = t === 'MA_BELOW' || t === 'MA_ABOVE';
        const isPull = t === 'PULLBACK_MA' || t === 'PULLBACK_KEYK';
        const isPct = t === 'CHANGE_PCT';
        const isL1 = (t || '').indexOf('INDEX_') === 0 || (t || '').indexOf('AMV_') === 0 || t === 'BREADTH_UP_BELOW';
        const needValue = !isPull && !isLine && !isMa;
        const f = div('al-form');
        if (!st.formOpen) f.hidden = true;

        const sel = document.createElement('select');
        types.forEach(x => {
          const o = document.createElement('option');
          o.value = x.code; o.textContent = x.name + '（' + x.code + '）';
          if (x.code === t) o.selected = true;
          sel.append(o);
        });
        sel.onchange = () => { st.form.type = sel.value; render(); };
        const fType = div('al-f'); fType.append(document.createTextNode('类型'), sel);
        f.append(fType);

        const mk = (label, key, attrs) => {
          const box = div('al-f');
          const inp = document.createElement('input');
          Object.entries(attrs || {}).forEach(([k, v]) => { inp[k] = v; });
          inp.value = st.form[key] == null ? '' : st.form[key];
          inp.oninput = () => { st.form[key] = inp.value; };
          box.append(document.createTextNode(label), inp);
          return box;
        };
        const mkSel = (label, key, opts) => {
          const box = div('al-f');
          const s = document.createElement('select');
          opts.forEach(([v, n]) => {
            const o = document.createElement('option');
            o.value = v; o.textContent = n;
            if (String(st.form[key]) === String(v)) o.selected = true;
            s.append(o);
          });
          s.onchange = () => { st.form[key] = s.value; };
          box.append(document.createTextNode(label), s);
          return box;
        };

        f.append(mk('代码', 'code', { placeholder: isL1 ? '留空=880008.SH' : '600000.SH' }));
        f.append(mk('名称', 'name', { placeholder: '可留空' }));
        if (needValue) f.append(mk(isPct ? '跌幅%（正数）' : '阈值', 'value', { placeholder: isPct ? '5' : '', type: 'number', step: '0.001' }));
        if (isLine) f.append(mkSel('线', 'line', [['white', '白线'], ['yellow', '黄线']]));
        if (isMa) f.append(mkSel('均线', 'ma', [['ma5', '5日线'], ['ma10', '10日线'], ['ma20', '20日线'], ['ma60', '60日线']]));
        if (isPct) f.append(mkSel('方向', 'dir', [['down', '跌'], ['up', '涨']]));
        if (isPull) f.append(mkSel('档位', 'target', t === 'PULLBACK_MA'
          ? [['auto', '自动（白/黄）'], ['white', '白线'], ['yellow', '黄线']]
          : [['auto', '自动（上/下沿）'], ['upper', '动能K上沿'], ['lower', '动能K下沿']]));
        f.append(mk('窗口', 'window', { placeholder: '09:35-14:55' }));
        // 触发来源不给人选（2026-11-11）：面板里加的一律 manual（会推送）；
        // source 只作留痕（后端缺省 session），来源分发看后端下发的 origin。

        const foot = div('al-foot');
        foot.append(btn('加入预警', 'pri', 'add'), btn('收起', '', 'form-close'),
          div('al-msg' + (st.msgErr ? ' err' : ''), st.msg || ''));
        f.append(foot);
        return f;
      }

      function stateText(p) {
        // 2026-11-07：PUSHED 只代表「出口已执行」，要把送达程度接在后面（入队/已发出/已确认）
        // 2026-11-11：来源 auto 命中后不触达（静默），显示「命中·静默」而不是「已交付」
        const base = p.state_cn || p.state || '';
        const dv = p.delivery || {};
        if (p.state === 'PUSHED' && dv.status === 'silent') return '命中·静默';
        if (p.state === 'PUSHED' && dv.status_cn) return base + '·' + dv.status_cn;
        return base;
      }

      function originChip(p) {
        const el = div('org o-' + (p.origin || 'auto'), p.origin_cn || '');
        el.title = p.notify ? '来源：' + (p.origin_cn || '') + '（命中会推送）'
          : '来源：' + (p.origin_cn || '') + '（只落盘，不推 QQ / 不弹窗 / 不出声）';
        return el;
      }

      function planRow(p) {
        const row = div('wc-row' + (p.lit ? '' : ' dead'));
        const left = div('', (p.badge || '') + ' · ' + stateText(p));
        const mid = div('', '');
        mid.append(div('', p.condition_text || ''));
        if (p.broken) {
          mid.append(div('wc-foot', '已跌破（关注池判破 ' + (p.broken_since || '') + '）'));
        }
        const cr = p.close_review || null;
        if (cr && cr.result) {
          const d = cr.result === '回踩不破'
            ? `收盘确认：低 ${fmt(cr.low)} 碰过，收 ${fmt(cr.close)} 守住`
            : (cr.result === '跌破'
              ? `收盘确认：低 ${fmt(cr.low)} 碰过，收 ${fmt(cr.close)} 跌破了`
              : `收盘确认：${cr.result}`);
          mid.append(div('wc-foot', d));
        }
        const ops = div('ops');
        ops.append(btn(p.enabled ? '停' : '启', '', 'toggle', { dp: p.dp_id, en: p.enabled ? '1' : '0' }));
        ops.append(btn('删', 'danger', 'remove', { dp: p.dp_id }));
        row.append(left, mid, ops);
        const foot = div('wc-foot', (p.kind_cn || '') + ' · ' + (p.window || '—') + ' · ');
        foot.append(originChip(p));
        left.append(foot);
        return row;
      }

      const fmt = (v) => (v == null || !isFinite(Number(v)) ? '—' : Number(v).toFixed(2));

      // ---- 卡片右键菜单（2026-11-07 主人提：卡片要能联动 K 线）
      let menuEl = null, menuOff = null;
      function closeCardMenu() {
        if (menuEl) { try { menuEl.remove(); } catch (e) { /* ignore */ } menuEl = null; }
        if (menuOff) { document.removeEventListener('click', menuOff, true); menuOff = null; }
      }

      function openKlineFor(code, name) {
        // 不直接调壳的内部函数（插件与壳无 import 关系），而是造一个同样带 data-open-kline 的
        // 节点真点一下 —— 壳的 K 线桥是 document 捕获监听，认得这个属性，弹窗/嵌父页两条路都走原逻辑。
        const probe = document.createElement('span');
        probe.setAttribute('data-open-kline', code);
        probe.setAttribute('data-open-name', name || '');
        probe.style.display = 'none';
        document.body.appendChild(probe);
        try { probe.click(); } finally { probe.remove(); }
      }

      function openCardMenu(x, y, g) {
        closeCardMenu();
        const m = div('al-menu');
        const W = window.innerWidth || 1200;
        const H = window.innerHeight || 800;
        m.style.left = Math.max(4, Math.min(x, W - 170)) + 'px';
        m.style.top = Math.max(4, Math.min(y, H - 190)) + 'px';
        const item = (label, fn, danger) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'al-mi' + (danger ? ' danger' : '');
          b.textContent = label;
          b.onclick = (ev) => { ev.stopPropagation(); closeCardMenu(); fn(); };
          m.append(b);
        };
        if (g.code) item('打开 K 线', () => openKlineFor(g.code, g.name));
        item(st.open[g.key] ? '收起明细' : '展开明细', () => { st.open[g.key] = !st.open[g.key]; render(); });
        if (g.code) {
          item('给这只票加预警', () => {
            st.form.code = g.code;
            st.form.name = g.name || '';
            st.formOpen = true;
            st.msg = '';
            render();
            const f = wrap.querySelector('.al-form');
            if (f && f.scrollIntoView) f.scrollIntoView({ block: 'center' });
          });
        }
        item(g.frozen ? '恢复这张卡' : '冻结这张卡', async () => {
          try {
            const r = await post({ action: 'toggle_group', code: g.code || '',
              exit: g.exit || '', enabled: g.frozen });
            if (!r.ok) throw new Error(r.error || '写入失败');
            await load(true);
          } catch (e) { st.msg = '冻结失败：' + (e.message || e); st.msgErr = true; render(); }
        });
        item('恢复默认排序', async () => {
          try {
            const r = await post({ action: 'reorder_cards', order: [] });
            if (!r.ok) throw new Error(r.error || '写入失败');
            await load(true);
          } catch (e) { st.msg = '重置排序失败：' + (e.message || e); st.msgErr = true; render(); }
        });
        document.body.appendChild(m);
        menuEl = m;
        // 下一次点击（任意地方）先关菜单；capture 阶段，保证点菜单外能收
        menuOff = (ev) => { if (!m.contains(ev.target)) closeCardMenu(); };
        setTimeout(() => { if (menuEl === m) document.addEventListener('click', menuOff, true); }, 0);
      }

      // ---- 长按拖动（2026-11-07 主人：不要抓手，鼠标按住卡片 0.3 秒就能拖）
      // 为什么要自己写：原生 HTML5 拖拽是「按下即拖」，挡不住误触；长按才能区分「点一下」与「拖」。
      // 按下到 280ms 前移动超 8px 就当普通点击/选字处理，不劫持。
      const DRAG_HOLD_MS = 280;
      let press = null;

      function startPress(ev, key, el) {
        if (ev.button !== undefined && ev.button !== 0) return;      // 只认左键
        const t = ev.target;
        if (t && t.closest && t.closest('button, a, input, select, textarea, label')) return;
        if (press) return;
        const p = { key, el, x: ev.clientX, y: ev.clientY, active: false,
                    ghost: null, target: null, lastEl: null, timer: null };
        press = p;
        p.timer = setTimeout(() => beginDrag(p), DRAG_HOLD_MS);
        const onMove = (e) => {
          if (press !== p) return;
          if (!p.active && (Math.abs(e.clientX - p.x) > 8 || Math.abs(e.clientY - p.y) > 8)) {
            cancelPress(p);
            return;
          }
          if (p.active) moveGhost(p, e);
        };
        const onUp = () => endPress(p);
        p.off = () => {
          document.removeEventListener('pointermove', onMove, true);
          document.removeEventListener('pointerup', onUp, true);
          document.removeEventListener('pointercancel', onUp, true);
        };
        document.addEventListener('pointermove', onMove, true);
        document.addEventListener('pointerup', onUp, true);
        document.addEventListener('pointercancel', onUp, true);
      }

      function cancelPress(p) {
        clearTimeout(p.timer);
        if (p.off) p.off();
        p.el.className = String(p.el.className).replace(' pressing', '');
        if (press === p) press = null;
      }

      function beginDrag(p) {
        if (press !== p) return;
        p.active = true;
        p.el.className = String(p.el.className) + ' pressing';
        const r = p.el.getBoundingClientRect();
        const ghost = p.el.cloneNode(true);
        ghost.className = 'wc wc-ghost';
        ghost.style.width = r.width + 'px';
        ghost.style.left = r.left + 'px';
        ghost.style.top = r.top + 'px';
        document.body.appendChild(ghost);
        p.ghost = ghost;
        p.offx = p.x - r.left;
        p.offy = p.y - r.top;
        document.body.style.userSelect = 'none';
      }

      function moveGhost(p, e) {
        if (!p.ghost) return;
        p.ghost.style.left = (e.clientX - p.offx) + 'px';
        p.ghost.style.top = (e.clientY - p.offy) + 'px';
        const under = document.elementFromPoint ? document.elementFromPoint(e.clientX, e.clientY) : null;
        const cardEl = under && under.closest ? under.closest('.wc[data-key]') : null;
        const key = cardEl ? cardEl.getAttribute('data-key') : null;
        if (p.target !== key) {
          if (p.lastEl) p.lastEl.className = String(p.lastEl.className).replace(' drop', '');
          p.target = key;
          p.lastEl = cardEl;
          if (cardEl) cardEl.className = String(cardEl.className) + ' drop';
        }
      }

      function endPress(p) {
        if (press !== p) return;
        clearTimeout(p.timer);
        if (p.off) p.off();
        if (p.ghost) { try { p.ghost.remove(); } catch (e) { /* ignore */ } document.body.style.userSelect = ''; }
        if (p.lastEl) p.lastEl.className = String(p.lastEl.className).replace(' drop', '');
        p.el.className = String(p.el.className).replace(' pressing', '');
        const active = p.active;
        const target = p.target;
        const fromKey = p.key;
        press = null;
        if (!active) return;
        // 拖完这一次点击不要再生效（否则松手处若正好在股票名上会顺手弹 K 线）
        document.addEventListener('click', (ev) => {
          ev.stopPropagation();
          ev.preventDefault();
        }, { capture: true, once: true });
        if (target && target !== fromKey) reorderCards(fromKey, target);
      }

      function reorderCards(dragKey, targetKey) {
        const groups = (st.data.groups || []).slice();
        const from = groups.findIndex(x => x.key === dragKey);
        if (from < 0 || groups.findIndex(x => x.key === targetKey) < 0) return;
        const moved = groups.splice(from, 1)[0];
        const to = groups.findIndex(x => x.key === targetKey);
        groups.splice(Math.max(0, to), 0, moved);
        st.data.groups = groups;
        render();
        post({ action: 'reorder_cards', order: groups.map(x => x.key) })
          .then(r => { if (!r || !r.ok) throw new Error((r && r.error) || '保存排序失败'); })
          .catch((e) => { st.msg = '排序没存上：' + (e.message || e); st.msgErr = true; load(true); });
      }

      function card(g) {
        const frozen = !!g.frozen;
        const el = div('wc' + (frozen ? ' frozen' : '') + (g.exit === 'L1' ? ' sys' : (g.lit ? ' armed' : '')));

        el.dataset.key = g.key;
        const hd = div('wc-hd');
        const nameEl = div('wc-name', g.name || g.code || '—');
        if (g.code) {
          // 壳的 K 线桥（capture 阶段）认得这两个属性：点名称直接弹 K 线弹窗
          nameEl.dataset.openKline = g.code;
          nameEl.dataset.openName = g.name || '';
          nameEl.title = '点名称看 K 线（右键更多）';
        }
        hd.append(nameEl);
        if (g.code) hd.append(div('wc-code', g.code));
        if (frozen) hd.append(div('wc-tag', '已冻结'));
        hd.append(div('wc-count', g.total + ' 道 · ' + g.lit + ' 亮'));
        hd.onclick = () => { st.open[g.key] = !st.open[g.key]; render(); };
        hd.title = '左键展开明细，右键菜单';
        el.addEventListener('contextmenu', (ev) => {
          ev.preventDefault();
          openCardMenu(ev.clientX, ev.clientY, g);
        });
        // 拖动落点：拖到哪张卡上就插到它前面
        el.addEventListener('pointerdown', (ev) => startPress(ev, g.key, el));
        const fr = document.createElement('button');
        fr.type = 'button'; fr.className = 'wc-freeze';
        fr.textContent = frozen ? '恢复' : '冻结';
        fr.dataset.act = 'freeze';
        fr.dataset.key = g.key;
        fr.dataset.code = g.code || '';
        fr.dataset.exit = g.exit || '';
        fr.dataset.en = frozen ? '1' : '0';
        hd.append(fr);
        el.append(hd);

        const badges = div('wc-badges');
        (g.plans || []).forEach(p => {
          const b = document.createElement('button');
          b.type = 'button';
          const near = typeof p.prox === 'number';
          b.className = 'bdg ' + (p.lit ? 'lit' : 'dead') + (near ? ' near' : '') + (p.hot ? ' hot' : '') + (p.broken ? ' broken' : '') + (p.origin === 'auto' ? ' au' : '');
          if (near) {
            if (b.style && b.style.setProperty) b.style.setProperty('--prox', String(p.prox));
            else b.style['--prox'] = String(p.prox);
          }
          b.title = (p.theme || '') + '｜' + stateText(p) + '｜' +
            (typeof p.dist_pct === 'number' ? '离防线 ' + p.dist_pct.toFixed(1) + '%' : '') + '｜' +
            (p.window || '') + '｜' + (p.origin_cn || '') + (p.notify ? '（会推送）' : '（不推送）');
          b.dataset.act = 'badge';
          b.dataset.dp = p.dp_id;
          const dot = document.createElement('span'); dot.className = 'dot';
          b.append(dot, document.createTextNode(p.badge || p.kind_cn || ''));
          badges.append(b);
        });
        el.append(badges);

        const foot = div('wc-foot');
        foot.append(div('', '亮 ' + g.lit + ' / ' + g.total));
        if (typeof g.push === 'number' && g.push < g.total) {
          foot.append(div('', '会推送 ' + g.push));
        }
        const hitTs = (g.plans || []).filter(p => p.last_hit && p.last_hit.ts)
          .map(p => String(p.last_hit.ts)).sort();
        if (hitTs.length) foot.append(div('', '最近命中 ' + hitTs[hitTs.length - 1].slice(5, 16)));
        if (g.exit === 'L1') foot.append(div('', '系统级 · 唤醒夏天'));
        el.append(foot);

        if (st.open[g.key]) {
          const rows = div('wc-rows');
          (g.plans || []).forEach(p => rows.append(planRow(p)));
          el.append(rows);
        }
        return el;
      }

      // ---- 决策点分区（2026-11-16 主人 20:47：并进预警卡，不新开页；只展示不判定）
      const SEV_CN = { alert: '告警', action: '行动' };
      const t8 = (v) => {
        const s = String(v == null ? '' : v);
        const m = s.match(/(\d{2}:\d{2}:\d{2})/);
        return m ? m[1] : (s || '—');
      };
      function ageText(sec) {
        if (sec == null || !isFinite(Number(sec)) || Number(sec) < 0) return '—';
        const n = Number(sec);
        if (n < 60) return n + ' 秒前';
        if (n < 3600) return Math.floor(n / 60) + ' 分钟前';
        return (n / 3600).toFixed(1) + ' 小时前';
      }

      function dpEvalPill(ev) {
        let cls = 'unknown';
        let txt = '评估器 状态未知';
        if (ev && ev.state === 'RUNNING' && !ev.stale) { cls = 'run'; txt = '评估器 在跑'; }
        else if (ev && ev.state === 'RUNNING') { cls = 'stale'; txt = '评估器 在跑·心跳陈旧'; }
        else if (ev && ev.state === 'EXITED') { cls = 'exit'; txt = '评估器 已退出'; }
        const p = div('al-dp-pill ' + cls);
        const dot = document.createElement('span'); dot.className = 'dot';
        p.append(dot, document.createTextNode(txt));
        return p;
      }

      function renderDp() {
        const box = div('al-dp');
        const dp = st.dp || {};
        if (st.dpErr || dp.ok === false) {
          const bar = div('al-dp-bar');
          bar.append(div('al-dp-ttl', '决策点'));
          box.append(bar);
          box.append(div('al-msg err', '决策点读取失败：' +
            (st.dpErr || (dp && (dp.detail || dp.error)) || '接口异常')));
          return box;
        }
        const ev = dp.evaluator || {};
        const sw = dp.switch || {};
        const plan = dp.plan || {};
        const hits = dp.hits || {};

        const bar = div('al-dp-bar');
        bar.append(div('al-dp-ttl', '决策点'));
        bar.append(div('', '数据日 ' + (dp.date || '—') + (dp.is_today === false ? '（非今日）' : '')));
        bar.append(dpEvalPill(ev));
        bar.append(div('', ev.last_heartbeat
          ? '最后心跳 ' + t8(ev.last_heartbeat) + '（' + ageText(ev.heartbeat_age_s) + '）'
          : '无心跳记录'));
        box.append(bar);

        // 唤醒总开关：关 / 异常 → 通栏醒目横幅（主人：一眼能看出“现在不会叫醒夏天”）；开 → 一行小字
        if (!sw.enabled || sw.ok === false) {
          const sane = sw.ok !== false;
          const banner = div('al-dpw' + (sane ? '' : ' warn'));
          const strong = document.createElement('b');
          strong.textContent = sane
            ? '唤醒总开关已关闭 —— 现在不会叫醒夏天'
            : '唤醒总开关状态异常，按「开」处理';
          banner.append(strong);
          banner.append(div('', sane
            ? '评估照常跑、命中照常落 wake_events.jsonl、面板命中照常显示；只停了「新建会话叫醒」这一步，alert 的 QQ 推送不受影响。关闭于 '
              + (sw.updated_at || '—') + '，操作者 ' + (sw.by || '—') + '。'
            : '开关文件读不出或字段非法（' + (sw.reason || '—') +
              '），程序按「开」执行：误唤醒比漏唤醒安全，但你不能以为已经关了。'));
          banner.append(btn(sane ? '恢复叫醒' : '重置为开', '', 'wake-switch', { en: '1' }));
          box.append(banner);
        } else {
          const onRow = div('al-dp-on');
          onRow.append(div('', '唤醒总开关：开（触发时会新建会话叫醒夏天）'));
          onRow.append(btn('关闭唤醒', '', 'wake-switch', { en: '0' }));
          box.append(onRow);
        }

        // KPI：数字全取流水原值（计数用「数字在前」与预警卡既有 KPI 一致；时间用「标签在前」更好读）
        const tl = hits.timeline || [];
        const kp = div('al-dp-kpis');
        const addNum = (name, val, zero) => {
          const b = div('');
          b.innerHTML = '<b' + (zero ? ' class="zero"' : '') + '>' + esc(val) + '</b>' + esc(name);
          kp.append(b);
        };
        const addText = (name, val) => {
          const b = div('');
          b.append(document.createTextNode(name + ' ' + (val == null || val === '' ? '—' : String(val))));
          kp.append(b);
        };
        addNum('今日命中', hits.count || 0, !hits.count);
        addNum('计划条目', plan.present ? (plan.count || 0) : '—', !plan.present);
        addText('首触发', tl.length ? t8(tl[0].ts) : '—');
        addText('末触发', tl.length ? t8(tl[tl.length - 1].ts) : '—');
        box.append(kp);

        // 今日计划（原样展示；不打「已满足/未满足」）
        if (!plan.present) {
          const reason = {
            not_generated: '今日计划未生成',
            trade_date_mismatch: '计划文件日期不符',
            unreadable: '计划文件无法解析',
          }[plan.reason] || '今日计划不可用';
          let tail = '';
          if (plan.reason === 'not_generated') {
            tail = '：盘前 build_wake_plan.py 今天还没落盘（或今天不是交易日）。';
          } else if (plan.reason === 'trade_date_mismatch') {
            tail = '：文件里 meta.trade_date = ' + (plan.trade_date || '—') + '，与今天不符，按口径拒收。';
          } else {
            tail = '：JSON 解析失败（写到一半/被改坏），不拿它充数。';
          }
          tail += plan.latest_plan_date
            ? '最近一份可用计划：' + plan.latest_plan_date + '（仅提示，不是今天的数据）。'
            : '回溯 10 天也没有可用计划。';
          box.append(div('al-empty', reason + tail));
        } else {
          (plan.items || []).forEach(it => {
            const det = document.createElement('details');
            det.className = 'al-dpi';
            if (st.dpOpen[it.dp_id]) det.open = true;
            det.addEventListener('toggle', () => { st.dpOpen[it.dp_id] = det.open; });
            const sum = document.createElement('summary');
            sum.className = 'al-dpr';
            sum.append(div('i', it.dp_id || '—'));
            sum.append(div('t', it.theme || '—'));
            sum.append(div('sev ' + (SEV_CN[it.severity] ? it.severity : 'other'),
              SEV_CN[it.severity] || it.severity || '—'));
            sum.append(div('w', it.window || '—'));
            sum.append(div('c', '确认 ' + (it.confirm_points == null ? '—' : it.confirm_points) + ' 点'));
            sum.append(div('cd', it.condition_text || '（无条件摘要）'));
            sum.append(div('h' + (it.hits ? ' on' : ''), it.hits
              ? '命中 ' + it.hits + ' 次 · ' + t8(it.last_hit_ts)
              : '命中 0 次'));
            det.append(sum);

            const body = div('al-dpd');
            const rb = it.runbook || {};
            const line = (k, v) => { if (v) body.append(div('', k + '：' + v)); };
            line('为什么', rb.why);
            line('待判断', (rb.questions || []).join('；'));
            line('要查', (rb.data_to_check || []).join('、'));
            line('选项', (rb.options || []).join(' / '));
            line('滞回复位', it.hysteresis_text);
            if (it.require_cross_down) line('跨线', '首点须由上方下穿阈值才计数（防开盘已在下方时假触发）');
            if (it.last_trigger_text) line('最后触发值', it.last_trigger_text);
            body.append(div('raw', '条件原文（计划原样，未做任何判定）：' +
              JSON.stringify(it.condition || {}) +
              (it.hysteresis_exit ? ' · 复位：' + JSON.stringify(it.hysteresis_exit) : '')));
            det.append(body);
            box.append(det);
          });
        }

        // 今日命中时间线（原值直出）
        if (tl.length) {
          const fl = div('al-flow');
          tl.forEach(r => {
            const row = div('al-fr');
            row.append(div('t', t8(r.ts)));
            row.append(div('s', (r.dp_id || '') + '　' + (r.theme || '')));
            row.append(div('d', r.trigger_text || '（无 trigger_values）'));
            fl.append(row);
          });
          box.append(fl);
        } else if (plan.present) {
          box.append(div('al-empty',
            '今日无命中：评估器在窗口内跑满 60s 一轮也没触发条件，这是正常态（不是数据缺失）。'));
        }

        const un = hits.unplanned || [];
        if (un.length) {
          box.append(div('al-dp-note', '另有命中的 dp 不在今日计划里：' +
            un.map(u => u.dp_id + ' × ' + u.count + '（最后 ' + t8(u.last_ts) + '）').join('、') +
            '。计划白天重渲或长期预警额外跑过，原样列出不改口径。'));
        }
        const cal = dp.caliber || {};
        const src = cal.sources || {};
        box.append(div('al-dp-note',
          '只读：命中次数/触发值全部取 wake_events.jsonl 原值，面板不重算条件；计划条目原样展示，不标已满足/未满足。' +
          '数据源：' + [src.plan, src.events, src.status, src.switch].filter(Boolean).join(' / ')));
        return box;
      }

      function render() {
        closeCardMenu();
        const d = st.data || {};
        wrap.textContent = '';

        const top = div('al-top');
        const k = div('al-kpis');
        const stats = d.stats || {};
        [['监控中', stats.monitoring || 0], ['今日命中', stats.hit_today || 0],
         ['启用', stats.enabled || 0], ['手工关闭', stats.closed || 0]].forEach(([n, v]) => {
          const b = div('al-kpi'); b.innerHTML = '<b>' + esc(v) + '</b>' + esc(n); k.append(b);
        });
        top.append(k, div('al-spacer'));

        const chBox = div('al-ch');
        const chNames = { page: '页内', sound: '声音', qq: 'QQ' };
        const ch = d.channels || {};
        Object.keys(chNames).forEach(code => {
          const on = !!ch[code];
          if (code === 'sound') st.soundOn = on;
          chBox.append(btn(chNames[code] + (on ? ' ✓' : ''), 'al-chbtn' + (on ? ' on' : ''), 'ch', { ch: code }));
        });
        top.append(chBox, btn('收盘确认', '', 'review'), btn('＋ 新增预警', 'pri', 'form-open'));
        wrap.append(top);
        if (st.msg) wrap.append(div('al-msg' + (st.msgErr ? ' err' : ''), st.msg));
        wrap.append(renderForm(d));
        wrap.append(renderDp());

        // ---- 条件（按股票一张卡）
        const groups = d.groups || [];
        const bo = stats.by_origin || {};
        const pushN = (bo.manual || 0) + (bo.review || 0);
        wrap.append(div('al-sec', '条件（' + (d.plans || []).length + ' 条 / ' + groups.length + ' 只'
          + ' · 会推送 ' + pushN + (bo.auto ? ' · 自选池静默 ' + bo.auto : '') + '）'));
        const grid = div('al-grid');
        if (!groups.length) grid.append(div('al-empty', '还没有条件。用右上「新增预警」，或在 K 线图上右键「设置预警」。'));
        groups.forEach(g => grid.append(card(g)));
        wrap.append(grid);

        // ---- 今日流水
        const flow = d.flow || [];
        wrap.append(div('al-sec', '今日流水（' + flow.length + '）'));
        const fl = div('al-flow');
        if (!flow.length) fl.append(div('al-empty', '今日还没有状态跃迁。监控中的条件进入窗口后会在这里留痕。'));
        const cn = {};
        ((d.enums && d.enums.states) || []).forEach(x => { cn[x.code] = x.name; });
        flow.slice(0, 30).forEach(r => {
          const row = div('al-fr');
          row.append(div('t', String(r.ts || '').slice(11)));
          row.append(div('s', (r.theme || r.dp_id || '') + '　' +
            (cn[r.from] || r.from || '—') + ' → ' + (cn[r.to] || r.to || '—') +
            (r.reason ? '　' + r.reason : '')));
          const dsc = [];
          (r.trigger_values || []).forEach(t => dsc.push(t.metric + '=' + num(t.value, 3)));
          const dv = r.delivery || {};
          if (dv.qq) {
            const qs = { queued: '已入队', sent: '已发出', confirmed: '已确认', failed: '未送达' }[dv.qq.status]
              || (dv.qq.msg_id ? '已确认' : (dv.qq.ok ? '未确认' : '未送达'));
            dsc.push('QQ·' + qs + (dv.qq.msg_id ? ' ' + dv.qq.msg_id : ''));
          }
          if (dv.wake) dsc.push('唤醒' + (dv.wake.ok ? '✓' : '✗'));
          row.append(div('d', dsc.join(' · ')));
          fl.append(row);
        });
        wrap.append(fl);

        // ---- 今日异动（只读）
        const todo = d.todo || {};
        wrap.append(div('al-sec', '今日异动（只读，来自 alert_todo）'));
        const td = div('al-todo');
        (todo.alerts || []).slice(0, 8).forEach(a => {
          td.append(div('al-td ' + (a.level === 'danger' ? 'danger' : ''),
            '· ' + (a.message || '') + (a.ts ? '（' + String(a.ts).slice(11, 16) + '）' : '')));
        });
        (todo.todos || []).slice(0, 5).forEach(t => {
          td.append(div('al-td', '· ' + (t.text || '')));
        });
        if (!(todo.alerts || []).length && !(todo.todos || []).length) {
          td.append(div('al-empty', '今日无异动（非盘中快照时这里可能是上一交易日的）。'));
        }
        wrap.append(td);

        const nh = d.notify || {};
        wrap.append(div('al-note',
          '存储：' + ((d.paths && d.paths.store) || '—') + '\n' +
          'QQ 通道：' + (nh.configured ? '已配置' : '未配置') +
          (nh.last_ok_ts ? '，最近成功 ' + nh.last_ok_ts : '') +
          (nh.consecutive_fails ? '，连续失败 ' + nh.consecutive_fails : '') + '\n' +
          '徽标：亮 = 防线还在盯着；划掉 = 这道防线今天已经破了/已失效；带 ✓ = 收盘确认「回踩不破」（当天最低碰过、收盘没丢）。' +
          '点线边的徽标 = 自选池派生的（只记不推）。点股票名看 K 线，右键卡片有更多操作；按住卡片 0.3 秒可以拖动换位置（顺序会存下来，冻结也不动位置）；' +
          '默认排序 = 离防线最近的排前面，没行情的排最后。整卡冻结 = 这只票的预警全部暂停。' +
          '声音需先点一次「声音」开关解锁（浏览器限制）。'));

        bind(wrap);
      }

      async function load(force) {
        try {
          const [d, dp] = await Promise.all([
            ctx.api.get('/alert/list'),
            ctx.api.get('/alert/decision_points').catch(e => ({ ok: false, detail: (e && e.message) || String(e) })),
          ]);
          st.dp = dp || null;
          st.dpErr = (dp && dp.ok === false) ? (dp.detail || dp.error || '接口异常') : '';
          const prev = st.lastFlowTs;
          st.data = d;
          const flow = d.flow || [];
          const newest = flow.length ? flow[0] : null;
          if (st.booted && newest && newest.ts !== prev) {
            const hits = flow.filter(r => r.ts !== prev && ['PUSHED', 'WOKEN'].indexOf(r.to) >= 0
              && r.origin !== 'auto');
            if (hits.length && (st.data.channels || {}).sound) beep();
          }
          if (newest) st.lastFlowTs = newest.ts;
          st.booted = true;
          render();
        } catch (e) {
          wrap.textContent = '';
          wrap.append(div('al-msg err', '加载失败：' + (e && e.message ? e.message : e)));
        }
      }

      function bind(scope) {
        scope.querySelectorAll('[data-act]').forEach(el => {
          el.onclick = async (ev) => {
            if (ev && ev.stopPropagation) ev.stopPropagation();
            const act = el.dataset.act;
            if (act === 'form-open') { st.formOpen = true; st.msg = ''; render(); return; }
            if (act === 'form-close') { st.formOpen = false; st.msg = ''; render(); return; }
            if (act === 'badge') {
              const dp = el.dataset.dp;
              const g = (st.data.groups || []).find(x => (x.plans || []).some(p => p.dp_id === dp));
              if (g) { st.open[g.key] = true; st.msg = ''; render(); }
              return;
            }
            if (act === 'wake-switch') {
              // 唤醒总开关（2026-11-16）：关 = 不叫醒夏天，评估与事件落盘照常。
              // 关闭是「止血」方向，先弹一次确认防手滑；开启不拦。
              const on = el.dataset.en === '1';
              if (!on && typeof window !== 'undefined' && typeof window.confirm === 'function'
                  && !window.confirm('关闭后 wake_eval 不再新建会话叫醒夏天（评估照常跑、命中照常落盘、面板照常显示）。确定关闭？')) {
                return;
              }
              try {
                const r = await ctx.api.post('/alert/wake_switch', { enabled: on, by: 'panel' });
                if (!r || !r.ok) throw new Error((r && r.error) || '写入失败');
                st.msg = '唤醒总开关已' + (on ? '开启（会叫醒夏天）' : '关闭（不会叫醒夏天）') +
                  '（' + ((r.switch && r.switch.updated_at) || '') + '）';
                st.msgErr = false;
                await load(true);
              } catch (e) {
                st.msg = '切换唤醒开关失败：' + (e.message || e);
                st.msgErr = true;
                render();
              }
              return;
            }
            if (act === 'review') {              try {
                const r = await post({ action: 'review_close' });
                if (!r.ok) throw new Error(r.error || '确认失败');
                const s = r.stats || {};
                st.msg = '收盘确认（' + (r.trade_date || '') + '）：回踩不破 ' + (s['回踩不破'] || 0) +
                  ' 条，跌破 ' + (s['跌破'] || 0) + ' 条，未触及 ' + (s['未触及'] || 0) + ' 条';
                st.msgErr = false;
                await load(true);
              } catch (e) { st.msg = '确认失败：' + (e.message || e); st.msgErr = true; render(); }
              return;
            }
            if (act === 'freeze') {
              const on = el.dataset.en === '1';
              try {
                const r = await post({ action: 'toggle_group', code: el.dataset.code,
                  exit: el.dataset.exit, enabled: on });
                if (!r.ok) throw new Error(r.error || '写入失败');
                await load(true);
              } catch (e) { st.msg = '冻结失败：' + (e.message || e); st.msgErr = true; render(); }
              return;
            }
            if (act === 'ch') {
              const code = el.dataset.ch;
              const cur = Object.assign({}, (st.data && st.data.channels) || {});
              cur[code] = !cur[code];
              if (code === 'sound' && cur.sound) unlockAudio();
              try {
                const r = await post({ action: 'channels', channels: { [code]: cur[code] } });
                if (!r.ok) throw new Error(r.error || '写入失败');
                await load(true);
              } catch (e) {
                st.msg = '开关失败：' + (e.message || e); st.msgErr = true; render();
              }
              return;
            }
            if (act === 'toggle') {
              const en = el.dataset.en === '1';
              try {
                const r = await post({ action: 'toggle', dp_id: el.dataset.dp, enabled: !en });
                if (!r.ok) throw new Error(r.error || '写入失败');
                await load(true);
              } catch (e) { st.msg = '开关失败：' + (e.message || e); st.msgErr = true; render(); }
              return;
            }
            if (act === 'remove') {
              if (!window.confirm('删除这条预警？删除后不再提醒，流水保留。')) return;
              try {
                const r = await post({ action: 'remove', dp_id: el.dataset.dp });
                if (!r.ok) throw new Error(r.error || '删除失败');
                await load(true);
              } catch (e) { st.msg = '删除失败：' + (e.message || e); st.msgErr = true; render(); }
              return;
            }
            if (act === 'add') {
              const f = st.form;
              const body = {
                action: 'add', type: f.type, code: (f.code || '').trim(), name: (f.name || '').trim(),
                line: f.line, ma: f.ma, dir: f.dir, target: f.target,
                window: (f.window || '').trim() || '09:35-14:55', source: f.source,
                value: f.value === '' ? null : Number(f.value),
                dynamic: ['LINE_BELOW', 'MA_BELOW', 'MA_ABOVE'].indexOf(f.type) >= 0,
              };
              try {
                const r = await post(body);
                if (!r.ok) throw new Error(r.error || '写入失败');
                st.msg = r.duplicate ? '这条已经存在，没有重复加' :
                  ('已加入：' + (r.dp_id || '') + ((r.warnings || []).length ? '（' + r.warnings.join('；') + '）' : ''));
                st.msgErr = false;
                st.formOpen = false;
                await load(true);
              } catch (e) { st.msg = '加入失败：' + (e.message || e); st.msgErr = true; render(); }
              return;
            }
          };
        });
      }

      await load(false);
      return {
        refresh: () => load(true),
        unmount: () => { wrap.textContent = ''; },
      };
    },
  }],
};
