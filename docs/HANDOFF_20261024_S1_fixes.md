# HANDOFF · 周末批次 S1 三问题修复（2026-10-24）

任务书：`docs/task_S1_fixes.md` ｜ 执行：01 ｜ 不 commit、不重启 8810

## 结论

三问题全部修复并有实测证据。**任务书对问题 3 的根因判断不完整**——补 B2 段之后
F10 仍会「经常不可用」，真触发条件见 §3.1，已在门面层一并修掉。

| # | 问题 | 状态 | 证据 |
|---|---|---|---|
| 1 | 板块详情卡点个股无反应 | 已修 | ego-browser 实测 8810/8800 两场景均弹 K 线（§1.2） |
| 2 | K 线主力动向 mf 骨架化 | 已修 | 活体 5 列与旧 8800 逐位相等（§2.3） |
| 3 | F10 的 B2 段 15 字段 null | 已修 | 活体 21 字段逐值相等 + 代理旁路（§3.3） |

自测：`python -m pytest -q` → **1243 passed, 4 skipped**（exit 0）；
`python tools\cutover_gate.py` → **PASS**（7 项全 OK，exit 0）。

## 1. 问题 1 · 板块详情卡点个股无反应

### 1.1 改动（1 处，`夏天-activity\工具\dashboard\web\app.js`）

`PANEL_ORIGIN` 从写死 `http://127.0.0.1:8800` 改为 IIFE 动态求值：

```js
const PANEL_ORIGIN = (function () {
  try {
    const ref = document.referrer || '';
    if (ref) {
      const u = new URL(ref);
      if (u.protocol === 'http:' || u.protocol === 'https:') return u.origin;
    }
  } catch (e) { /* referrer 非法 → 走保底 */ }
  return 'http://127.0.0.1:8800';
})();
```

- 只动这一处逻辑；消息形状、`IN_IFRAME` 判断、其余 6 个 `PANEL_ORIGIN` 使用点全部未改。
- `node --check` 通过（exit 0）；文件仍为 UTF-8 无 BOM、LF，CRLF=0，无换行风格漂移。

### 1.2 实测（ego-browser，真实 8810/8800 + 真实 8002）

机制对照（8810 父页 + 8002 iframe 内同时发两条同形消息，只有一条被父页收到）：

```
父页收到: [{"origin":"http://127.0.0.1:8002","target":"http://127.0.0.1:8810"}]
K线弹窗: {"display":"flex","title":"茅台 600519.SH"}
```

→ targetOrigin 写 `8800` 的那条被浏览器丢弃，写 `8810` 的那条送达并弹出 K 线。根因实锤。

端到端点击（板块详情卡 → 点成分股）：

| 场景 | iframe referrer | 点前 | 点后 |
|---|---|---|---|
| 8810 面板 | `http://127.0.0.1:8810/` | `klineModal display:none` | `display:flex`，标题「平潭发展 000592.SZ 1/29」 |
| 8800 面板（回归） | `http://127.0.0.1:8800/` | `display:none` | `display:flex`，标题同上 |

两点补充证据：卡内 iframe 的 `document.referrer` 实测就是父页面源（跨源 iframe 唯一可得，
且未被 `Referrer-Policy` 清空）；8810 下 8002 的主题回执 `__sectorThemeAck` 也已恢复
（此前同样因为 targetOrigin 错而永远到不了父页）。

## 2. 问题 2 · K 线主力动向 mf

### 2.1 白名单登记（`app/core/paths.py`）

```python
"l2_mf": LibSpec(
    path=WS / "Hubagent" / "01" / "_tools" / "l2_mf.py",
    exports=("build_mf", "DEFAULT_LOOKBACK", "LOOKBACK_MIN", "LOOKBACK_MAX"),
),
```

exports 定法（先读源码再定）：`l2_mf.py` 是「取数 + 口径」一体模块（`fetch_cah_live` 自连 7709，
`default_free_float` 走 eltdx），import 期零副作用（顶层只有常量与函数定义，AST 扫描通过，
`ctx.lib()` 加载成功）。**只导出 `build_mf` 装配入口 + 3 个边界常量**，不导出
`load_cah` / `fetch_cah_live` / `default_free_float` 等底层取数原语——插件拿不到独立取数句柄，
只能整段装配，避免出现「插件自己拼一半口径」的第二条路径。

### 2.2 接线（`app/plugins/compat_kline/backend.py` + `plugin.json`）

