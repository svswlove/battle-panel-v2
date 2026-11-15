# 交接书 · 旧版弹出 K 线移植（2026-10-27 01:40）

> 本文件给下一个会话接续用。前一会话已用掉大量上下文，以下为**当前真实状态**与**下一步**。
> 所有数字均为实测，未实测的明确标「未验证」。

---

## 0. 主人最新指令（逐字，最高优先级）

> **「最关键的是先把旧版的弹出K线做出来，剩下的可以慢慢优化」**

含义：**弹窗功能完整性 > 一切优化**。F10 提速、缓存、首屏优化等都属于「剩下的」，不要再往那边投入。
当前弹窗只是「能看」的半成品（走势 + 盘口 + F10），**旧版的下单/条件单/标记/叠加对比全部没有**。

---

## 1. 一句话现状

- **弹窗已能打开**：点卡片里的股票名 → 弹窗（左侧走势、中间五档盘口+筹码、右侧 F10），不切卡。
- **但功能只移植了约 1/3**：旧版弹窗由 4 个 JS（合计约 504 KB）构成，v2 现在只有 1 个 `overlay.js`（20.5 KB）。
- **剩下的大头是 `kline_order.js`（69 KB）**：右键下单、条件单、改撤、标记、叠加对比、仓位计算，**一行都没移植**。

---

## 2. 旧版弹窗到底有什么（对照清单）

旧版文件位于 `engine/web/`：

| 文件 | 大小 | 作用 | v2 移植状态 |
|---|---|---|---|
| `kline_lc.js` | 358 KB | 走势图主体（LightweightCharts + 各种副图/画线） | **部分**：日线/分时、蜡烛、黄白线、AMV 副图 |
| `kline_quote5.js` | 28 KB | 五档盘口卡 | **已移植**（含筹码峰） |
| `kline_f10.js` | 46 KB | F10 信息侧栏 | **已移植**（含 `fetched_at` 时间标注 + 右键刷新） |
| `kline_order.js` | 69 KB | **下单 / 条件单 / 标记 / 叠加对比** | **完全未移植** |
| 内嵌 `script#kline-app` | — | 弹窗装配、Tab 切换、尺寸、快捷键 | 部分 |

### 2.1 `kline_order.js` 的功能点（按函数名归组，来自源码）

| 组 | 函数 | 说明 |
|---|---|---|
| 右键菜单 | `openMenu` `closeMenu` `placeAt` `menuHtml` `overlayMenuHtml` `markMenuHtml` `condMenuHtml` | 图上右键出菜单，随位置/上下文变化 |
| 标记 | `marks` `saveMarks` `syncChartLines` | 手工画线标记，存 `localStorage["ko_marks"]` |
| 叠加对比 | `overlayClear` `overlayFetch` `overlayOpen` `_pctFromIntraday` | 叠加其它标的/指数做百分比对比 |
| 下单面板 | `ensurePanel` `shellHtml` `orderBodyHtml` `pendRowHtml` `syncPendRow` | 弹窗内下单面板（买卖 Tab、价格、数量、金额/仓位模式） |
| 条件单 | `condBodyHtml` `condCostText` `syncCondPreview` | 条件单表单与预览 |
| 仓位计算 | `availCash` `totalAsset` `posOf` `availQty` `posQty` `levelPrice` | 可用资金/持仓/档位价 |
| 数量记忆 | `qtyMode` `setQtyMode` `lastAmount` `saveAmount` `loadLock` `saveLock` `lastQty` `saveQty` `clampLot` | 记住上次数量与金额锁定 |
| 档位选择 | `chipOn` `clearPick` `priceNow` `quoteReady` | 点盘口档位带出价格 |

### 2.2 旧版下单依赖的接口（v2 现状）

