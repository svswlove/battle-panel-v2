/**
 * AMV 预测实时刷新 · 闸门与频次（2026-10-27「预测实时源」）
 *
 * 直接 import frontend/amvrefresh.js 跑：纯函数判定 + 带假 api 的完整 refresher。
 * 守四件事：
 *   ① 频次：连续 5 个 20s tick → 只有 1 次 POST（60s 节流，不许每拍盲 POST）；
 *   ② 非交易时段不调用：周末 / 午休 / 收盘后 / 竞价前，一次都不发；
 *   ③ 成功后重新 GET amv_live.json，且回调拿到的是**当天**那份（预测可判今日）；
 *   ④ 失败（ok:false / 抛错 / GET 失败）→ 明示「刷新失败」，不写成实时；
 *   ⑤ 卸载 / 整卡 loading / 页面不可见 → 不刷（防递归）。
 *
 * 输出：最后一行 JSON（pytest 取最后一行）。
 */
const A = await import(new URL('../frontend/amvrefresh.js', import.meta.url).href);

const TODAY = '2026-10-27';           // 周二
function at(hh, mm, ss, day) {
  const d = new Date(2026, 9, 27, hh, mm, ss || 0);
  if (day) d.setDate(d.getDate() + day);
  return d;
}
const liveDoc = (day) => ({
  trade_date: day, tick_date: day, asof: day + ' 10:30:05',
  meta: { trade_date: day }, all: { amv_pct: 0.5 },
  forecast: { ok: true, pred: 0.4 },
});

/** 假 api：记住每次调用；post 结果可配 */
function mkApi(opt) {
  const o = opt || {};
  const calls = { post: 0, get: 0 };
  return {
    calls: calls,
    async post(p) {
      calls.post += 1;
      calls.postPath = p;
      if (o.throw) throw new Error(o.throw);
      return o.postRes === undefined ? { ok: true } : o.postRes;
    },
    async get(p) {
      calls.get += 1;
      calls.getPath = p;
      if (o.getThrow) throw new Error(o.getThrow);
      return o.doc === undefined ? liveDoc(TODAY) : o.doc;
    },
  };
}

/** 跑 n 个 tick，间隔 stepMs（时间由 nowFn 推进，避免真 sleep） */
async function runTicks(api, opt) {
  const o = opt || {};
  let t = o.start || at(10, 0, 0);
  const notes = [];
  const got = [];
  const st = Object.assign({ alive: true, loading: false, visible: true, lv: liveDoc(TODAY) }, o.state || {});
  const r = A.createRefresher({
    api: api,
    now: () => t,
    state: () => st,
    onAmv: (d) => got.push(d),
    onNote: (x) => notes.push(x),
  });
  const res = [];
  const n = o.n === undefined ? 5 : o.n;
  for (let i = 0; i < n; i += 1) {
    res.push(await r.tick());
    t = new Date(t.getTime() + (o.stepMs === undefined ? 20000 : o.stepMs));
  }
  return { res: res, got: got, notes: notes, state: st, refresher: r };
}

