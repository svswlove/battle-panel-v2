/**
 * intraday 卡 · 板块与个股研究（照旧页搬，10 个函数）
 *
 * 行为基准 = engine/web/index.html:4306-4531：
 *   rsSet(4314) / loadResearch(4327) / rsForceRetry(4369) / _rsTarget(4383) / _rsPaint(4391) /
 *   rsFmt(4408) / rsSp(4411) / rsSort(4418) / rsHead(4426) / rsRows(4430) / rsPartsHtml(4451) / _rsHtml(4473)
 *   状态块 = 4306-4313（_rs/_rsLvl/_rsSel/_rsSrt/_rsBusy/_rsErr/_rsAt/_rsLive/_rsBusyAt/_rsScope/_rsScroll/_RS_SRT）
 *   配套 = 旧页 2856-2881 kName / kTsPlayable（成分股名可点，口径照搬）
 *
 * 与旧页的结构性差别（都是「整页 → 卡片」的必然结果，语义没动，逐条对应任务书四条约束）：
 *
 * ① 模块级状态 → 每实例状态 + AbortController。
 *    旧页 state 全是模块级 let _rs/_rsLvl/_rsSel/...，监听靠一类的模块级布尔防重复绑；
 *    卡片里状态放 createState() 返回的 st，监听信号放 st.ac，unbind 一次 abort 全解。
 *    看门狗（旧页 4374 的模块级 setInterval）同样下放到实例：定时器句柄在 unbind 时 clearInterval。
 *
 * ② document.getElementById → 容器内查询。旧页 _rsTarget()（4383）是「面板级双槽位」：
 *    同一页里有两个 id=rsBody / rsBodyIntraday，靠 section.active 选当前可见那个——那是整页
 *    「3s 重建面板」的产物。卡片里宿主只有一个（mount 传入的 body），双槽位逻辑整体去掉，
 *    paint() 直接写 st.body。（见 HANDOFF 偏离清单第 1 条）
 *
 * ③ 浮层/子节点挂容器内。本卡旧页没有浮层，唯一的「越界」是 _rsScroll 靠内联 onscroll 写回
 *    模块级对象；这里改成容器内 capture 阶段 scroll 委托，写回 st.scroll（每实例）。
 *
 * ④ 内联 onclick → data-act 委托。旧页 6 处 onclick="rsSet(...)"（层级 3 键 / 排序 3 键 /
 *    表头 3 列 / 板块行 / 返回全A / 回成分股）与 2 处内联 onscroll，全部改成属性 + .it-card
 *    上一处委托，data-act 值见 ACT_* 常量。表头原有的 data-k（列键）必须保留——旧页 CSS
 *    .rs-tbl thead th[data-k]{cursor:pointer} 靠它，故动作参数另开 data-v。
 *
 * 数据层零写 DOM：本模块只读 /api/amv_research.json 原样字段，不取数以外的事、不写盘。
 * 纯渲染函数（rsFmt/rsSp/rsSort/rsHead/rsRows/rsPartsHtml/html/asofText/kName）不碰 DOM，
 * 可在 Node 里直接导入做逐值对照（tests/test_research.py 就是这么测的）。
 *
 * ---- 2026-10-26 修的旧页/前版本缺陷（不是本轮排版改动引入的，是原来就有）：
 * 「点『返回全A』没反应」。三个环累加：
 *   ① `rsSet` 先改 st.scope 再调 load，而 load 碰到 busy **静默 return** → 点击被吞；
 *   ② 在飞响应回来后，paint() 按**新** st.scope 渲染**旧**响应 → 标题说全A、表格仍是成分股；
 *   ③ 签名只含 st.scope 不含数据自报的 rs.scope / members 行数 → 补发的全A 数据到了也不重绘。
 * 修法：加载闸门改成**排队**（st.pending）、渲染口径改看 rs.scope（数据说什么就画什么）、
 * 签名补 scope + members 行数。本机直连窗口 ~100ms 很难撞上（C2a 验收因此是绿的），
 * 盘中数据大或网络一慢必现；jsdom 用闸门 + 真浏览器加 1.2s 延迟都已稳定复现。
 */

import { esc, num, clsUp as cls } from './format.js';

// ---------------------------------------------------------------- 常量（旧页 4313 / 4337 / 4349 / 4373）
/** 旧页 4313 _RS_SRT：排序 3 键（顺序即按钮顺序，不许改） */
export const RS_SRT = { contrib: '指数贡献', px_pct: '涨跌', amv_pct: 'AMV' };
/** 旧页 4306：层级 3 档（CONCEPT 是自建板块） */
export const RS_LEVELS = [['L2', '同花顺二级'], ['CONCEPT', '自建'], ['L3', '同花顺三级']];
/** 旧页 4337：盘中 30s / 盘外 120s（数据已定格，不必高频烧 QMT） */
export const RS_TTL_LIVE = 30000;
export const RS_TTL_CLOSE = 120000;
/** 旧页 4349：8s 请求超时（以前请求悬挂会让 busy 永久为 true，卡在「加载中」） */
export const REQ_TIMEOUT_MS = 8000;
/** 旧页 4330：busy 超过 15s 视为上一轮悬挂，强制释放 */
export const BUSY_RELEASE_MS = 15000;
/** 旧页 4374：看门狗 20s（TTL 到期且仍在盘中窗口 → 重试；见 watchdogDue） */
export const WATCHDOG_MS = 20000;

