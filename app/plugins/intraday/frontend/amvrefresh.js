/**
 * 盘中 · AMV 实时刷新闸（2026-10-27，标注「预测实时源」）
 * ----------------------------------------------------------------------------
 * 背景：`/api/amv_live.json` 由 `engine/executor/amv_live.run_once(publish=True)` 生产，
 * 旧设计就是**按需刷新**（`POST /api/amv_refresh` → core ops_run → run_once），不新增常驻。
 * 卡片此前只 GET 静态文件，文件停在上一份 → 预测浮层永远标「历史预测」。
 * 这里补上「何时该 POST 一次」的判定，一律纯函数（`now` 可注入，便于单测），
 * 请求本身由 frontend.js 发（本模块零 IO、零 import、零写盘）。
 *
 * 判定顺序（先便宜后昂贵）：
 *   ① 已卸载 / 正在整卡取数 / 页面不可见 → 不刷（防递归、防后台盲刷）；
 *   ② 非交易日或非连续竞价 → 不刷（午休、收盘后、周末一次都不发）；
 *   ③ 距上次 POST < 60s → 不刷（**绝不 20s 一拍盲 POST**）；
 *   ④ 其余 → 刷；`why` 区分「常规逐分钟」与「首轮文件非今日」。
 *
 * 交易日只按**工作日**判：节假日表在 Python（`common.trading_calendar`），前端不复制。
 * 节假日落在工作日 + 竞价时段时会各发一次，服务端返「无新数据（ok:false）」→ 卡片显式标
 * `刷新失败`，不冒充实时；请求频率仍受 ③ 节流，不会打爆。
 */

export const GAP_MS = 60000;            // 同一页面最多 60s 一次 POST（要求 B）
export const TICK_MS = 20000;           // 卡片既有刷新节拍（与 refreshEvery 一致）
// 连续竞价两段：09:30–11:30、13:00–15:00（午休与收盘后不刷）
export const AUCTION_WINDOWS = [[570, 690], [780, 900]];

const pad2 = (n) => (n < 10 ? '0' : '') + n;

/** 本地日期 YYYY-MM-DD */
export function dayStr(d) {
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}

/** 本地时分 HH:MM */
export function hhmmStr(d) {
  return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}

/** 交易日（工作日口径）+ 连续竞价时段 */
export function inContinuousAuction(now) {
  const d = (now instanceof Date) ? now : new Date();
  const wd = d.getDay();
  if (wd === 0 || wd === 6) return false;
  const m = d.getHours() * 60 + d.getMinutes();
  return AUCTION_WINDOWS.some(([a, b]) => m >= a && m < b);
}

/**
 * 快照文件的交易日（判「首轮文件非今日」）。
 * 只认文件自身声明的交易日，取不到 → ''（不拿现在几点当免责理由）。
 */
export function fileDay(lv) {
  const meta = (lv && lv.meta) || {};
  const raw = String((lv && (lv.trade_date || lv.tick_date)) || meta.trade_date || '').trim();
  return (raw || String((lv && lv.asof) || meta.generated_at || '')).slice(0, 10);
}

/**
 * 这一次该不该 POST。
 * @returns {{act: boolean, why: string}}
 *   why ∈ unmounted / loading / hidden / off-session / throttled / tick / stale-file
 */
export function planRefresh(opt) {
  const o = opt || {};
  const now = (o.now instanceof Date) ? o.now : new Date();
  if (o.alive === false) return { act: false, why: 'unmounted' };
  if (o.loading) return { act: false, why: 'loading' };       // 防「刷新→整卡取数→再刷新」递归
  if (o.visible === false) return { act: false, why: 'hidden' };
  if (!inContinuousAuction(now)) return { act: false, why: 'off-session' };
  const last = Number(o.lastPostAt) || 0;
  if (last && (now.getTime() - last) < GAP_MS) return { act: false, why: 'throttled' };
  const today = dayStr(now);
  const fd = fileDay(o.lv);
  return { act: true, why: fd && fd === today ? 'tick' : 'stale-file' };
}

/** POST 结果 → 展示文案（成功清空；失败必须显式，不写成实时） */
export function refreshNote(res, now) {
  const t = hhmmStr((now instanceof Date) ? now : new Date());
  if (res && res.ok) return '';
  const why = (res && (res.detail || res.reason || res.status)) || '无响应';
  return '刷新失败 ' + t + '（' + String(why) + '）';
}

/**
 * 刷新器（一次 tick = 判定 + 一次 POST + 成功后重取 amv_live.json）。
 *
 * 为什么把它放在这里而不是 frontend.js 内联：① 只靠纯函数测不出「真的只 POST 一次」；
 * ② 卡片 mount 里 DOM / API 一堆状态，把循环抽出来才能拿假 api 跑频次与非交易时段。
 * 本模块仍零 IO：api 由调用方给（与 evo.js 藏 `hooks.api` 同一手法）。
 *
 * @param {object} o
 *   o.api               ctx.api（只用 post/get）
 *   o.state()           → {alive, loading, visible, lv}（每次 tick 现取）
 *   o.now()             → Date（可注入）
 *   o.onAmv(doc)        重取成功回调（调用方负责渲染）
 *   o.onNote(text)      失败/成功文案回调（'' = 清空）
 * @returns {{tick():Promise<object>, note():string, lastPostAt():number}}
 */
export function createRefresher(o) {
  const opt = o || {};
  const api = opt.api || {};
  const nowOf = typeof opt.now === 'function' ? opt.now : () => new Date();
  const stateOf = typeof opt.state === 'function' ? opt.state : () => ({});
  let lastPostAt = 0;
  let busy = false;          // 本次 POST 未回 → 后面的 tick 直接跳过（防并发双发）
  let note = '';

  async function tick() {
    const s = stateOf() || {};
    const plan = planRefresh({ now: nowOf(), alive: s.alive, loading: s.loading || busy,
      visible: s.visible, lastPostAt: lastPostAt, lv: s.lv });
    if (!plan.act) return { act: false, why: plan.why };
    if (typeof api.post !== 'function') return { act: false, why: 'no-post-api' };
    lastPostAt = nowOf().getTime();       // 先占位：失败也算一次，防失败后每拍重试
    busy = true;
    let res = null;
    try {
      res = await api.post('/api/amv_refresh');
    } catch (e) {
      res = { ok: false, detail: String((e && e.message) || e) };
    }
    try {
      if (res && res.ok) {
        let doc = null;
        try {
          doc = await api.get('/api/amv_live.json');
        } catch (e) {
          doc = null;
        }
        if (doc && typeof doc === 'object') {
          note = '';
          if (typeof opt.onAmv === 'function') opt.onAmv(doc);
        } else {
          note = refreshNote({ ok: false, detail: 'amv_live.json 读取失败' }, nowOf());
        }
      } else {
        note = refreshNote(res, nowOf());
      }
    } finally {
      busy = false;
    }
    if (typeof opt.onNote === 'function') opt.onNote(note);
    return { act: true, why: plan.why, ok: !!(res && res.ok), note: note };
  }

  return { tick: tick, note: () => note, lastPostAt: () => lastPostAt };
}
