# 前端批次 2a 交接：battle_plan 卡升级（签收 + 仓位门）

> 任务书：`docs/task_fe_batch2a.md`｜派单：01 → DSHana｜2026-10-24
> 行为基准：`engine/web/index.html` `renderBattlePlan`（3533-3653）+ 签收交互（8721-8738）+ `MODULE_META`（8740-8749）
> 状态：**代码与静态检查已落盘**；`pytest` / `cutover_gate` / 浏览器截图**未跑** —— 本执行手沙箱禁止创建任何子进程
> （`spawn python EPERM` / `spawn cmd.exe EPERM`，实测见 §5.1），8810 也**未重启**（manifest 版本仍是 0.2.0，见 §6）。

---

## 1. 文件清单（严格在白名单内）

| 文件 | 动作 | 行数/大小 |
|---|---|---|
| `app/plugins/battle_plan/frontend.js` | 重写（99 → 663 行） | 36,174 B |
| `app/plugins/battle_plan/plugin.json` | 只改 `version` 0.2.0 → 0.3.0 | 593 B |
| `app/plugins/battle_plan/tests/test_battle_plan_frontend.py` | **新建**（31 条静态检查） | 14,034 B |
| `docs/battle_plan_contract.md` | 追加「卡片与签收交互（v0.3.0）」一节 + 抬头版本 | 3,497 B |
| `docs/HANDOFF_20261024_fe_batch2a.md` | 新建（本篇） | — |

**未改**（mtime 取证）：`backend.py`（10-22 15:22）、`test_battle_plan_contract.py`（10-22 15:23）、
`app/core/main.py`（10-23 18:24）、`app/web/shell.js`（10-23 18:16）、`engine/web/index.html`（10-23 16:51）、
`data/panel/battle_plan.json`（10-23 17:55，签收探针后未变）。**未 commit**。

## 2. 逐项对任务书 §2

| # | 要求 | 落点 | 备注 |
|---|---|---|---|
| 1 | 卡头 `plan_date · plan_version · 签收状态` | `render()` 的 `badgeSign` + `sub` | 文案逐字照旧 3556：`✔ 已全部签收` / `待签收 N/M` + ` @signed_at` |
| 2 | 段落顺序 | `wrap.append(...)` | meta → buy_plan → operations → watch_levels → risk_warnings → pending_todos → check_log → signal_watch → module_switches → 持仓账户（测试锁死该顺序） |
| 3 | meta 卡内容 | `metaHtml()` | 签收进度 / 引擎状态（已签收·可下单 vs 只读）/ 仓位口径徽标 paper-live / 总仓上限 + 状态流转说明；planSigned 时多一行签收时间与签收人 |
| 4 | buy_plan 预案表 + 逐项签收 | `buyPlanHtml()` | 9 列与旧版同（状态/预案ID/标的/进程描述/触发条件/止损参考/仓位金额（纸面）/来源/逐项签收）；已签项显示 `✔ 已签收` 而非按钮；`bpProcess()` 照旧 3036 复刻（含分批笔数） |
| 5 | 逐项签收 body | `signOne()` | `api.post('/api/sign', { signer: SIGNER, plan_id: planId })`，`SIGNER='主人'` |
| 6 | 一键全部 + allSigned disabled | `signAll()` / `metaHtml()` | `{signer:'主人', all:true}`；先 `confirm`（文案同旧 8731）；`allSigned` 时按钮 disabled 并显示 `✔ 已全部签收` |
| 7 | 以回执为准重拉（不乐观更新） | `signOne`/`signAll` | 成功后清防闪缓存 + `await load()`（重拉 `/summary`+`/plan`）；**不做任何本地状态改写** |
| 8 | 失败原样显示 | `signFail()` + `setStatus()` + `showToast()` | 判定用 `res.ok === true`：core 代理在 8800 不可达时返回 **HTTP 200 + `{ok:false, error:'panel_down'}`**，只看状态码会误判成功；行内红条 + toast 原样带 `detail`/`error` |
| 9 | stale 提示保留 | `render()` 的 `narrative` | `summary.narrative_warning` 显示在卡片顶部（实测有值：`AMV +0.18%` 旧值命中） |
| 10 | 刷新 | 卡头「刷新」按钮 + `refresh()` 占位 | **不声明 `refreshEvery`**（任务书给的第一个选项）；`refresh()` 仍保留契约占位且带 `alive/loading` 守卫 |
| 11 | 择机单终态折叠 | `signalWatchHtml()` | `SW_TERMINAL` 六个状态**逐字照旧 3538**（测试锁死）；在场单出表，终态只出「另有 N 单历史择机单」 |
| 12 | module_switches 只读 | `switchesHtml()` | 八个模块徽标（`MODULE_META` 顺序/文案照旧 8740）+ 缺键视为启用（`sw[k] !== false`）+ exit_stop 关闭的强警示；**无编辑、无批量提交**；未知键（实测有 `vol_ratio_buy`）按原键名追加展示，不吞 |
| 13 | 仓位口径徽标 | `TRADE_MODE = 'paper'` 常量 | 按任务书 §4 选「paper 常量」，**不请求 `/api/order/list`**（少一个请求）；`title` 写明「仓位/金额按纸面账本总资产计」 |