| 旧接口 | v2 是否有 | 说明 |
|---|---|---|
| `POST /api/order/place` | **无** | 旧引擎实盘下单（QMT 通道，QMT 已停摆） |
| `POST /api/order/cancel` | **无** | 撤单 |
| `GET /api/order/list` | **无** | 委托列表 |
| `POST /api/condition/add` | **无** | 加条件单 |
| `POST /api/condition/cancel` | **无** | 撤条件单 |
| `/api/quote5/{code}` | 有（`compat_kline`） | |
| `/api/intraday/{code}` | 有（`compat_kline`） | |
| `/api/watchlist.json` | 有（`compat_watchlist`） | |

v2 侧现有的是 `app/core/paper/`（纸面交易：`broker.py` `orders.py` `service.py` `state.py`）
+ `app/plugins/paper`。**纸面 ≠ 实盘**。

**⚠ 必须先问主人一件事**：弹窗下单接到哪里？
1. 接 `paper`（纸面交易，可立即做、可回退、不涉外部系统）
2. 接实盘（QMT 已停摆，需要先恢复 QMT，风险高）
3. 先只做「标记 / 叠加对比 / 画线」，下单面板暂不做

按前一会话的经验，主人当时选的是「先移植弹窗形态，下单部分等他确认再评估」——
**所以这个问题现在仍然悬着，必须先确认再动手**。

---

## 3. 前一会话已交付（不要重做）

### 3.1 旧端口退役（8800 / 8801 / 8002 全部停掉）
明细：`projects/panel-v2/docs/DONE_20261026_old_ports_retire.md`
- `watchdog` 拆掉三个端口的探活与自愈；`run_daemons` 撤掉 server / amv_dashboard / shell
- `ops_status.json` 生产者从 8800 换到 `postclose_chain` 的 `opssnap` 步
- 新增 `ops_snapshot` 动作，`/api/ops.json?refresh=1` 真实重算（实测 5.3s）
- `cutover_gate` 的 shadow 双跑改成「结构基线 + 文件↔端点比对」，35 用例 0 漂移
- 清掉 core / v2bridge 的 8800 旧代码；`sector_restart` 返回 410（退役）
- 桌面 `作战面板.cmd` 与 `拉起引擎.cmd` 指向 8810

### 3.2 首页切到 v2（`/` 不再返回旧页）
明细：`projects/panel-v2/docs/DONE_20261026_home_to_v2.md`
- `main.py::index()` 返回 v2 壳；旧页保留在 `/legacy/`（回滚入口）
- 删除 `v2bridge.js` / `v2_map.json` 与整套注入机制
- 9 张导航卡端口审计通过，83 请求全 8810

### 3.3 侧边栏按旧版 9 个 Tab 重排
- `app/web/shell.js`：`NAV_CARDS` 表（card key + 旧版中文名 + 图标），图标从
  `engine/web/index.html:8548` 的旧版 `RAIL_ICONS` 逐字节复制
- 侧栏只留旧版 9 个页面；K 线 / 活跃市值 / HCL / 纸面 4 张卡不在导航位（与旧版一致：K 线是弹窗）

### 3.4 K 线弹窗（**半成品，本次待续**）
- 新增 `app/plugins/kline/frontend/overlay.js`（20.5 KB），由 `openKlineStandalone` 打开
- 左侧走势（日线/分时切换，**补了蜡烛图**——v2 的 `drawDaily` 原本只画收盘折线不画蜡烛）
- 中间五档盘口 + 筹码峰
- 右侧 F10（轻量首屏 + 后台补完整段 + `fetched_at` 时间标注 + 右键刷新）
- 遮罩 `rgba(5,8,15,.78)` 照抄旧版