export async function run() {
  const out = { cases: {} };

  // ---- ① 60s 节流：5 拍 20s = 80s → 2 次 POST（t=0 与 t=60s），不是 5 次
  {
    const api = mkApi();
    const r = await runTicks(api, { n: 5, stepMs: 20000 });
    out.cases.throttle = {
      posts: api.calls.post,
      gets: api.calls.get,
      postPath: api.calls.postPath,
      getPath: api.calls.getPath,
      whys: r.res.map((x) => x.why),
      actedFlags: r.res.map((x) => x.act),
      gotToday: r.got.length ? A.fileDay(r.got[r.got.length - 1]) : '',
      notes: r.notes.filter((x) => x !== ''),
    };
  }

  // ---- ② 非交易时段：周末 / 午休 12:00 / 收盘后 15:20 / 竞价前 09:10 → 零 POST
  {
    const cases = {};
    const scenarios = [
      ['weekend', new Date(2026, 9, 31, 10, 0, 0), 6],      // 周六
      ['sunday', new Date(2026, 10, 1, 10, 0, 0), 6],
      ['lunch', at(12, 0, 0), 6],
      ['afterClose', at(15, 20, 0), 6],
      ['preAuction', at(9, 10, 0), 6],
      ['auctionEdge1130', at(11, 30, 0), 3],
      ['afternoonOpen', at(13, 0, 0), 1],
    ];
    for (const [name, start, n] of scenarios) {
      const api = mkApi();
      const r = await runTicks(api, { start: start, n: n, stepMs: 20000 });
      cases[name] = { posts: api.calls.post, whys: Array.from(new Set(r.res.map((x) => x.why))) };
    }
    out.cases.sessions = cases;
  }

  // ---- ③ 成功 → 重新 GET，回调拿到当天那份（预测可判今日）
  {
    const api = mkApi({ postRes: { ok: true, asof: TODAY + ' 10:30:05' } });
    const r = await runTicks(api, { n: 1 });
    // 首轮：文件停在 10-23（state.lv 非今日）→ 必须触发一次（why=stale-file），不等下个 20s
    const staleApi = mkApi({ doc: liveDoc(TODAY) });
    const r2 = await runTicks(staleApi, { n: 1, state: { lv: liveDoc('2026-10-23') } });
    out.cases.success = {
      docs: r.got.length,
      day: r.got.length ? A.fileDay(r.got[0]) : '',
      notes0: r.notes[0],
      // 首轮文件非今日（10-23）→ 也要触发一次（why=stale-file），不靠「等下一个 20s」
      staleWhy: r2.res[0].why,
      stalePosts: staleApi.calls.post,
      // 文件非今日但 POST 成功 → 回填的是当天文件
      staleDocDay: r2.got.length ? A.fileDay(r2.got[0]) : '',
    };
  }

  // ---- ④ 失败：ok:false / 抛错 / GET 失败 → 明示「刷新失败」，不写实时
  {
    const f1 = mkApi({ postRes: { ok: false, status: 'timeout', detail: '刷新超时（>20s），后台仍在跑' } });
    const r1 = await runTicks(f1, { n: 2 });
    const f2 = mkApi({ throw: 'boom' });
    const r2 = await runTicks(f2, { n: 1 });
    const f3 = mkApi({ getThrow: 'nope' });
    const r3 = await runTicks(f3, { n: 1 });
    out.cases.failure = {
      okFalseNote: r1.notes.filter((x) => x)[0],
      thrownNote: r2.notes.filter((x) => x)[0],
      getFailNote: r3.notes.filter((x) => x)[0],
      // 失败也算一次节流（防失败后每 20s 重试打爆）：同一分钟第二拍不再 POST
      okFalsePosts: f1.calls.post,
      okFalseSecondWhy: r1.res.length > 1 ? r1.res[1].why : '',
      // 失败时不该把 amv 换掉
      okFalseDocs: r1.got.length,
      staleNoteStartsFail: /^刷新失败 /.test(r1.notes.filter((x) => x)[0] || ''),
    };
  }

  // ---- ⑤ 卸载 / loading / 不可见 → 不刷（防递归）
  {
    const a1 = mkApi();
    const r1 = await runTicks(a1, { n: 3, state: { alive: false } });
    const a2 = mkApi();
    const r2 = await runTicks(a2, { n: 3, state: { loading: true } });
    const a3 = mkApi();
    const r3 = await runTicks(a3, { n: 3, state: { visible: false } });
    out.cases.guards = {
      unmountedPosts: a1.calls.post,
      loadingPosts: a2.calls.post,
      hiddenPosts: a3.calls.post,
      whys: [r1.res[0].why, r2.res[0].why, r3.res[0].why],
    };
  }

  // ---- ⑥ 纯函数直测（不跑 refresher）
  {
    out.cases.plan = {
      at0930: A.inContinuousAuction(at(9, 30, 0)),
      at0929: A.inContinuousAuction(at(9, 29, 0)),
      at1130: A.inContinuousAuction(at(11, 30, 0)),
      at1129: A.inContinuousAuction(at(11, 29, 0)),
      at1300: A.inContinuousAuction(at(13, 0, 0)),
      at1500: A.inContinuousAuction(at(15, 0, 0)),
      sat: A.inContinuousAuction(new Date(2026, 9, 31, 10, 0, 0)),
      gap: A.GAP_MS,
      fileDay: A.fileDay(liveDoc('2026-10-23')),
    };
  }

  return out;
}

if (process.argv[1] && process.argv[1].endsWith('amv_refresh.mjs')) {
  const r = await run();
  console.log(JSON.stringify(r));
}
