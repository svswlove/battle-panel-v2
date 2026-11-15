// 后台卡片（原生 ES module，无构建）：计划任务 / 常驻进程 / 数据新鲜度。
//
// 行为基准 = 旧页 `renderOps`（engine/web/index.html:8399，只读参考，不复制业务判断）：
//   · 头信息：更新时间 ｜ 最近交易日 ｜ 今日（交易时段 / 交易日·非时段 / 非交易日）｜ 清单源
//   · 汇总三项：计划任务（失败/未跑/停用）、常驻进程（停）、数据源（陈旧）
//   · 三张表：计划任务 22 行、常驻进程 16 行、数据新鲜度 8 行
//   · 红卡两种：登记册解析失败（清单唯一源不可用）、覆盖度告警（判不了状态 / 漏登）
//
// 端点（ops 插件 mount=root，路径与旧端同名，故一律用绝对 /api/ 前缀）：
//   GET  /api/ops.json[?refresh=1]   聚合状态
//   POST /api/ops/run {key}          手动触发数据类任务（白名单/冷却/锁全在后端与旧仓）
//
// 纪律（docs/CONTRACT.md §5 / §8）：
//   1. 零 import、零构建；只操作自己的 root；不写全局变量。
//   2. 只走 ctx.api.get / ctx.api.post，不裸 fetch。
//   3. 颜色一律 themes.css 语义 token；涨跌/成败语义走 --up/--down/--warn/--danger 家族。
//   4. 红线任务（交易链）**只显示状态、不出按钮**；「手动更新」只对后端 manual 白名单出。
//   5. 不声明 refreshEvery：后台状态是低频数据，用卡头「刷新」按需拉（旧页也是切 Tab 才拉）。

const STYLE_ID = 'ops-card-css';

const CSS = `
.ops-card{display:flex;flex-direction:column;gap:10px}
.ops-hd{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.ops-hd .grow{flex:1 1 auto}
.ops-src{color:var(--dim);font-size:0.7rem;line-height:1.6}
/* 健康摘要条（2026-11-09 重做）：第一眼只看这一行——几项要处理，其余是正常 */
.ops-health{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;padding:6px 10px;border-radius:var(--radius-sm);
  background:var(--card2);border:1px solid var(--line);font-size:0.82rem}
.ops-health .big{font-weight:600;font-size:0.95rem}
.ops-health.ok .big{color:var(--down-ink)}
.ops-health.bad .big{color:var(--up-ink)}
.ops-health.warn .big{color:var(--warn-ink)}
.ops-health .sub{color:var(--dim);font-size:0.72rem}
.ops-sum{display:flex;flex-wrap:wrap;gap:12px;margin:2px 0 4px}
.ops-sum > div{font-size:0.8rem;color:var(--fg)}
.ops-sum b{color:var(--dim);font-weight:400;margin-right:4px}
.ops-pill{display:inline-block;padding:1px 8px;border-radius:var(--radius-pill);font-size:0.75rem;white-space:nowrap;background:var(--card2);color:var(--dim)}
.ops-pill.ops-ok{background:var(--down-bg);color:var(--down-ink)}
.ops-pill.ops-bad{background:var(--up-bg);color:var(--up-ink)}
.ops-pill.ops-warn{background:var(--warn-bg);color:var(--warn-ink)}
.ops-pill.ops-idle{background:var(--line-soft);color:var(--dim)}
.ops-pill.ops-red{background:var(--danger-bg);color:var(--danger-ink)}
.ops-pill.ops-run{background:var(--accent-bg);color:var(--accent)}
.ops-sec{display:flex;flex-direction:column;gap:6px;margin-top:4px}
.ops-sec-t{font-size:0.8rem;font-weight:600;color:var(--fg-strong)}
/* 滚动归属（标注 N20261027-012343-28）：卡片内不留滚动容器——唯一滚动方是外壳 .stage。 */
.ops-wrap{border:1px solid var(--line);border-radius:var(--radius-sm)}
.ops-table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:0.75rem}
.ops-table th,.ops-table td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;
  overflow-wrap:anywhere;word-break:break-word}
.ops-table th{background:var(--card);color:var(--dim);font-weight:500}
.ops-table th:first-child{width:74px}
.ops-table th:nth-child(2){width:30%}
.ops-table tr:hover td{background:var(--card2)}
.ops-name{color:var(--fg);font-weight:500}
.ops-desc{color:var(--dim);font-size:0.6875rem;line-height:1.45;margin-top:2px;max-width:380px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ops-mono{font-family:var(--font-mono);font-variant-numeric:tabular-nums;white-space:normal}
.ops-btn{border:1px solid var(--accent);background:transparent;color:var(--accent);border-radius:var(--radius-sm);padding:2px 8px;font-size:0.7rem;font-family:inherit;cursor:pointer;white-space:nowrap}
.ops-btn:hover{background:var(--accent-bg)}
.ops-btn:disabled{opacity:.45;cursor:not-allowed}
.ops-red-card{border:1px solid var(--danger);background:var(--danger-bg);border-radius:var(--radius-md);padding:8px 10px}
.ops-red-card h3{margin:0 0 4px;font-size:0.82rem;color:var(--danger-ink)}
.ops-red-card .kv{font-size:0.75rem;color:var(--fg);line-height:1.6;word-break:break-word}
.ops-red-card .kv b{color:var(--dim);font-weight:400;margin-right:4px}
.ops-attn{border-color:var(--warn);background:var(--warn-bg)}
.ops-attn h3{color:var(--warn-ink)}
.ops-attn ul{margin:2px 0 0;padding-left:18px}
.ops-attn li{font-size:0.75rem;line-height:1.7;color:var(--fg)}
.ops-foot{color:var(--dim);font-size:0.6875rem;line-height:1.7;margin-top:2px}
.ops-msg{font-size:0.75rem;color:var(--dim);min-height:14px}
.ops-msg.err{color:var(--up-ink)}
.ops-msg.ok{color:var(--down-ink)}
/* 已停用/已退役折叠区（默认收起）：不污染现役判断，但留痕可查 */
.ops-retired{margin-top:6px;border-top:1px dashed var(--line);padding-top:8px}
.ops-retired summary{cursor:pointer;font-size:0.78rem;color:var(--dim);outline:none;padding:2px 0}
.ops-retired summary:hover{color:var(--fg)}
.ops-retired[open] summary{color:var(--fg-strong)}
.ops-retired .ops-wrap{margin-top:6px}
@media(max-width:760px){.ops-table th:nth-child(4),.ops-table td:nth-child(4){display:none}}
`;