/**
 * 盘中窗口（本地钟）：工作日 08:45–15:35。
 * 只用于「看门狗要不要自动重试」——盘外历史帧不自动刷（2026-10-27 标注 N20261027-101349-33）。
 */
export function inLiveWindow(now) {
  const d = (now instanceof Date) ? now : new Date(now == null ? Date.now() : now);
  const wd = d.getDay();
  if (wd === 0 || wd === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return m >= 8 * 60 + 45 && m <= 15 * 60 + 35;
}

/**
 * 看门狗该不该重试（纯函数，now 可注入 → 可测）。
 *
 * 2026-10-27 修（标注 N20261027-101349-33「盘中研究区用昨日数据」）：
 *   旧实现 `if (st.rs) return;` —— 只要手里有**任何**旧数据（含后端实时失败后回退的收盘帧），
 *   看门狗就永远不再重试，盘中会一直停在昨日收盘数据上，且没有任何自动恢复路径。
 *   现改为按 TTL 判：TTL 内不打扰；到期且仍在盘中窗口 + 实时口径开启 → 重试。
 *   不区分「上一帧是实时还是收盘」：盘中窗口内拿到的收盘帧本身就是降级结果（后端 live_err
 *   或干脆没回话），必须继续试实时，否则永远回不到实时口径。
 *   盘外（周末/15:35 之后/08:45 之前）不自动刷历史帧，保持原 TTL 语义。
 */
export function watchdogDue(st, now) {
  if (!st) return false;
  if (!st.rs) return true;                       // 无数据：原语义（立刻重试）
  if (!st.live) return false;                    // 收盘口径（live=0）不自动刷
  const t = (now instanceof Date) ? now.getTime() : (now == null ? Date.now() : Number(now));
  if ((t - (st.at || 0)) < RS_TTL_LIVE) return false;
  return inLiveWindow(t);
}

// 动作键（旧页内联 onclick 的 4 种 k）
const ACT_SRT = 'rs-srt';
const ACT_LVL = 'rs-lvl';
const ACT_SEL = 'rs-sel';
const ACT_SCOPE = 'rs-scope';

/**
 * 「返回全A / 回成分股」按钮（含忙碌态）。
 * 主人 2026-10-26：点击到数据回来有 1~1.5s 静默期，这段必须给反馈。
 * 两种标签都写进 data-* 里，忙碌时只改文本/属性、**不重绘整块**
 * （重绘会重置两栏滚动位置，也会把焦点弄丢）。
 */
function backBtn(st, scope, idle, busyTxt, tip) {
  const on = !!st.busy;
  return '<button class="rs-back' + (on ? ' busy' : '') + '" data-act="' + ACT_SCOPE + '" data-v="' + scope
    + '" data-idle="' + esc(idle) + '" data-busy-txt="' + esc(busyTxt) + '" title="' + esc(tip) + '"'
    + (on ? ' aria-busy="true" disabled' : ' aria-busy="false"') + '>'
    + esc(on ? busyTxt : idle) + '</button>';
}

/**
 * 切忙碌态（不改数据、不重绘）：禁用 + aria-busy + 脉冲点 + 文案换「…」。
 * 禁用是自然的闸门 —— 请求在飞时再点同一个按钮没意义。
 */
export function setBackBusy(st, on) {
  if (!st || !st.body) return;
  const btn = st.body.querySelector('.rs-back');
  if (!btn) return;
  const idle = btn.dataset.idle || btn.textContent;
  const busyTxt = btn.dataset.busyTxt || (idle + '…');
  btn.textContent = on ? busyTxt : idle;
  btn.classList.toggle('busy', !!on);
  if (on) { btn.setAttribute('aria-busy', 'true'); btn.disabled = true; }
  else { btn.setAttribute('aria-busy', 'false'); btn.disabled = false; }
}

// ---------------------------------------------------------------- 格式化（旧页 4408 / 4411）

/** 旧页 4408 rsFmt：缺值 → 破折号；正数带 +。单位随调用方（‰ / %） */
export function rsFmt(v, u, d) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + num(v, d == null ? 2 : d) + (u || '');
}

/** 旧页 4411 rsSp：近 30 日 AMV 缩略线（上/下/MA10 三条路径，旧页 CSS .sp-up/.sp-dn/.sp-ma 上色） */
export function rsSp(sp) {
  if (!sp || (!sp.up && !sp.dn)) return '<span class="muted">—</span>';
  return '<svg class="rs-sp" viewBox="0 0 100 26" preserveAspectRatio="none" aria-hidden="true">'
    + (sp.dn ? '<path class="sp-dn" d="' + sp.dn + '"/>' : '')
    + (sp.up ? '<path class="sp-up" d="' + sp.up + '"/>' : '')
    + (sp.ma ? '<path class="sp-ma" d="' + sp.ma + '"/>' : '') + '</svg>';
}