- `plugin.json`：`data.lib` 加 `l2_mf`。
- 新增 `build_mf(ctx, code, bars, lookback)`：调 `ctx.lib("l2_mf").build_mf(...)`，
  逐列与 bars 对齐，`source` 透传；口径库不可用/取数抛异常/返回非法 → 骨架降级 +
  `source` 如实标注（`unavailable(口径库不可用: X)` / `unavailable(取数失败: X)` /
  `unavailable(l2无覆盖)`），**不抛**。
- `mf` 只在 `period == "d"` 且 bars 非空时下发，其余（周/分钟/空窗口）为 `null`（旧端同）。
- 端点 `mf_lookback` 参数从「收了不用」改为真透传，越界夹 [1,120]，非法回默认 20。

**口径关键点（第一版写错、活体比对抓出来的）**：必须传 `closes=None` 让口径库自己读 vipdoc
**不复权**收盘（`l2_master` 口径）。第一版传了 bars 的 `c`（`/api/kline` 是 qfq 前复权），
「占流通」分母带上复权因子 → 与旧端逐位不等（实测首日 `-1.91` vs `-1.87`）。改回 None 后一致。

### 2.3 活体验收（临时实例 8819，同参 vs 8800）

```
600519.SH ?period=d&limit=5    主力资金/主买/占成交/占流通/q  120 位全部一致，source=cache
600519.SH ?period=d&limit=120  同上全部一致
000001.SZ ?period=d&limit=5    主力资金/主买/占成交/q 一致；占流通见下
```

`000001.SZ` 的「占流通」：新端 120 位全有值，旧端 120 位全 `None`。这不是新端算错——
新端值与公式逐日自洽（`zc*10*amount/(ff*close)` 用 vipdoc 不复权收盘复算，120 日全部吻合），
自由流通股本 8160565600 可取到。属**旧端缺陷**（同一 `l2_mf.default_free_float` 在新端正常，
旧 8800 进程那侧取不到），按「以新端为准」记录，未反向迁就。

## 3. 问题 3 · F10 的 B2 段

### 3.1 任务书根因之外的真触发条件（重要）

任务书判断「B2 段未实现」是对的，但**只补 B2 段，F10 仍会「经常不可用」**。实测：

- 用户会话环境有 `HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:7897`（Clash 类代理）。
- 8800 进程（`pythonw.exe engine\server.py`）环境里**没有**这两个变量 → 它调 F10 一直正常。
- 8810 由带代理的会话拉起（`run.ps1` → uvicorn），urllib 每次请求都先连 `127.0.0.1:7897`；
  代理一关就是 `WinError 10061 目标计算机积极拒绝` → B2 段 15 字段全 null。

复现与修复验证：同一脚本带代理变量跑 → `TransportError [WinError 10061]`；
清空代理变量 → `valuation ok 0.26s`；再按修复方案（把两个网关主机追加进 no_proxy）→ 仍 ok。

**这就是「经常」的来源**：代理开着时正常、关掉就全空，与「上游 HTTP 抖动」是两回事。

### 3.2 改动

**`app/core/quotes.py`（门面，新增 F10 HTTP 客户端）**

- `f10_brief(glb)`：一次取全 B2 段 9 项 —— valuation(200191)、增减持、北向、
  盈利预测、业绩历史、融资融券、季度业绩(cwfx zyzb)、股东户数序列(gdyj gdrs)、行业。
  解析函数逐字移植旧 8800 `server.py::_f10_parse_*`（字段名、单位换算、取数窗口全照搬，
  含 `AVGMVM=流通市值 / ALIQMV=总市值` 这组反直觉字段）。
- `_f10_no_proxy()`：调用期间把 `static.tdx.com.cn` / `hot.icfqs.com` 追加进
  `no_proxy/NO_PROXY`，退出还原。不改全局 socket、不改 opener、不动 `HTTP_PROXY` 本身
  （`urllib.request.getproxies()` 每次重读环境变量，追加即生效）。
  注：Windows `os.environ` 大小写不敏感，两个名字实为同一个值，测试按合并值断言。
- `F10_TTL = 60s`（任务书指定）；`_f10_call` 失败重试 **1 次**（总 2 次尝试，禁止第三次，同旧端）。
- 单项失败 → 该项空 + 记 `errors`，其余项照常；整批 `F10_BUDGET = 12s` 总预算，
  超预算的剩余项跳过；整批全空连续 2 次 → 熔断 120s（避免每次刷新白等超时）。

**`app/plugins/compat_kline/backend.py`（B2 接线）**

