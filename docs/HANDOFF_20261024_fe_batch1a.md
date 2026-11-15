# 前端批次 1a 交接：watchlist 卡升级到旧 Tab 功能对等（2026-10-24）

> 任务书：`docs/task_fe_batch1a.md`｜路线图：`docs/FRONTEND_ROADMAP.md` 批次 1.1
> 行为基准：`app/web/legacy/index.html` 的 `#panel-watchlist`（5550-6024 + 拖拽 5916-5976）
> 状态：**代码已落盘，静态检查与真实数据行为验证已跑；pytest / 8810 重启 / CDP 截图未跑**（本执行手沙箱禁止创建子进程，见 §5）

---

## 1. 做了什么

| # | 任务书功能 | 落点（frontend.js） | 摘要 |
|---|---|---|---|
| 1 | 分组条 | `groupBarHtml` / `startRename` / `deleteGroup` / `addGroup` | 个股组 + `＋` + 分隔 + 板块组；当前组高亮；默认组无改名/删除；双击改名（内联 input）、右键或长按 600ms 删除；+ → `/group/add` |
| 2 | 盘后/实时 | `readMode`/`writeMode`/`switchMode`/`renderMode` | 药丸开关，状态存 `localStorage.wl_mode_v1`；实时档请求 `/api/watchlist.json?mode=live` |
| 3 | 卡片网格 | `stockCardHtml`/`sectorCardHtml` | 代码+名称（可点）/徽标前 2/现价/涨跌幅/主图 SVG/副图/四态点/板块 chip/5日背离/主力净流入 + **保留关键位列** |
| 4 | 涨跌配色 | CSS + `signCls` | 全部走 `var(--up)`/`var(--down)`/`var(--warn)`/`var(--dim)`/`var(--accent)`，**本文件零裸 hex** |
| 5 | 拖拽排序 | `dragend` 处理器 | 组内拖动即时重排 DOM，dragend → `POST /watchlist/reorder {codes, group}`；拖动中不重建 |
| 6 | 拖拽换组 | `dragover`/`drop` 处理器 | 落到同类型分组标签 → `/watchlist/move`；跨类型（stock↔sector）`dragover` 不 preventDefault + drop 直接拒绝 |
| 7 | 搜索添加 | `runSearch`/`renderDrop`/`addItem` | 200ms 防抖 → `POST /watchlist/search` → 下拉；点结果 → `/watchlist/add`；搜索中不重建 |
| 8 | 移除 | `removeItem` | 卡片右上 ✕ → `/watchlist/remove`，不弹确认（按任务书） |
| 9 | 板块卡 | `sectorCardHtml`/`paintSectorEod`/`paintSectorLive` | 板块名/多空 chip/level/走势+AMV 双图/涨幅·主力·AMV·权重两行 |
| 10 | 刷新 | `refreshEvery: 5000` + `refresh()` | **三种跳过**：① 拖拽中 ② 分组名编辑中 ③ 搜索框聚焦或非空 |

### 绘图数据源（客户端零业务计算，只画后端算好的序列）

| 图 | 端点 | 说明 |
|---|---|---|
| 个股主图（盘后） | `GET /api/kline/{code}?period=d&limit=260` | `bars[]` 的 `c/yellow/white`；取末 45 根，三条共用归一化区间（同旧 Tab sparkPath） |
| 个股副图（盘后） | 同上 | `c` 与 `amv` 各自除以首值×100 后归一（同旧 Tab） |
| 个股主/副图（实时） | `GET /api/intraday/{code}` | `points[].close/avg/volume`；走势线+均价线+量柱；并发上限 4 + 序号作废旧回调 |
| 板块双图（盘后） | `GET /api/sector_card.json?level=&days=60&win=5&codes=` | 后端已拼好的 SVG path（`close.c/yl/wh`、`amv.a/ma`）+ `real_pct/main_net/amv_pct/w_delta` |
| 板块当日轨迹 | `GET /api/sector_intraday.json?codes=` | `amv_pct` 轨迹线 + 客户端累计均值（均价线语义，同旧 Tab） |

> **与旧 Tab 的取数差异（如实登记）**：旧版个股走势走 `/api/kline/{code}`（compat 根路径），本卡走同一形状的
> 兼容端点（`/api/kline/`）。旧版的 `watchSeries` 在浏览器里跑，本卡把同一套 sparkPath/归一逻辑照搬，
> 数值口径不变；板块双图直接消费 `sector_card.json` 的后端 path，客户端不重算。