/** 旧页 4418 rsSort：按当前排序列降序；缺值/NaN 排最后（-Infinity，不是 0） */
export function rsSort(list, k) {
  return (list || []).slice().sort((a, b) => {
    const av = (a[k] == null || isNaN(a[k])) ? -Infinity : a[k];
    const bv = (b[k] == null || isNaN(b[k])) ? -Infinity : b[k];
    return bv - av;
  });
}

/** 旧页 2866 kTsPlayable：能否跳 K 线（带市场后缀 → 可跳；裸 6 位码首位 ∈ {0,2,3,5,6,9}） */
export function kTsPlayable(ts) {
  const s = (ts === null || ts === undefined) ? '' : String(ts).trim();
  if (!s) return false;
  if (/^\d{6}\.(SH|SZ|BJ)$/.test(s)) return true;
  return /^[023569]\d{5}$/.test(s);
}

/**
 * 旧页 2856 kName：个股名 + 可点占位。
 * 差别（约束④）：旧页挂 onclick="kNameClick(this,event)" 直调 KLineApp；
 * 卡片里在 iframe 内，改成 data-open-kline / data-open-name 占位，由 shell 捕获阶段委托转发父页。
 * 不可跳的码照旧输出 .k-nolink（显式不可点，不伪装）。
 */
export function kName(name, tsCode) {
  const nm = (name === null || name === undefined) ? '' : String(name);
  const ts = (tsCode === null || tsCode === undefined) ? '' : String(tsCode).trim();
  const txt = esc(nm) || esc(tsCode);
  if (!txt) return '—';
  if (!kTsPlayable(ts)) return '<span class="k-nolink" title="' + esc(tsCode) + '">' + txt + '</span>';
  return '<span class="k-name" data-kname="' + esc(ts) + '" data-open-kline="' + esc(ts)
    + '" data-open-name="' + esc(nm) + '" title="' + esc(ts) + '">' + txt + '</span>';
}

// ---------------------------------------------------------------- 口径行（旧页 4486-4495）

/**
 * 旧页 4486-4495：口径标注。服务端决定给实时还是收盘，前端只按 live_mode 如实标注：
 *   live 且今天且未到 15:00 → 「实时 HH:MM:SS」
 *   live 且今天且已过 15:00 → 「收盘定格 HH:MM:SS」
 *   live 但快照不是今天      → 「末帧 <日期> <时间>（非今日）」——绝不冒充今天的实时
 *   回退收盘口径            → 「收盘 YYYY-MM-DD」
 * now 可注入（对照测试要可复现，旧页直接 new Date()）。
 */
export function asofText(rs, now) {
  const d = (now instanceof Date) ? now : new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const today = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const mins = d.getHours() * 60 + d.getMinutes();
  const liveOn = !!(rs && rs.live_mode);
  const isToday = String((rs && rs.live_date) || '') === today;
  const asof = esc((rs && rs.asof) || '');
  if (liveOn && isToday && mins < 15 * 60) return '实时 ' + asof;
  if (liveOn && isToday) return '收盘定格 ' + asof;
  if (liveOn) return '末帧 ' + esc((rs && rs.live_date) || '') + ' ' + asof + '（非今日）';
  return '收盘 ' + asof;
}

// ---------------------------------------------------------------- 表头 / 行（旧页 4426 / 4430）

/** 旧页 4426 rsHead：6 列；排序按钮态靠 .on + ↓，动作走 data-act（旧页是内联 onclick） */
export function rsHead(st) {
  const srt = st.srt;
  const c = (k, txt) => '<th class="n' + (srt === k ? ' on' : '') + '" data-k="' + k + '" data-act="'
    + ACT_SRT + '" data-v="' + k + '" title="按' + txt + '降序（再点其他列切换）">' + txt
    + (srt === k ? '↓' : '') + '</th>';
  return '<thead><tr><th class="n">#</th><th class="l">名称</th>'
    + c('contrib', '指数贡献') + c('px_pct', '涨跌') + c('amv_pct', 'AMV')
    + '<th class="n" title="近30交易日活跃市值 + MA10（工作台详情卡同规则）：线上红多 / 线下绿空">近30日AMV</th>'
    + '</tr></thead>';
}

/**
 * 旧页 4430 rsRows：6 列（名次 / 名称 / 指数贡献‰ / 涨跌% / AMV% / 缩略线）。
 * 板块行：pk 可点 + data-ctx="sector"（shell 右键菜单）；成分股行：名走 kName → data-open-kline。
 * 旧页两者都带 data-ctx（2026-10-19：板块行→板块，成分股行→个股）。
 */