- 新增 `_f10_b2(ctx, glb)` → 调 `ctx.quotes.f10_brief(glb)`；门面缺失/抛异常 → 全空 + 记因，不阻塞 B1。
- `build_f10_brief` 把 B2 的 15 个差异字段（`sector/total_mcap/float_mcap/pe_ttm/pe_pct/
  pb_mrq/pb_pct/gd_plans/north/eps_forecast/perf/rzrq/perf_q/gdhs`）全部接线；
  `source` 与旧端同款 `eltdx:f10(200191)+eltdx:socket(0x0ffc)`，B2 有降级时追加 `(B2部分降级:...)`。

### 3.3 活体验收（8819 vs 8800，同参 `600519.SH`）

21 个顶层字段**逐值相等**，`key set` 相同，diff 为空：

```
sector 酿酒 | ipo_date 2001-08-27 | is_new false
total_mcap/float_mcap 1668858904576.0 | pe_ttm 20.49 | pe_pct 19.06 | pb_mrq 6.64 | pb_pct 9.65
holders 296404 | holder_report 2026-08-15
main_net_today -174426353.34 | main_ratio_today -2.272 | main_net_5d 88190554.28
gd_plans 1 条（拟增持/控股股东/完成）| north {2026-09-30, 4.42, 2.8896}
eps_forecast {2026, 67.191, 39} | perf 6 年 | rzrq 8 条 | perf_q 12 季 + years | gdhs 12 期
source: eltdx:f10(200191)+eltdx:socket(0x0ffc)（两端完全相同）
```

该实例**带着 `HTTP_PROXY=127.0.0.1:7897` 启动**（与生产 8810 同条件），B2 全字段仍取到值
→ 代理旁路在真实环境生效。

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `夏天-activity\工具\dashboard\web\app.js` | `PANEL_ORIGIN` 动态化（+11/−1 行，仅此一处逻辑） |
| `app/core/paths.py` | `READABLE_LIB` 登记 `l2_mf`（+18 行） |
| `app/core/quotes.py` | F10 HTTP 门面 `f10_brief` + 9 个解析函数 + 代理旁路 + 熔断（+约 430 行） |
| `app/plugins/compat_kline/backend.py` | `build_mf` 真实现、`mf` 接线、`_f10_b2`、B2 15 字段接线（+约 200 行） |
| `app/plugins/compat_kline/plugin.json` | `data.lib` 加 `l2_mf` |
| `app/plugins/compat_kline/tests/test_compat_kline.py` | mf 5 项 + F10 B2 3 项；`_StubMf` 替身；manifest 断言 |
| `app/tests/test_quotes_f10.py` | 新增 9 项（取值/缓存/单项降级/空表/重试/熔断/代理旁路） |
| `docs/HANDOFF_20261024_S1_fixes.md` | 本文 |

测试零网络：`l2_mf` 一律用 `_StubMf` 替身（真库会连 7709）；F10 一律用 `_FakeF10` 假客户端。

## 5. 未验证 / 遗留

- **8810 常驻进程仍在跑旧代码**（按纪律未重启）：`/api/kline/{code}` 的 `mf` 与
  `/api/f10_brief/{code}` 的 B2 段需下次重启 8810 才生效。临时实例 8819 已全量验证。
- **8002 侧改动即时生效**（静态 JS，无进程内缓存），但用户浏览器需强刷一次（Ctrl+F5）
  才会加载新 `app.js`；旧缓存的 app.js 仍会走写死 8800。
- `000001.SZ` 的「占流通」旧端全 null（旧端缺陷，见 §2.3），未纳入 shadow 判定；
  若日后要让 shadow 覆盖 mf，需为该票加 known_old_defect 白名单。
- F10 熔断阈值（连续 2 次整批空 → 停 120s）是按「代理断开」这一实测场景定的经验值，
  上游长时间故障时会让面板最多 120s 后才重试；如需更快恢复，改 `F10_SKIP_TTL`。
- 板块指数 K 线（`*.TI`）仍 400（`ths_index.db` 不在白名单），属既有边界，本次未动。

## 6. 回滚

三处独立、互不耦合，可分别回退：

1. `app.js` 的 `PANEL_ORIGIN` 恢复为 `const PANEL_ORIGIN = 'http://127.0.0.1:8800';`
2. `paths.py` 删 `l2_mf` 条目 + `plugin.json` 删 `l2_mf` + `backend.py` 的 `build_mf` 调用改回
   `_mf_skeleton()`（骨架函数仍在）
3. `quotes.py` 新增块整体删除 + `backend.py` 的 `_f10_b2` 调用删掉（`build_f10_brief` 回到只填 B1）

回退入口：HANA 文件历史（见 `OH-Works/文件恢复指引.md`）。
