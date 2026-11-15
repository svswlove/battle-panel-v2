// K 线右键「设置预警」小面板（2026-11-07 预警页重建任务书 §2.1 来源②）
//
// 为什么单独一个文件、而不是复用 order_panel：
//   任务书要求 K 线右键「改完不能再下单」。现成的 order_panel 是下单面板（买卖/委托/条件单
//   全在里面），把预警塞进去等于把入口又接回了交易链。这里只做**设预警**：没有数量、
//   没有方向、没有账户，写的是 `/api/p/alert/alert/write`。
//
// 口径：
//   · 右键点中的 Y 轴价 = 价格类预警的默认阈值（可改）；
//   · 跌幅% 默认按「跌」方向（主人右键场景绝大多数是防跌）；
//   · 回踩类不给阈值——档位由后端 select_levels 每日选，页面只选「自动/白线/黄线/上沿/下沿」；
//   · 来源固定 kline（四类来源之一，落同一份存储）。
const CSS = `
.kap-mask{position:fixed;inset:0;background:color-mix(in srgb, var(--fg) 42%, transparent);z-index:9998;display:flex;
  align-items:center;justify-content:center}
.kap-box{width:380px;max-width:calc(100vw - 32px);background:var(--card,var(--bg));
  color:var(--fg);border-radius:10px;box-shadow:0 18px 60px color-mix(in srgb, var(--fg) 32%, transparent);
  border:1px solid var(--line);font:inherit;overflow:hidden}
.kap-hd{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--line-soft)}
.kap-hd b{font-size:13px}
.kap-hd .cap{font-size:11px;color:var(--dim);margin-left:auto}
.kap-x{border:none;background:transparent;color:var(--dim);font-size:15px;cursor:pointer;padding:0 2px}
.kap-bd{padding:10px 12px;display:grid;gap:8px}
.kap-f{display:grid;grid-template-columns:76px 1fr;gap:8px;align-items:center;font-size:12px;color:var(--dim)}
.kap-f select,.kap-f input{font:inherit;font-size:12px;padding:4px 6px;border-radius:6px;
  border:1px solid var(--line);background:var(--card2,var(--bg));color:var(--fg)}
.kap-note{font-size:11px;color:var(--dim);line-height:1.6}
.kap-ft{display:flex;gap:8px;align-items:center;padding:10px 12px;border-top:1px solid var(--line-soft)}
.kap-msg{font-size:11px;color:var(--dim);flex:1;min-height:14px}
.kap-msg.err{color:var(--down-ink)}
.kap-btn{font:inherit;font-size:12px;padding:5px 12px;border-radius:6px;cursor:pointer;
  border:1px solid var(--line);background:transparent;color:var(--fg)}
.kap-btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-on)}
.kap-btn[disabled]{opacity:.55;cursor:default}
`;

const STYLE_ID = 'kline-alert-panel-css';

const TYPES = [
  ['PRICE_BELOW', '跌破价', 'price'],
  ['PRICE_ABOVE', '突破价', 'price'],
  ['CHANGE_PCT', '跌幅%', 'pct'],
  ['PULLBACK_MA', '回踩均线不破', 'pull-ma'],
  ['PULLBACK_KEYK', '回踩关键K不破', 'pull-kk'],
];

function ensureCss() {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID; s.textContent = CSS;
  document.head.appendChild(s);
}