export function rsRows(list, kind, st) {
  if (!list.length) return '<tr><td colspan="6" class="nm muted">（无数据）</td></tr>';
  return list.map((x, i) => {
    const sel = (kind === 'b' && x.c === st.sel) ? ' sel' : '';
    const click = kind === 'b'
      ? ' class="pk' + sel + '" data-act="' + ACT_SEL + '" data-v="' + esc(x.c) + '"'
      : ' class="' + (sel ? 'sel' : '') + '"';
    const nm = kind === 'b'
      ? '<span class="rs-nm">' + esc(x.n) + '</span><span class="cd">'
        + esc(String(x.c || '').replace('.TI', '')) + '</span>'
      : kName(x.n, x.ts);
    const tip = x.n + '｜指数贡献 ' + rsFmt(x.contrib, '‰', 3) + '｜AMV ' + rsFmt(x.amv_pct, '%')
      + '｜涨跌 ' + rsFmt(x.px_pct, '%') + '｜距MA10 ' + rsFmt(x.bias, '%');
    const ctxAttrs = kind === 'b'
      ? ' data-ctx="sector" data-code="' + esc(x.c) + '" data-name="' + esc(x.n) + '"'
      : ' data-ctx="stock" data-code="' + esc(x.ts || '') + '" data-name="' + esc(x.n || '') + '"';
    return '<tr' + click + ctxAttrs + ' title="' + esc(tip) + '">'
      + '<td class="rk">' + (i + 1) + '</td><td class="nm">' + nm + '</td>'
      + '<td class="' + cls(x.contrib) + '">' + rsFmt(x.contrib, '‰', 3) + '</td>'
      + '<td class="' + cls(x.px_pct) + '">' + rsFmt(x.px_pct, '%') + '</td>'
      + '<td class="' + cls(x.amv_pct) + '">' + rsFmt(x.amv_pct, '%') + '</td>'
      + '<td class="sp">' + rsSp(x.sp) + '</td></tr>';
  }).join('');
}

// ---------------------------------------------------------------- 贡献分项条（旧页 4451）

/**
 * 旧页 4451 rsPartsHtml：指数贡献四分项（涨跌/流通股/活跃度/新股 + 停牌/缺数 + 大盘基准）。
 * 停牌项只在 |susp| ≥ 0.01 时出现（数据没有就不画一排 0）。合计与左栏同板块的 contrib 同源。
 *
 * 2026-11-06 主人：全A 视图下也要看 AMV 贡献分项分解（原先 all 模式整块不画）。
 * allMode=true 时改读后端 `all_parts`（沪深全A 汇总，口径同板块分项：四档分项 + 大盘基准，
 * 全A 以自己为分母所以合计恒 0‰），不挂「工作台详情」按钮（它不是板块，没有板块详卡）。
 */
export function rsPartsHtml(st, allMode) {
  const rs = st.rs;
  if (!rs) return '';
  const p = allMode ? rs.all_parts : rs.sel_parts;
  if (!p) return '';
  const items = [['涨跌贡献', p.px], ['流通股贡献', p.sh], ['活跃度贡献', p.act], ['新股上市贡献', p.new]];
  if (Math.abs(p.susp || 0) >= 0.01) items.push(['停牌/缺数', p.susp]);
  items.push(['大盘基准', p.den]);
  const mx = Math.max.apply(null, items.map((x) => Math.abs(x[1] || 0)).concat([0.01]));
  const bars = items.map((pair) => {
    const k = pair[0];
    const v = pair[1];
    const val = (v == null ? 0 : v);
    const w = Math.min(50, Math.abs(val) / mx * 50);
    const left = val >= 0 ? 50 : 50 - w;
    return '<div class="rs-part' + (Math.abs(val) < 0.0005 ? ' zero' : '') + '"><span class="rs-part-k">'
      + k + '</span><span class="rs-part-bar"><i class="' + cls(val) + '" style="left:'
      + left.toFixed(1) + '%;width:' + w.toFixed(1) + '%"></i></span>'
      + '<span class="rs-part-v ' + cls(val) + '">' + rsFmt(val, '‰', 3) + '</span></div>';
  }).join('');
  const name = allMode ? '沪深全A' : (rs.sel_name || '');
  const code = allMode ? 'ALL' : String(rs.sel || '').replace('.TI', '');
  const openBtn = allMode ? '' : '<button class="rs-open" data-open-amv="' + esc(rs.sel || '')
    + '" data-open-name="' + esc(rs.sel_name || '')
    + '" title="打开 0AMV 工作台详情卡（权威实现：AMV+MA10 / 板块指数+黄白线 / 成分股全表）">▤ 工作台详情</button>';
  const foot = allMode
    ? '合计 <b class="' + cls(p.total) + '">' + rsFmt(p.total, '‰', 3) + '</b>'
      + '<span class="src">全A 汇总：四档分项 + 大盘基准（−1×全A涨幅），明细与全A 总量对齐时合计为 0‰</span>'
    : '合计 <b class="' + cls(p.total) + '">' + rsFmt(p.total, '‰', 3) + '</b>'
      + '<span class="src">四档行业分项 + 大盘基准（−权重×全A涨幅）= 指数贡献，与左侧同名次一致</span>';
  return '<div class="rs-parts"><div class="rs-parts-h">'
    + '<span class="rs-parts-t">' + esc(name) + ' <span class="src">'
    + esc(code) + ' · 贡献分项</span></span>' + openBtn
    + '</div>' + bars
    + '<div class="rs-parts-f">' + foot + '</div></div>';
}