补充搬运（旧版有、任务书未点名但属同一张卡）：持仓股操作表（`opTag`/`opProcess`）、盯盘价位卡、
风险提示卡（level 左描边 + 应对）、待办清单、盘中核对表、实盘持仓/账户快照（summary 侧数据）。
**未搬**：逐条启停/取消按钮（写操作，仍属旧 8800）、持仓股操作表的启停列、通知类型面板、手动仓位滑块。

## 3. 数据源与后端边界

| 用途 | 端点 | 提供方 |
|---|---|---|
| 汇总（账户/持仓/告警计数/stale 提示） | `GET /api/p/battle_plan/summary` | battle_plan 插件（既有） |
| 原始作战表（buy_plan/operations/watch_levels/risk/pending_todos/check_log/signal_watch/module_switches） | `GET /api/p/battle_plan/plan` | battle_plan 插件（既有） |
| 签收 | `POST /api/sign` | **core 代理**（`app/core/main.py:396`）→ 8800 `/api/sign` |

后端零改动、零新增路由；`data.write` 仍为空；本插件不写 `data/panel/`。

## 4. 与旧版的差异（如实登记）

1. **模块开关不可编辑**（任务书 §2 明示只读）：旧版有锁/待确认/dirty/批量提交一整套，本卡只渲染开关徽标。
   要改开关仍回旧 8800。
2. **逐条启停 / 取消按钮未搬**：任务书 §2 段落清单里没有，且属写操作（`/api/toggle`、`/api/cancel`、
   `/api/signal_watch/*`）。本卡 `api.post` 只有 `/api/sign` 一处（测试锁死）。
3. **trade_mode 走 paper 常量**（任务书 §4 授权）：实测 8800 `/api/status.trade_mode = 'paper'`，与常量一致。
   日后若真实盘，改 `frontend.js` 的 `TRADE_MODE` 一处即可。
4. **不声明 `refreshEvery`**：旧版 3s 轮询是「值变才重渲」，本卡数据日频，按任务书选项一，签收后手动刷新。
5. **无 K 线点击跳转**：旧版标的名称/代码可点开 K 线弹窗（`kName`）；v2 的 K 线入参契约在批次 1c，本批不出可点样式（不伪装可点）。
6. **待办无勾选**：旧版 `toggleTodo` 用 localStorage 做纯展示勾选；本卡只列清单 + 状态，不引入第二份本地状态。
7. **无「临近触发」「长期预警」两张子卡**：任务书 §2 段落清单未列，属批次 2 之后。

## 5. 自测状态

### 5.1 未跑（沙箱限制，必须由 01 补跑）

本执行手沙箱对**任何**子进程返回 EPERM，实测：

```
spawnSync python -c "print(1)"  -> status=null err="spawnSync python EPERM"
spawnSync cmd /c echo hi        -> err="spawnSync cmd.exe EPERM"
spawnSync node -e "console.log(1)" -> err="spawnSync node EPERM"
```

申请提权（danger-full-access）被拒。故以下三条**未跑、无退出码**，请 01 在正常 shell 里补：

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/battle_plan/tests app/core/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

### 5.2 已做的等价验证（不依赖子进程）