## 2. 与旧 Tab 的差异（逐条，不隐藏）

1. **搜索结果点击 = 直接加入自选池**（任务书 §功能 7 明确要求）。旧版是打开 K 线/板块详情，再点详情里的「☆ 自选」。
2. **移除不弹 confirm**（任务书 §功能 8 口径「旧版无确认，保持一致不弹」）。⚠ 实测旧版 `watchRemove` **是有 confirm 的**（legacy:5568）。按任务书为准，但这条口径与旧版实际不一致，验收时请确认取舍。
3. **分组改名用组内联 input**（旧版用 `prompt`）。原因：需要一个真实的「编辑中」状态给 refresh 的三种跳过条件用；`prompt` 是阻塞式，轮询会被浏览器自然挂起但状态不可观测。新建分组仍用 `prompt`（无现成输入点）。
4. **分组长按 600ms 也可删除**（旧版只有右键）。触摸屏无右键，补一条等价路径；右键仍保留。
5. **K 线/板块详情不弹窗**（任务书 §不做）：名称/代码/板块 chip 只派发 `CustomEvent('panel:open-kline'|'panel:open-sector')`，无监听方时 `console.debug`。
6. 旧版顶部标题里的「行情 asof / 只读」改为卡内 `wl-meta` 一行（共 N（股 x / 板块 y）· 行情日期 · 档位），信息不丢。
7. 旧版的 `.src` 长说明保留为卡底 `wl-src`。

## 3. shell.js 的唯一改动（任务书 §契约 1 选项 ①）

`app/web/shell.js` 的 `makeCtx` 里，`api` 对象**新增 `async post(path, body, init)`**（24 行，与 `get` 同构）：

- 绝对路径（`http` / `/api/` 开头）不拼插件前缀，其余拼 `/api/p/<id>`；
- body 走 JSON（`Content-Type: application/json`）；
- 非 2xx 抛错，`Error.message` 带 `POST <url> -> <status>: <detail>`，并挂 `e.status` / `e.payload`。

**未改动 shell.js 其他任何逻辑**（diff 只有这一个方法块）。写端点全部用绝对 `/api/watchlist/*`，直接消费 `compat_watchlist` 既有 8 条写路由，**后端零改动**。

## 4. 文件清单

```
app/plugins/watchlist/frontend.js                    改（整体重写：122 → 1252 行）
app/plugins/watchlist/tests/test_watchlist_frontend.py  新（143 行，12 条静态检查）
app/web/shell.js                                     改（+24 行：api.post；其余零改动）
docs/HANDOFF_20261024_fe_batch1a.md                   新（本篇）
```

未动：`app/web/legacy/`、`engine/`、`app/plugins/watchlist/backend.py`、`app/plugins/compat_watchlist/`、其他插件、任何依赖、`plugin.json`（卡片声明无需调整）。

## 5. 自测状态

### 已跑（本执行手）

| 项 | 结果 |
|---|---|
| `frontend.js` 模块语法 | **动态 import 通过**（`export default` → `cards[0].id = watchlist.main`，`refreshEvery = 5000`） |
| 裸 hex 扫描 | 0 处（`#[0-9a-fA-F]{3,8}` 无命中） |
| `tools/cutover_gate.py` banned tokens | 0 命中；无 `open(...,'w')`；**零 import**；无 `fetch(` 裸调用；无全局变量赋值 |
| 12 条静态测试断言 | 逐条脚本预演**全 PASS**（真实 pytest 未跑，见下） |
| 真实数据行为验证（DOM shim + 真实 8810 HTTP） | **30/30 PASS**，明细见 §7 |

### ⚠ 未跑（沙箱限制：`spawnSync` 对任何可执行文件一律 EPERM，已实测并确认）

`node --input-type=module --check` **无法执行**（需子进程）。改用**等价且更强**的验证：在 Node 里真实 `import()` 该 ES module——语法错误会直接抛，且顺带校验了导出形状。shell.js 因顶层访问 `document` 无法 import，改为逐行回读 + 正则确认 `post` 方法完整。

请派活方跑：