// ---------------------------------------------------------------- 整卡（旧页 4473 _rsHtml）

/**
 * 旧页 4473 _rsHtml：工具条（层级 3 键 / 排序 3 键 / 口径行）+ 两栏表 + 脚注。
 * 旧页注释原话保留：数据在就永远画数据，请求失败只降级为标题栏一个「⚠ 未更新」，
 * 不把整卡换成一行错误文字（2026-10-22 主人报「收盘之后卡片就消失」的根因）。
 */
export function html(st, now) {
  const rs = st.rs;
  if (!rs) {
    return '<p class="muted">' + (st.err
      ? ('板块与个股研究数据不可用：' + esc(st.err))
      : '数据加载中…（/api/amv_research.json）') + '</p>';
  }
  const asofTxt = asofText(rs, now);
  // 2026-10-27（标注 N20261027-101349-33）：实时取不到、回退收盘时，失败原因必须可见——
  // 只在 asof 写「收盘」等于把失败藏起来（盘中看到昨日数据却不知道是降级）。
  // 两个来源：服务端自报 rs.live_err（live=1 请求但后端取不到快照），或前端本轮实时失败
  // 后回落收盘时的 st.liveErr（后端根本没回话 / 超时）。
  const liveErr = (rs && rs.live_err) || st.liveErr || '';
  const liveWarn = liveErr
    ? '<span class="stale-warn" title="' + esc(liveErr) + '">⚠ 实时不可用，当前为收盘（'
      + esc(liveErr) + '）</span>'
    : '';
  const staleTip = (st.err || rs.stale)
    ? '<span class="stale-warn" title="' + esc(st.err || '接口本次未返回新数据，沿用上一次成功结果')
      + '">⚠ 未更新</span>' : '';
  const bar = '<div class="rs-bar st-bar">'
    + '<span class="st-grp">'
    + RS_LEVELS.map((pair) => '<button class="' + (st.lvl === pair[0] ? 'on' : '') + '" data-act="'
      + ACT_LVL + '" data-v="' + pair[0] + '">' + pair[1] + '</button>').join('')
    + '</span>'
    + '<span class="st-grp"><span class="st-k">排序</span>'
    + Object.keys(RS_SRT).map((k) => '<button class="' + (st.srt === k ? 'on' : '') + '" data-act="'
      + ACT_SRT + '" data-v="' + k + '">' + RS_SRT[k] + '</button>').join('')
    + '</span>'
    + '<span class="src">' + asofTxt + liveWarn + staleTip + ' ｜ 基准 ' + esc(rs.prev || '') + ' ｜ 全A AMV '
    + rsFmt(rs.total_pct, '%') + ' ｜ ' + esc(rs.level_label || '') + ' ' + rs.n + ' 个</span>'
    + '</div>';
  const bRows = rsSort(rs.boards || [], st.srt);
  const mAll = rs.members || [];
  const mRows = rsSort(mAll, st.srt);
  const cap = (rs.n_members || 0) > mAll.length
    ? ' · 共 ' + rs.n_members + ' 只，按指数贡献列前 ' + mAll.length : '';
  // 全A 模式判定：**以数据自报的 scope 为准**（rs.scope），拿不到才回退到意图（st.scope）。
  // 为什么：st.scope 是「用户想看的」，rs.scope 是「手上这份数据实际是什么」。两者不一致时
  //   （点击在请求在飞时到达、旧响应先回来）必须按数据渲染，否则会出现
  //   「标题说全A个股、表格却是那个板块的成分股」的互相矛盾（主人 2026-10-26 报的现象）。
  const allMode = (((rs && rs.scope) ? rs.scope : st.scope) === 'all');
  // 中栏标题（旧页 4513）：成分股旁挂「返回全A」；已在全A 时标题换成「全A个股」并给「回到成分股」
  // 2026-10-26 主人：1~1.5s 静默期要有反馈 —— 两个按钮都走 backBtn()，自带忙碌态
  const mTtl = allMode
    ? '<span class="rs-ttl">全A个股</span>'
      + backBtn(st, 'board', '← 回成分股', '回成分股…', '回到选中板块的成分股')
    : '<span class="rs-ttl">' + esc(rs.sel_name || '') + ' · 成分股</span>'
      + backBtn(st, 'all', '返回全A', '返回全A…', '看全A 个股里到底哪些在做贡献（按指数贡献排序）');
  return bar + '<div class="rs-split">'
    + '<div class="rs-col">'
    + '<div class="rs-hd"><span class="rs-ttl">板块排名</span><span class="src">点行选板块 ·'
    + ' 涨跌 = 成分股按昨收 AMV 加权 · 线色 = AMV 对 MA10（红多/绿空）</span></div>'
    + '<div class="rs-scroll" data-rs-col="b">'
    + '<table class="rs-tbl">' + rsHead(st) + '<tbody>' + rsRows(bRows, 'b', st) + '</tbody></table></div>'
    + '</div>'
    + '<div class="rs-col">'
    + (allMode ? rsPartsHtml(st, true) : rsPartsHtml(st))
    + '<div class="rs-hd">' + mTtl + '<span class="src">' + mAll.length + ' 只' + cap
    + ' · 点名称开 K 线</span></div>'
    + '<div class="rs-scroll" data-rs-col="m">'
    + '<table class="rs-tbl">' + rsHead(st) + '<tbody>' + rsRows(mRows, 'm', st) + '</tbody></table></div>'
    + '</div>'
    + '</div>'
    + '<div class="src" style="margin-top:4px">' + esc(rs.note || '')
    + ' ｜ 成分表已剔北交所（与板块 AMV 聚合同口径）</div>';
}