**(a) 模块可真实 import + 卡片形状**：把 `frontend.js` 以 `data:text/javascript;base64` 动态 import，
导出 `default.cards` 单卡：`id=battle_plan.main` / `slot=main` / `order=40` / `refreshEvery=undefined` /
`mount·unmount·refresh` 均为 function；CSS 注入 id `battle-plan-card-css`（5,652 B）一次成功。

**(b) 真实 8810 数据 + DOM shim 端到端渲染**（不是 mock 数据）：`mount(root, ctx)` 走真 `fetch` 打到
`http://127.0.0.1:8810/api/p/battle_plan/{summary,plan}`，渲染文本 15,073 字符，逐段命中：
签收进度/引擎状态/仓位口径/总仓上限（¥142,079.01）/全部签收/待买入预案/持仓股操作/盯盘价位/
风险提示/待办/盘中核对/择机单/模块开关/持仓与账户/数据源。
模块开关实测渲染：买入·开 止损·开 止盈·开 **做T·关** 推送·开 仓位·开 唤醒·开 预警·开 **vol_ratio_buy·开**（未知键不吞）。
stale 提示实测可见：`作战表内 AMV 文字与当前 regime 不一致（检测到旧值：AMV +0.18%）…`。

**(c) 签收交互闭环**（`/api/sign` 用桩拦截，**绝不真签**，避免写生产 `battle_plan.json`）：

| 场景 | 断言 | 结果 |
|---|---|---|
| 逐项签收 | 请求 body 逐字 = `{"signer":"主人","plan_id":"BP-TEST"}` | **PASS** |
| 逐项成功后 | 新增 2 次 GET（重拉 summary+plan），toast「已签收…（以服务端回执为准，已重拉）」 | **PASS** |
| 一键全部 | body = `{"signer":"主人","all":true}`；confirm 文案与旧 8731 同义 | **PASS** |
| 一键成功 | toast「全部签收成功，引擎状态：已签收·可下单」 | **PASS** |
| 失败回执（200+`panel_down`） | 行内红条 `签收失败（BP-TEST）：引擎服务未启动，请先点「重启服务」`；**不重拉** | **PASS** |
| 4xx 抛错 | 行内红条 `签收失败（BP-X）：POST /api/sign -> 404: 预案不存在：BP-X` | **PASS** |
| confirm 取消 | POST 次数 0 | **PASS** |
| 防闪 | 数据不变时连续 `refresh()` → innerHTML 写入 **0** 次 | **PASS** |
| unmount 后 | `refresh()` 请求数 **0** | **PASS** |

**(d) 合成数据渲染**（覆盖真实数据没有的分支：未签预案、终态择机单、exit_stop 关闭、空态）：
未签 2 条 → 卡头 `签收：待签收 1/2`（warn 色）、BP-1 出「逐项签收」按钮、BP-2 出「✔ 已签收」；
择机单 3 条（1 在场 2 终态）→ 只出 1 行表 + 「另有 2 单历史择机单」；`exit_stop:false` → 强警示
「⚠ 止损已关，风控失效！…」；空态逐段「（空）」「（无待办）」「无择机单…」。
全部签收后卡头变 `签收：✔ 已全部签收 @2026-10-23 04:30:00`。

**(e) 33 条 pytest 断言逐条预演**（用等价 JS 正则/字符串判定，逐条对同一份 `frontend.js`）：
**32/32 通过**（`test_* ` 共 31 条 + manifest 断言）。唯一预演差异是我自己写的辅助检查「文件里不得出现 `#`」——
实际 `#` 只出现在 `esc()` 的 `'&#39;'` 实体里，不属于裸 hex，正式断言 `#[0-9a-fA-F]{3,8}\b` **零命中**。

**(f) cutover_gate 的 8 项里可走 HTTP / 纯文件读的 5 项**（照抄 gate 判定逻辑）。
> 注意：`docs/cutover_gate_latest.md` 现存记录是 **2026-10-24 03:07:26 PASS（385 passed, 1 skipped）**，
> 早于本次改动（frontend.js 03:15:23），**不覆盖本批**；下表是我在改动后逐项复算的结果。