```powershell
cd OH-WorkSpace\projects\panel-v2
$py = OH-WorkSpace\envs\qmt312\Scripts\python.exe

# 1) 本插件测试
& $py -m pytest app/plugins/watchlist/tests -q
# 2) 全套（基线 721 passed）
& $py -m pytest -q
# 3) 语法（本执行手跑不了的那条）
node --input-type=module --check app/plugins/watchlist/frontend.js
node --input-type=module --check app/web/shell.js
# 4) 门禁
& $py tools/cutover_gate.py
# 5) 重启 8810（frontend.js 由浏览器拉取，**改完不必重启**；shell.js 同理。
#    只有改了 backend.py/plugin.json 才要重启）
# 6) CDP 实测：打开 http://127.0.0.1:8810/ → 点 rail「自选池」
```

> **无需重启 8810**：本批只改前端 `.js`（浏览器按 `?v=<version>` 拉取）。`plugin.json` 的 version 仍为 0.3.1，
> 若验收时浏览器命中旧缓存，硬刷新一次即可；把 version 提到 0.3.2 可让所有客户端自动拉新（本批未改，避免越界）。

## 6. CDP 断言清单（给验收人，逐条对应任务书 §自测 4）

打开 `http://127.0.0.1:8810/` → 点 rail「自选池」后断言：

| 断言 | 期望 | 选择器 |
|---|---|---|
| 分组标签数 | **5**（自选/追踪/走坏/权重/板块） | `.wl-group` |
| 当前组卡片数 | **12**（stock_default） | `.wl-wc` |
| 盘后/实时开关可切 | 点后加 `.is-live`，网络出现 `?mode=live` | `.wl-sw` |
| 搜索「银行」有结果 | ≥1 条，含平安银行 | `.wl-drop .wl-sr` |
| 控制台 0 错误 | — | — |
| 截图 | `docs/fe_batch1a_watchlist.png` | — |

参考实现：`engine/tests/panel/_fe_batch0_cdp.js`（该文件在本工作区不存在，批次 0 交接里提到的路径需确认）。

## 7. 真实数据行为验证明细（DOM shim + 真实 8810 HTTP，30/30 PASS）

```
功能1 分组条   5 组标签[自选,追踪,走坏,权重,板块] / 当前组高亮 / 3 个非默认组有 ✕ / 默认组无 ✕ / ＋ 按钮
功能2 开关     默认盘后 / 切实时→is-live+aria-pressed / 记忆 wl_mode_v1=live / 请求带 ?mode=live
功能3/4 卡片   12 张个股卡 / 实时主图 wl-live-px / 实时副图 wl-live-vol / 四态点 / 板块chip /
               5日背离 / 主力净流入 / 关键位列 / 盘后主图 close+yl+wh / 盘后副图 pc+amv
功能9 板块卡   9 张 / is-sec / 走势已画 / 两行「+5.47%·+27.13亿」「+14.66%·+0.27pp」
功能7 搜索     24 条结果 / 含平安银行 / 带股·板块标签 / 点结果→POST /api/watchlist/add / 加完清空
功能5/6 拖拽   dragend→POST /watchlist/reorder{codes,group} / 跨类型 drop 无 move 请求 / 同类型 drop→move
功能8 移除     ✕→POST /api/watchlist/remove
不做项事件     点名称→panel:open-kline{code,name} / 点板块chip→panel:open-sector{code,name}（无监听方 debug）
刷新纪律       空闲打 watchlist.json / 聚焦跳过 / 非空跳过 / 拖拽中跳过 / 改名中跳过
```

> 写操作在验证中被**故意拦截**（只断言请求 URL+body 正确，不真的落盘），因此 **`data/panel/watchlist.json` 未被修改**。

## 8. 已知遗留

1. **pytest / CDP 截图未跑**（沙箱禁子进程），见 §5。
2. 任务书 §功能 8 的「旧版无确认」与实测不符（旧版有 `confirm`）。当前按任务书不弹；若主人要防误删，改 `removeItem` 加一行 `confirm` 即可。
3. 搜索不支持拼音（`compat_watchlist` 已知差异 3，非本批范围）。
4. K 线/板块详情弹窗属批次 1b 之后的跨卡跳转，本批只发事件。
5. `plugin.json` version 未升（0.3.1），浏览器缓存需硬刷新或后续升版。