// ---------------------------------------------------------------- 实例状态（旧页 4306-4313）

/**
 * 建立本实例的研究卡状态（除 mouse 坐标外不碰 DOM）。
 * @param {Element} cardEl 不参与重绘的卡片根（.it-card）：事件委托宿主
 * @param {Element} bodyEl 参与重绘的内容容器：paint 只写这里
 * @param {{get:Function}} api shell 注入的 ctx.api（只用到 get）
 */
export function createState(cardEl, bodyEl, api) {
  const st = {
    el: cardEl,
    body: bodyEl,
    api: api || null,
    alive: true,
    // ---- 旧页 4306-4313 的状态位，逐个下放到实例
    rs: null,            // _rs       最近一次成功数据
    lvl: 'L2',           // _rsLvl    层级
    sel: '',             // _rsSel    选中板块码
    srt: 'contrib',      // _rsSrt    排序列
    busy: false,         // _rsBusy   请求中
    busyAt: 0,           // _rsBusyAt 悬挂自愈起点
    err: '',             // _rsErr    最近一次失败原因
    liveErr: '',         // 最近一次「实时口径」失败原因（2026-10-27：回落收盘时必须可见）
    at: 0,               // _rsAt     上次成功时间（TTL 判定）
    live: 1,             // _rsLive   实时口径开关（旧页恒 1，不给手动切换按钮）
    scope: 'board',      // _rsScope  board=成分股 / all=全A个股
    pending: false,      // 被 busy 闸门吞掉的**强制**请求：本轮结束后补发（旧页没有这个位，是本次修的缺陷）
    scroll: {},          // _rsScroll 两栏滚动位置（旧页模块级对象 → 每实例）
    // ---- 渲染缓存（旧页 4367 _rsNode/_rsSig）：签名不变直接搬同一个节点，零重解析
    node: null,
    sig: '',
    watch: 0,            // 看门狗定时器句柄（旧页 4374 模块级 setInterval → 每实例）
    ac: new AbortController(),
  };
  st.load = (force) => load(st, force);
  st.watch = setInterval(() => {
    try {
      if (!st.alive || !st.body || !st.body.isConnected) return;
      if (typeof document !== 'undefined' && document.hidden) return;
      // 2026-10-27：旧实现 `if (st.rs) return;` 让「有旧数据」等于「永不重试」——
      // 后端实时失败回退收盘后，卡片就永久停在旧数据上。改为按 TTL 判（见 watchdogDue）。
      if (!watchdogDue(st, Date.now())) return;
      retry(st);
    } catch (e) { /* 看门狗自身绝不抛 */ }
  }, WATCHDOG_MS);
  return st;
}

// ---------------------------------------------------------------- 交互（旧页 4314 rsSet）

/** 旧页 4314 rsSet：按 k 改状态；需要新数据的（lvl/sel/scope）立刻重拉 */
export function rsSet(st, k, v) {
  if (k === 'lvl') {
    if (st.lvl === v) return;
    st.lvl = v; st.sel = ''; st.rs = null; st.at = 0; st.load(true); return;
  }
  if (k === 'live') {
    if (st.live === v) return;
    st.live = v; st.rs = null; st.at = 0; st.load(true); return;
  }
  if (k === 'srt') { st.srt = v; paint(st); return; }
  if (k === 'sel') {
    if (st.sel === v && st.rs && st.rs.sel === v && st.scope === 'board') return;
    st.sel = v; st.scope = 'board'; st.at = 0; st.load(true); return;
  }
  if (k === 'scope') {
    if (st.scope === v) return;
    st.scope = v; st.at = 0; st.load(true);
  }
}

// ---------------------------------------------------------------- 取数（旧页 4327 loadResearch）