export function openAlertPanel(opts) {
  const o = opts || {};
  ensureCss();
  const code = String(o.code || '');
  const name = String(o.name || code);
  const price = Number(o.price) > 0 ? Number(o.price) : null;

  const mask = document.createElement('div');
  mask.className = 'kap-mask';
  const box = document.createElement('div');
  box.className = 'kap-box';
  mask.appendChild(box);

  const hd = document.createElement('div');
  hd.className = 'kap-hd';
  const hb = document.createElement('b');
  hb.textContent = '设置预警 · ' + name;
  const cap = document.createElement('span');
  cap.className = 'cap';
  cap.textContent = price ? '右键价 ' + price.toFixed(2) : '无右键价';
  const x = document.createElement('button');
  x.type = 'button'; x.className = 'kap-x'; x.textContent = '✕';
  hd.append(hb, cap, x);

  const bd = document.createElement('div');
  bd.className = 'kap-bd';

  const rowType = document.createElement('label');
  rowType.className = 'kap-f';
  rowType.append(document.createTextNode('类型'));
  const selType = document.createElement('select');
  TYPES.forEach(([v, label]) => {
    const op = document.createElement('option');
    op.value = v; op.textContent = label;
    selType.append(op);
  });
  rowType.append(selType);

  const rowVal = document.createElement('label');
  rowVal.className = 'kap-f';
  const lblVal = document.createTextNode('阈值');
  const inpVal = document.createElement('input');
  inpVal.type = 'number'; inpVal.step = '0.001';
  inpVal.value = price ? price.toFixed(2) : '';
  rowVal.append(lblVal, inpVal);

  const rowTgt = document.createElement('label');
  rowTgt.className = 'kap-f';
  rowTgt.append(document.createTextNode('档位'));
  const selTgt = document.createElement('select');
  rowTgt.append(selTgt);
  rowTgt.hidden = true;

  const rowWin = document.createElement('label');
  rowWin.className = 'kap-f';
  rowWin.append(document.createTextNode('窗口'));
  const inpWin = document.createElement('input');
  inpWin.type = 'text'; inpWin.value = '09:35-14:55';
  rowWin.append(inpWin);

  const note = document.createElement('div');
  note.className = 'kap-note';
  note.textContent = '预警只提醒、不下单。命中后推送到面板预警页 + QQ。';

  bd.append(rowType, rowVal, rowTgt, rowWin, note);

  const ft = document.createElement('div');
  ft.className = 'kap-ft';
  const msg = document.createElement('span');
  msg.className = 'kap-msg';
  const ok = document.createElement('button');
  ok.type = 'button'; ok.className = 'kap-btn pri'; ok.textContent = '加入预警';
  const cancel = document.createElement('button');
  cancel.type = 'button'; cancel.className = 'kap-btn'; cancel.textContent = '取消';
  ft.append(msg, cancel, ok);

  box.append(hd, bd, ft);
  document.body.appendChild(mask);

  function close() {
    try { mask.remove(); } catch (e) { /* ignore */ }
    if (typeof o.onClose === 'function') { try { o.onClose(); } catch (e) { /* ignore */ } }
  }

  function syncMode() {
    const t = selType.value;
    const mode = (TYPES.find(x => x[0] === t) || [])[2];
    const isPrice = mode === 'price';
    const isPct = mode === 'pct';
    const isPullMa = mode === 'pull-ma';
    const isPullKk = mode === 'pull-kk';
    rowVal.hidden = isPullMa || isPullKk;
    lblVal.textContent = isPct ? '跌幅%' : '阈值';
    if (isPrice && price) inpVal.value = price.toFixed(2);
    if (isPct && (!inpVal.value || inpVal.value === (price ? price.toFixed(2) : ''))) inpVal.value = '3';
    rowTgt.hidden = !(isPullMa || isPullKk);
    selTgt.innerHTML = '';
    (isPullKk
      ? [['auto', '自动（上沿/下沿）'], ['upper', '动能上'], ['lower', '动能下']]
      : [['auto', '自动（白线/黄线）'], ['white', '白线'], ['yellow', '黄线']]
    ).forEach(([v, label]) => {
      const op = document.createElement('option');
      op.value = v; op.textContent = label;
      selTgt.append(op);
    });
    inpWin.value = (isPullMa || isPullKk) ? '09:35-14:55' : '09:35-14:55';
  }
  selType.onchange = syncMode;
  syncMode();

  x.onclick = close;
  cancel.onclick = close;
  mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
  document.addEventListener('keydown', function onKey(e) {
    if (e.key === 'Escape') { document.removeEventListener('keydown', onKey); close(); }
  });

  ok.onclick = async function () {
    const t = selType.value;
    const mode = (TYPES.find(x => x[0] === t) || [])[2];
    const body = {
      action: 'add', type: t, code, name, source: 'kline',
      window: (inpWin.value || '').trim() || '09:35-14:55',
      value: (mode === 'pull-ma' || mode === 'pull-kk') ? null : Number(inpVal.value),
      dir: 'down',
      dynamic: false,
      target: selTgt.value || 'auto',
    };
    if (mode !== 'pull-ma' && mode !== 'pull-kk' && !(body.value > 0)) {
      msg.className = 'kap-msg err'; msg.textContent = '阈值需大于 0';
      return;
    }
    ok.disabled = true;
    msg.className = 'kap-msg'; msg.textContent = '写入中…';
    try {
      const res = await fetch('/api/p/alert/alert/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || data.detail || ('HTTP ' + res.status));
      msg.textContent = data.duplicate ? '这条已经存在，没有重复加' :
        ('已加入：' + (data.dp_id || '') + ((data.warnings || []).length ? '（' + data.warnings.join('；') + '）' : ''));
      setTimeout(close, 900);
    } catch (e) {
      msg.className = 'kap-msg err';
      msg.textContent = '失败：' + ((e && e.message) || e);
      ok.disabled = false;
    }
  };

  return { close };
}