### 3.5 F10 缓存与提速（属于「剩下的」，已做完，勿再投入）
明细：`projects/panel-v2/docs/DONE_20261027_f10_speed_firstpaint.md`
- 磁盘缓存：进程重启后首次 94ms（改前 7000ms+），B2 读盘 1ms
- `F10_HTTP_TIMEOUT` 8.0 → 2.5
- endpoint 探测选优（IP 直连 / 域名，结果落盘 `data/cache/f10_endpoint.json`）
- 轻量首屏 `GET /api/f10_brief/{code}/quick`：冷查 203–360ms，命中缓存 16–31ms
- **弹窗实测：78ms 打开 → 353ms 首屏出估值 → 5797ms 完整段补齐**
- `hot.icfqs.com` 不能当主用（9 段里 7 段 503，只服务涨停梯队）

---

## 4. 关键坑（一定会踩，先读）

### 4.1 F10 的死锁事故（前一会话最大教训）
`app/core/quotes.py` 的 `_F10_LOCK` **必须是 `threading.RLock`**，不能是 `Lock`。
调用链是嵌套拿锁的：`_f10_no_proxy()`（拿锁，需在整个调用期间持有）→ `_f10_call()` → `_f10_client()`（再拿锁）。
用普通 `Lock` 会自锁死，**表象是「网络间歇超时」**，前一会话查了两小时才定位。
已立守卫 `app/core/tests/test_f10_lock.py`（3 条，带 3 秒看门狗线程）。

### 4.2 12 条 intraday 对齐测试是**既有失败**，不是本批引入
`app/plugins/intraday/tests/test_audit.py` / `test_evo.py` / `test_quotes.py` / `test_research.py`
共 12 条，报 `node 执行失败`——`audit_align.mjs` 硬编码旧页行号，主题改动插行后偏移不一致，
**不能靠单一固定差值修复**。
当前全量基线：**12 failed / 1473 passed / 4 skipped**，12 条全部在此列（纯既有失败，无本批引入）。

### 4.3 node 不在测试子进程的 PATH
`node` 在 `C:\Program Files\nodejs\node.exe`（`Get-Command node` 能找到，但 pytest 子进程里失败）。
手动跑 `.js` 校验一律用绝对路径 `& "C:\Program Files\nodejs\node.exe"`。

### 4.4 没有 playwright
`require('playwright')` 会 `MODULE_NOT_FOUND`。浏览器验证一律用 **Chrome CDP**，
骨架抄 `Hubagent/01/_vis/_klm_verify.js` 或 `_klm_quickpaint.js`（spawn Chrome + `--remote-debugging-port` + WebSocket）。
**弹窗不能用 `window.__HANAKO_PLUGINS__` 打开**（不存在），必须动态 import：
```js
var m = await import('/plugins/kline/frontend.js?v=test');
var api = { get: function(p){ return fetch('/api/p/kline'+p,{cache:'no-store'}).then(...) } };
await m.openKlineStandalone({api:api, code:'600036.SH', name:'招商银行'});
```

### 4.5 服务启动只能用 `tools\ensure_8810.ps1`
不要手搓 uvicorn 命令（前一会话踩过，`app.main` 不存在，真实入口是 `app.core.main:app`）。
重启：先 `Stop-Process` 掉 8810 监听进程，再 `powershell -NoProfile -ExecutionPolicy Bypass -File "tools\ensure_8810.ps1"`。

### 4.6 大文件是 GBK
`engine/tests/test_extreme_drill_d.py` 等旧文件是 GBK，`edit` 工具直接匹配会失败，要用 Python 按原始编码读写。

---

## 5. 下一步（按主人指令排序）

### P0 · 先确认一件事（阻塞后续）
**弹窗下单接到哪里？** 纸面（paper）/ 实盘（需先恢复 QMT）/ 先不做下单只做其它。
这个问题不问清楚，`kline_order.js` 的 69 KB 无法开工。