/**
 * 旧页 4327 loadResearch：8s 超时 + 15s 悬挂自愈 + TTL 缓存 + 实时取不到自动回落收盘口径一次。
 * 这段必须原样保留——旧页踩过「请求悬挂 → busy 永久 true → 卡在加载中」的坑。
 * liveOv=0 → 本次强制走收盘口径（单次回落，不改 st.live，下一轮仍会试实时）。
 */
export async function load(st, force, noFallback, liveOv) {
  if (!st || !st.alive) return;
  if (!st.api || typeof st.api.get !== 'function') return;
  if (st.busy) {
    if (Date.now() - st.busyAt < BUSY_RELEASE_MS) {
      // 2026-10-26 修（主人报「点『返回全A』没反应」）：
      //   `rsSet` 是**先改状态、再调 load**，而这里以前是静默 return。于是一个显式请求
      //   （点行 / 切层 / 切 scope）碰到请求在飞就被**吞掉**：状态已变、DOM 没变（看着就是没反应），
      //   等那个在飞请求回来，paint() 又按**新状态**渲染**旧响应**→ 标题说「全A个股」、
      //   表格还是那个板块的成分股。本机直连时窗口只有 ~100ms（C2a 验收就是这样过的），
      //   盘中数据大 / 网络一慢就必现（用 CDP 加 1.2s 延迟可稳定复现）。
      //   改法：强制请求**排队**，本轮结束后补发一次（只排一个，连点不堆积）。
      if (force) st.pending = true;
      return;
    }
    st.busy = false;                                        // 卡死（上一轮悬挂）→ 释放
  }
  const ttl = st.live ? RS_TTL_LIVE : RS_TTL_CLOSE;
  const useLive = (liveOv === 0) ? 0 : st.live;
  if (!force && st.rs && (Date.now() - st.at) < ttl) { paint(st); return; }
  st.busy = true; st.busyAt = Date.now(); st.err = '';
  // 2026-10-26 主人：1~1.5s 静默期要有反馈 —— 置忙碌态（禁用 + 脉冲 + 文案「返回全A…」）
  setBackBusy(st, true);
  let ctrl = null;
  let timer = 0;
  try {
    const q = '?level=' + encodeURIComponent(st.lvl)
      + (st.sel ? '&code=' + encodeURIComponent(st.sel) : '')
      + (st.scope === 'all' ? '&scope=all' : '')
      + (useLive ? '&live=1' : '');
    ctrl = new AbortController();
    timer = setTimeout(() => { try { ctrl.abort(); } catch (e) { /* 已结束 */ } }, REQ_TIMEOUT_MS);
    const r = await st.api.get('/api/amv_research.json' + q, { signal: ctrl.signal });
    if (r && r.ok) {
      st.rs = r; st.at = Date.now(); st.sel = r.sel || '';
      // 2026-10-27：实时口径的失败原因跟数据走。live=1 成功但后端取不到快照 → 服务端自报
      // live_err；live=1 真正成功 → 清掉。回落收盘（liveOv=0）不动它，让失败提示继续显示。
      if (useLive) st.liveErr = r.live_err || '';
    } else {
      st.err = (r && r.detail) || '接口返回异常'; st.sel = (st.rs && st.rs.sel) || '';
      if (useLive) st.liveErr = st.err;
    }
  } catch (e) {
    st.err = (e && e.name === 'AbortError') ? '请求超时（8s）' : '接口不可达';
    st.sel = (st.rs && st.rs.sel) || '';
    if (useLive) st.liveErr = st.err;
  } finally {
    if (timer) clearTimeout(timer);
    st.busy = false;
    setBackBusy(st, false);      // 数据回来了（或失败）→ 恢复可点，不把用户锁在忙碌态
    // 被吞掉的那个显式请求在这里补发（排在当前响应应用完、paint 之后再跑，避免两拍互盖）
    if (st.pending && st.alive) {
      st.pending = false;
      setTimeout(() => { if (st.alive) { try { load(st, true); } catch (e) { /* 下一拍看门狗兜 */ } } }, 0);
    }
  }
  // 实时口径取不到 → 自动回落收盘口径重试一次（收盘后基本不会失败）
  if (!st.rs && st.err && useLive && !noFallback) return load(st, true, true, 0);
  paint(st);
}

/** 旧页 4369 rsForceRetry：手动/自动重试入口（看门狗与失败重试都用它） */
export function retry(st) {
  if (!st || !st.alive) return;
  st.busy = false; st.err = ''; st.at = 0;
  try { load(st, true); } catch (e) { /* 重试失败由下一拍看门狗兜 */ }
}

// ---------------------------------------------------------------- 渲染入口（旧页 4391 _rsPaint）

/**
 * 数据签名：内容没变就复用同一份节点（旧页 4395，防 90 行 + SVG 反复重解析）。
 *
 * **与旧页的一处有意偏离（旧页 bug 修正）**：旧页 4395 的 sig 里没有 _rsSrt，
 * 而 rsSet('srt') 只调 _rsPaint() 不走取数 —— 于是点「涨跌/AMV」排序时状态改了、
 * DOM 因签名相同被原样搬回，表既不重排、表头箭头也不动（实测：连点三列，
 * 首行与 th class 逐字节不变）。旧页自己的注释写着「静默失败零容忍」，
 * 所以这里把 srt 计入签名，排序立刻生效。详见 HANDOFF 偏离清单第 5 条。
 */