| gate 项 | 结果 | 证据 |
|---|---|---|
| 8810 `/api/health` | **PASS** | HTTP 200 ok=true（core 0.3.1） |
| 全部插件健康 | **PASS** | HTTP 200 n=14 bad=[] |
| shadow 双跑 | **未跑** | 需子进程 |
| AMV 原始 JSON 比对 | **PASS**（等价复算） | amv_live / amv_regime_latest / position_cap_latest 三对 **equal=true** |
| battle_plan 无写接口 | **PASS** | POST /api/p/battle_plan/plan → HTTP 405 |
| 插件无写代码 | **PASS** | 静态扫描 27 个 .py/.js（排除 tests，与历史 gate 同口径同数量）→ clean，含新 frontend.js |
| kline 边界声明 | **PASS** | HTTP 200 intraday=False last=20261023 |
| pytest 全套 | **未跑** | 需子进程 |

**(g) 与旧 8800 的同源取证**（截图替代证据，说明两卡数据面完全一致）：

- `8800 /api/battle_plan.json` ≡ `8810 /api/p/battle_plan/plan` → **equal=True**（19 键）
- `8800 /api/alert_todo.json` ≡ `8810 /api/p/battle_plan/alerts` → **equal=True**（7 键）
- 段落条数（两侧同源）：buy_plan 0 / operations 1 / watch_levels 4 / risk_warnings 14 / pending_todos 22 / check_log 0 / signal_watch.orders 0

### 5.3 未做的浏览器实测（诚实标注）

- **无 CDP 端口**：9222 / 9223 / 9333 / 9515 / 4444 全部 closed，`DevToolsActivePort` 三处均不存在。
- **无 playwright/puppeteer**：`node_modules` 内零匹配，`tools/` 无截图脚本。
- **无法自起浏览器**：`spawn EPERM`（见 5.1）。
- 结论：**同屏对照截图 2 张、控制台 0 报错、真实签收冒烟均未做**。已用 5.2(b)(c) 的 DOM shim + 真实 HTTP
  渲染与 5.2(g) 的数据同源比对替代；截图请 01 在浏览器里补。

**关于「只签一条已 SIGNED 不产生变更」**：当前 `buy_plan` 为 **0 条**，无已签条目可选；真签会改
`battle_plan.json` 的 `sign` 段（生产文件），故**未真签**。已做的最接近探针是
`POST /api/sign {signer:'主人', plan_id:'BP-NOT-EXIST-PROBE-2a'}` → 8800 在写盘前抛 404，
回执 `{"detail":"预案不存在：…","ok":false,"engine_error":true,"http_status":404}`，
**证明代理链路通、且未落盘**（`battle_plan.json` mtime 探针前后不变：2026-10-23T17:55:52Z）。

## 6. 上线前置（01 必做）

1. **重启 8810**：`/_plugins` 目前仍报 `battle_plan 0.2.0`（manifest 在进程启动时缓存，
   `_load_all` 只跑一次）。不重启则 shell 的 import URL 仍是 `?v=0.2.0`。
   前端资源本身已实时从磁盘出：`GET /plugins/battle_plan/frontend.js` 返回 36,174 B、
   与本地文件**逐字节相同**、`last-modified` 已更新到 03:15:23、ETag 已变。
2. 跑 §5.1 三条命令并贴退出码。
3. 浏览器补：8810 作战表卡 / 8800 作战表 Tab 同屏截图 ×2 + 控制台 0 报错。
   真签冒烟建议：等出现 `buy_plan` 且有一条**已 SIGNED** 时，点该条以外的未签条 → 观察
   8800 侧变 SIGNED、8810 重拉同步；或直接按 §5.3 的 404 探针口径验「请求发出 + 回执渲染」。

## 7. 遗留 / 下一批

- 逐条启停、取消（二次确认）、模块开关编辑、通知类型面板、手动仓位滑块 —— 全属写操作，仍在旧 8800。
- K 线点击跳转（`data-open-kline`）待批次 1c 统一接。
- 「临近触发」「长期预警」两张子卡未搬。
- 待办勾选（旧版 localStorage 纯展示）未搬，避免第二份本地状态。
- 若日后要按真实 `trade_mode` 渲染，改 `frontend.js` 的 `TRADE_MODE` 或补一次 `/api/status` 请求。