if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = CSS;
  document.head.appendChild(s);
}

// 状态 → 胶囊配色。键与后端返回值逐字对应。
// 成败语义：成功/活/新鲜 = 绿系（--down），失败/停/陈旧 = 红系（--up），未跑/待更新 = 黄系，
// 运行中 = 强调色（2026-11-09 新增：正在跑的链不是失败），停用/已退役/窗口外 = 灰（正常态，不是故障）。
const TASK_CLS = { '成功': 'ops-ok', '失败': 'ops-bad', '未跑': 'ops-warn', '运行中': 'ops-run', '停用': 'ops-idle', '已退役': 'ops-idle', '未知': 'ops-idle' };
const DAEMON_CLS = { '活': 'ops-ok', '停': 'ops-bad', '窗口外': 'ops-idle', '已退役': 'ops-idle', '未知': 'ops-idle' };
const FRESH_CLS = { '新鲜': 'ops-ok', '陈旧': 'ops-bad', '待更新': 'ops-warn', '未知': 'ops-idle' };

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function pill(text, cls) {
  return '<span class="ops-pill ' + esc(cls || 'ops-idle') + '">' + esc(text == null || text === '' ? '—' : text) + '</span>';
}

// 红线标记：交易链任务/常驻只显示状态，禁止手动触发（旧页 opsRed 同文案）
function redPill(row) {
  return row && row.redline
    ? ' <span class="ops-pill ops-red" title="交易链红线：只显示状态，严禁手动触发">红线</span>'
    : '';
}

function desc(text) {
  return text ? '<div class="ops-desc" title="' + esc(text) + '">' + esc(text) + '</div>' : '';
}

function dash(v) {
  return v === null || v === undefined || v === '' ? '—' : String(v);
}