export function signature(st) {
  const rs = st.rs;
  // scope 取**数据自报**（rs.scope），并带上 members 行数：接口参数不同但返回形状相同时
  // （全A 与成分股可能同样是 90 个板块、同 asof），只靠 st.scope 无法区分，会导致
  // 「新数据到了但签名没变、paint 直接跳过」——2026-10-26 修「返回全A 点了没反应」时发现。
  return (rs ? [rs.asof, rs.prev, st.sel, (rs.scope || st.scope), st.lvl, st.srt, st.live ? 1 : 0,
    (rs.boards || []).length, (rs.members || []).length, rs.live_err || st.liveErr || ''].join('|') : '')
    + '#' + (st.err || '');
}

/**
 * 旧页 4391 _rsPaint：写内容 + 还原两栏滚动位置。
 * 差别（约束②）：旧页 _rsTarget() 双槽位选可见 body，这里直接写 st.body。
 */
export function paint(st, now) {
  const el = st.body;
  if (!el) return;
  const sig = signature(st);
  if (!st.node || sig !== st.sig) {
    const doc = el.ownerDocument || (typeof document !== 'undefined' ? document : null);
    const node = doc.createElement('div');
    node.className = 'rs-wrap';
    node.innerHTML = html(st, now);
    st.node = node;
    st.sig = sig;
  }
  if (st.node.parentNode !== el) el.replaceChildren(st.node);
  ['b', 'm'].forEach((k) => {
    const box = el.querySelector('[data-rs-col="' + k + '"]');
    if (box && st.scroll[k]) box.scrollTop = st.scroll[k];
  });
  // 2026-10-26 主人：「点击板块时…跳到下方显示，同时板块排名内对应板块出现选中效果」
  // 选中项变了就把它滚进左栏可视区。
  // 注意只用**左栏自己的 scrollTop**，不能用 scrollIntoView —— 那会把外层的
  // section.stage（整页滚动容器）一并带走，把上面 evo 刚滚到研究区头的位给顶回去（实测）。
  const selChanged = (st.selSeen !== st.sel);
  st.selSeen = st.sel;
  if (selChanged) {
    try {
      const box = el.querySelector('[data-rs-col="b"]');
      const row = el.querySelector('tr.pk.sel');
      if (box && row) {
        const br = box.getBoundingClientRect(); const rr = row.getBoundingClientRect();
        if (rr.top < br.top || rr.bottom > br.bottom) {
          box.scrollTop += (rr.top - br.top) - (br.height - rr.height) / 2;
        }
      }
    } catch (e) { /* 滚动失败不影响渲染 */ }
  }
}

// ---------------------------------------------------------------- 事件委托（旧页内联 onclick 的替代）

/**
 * 绑定本实例的全部监听（宿主 = st.el，不参与重绘的卡片根）。
 * 一次 mount 绑一次、unbind abort 全解（约束①）；旧页靠模块级布尔防重复绑，那种写法在
 * 卡片里会静默坏掉（第二张卡布尔已为 true，事件永远不绑）。
 */
export function bind(st) {
  const host = st.el;
  if (!host) return;
  const opt = { signal: st.ac.signal };

  host.addEventListener('click', (ev) => {
    const t = ev.target;
    if (!t || !t.closest) return;
    const node = t.closest('[data-act]');
    if (!node) return;
    const act = node.getAttribute('data-act');
    const v = node.getAttribute('data-v');
    if (act === ACT_SRT) rsSet(st, 'srt', v);
    else if (act === ACT_LVL) rsSet(st, 'lvl', v);
    else if (act === ACT_SEL) rsSet(st, 'sel', v);
    else if (act === ACT_SCOPE) rsSet(st, 'scope', v);
  }, opt);

  // 旧页内联 onscroll 写模块级 _rsScroll；这里 capture 阶段委托写每实例 st.scroll。
  // （scroll 不冒泡，但 capture 阶段仍会经过祖先，故用 capture:true）
  host.addEventListener('scroll', (ev) => {
    const box = ev.target;
    if (!box || !box.getAttribute) return;
    const k = box.getAttribute('data-rs-col');
    if (k) st.scroll[k] = box.scrollTop;
  }, { passive: true, capture: true, signal: st.ac.signal });

  paint(st);   // 无数据时输出「加载中…」，不留空白块
}

/** 解绑：abort 一次解掉全部监听 + 停看门狗（旧页模块级 setInterval 没有卸载概念） */
export function unbind(st) {
  if (!st) return;
  st.alive = false;
  if (st.watch) { clearInterval(st.watch); st.watch = 0; }
  try { st.ac.abort(); } catch (e) { /* 已 abort：忽略 */ }
  st.node = null;
  st.sig = '';
}