### P1 · 弹窗功能对齐（不依赖上面的答案，现在就能做）
1. **右键菜单框架**：图上右键出菜单（`openMenu`/`placeAt`/`closeMenu` 那套交互）
2. **标记 / 画线**：`marks` / `saveMarks` / `syncChartLines`，存 `localStorage`
3. **叠加对比**：`overlayOpen` / `overlayFetch` / `_pctFromIntraday`（多标的百分比对比）
4. **弹窗装配补齐**：Tab 切换、尺寸记忆、快捷键（`onKey` 现在只有 Esc 关闭）
5. **走势图副图**：旧版 `kline_lc.js` 里还有哪些副图/画线工具，逐项对照补齐

### P2 · 下单面板（等 P0 答案）
- 面板 UI（`shellHtml` / `orderBodyHtml` / `pendRowHtml`）
- 仓位与数量（`availCash` / `qtyMode` / `clampLot` / 数量记忆）
- 条件单（`condBodyHtml` / `syncCondPreview` / `condCostText`）
- 委托列表与改撤

---

## 6. 环境与常用命令

```powershell
# 项目根
OH-WorkSpace\projects\panel-v2

# Python（唯一）
OH-WorkSpace\envs\qmt312\Scripts\python.exe

# node
C:\Program Files\nodejs\node.exe

# 起/修 8810
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\ensure_8810.ps1"

# 单套件测试
& $py -m pytest app\plugins\compat_kline -q --no-header -p no:cacheprovider

# 全量（约 90 秒）
& $py -m pytest app -q --no-header -p no:cacheprovider --tb=no

# 语法检查
& "C:\Program Files\nodejs\node.exe" --check app\plugins\kline\frontend\overlay.js
```

---

## 7. 关键文件清单

| 文件 | 作用 |
|---|---|
| `app/plugins/kline/frontend/overlay.js` | **弹窗主体（本次主战场）** |
| `app/plugins/kline/frontend.js` | 插件入口，导出 `openKlineStandalone` |
| `app/plugins/kline/frontend/*.js` | 走势图绘制（`drawDaily` / `drawIntraday` / `drawChips` 等） |
| `app/plugins/compat_kline/backend.py` | 弹窗全部数据端点（9 个，含 `/quick`） |
| `app/core/quotes.py` | 行情门面 + F10 客户端与缓存（**锁是 RLock，别改**） |
| `app/web/shell.js` | v2 壳：导航、卡片路由、主题 |
| `engine/web/index.html` | **旧页（唯一权威参照）**，行 8548 是旧版 `RAIL_ICONS` |
| `engine/web/kline_lc.js` | 旧版走势图（358 KB，对照用） |
| `engine/web/kline_order.js` | **旧版下单（69 KB，待移植）** |
| `engine/web/kline_quote5.js` | 旧版盘口（对照用） |
| `engine/web/kline_f10.js` | 旧版 F10（对照用） |
| `Hubagent/01/_vis/_klm_verify.js` | CDP 弹窗验证骨架（可抄） |
| `Hubagent/01/_vis/_klm_quickpaint.js` | CDP 首屏计时验证（可抄） |

---

## 8. 未验证 / 遗留

- [ ] 弹窗在**非交易时段**（当前 01:40）的走势是否与旧版一致 —— 只验过 600036 分时与日线静态形态
- [ ] 弹窗右键刷新 F10 的「N 小时前 / N 天前」长尾文案未实测（只验过「刚刚 / N 分钟前」）
- [ ] `test_panic_store_mode_shape` 断言未更新（eltdx 迁移遗留）——注意：本轮全量为 12 failed，
      该条已不在失败列表内（可能先前已被他人修好或环境差异），接入新会话时以实跑为准
- [ ] 12 条 intraday 行号对齐失败（需重写 `audit_align.mjs` 的定位方式）
- [ ] `engine/state/manual_lock.json` 曾被全量测试写入一条 `000001.SZ / PP20261026-0003`
      （21:34:48，测试污染生产状态），仅当日有效，前一会话未清 —— 如已过期可忽略
- [ ] 交易日 09:00–15:30 需确认 `cond_watch` 已被拉起（未起来不报错也不推送）