export default {
  cards: [{
    id: 'ops.main',
    title: '后台',
    slot: 'main',
    order: 70,

    async mount(root, ctx) {
      root.textContent = '';
      const card = document.createElement('div');
      card.className = 'ops-card';
      root.append(card);

      const hd = document.createElement('div');
      hd.className = 'ops-hd';
      const hdSrc = document.createElement('div');
      hdSrc.className = 'ops-src grow';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ops-btn';
      btn.textContent = '刷新';
      const msg = document.createElement('div');
      msg.className = 'ops-msg';
      hd.append(hdSrc, btn, msg);
      card.append(hd);

      const body = document.createElement('div');
      card.append(body);

      this._root = root;
      this._card = card;
      this._hdSrc = hdSrc;
      this._body = body;
      this._msg = msg;
      this._ctx = ctx;
      this._dead = false;

      const run = (force) => this._load(force);
      btn.addEventListener('click', () => { run(true).catch(() => {}); });
      this._btn = btn;

      // 手动更新按钮：事件委派在 body 上**只绑一次**（渲染不重建监听，防重复触发）
      body.addEventListener('click', (ev) => {
        const t = ev.target;
        if (!t || !t.closest) return;
        const b = t.closest('[data-run]');
        if (!b) return;
        this._runTask(b.getAttribute('data-run'), b).catch(() => {});
      });

      await run(false);
    },

    async unmount() {
      this._dead = true;
      this._ctx = null;
      this._root = null;
      this._card = null;
      this._hdSrc = null;
      this._body = null;
      this._msg = null;
      this._btn = null;
    },

    // 不声明 refreshEvery（后台状态低频），此方法只作契约占位：外部/手动可调。
    async refresh() {
      if (this._dead || !this._ctx) return;
      await this._load(true);
    },

    // ------------------------------------------------------------ 数据

    async _load(force) {
      if (this._dead || !this._ctx) return;
      const ctx = this._ctx;
      const btn = this._btn;
      if (btn) btn.disabled = true;
      this._setMsg('加载中…', '');
      let d = null;
      try {
        // ops 插件 mount=root：端点与旧端同名，走绝对路径（不是 /api/p/ops/...）
        d = await ctx.api.get('/api/ops.json' + (force ? '?refresh=1' : ''));
      } catch (e) {
        if (this._dead) return;
        this._setMsg('读取失败：' + (e && e.message ? e.message : e), 'err');
        if (this._body) this._body.textContent = '';
        if (btn) btn.disabled = false;
        return;
      }
      if (this._dead) return;
      if (!d || d.error) {
        this._setMsg('', '');
        this._renderUnavailable(d);
        if (btn) btn.disabled = false;
        return;
      }
      this._render(d);
      // 2026-10-26：refresh=1 现在是真重算（~5s）。重算失败但旧快照可读时后端回 `_refresh`，
      // 必须如实说「显示的是旧快照」，否则按钮看着生效、数据其实是上一夜的。
      const rf = d._refresh;
      if (rf && rf.ok === false) {
        this._setMsg('重算失败，显示旧快照（' + dash((d.meta || {}).updated_at) + '）：'
          + (rf.detail || '未知原因'), 'err');
      } else {
        this._setMsg('更新 ' + dash((d.meta || {}).updated_at), 'ok');
      }
      if (btn) btn.disabled = false;
    },

    _setMsg(text, cls) {
      if (!this._msg) return;
      this._msg.textContent = text || '';
      this._msg.className = 'ops-msg' + (cls ? ' ' + cls : '');
    },

    _renderUnavailable(d) {
      if (!this._body) return;
      this._body.textContent = '';
      const box = document.createElement('div');
      box.className = 'ops-red-card';
      const h = document.createElement('h3');
      h.textContent = '后台状态不可用';
      const kv = document.createElement('div');
      kv.className = 'kv';
      kv.textContent = (d && (d.error || d.detail)) || '未知';
      box.append(h, kv);
      this._body.append(box);
      if (this._hdSrc) this._hdSrc.textContent = '';
    },

    // ------------------------------------------------------------ 渲染

    _render(d) {
      const body = this._body;
      if (!body) return;
      const m = d.meta || {};
      const S = d.summary || {};
      const cov = m.coverage || {};

      // 头信息
      if (this._hdSrc) {
        const today = dash(m.today);
        const phase = m.is_trading_day
          ? (m.in_session ? '（交易时段）' : '（交易日·非时段）')
          : '（非交易日）';
        this._hdSrc.textContent = '后台监控（只读）· 更新 ' + dash(m.updated_at)
          + ' ｜ 最近交易日 ' + dash(m.trade_date)
          + ' ｜ 今日 ' + today + phase
          + ' ｜ 清单源：A1/A2 登记册（按状态列分现役/退役）';
      }

      const tasks = d.tasks || [];
      const daemons = d.daemons || [];
      const fresh = d.freshness || [];
      const tActive = tasks.filter((x) => x.active !== false);
      const tRetired = tasks.filter((x) => x.active === false);
      const dActive = daemons.filter((x) => x.active !== false);
      const dRetired = daemons.filter((x) => x.active === false);

      // 需要处理：失败任务 / 已停常驻 / 陈旧数据 / 判不了状态
      const attn = [];
      tActive.forEach((x) => {
        if (x.status === '失败') attn.push({ k: '任务失败', t: x.name, v: '结果码 ' + dash(x.last_result) + '，最后运行 ' + dash(x.last_run) });
        else if (x.status === '未知') attn.push({ k: '任务判不了状态', t: x.name, v: x.reg_status || '' });
      });
      dActive.forEach((x) => {
        if (x.status === '停') attn.push({ k: '常驻已停', t: x.name, v: x.reg_status || '' });
        else if (x.status === '未知') attn.push({ k: '常驻判不了状态', t: x.name, v: '' });
      });
      fresh.forEach((x) => {
        if (x.state === '陈旧') {
          attn.push({
            k: '数据陈旧', t: x.label || x.key,
            v: '最新 ' + dash(x.latest) + '（' + (x.cadence === 'weekly' ? '周更' : '日更') + '）',
          });
        }
      });

      const frag = document.createDocumentFragment();

      // 健康摘要（第一眼只看这一行）
      const health = document.createElement('div');
      health.className = 'ops-health ' + (attn.length ? 'bad' : 'ok');
      health.innerHTML =
        '<span class="big">' + (attn.length ? esc(attn.length) + ' 项要处理' : '全部正常') + '</span>'
        + '<span class="sub">计划任务 ' + esc(dash(S.tasks_total != null ? S.tasks_total : tActive.length))
          + '（失败 ' + esc(dash(S.tasks_fail)) + ' · 运行中 ' + esc(dash(S.tasks_running)) + '）</span>'
        + '<span class="sub">常驻 ' + esc(dash(S.daemons_total != null ? S.daemons_total : dActive.length))
          + '（停 ' + esc(dash(S.daemons_down)) + '）</span>'
        + '<span class="sub">数据源 ' + esc(dash(S.fresh_total != null ? S.fresh_total : fresh.length))
          + '（陈旧 ' + esc(dash(S.fresh_stale)) + '）</span>'
        + '<span class="sub">已退役 ' + esc(dash((S.tasks_retired || 0) + (S.daemons_retired || 0))) + ' 项（折叠在底部）</span>';
      frag.append(health);

      // 要处理清单（置顶，逐项可读，不只甩数字）
      if (attn.length) {
        const box = document.createElement('div');
        box.className = 'ops-red-card ops-attn';
        const li = attn.slice(0, 12).map((a) => '<li><b>' + esc(a.k) + '</b> ' + esc(a.t)
          + (a.v ? '<span class="ops-src"> · ' + esc(a.v) + '</span>' : '') + '</li>').join('');
        box.innerHTML = '<h3>要处理（' + attn.length + '）</h3><ul>' + li
          + (attn.length > 12 ? '<li>…共 ' + attn.length + ' 项，余下见下表</li>' : '') + '</ul>';
        frag.append(box);
      }

      // 红卡 1：登记册解析失败（清单唯一源不可用，不许静默返回空清单）
      if (m.registry_ok === false) {
        const box = document.createElement('div');
        box.className = 'ops-red-card';
        box.innerHTML = '<h3>⚠ 登记册解析失败（监控对象清单唯一源不可用）</h3>'
          + '<div class="kv"><b>错误</b>' + esc(dash(m.registry_error)) + '</div>'
          + '<div class="kv">清单唯一源 = OH-Works/自动化治理/登记册 A0/A1/A2；'
          + '解析失败时不返回清单，请先修复登记册表格格式。</div>';
        frag.append(box);
      }

      // 覆盖度告警（登记册列的对象凡判不了状态必须显式标出）
      const covMsgs = [];
      if ((cov.tasks_unknown || []).length) covMsgs.push('计划任务判不了状态：' + cov.tasks_unknown.join('、'));
      if ((cov.daemons_unknown || []).length) covMsgs.push('常驻判不了状态：' + cov.daemons_unknown.join('、'));
      if ((cov.daemons_unregistered || []).length) {
        covMsgs.push('run_daemons.DAEMONS 有而登记册未登（漏登）：' + cov.daemons_unregistered.join('、'));
      }
      if (covMsgs.length) {
        const box = document.createElement('div');
        box.className = 'ops-red-card';
        box.innerHTML = '<h3>覆盖度告警</h3><div class="kv">' + esc(covMsgs.join('；')) + '</div>';
        frag.append(box);
      }

      // 表 1：计划任务·现役（含手动更新按钮；红线任务不出按钮）
      const manual = (m.manual && m.manual.enabled) || {};
      const taskRows = tActive.map((x) => {
        const canRun = Object.prototype.hasOwnProperty.call(manual, x.name);
        let act = '';
        if (canRun) {
          const running = x.status === '运行中' || String(x.state || '').toLowerCase() === 'running';
          act = running
            ? '<button class="ops-btn" type="button" disabled title="任务运行中，按钮不可用">运行中…</button>'
            : '<button class="ops-btn" type="button" data-run="' + esc(x.name) + '" title="'
              + esc(manual[x.name]) + '（幂等）">手动更新</button>';
        }
        return [
          pill(x.status, TASK_CLS[x.status]),
          '<div class="ops-name">' + esc(x.name) + redPill(x) + '</div>'
            + '<div class="ops-src">' + esc(dash(x.id)) + ' · ' + esc(dash(x.group)) + '</div>' + desc(x.desc),
          '<span class="ops-mono">' + esc(dash(x.last_run)) + '</span>',
          '<span class="ops-mono">' + esc(x.last_result === null || x.last_result === undefined ? '—' : String(x.last_result)) + '</span>',
          '<span class="ops-mono">' + esc(dash(x.next_run)) + '</span>',
          act,
        ];
      });
      frag.append(this._table('计划任务·现役（跑没跑 / 成功没 / 最后运行）',
        ['状态', '任务', '最后运行', '结果码', '下次运行', '操作'], taskRows));

      // 表 2：常驻进程·现役（红线常驻同样只显示）
      const daemonRows = dActive.map((x) => [
        pill(x.status, DAEMON_CLS[x.status]),
        '<div class="ops-name">' + esc(x.name) + redPill(x) + '</div>'
          + '<div class="ops-src">' + esc(dash(x.id)) + ' · ' + esc(dash(x.group)) + '</div>' + desc(x.desc),
        '<span class="ops-mono">' + esc(x.pid == null ? '—' : String(x.pid)) + '</span>',
        '<span class="ops-mono">' + esc(dash(x.signal)) + '</span>',
        '<span class="ops-src">' + esc(x.watchdog || (x.probe === true ? '端口通' : '—')) + '</span>',
      ]);
      frag.append(this._table('常驻进程·现役（活 / 停 / 窗口外）',
        ['状态', '进程', 'PID', '最后活跃', 'watchdog'], daemonRows));

      // 表 3：数据新鲜度
      const freshRows = fresh.map((x) => [
        pill(x.state, FRESH_CLS[x.state]),
        esc(x.label || x.key || ''),
        '<span class="ops-mono">' + esc(dash(x.latest)) + '</span>',
        '<span class="ops-src">' + esc(x.source || '') + (x.cadence === 'weekly' ? ' · 周更' : '') + '</span>',
      ]);
      frag.append(this._table('数据新鲜度（最新数据日 vs 最近交易日）',
        ['状态', '数据源', '最新数据日', '来源'], freshRows));

      // 已停用 / 已退役（折叠，默认收起；不探活、不计入上方任何告警）
      if (tRetired.length || dRetired.length) {
        const det = document.createElement('details');
        det.className = 'ops-retired';
        const sm = document.createElement('summary');
        sm.textContent = '已停用 / 已退役（' + (tRetired.length + dRetired.length)
          + '）· 只留痕，不探活、不计入告警';
        det.append(sm);
        const rows = [];
        tRetired.forEach((x) => rows.push([
          pill('已退役', 'ops-idle'),
          '<div class="ops-name">' + esc(x.name) + redPill(x) + '</div>'
            + '<div class="ops-src">' + esc(dash(x.id)) + ' · 计划任务 · 登记：' + esc(dash(x.reg_status)) + '</div>',
        ]));
        dRetired.forEach((x) => rows.push([
          pill('已退役', 'ops-idle'),
          '<div class="ops-name">' + esc(x.name) + redPill(x) + '</div>'
            + '<div class="ops-src">' + esc(dash(x.id)) + ' · 常驻进程 · 登记：' + esc(dash(x.reg_status)) + '</div>',
        ]));
        det.append(this._table('退役对象（登记册状态列非现役）', ['状态', '对象'], rows));
        frag.append(det);
      }

      // 口径脚注
      const foot = document.createElement('div');
      foot.className = 'ops-foot';
      foot.textContent = '口径：现役/退役以 A1/A2 登记册「状态」列为唯一源（停用/退役/已删/历史 → 折叠区，不探活）；'
        + '任务状态取 Windows 计划任务（运行中 / 成功 / 失败 / 未跑 / 未知），结果码 267009=正在跑、不计失败；'
        + '常驻按 WMI 命令行匹配登记册路径片段 + watchdog 快照交叉印证 + 端口兜底；'
        + '交易窗口外标「窗口外」属正常态；数据新鲜度＝数据日 vs 最近交易日（周更源允许 7 天滞后），'
        + '交易日 17:00 前滞后一天标「待更新」。'
        + '红线任务只显示、不可手动触发；「手动更新」仅对数据类白名单（schtasks /run，幂等 + 运行中禁用）。';
      frag.append(foot);

      body.textContent = '';
      body.append(frag);
    },

    _table(title, headers, rows) {
      const sec = document.createElement('div');
      sec.className = 'ops-sec';
      const t = document.createElement('div');
      t.className = 'ops-sec-t';
      t.textContent = title + '（' + rows.length + '）';
      const wrap = document.createElement('div');
      wrap.className = 'ops-wrap';
      const table = document.createElement('table');
      table.className = 'ops-table';
      const thead = document.createElement('thead');
      const htr = document.createElement('tr');
      for (const h of headers) {
        const th = document.createElement('th');
        th.textContent = h;
        htr.append(th);
      }
      thead.append(htr);
      const tbody = document.createElement('tbody');
      for (const cells of rows) {
        const tr = document.createElement('tr');
        for (const c of cells) {
          const td = document.createElement('td');
          // 单元格内容只来自本卡拼的受限 HTML（esc 过的字段 + 固定标签），不接外部富文本
          td.innerHTML = c == null ? '—' : String(c);
          tr.append(td);
        }
        tbody.append(tr);
      }
      table.append(thead, tbody);
      wrap.append(table);
      sec.append(t, wrap);
      return sec;
    },

    // ------------------------------------------------------------ 手动更新

    async _runTask(name, btn) {
      if (this._dead || !this._ctx || !name) return;
      // 红线任务在后端白名单里会被拒；前端先按 data-run 只出白名单按钮，这里不再二次判断
      if (!confirm('确认手动更新「' + name + '」？将立即触发该数据任务（幂等，可重入）。')) return;
      const old = btn ? btn.textContent : '';
      if (btn) { btn.disabled = true; btn.textContent = '触发中…'; }
      this._setMsg('触发中…', '');
      let x = null;
      try {
        x = await this._ctx.api.post('/api/ops/run', { key: name });
      } catch (e) {
        this._setMsg('触发失败：' + (e && e.message ? e.message : e), 'err');
      }
      if (this._dead) return;
      if (x) {
        this._setMsg((x.ok ? '✅ ' : '⚠ ') + (x.message || JSON.stringify(x)), x.ok ? 'ok' : 'err');
      }
      if (btn) { btn.disabled = false; btn.textContent = old; }
      // 任务 State 需数秒才转 Running：稍等再强刷（旧页同口径 4s）
      const self = this;
      setTimeout(() => {
        if (self._dead) return;
        self._load(true).catch(() => {});
      }, 4000);
    },
  }],
};
