# HANDOFF · v2 卡 → 父页开 K 线/板块卡 桥（R5）

> 2026-10-24 ｜ 01 ｜ 交付：v2 卡（`/v2/?card=…` iframe）内的个股 / 板块可点元素，点击后在 8810 父页打开旧页既有弹窗。
> 旧页 `app/web/legacy/` 零改动；`v2_map.json` 未改（仍全 `old`，功能默认不触发）。

## 1. 消息契约

v2 卡（iframe）→ 父页，`window.parent.postMessage(msg, origin)`：

| type | 触发元素 | 字段 | 父页动作 |
|---|---|---|---|
| `v2:openKline` | `[data-open-kline]` | `{type, code, name, list?}` | `window.KLineApp.open(code, name, list)` |
| `v2:openAmv` | `[data-open-amv]` | `{type, code, name}` | `window.AmvCard.open(code, name)` |

- `code`：`600519.SH` / `886108.TI`，非空才发（空值不发，不伪装可点）。
- `name`：取 `data-open-name`（该属性恒由渲染方输出）。
- `list`：`[{code, name}]`，可选；给了父页 K 线弹窗才有「上/下一只」（标题显示 `1/2`），缺了照常打开。
- **为什么分成两个 type 而不是按码猜**：旧页 `data-open-kline` 允许 `.TI` 指数码走 K 线，`.TI = 板块` 只是右键菜单的口径（`_ctxIsIndexCode`），拿来分派会判错。type 由卡内元素属性决定，与父页面板委托（`openTarget`）同一判据。

## 2. 两端改动点

### 2.1 `app/web/v2bridge.js`（父页侧，+86 行）

| 位置 | 内容 |
|---|---|
| 新增 `frameEls` | `mountFrame()` 里 `frameEls.push(iframe)` 登记本脚本创建的 iframe |
| 新增 `isOwnFrame(win)` | 遍历 `frameEls`，`f.isConnected && f.contentWindow === win` 才认 |
| 新增 `window.addEventListener("message", …)` | 只接 `v2:openKline` / `v2:openAmv`，且 `ev.source` 命中白名单，转 `openFromCard()` |

- **来源校验用 `ev.source === iframe.contentWindow`，不校验 origin**：父页 `document.referrer` 在 iframe 侧只给出父页 URL，端口不写死；同一 8810 进程内父页 origin 恒等于自身，跨窗口（8801 外壳嵌 8810）时也不受影响。
- **父页入口缺失时** `log()`（`console.log` 前缀 `[v2bridge]`）后 `return`，不抛。入口名核对：`window.KLineApp`（`legacy/index.html:8944` 定义、`10725` 导出 `open`）、`window.AmvCard`（`10746` 定义、`10806` 导出 `open`）。
- **不复刻逻辑**：只调 `KLineApp.open` / `AmvCard.open`，与旧页面板委托 `index.html:10926-10928` 同一对函数。
- 旧页 8002 工作台的 `amv:openKline` 等消息由旧页自己的监听处理，本桥 type 前缀 `v2:` 不冲突。

### 2.2 `app/web/shell.js`（卡内，+50 行）

| 位置 | 内容 |
|---|---|
| 新增 `PM_KLINE` / `PM_AMV` / `parentOrigin()` | `document.referrer` → `new URL(...).origin`；解析失败或为 `null` 用 `'*'` |
| 新增 `bindKlineBridge()` | `el.stage` 上 capture 阶段 `click` 委托：`closest('[data-open-kline]')`，否则 `closest('[data-open-amv]')`；命中即 `stopPropagation()` + `postMessage` |
| 启动序列 | `bindChrome(); bindSearch(); bindKlineBridge();` 新增一行 |

- `window.parent === window` 直接 `return false`：**独立打开 `/v2/` 不安装监听**，点击不动作（实测见 §4.D）。
- 用 capture 阶段：与旧页面板委托同口径（`index.html:10936` 捕获阶段 `stopPropagation`），抢在卡片自身点击处理器之前拦下，避免「开 K 线 + 进详情」同时发生。
- 只 `stopPropagation`，不 `preventDefault`。
- `.cp-chip-link`/`.k-name` 的 `cursor:pointer` 由卡片样式自带，未改 CSS。

## 3. 手动验证步骤（真机）

前置：8810 在跑（`http://127.0.0.1:8810/`）。

1. **主路径（总入口 Tab，无需改映射）**
   1. 打开 8810，点侧栏最后一个 Tab「v2 插件」→ 页面下半区显示 `/v2/` iframe。
   2. iframe 左侧栏点「概念追踪」→ 卡渲染出成员 chips。
   3. 点任意个股名（如「三维天地 301159.SZ」）→ 父页弹 K 线弹窗，标题 `三维天地 301159.SZ`，canvas 有图。
   4. 点任意板块 chip（`data-open-amv`）→ 父页弹「板块详情」AMV 卡。
2. **切单个 Tab 到 v2 后同样可用**：把 `app/web/v2_map.json` 的 `concept` 改成 `"v2"`，刷新，点「概念」Tab → 卡视图里点个股名，同样弹 K 线。（验完改回 `"old"`。）
3. **上下键**：若卡内元素所在列表带 `list`（本次 shell 侧未构造 list，只有 `code`+`name` 两字段；旧页 iframe 路径带 list），弹窗标题出现 `1/N`，↑/↓ 可切。当前 shell 侧发的是不带 list 的消息，`1/N` 只在外部（如手工 postMessage）传入时才出现——**这是本轮的已知边界**。
4. **独立打开不动作**：新标签直开 `http://127.0.0.1:8810/v2/`，点个股名 → 无弹窗、无跳转、控制台无报错。
5. **无入口不抛**：控制台执行 `delete window.KLineApp` 后，在父页 `window.postMessage` 无效（来源不符）；从 v2 卡里点个股名 → 控制台出现 `[v2bridge] 父页无 KLineApp.open，忽略开 K 线请求 …`，页面不报错。

## 4. 自动化实测记录（ego-browser，2026-10-24）

| 项 | 结果 |
|---|---|
| A. 父页 → v2 Tab → 概念卡点个股 `301159.SZ` | `#klineModal` display=`flex`，标题 `三维天地 301159.SZ`，`#klineChart canvas` 存在 ✅ |
| A2. 概念卡点板块 chip `886108.TI` | `#amvModal` display=`flex` ✅ |
| B. 父页自身 `window.postMessage({type:'v2:openKline',code:'000001.SZ'})` | 弹窗保持 `none`、标题不变 → 来源校验生效 ✅ |
| C. 从 iframe 真实上下文发带 `list` 的消息 | display=`flex`，标题 `贵州茅台 600519.SH  1/2` ✅ |
| D. 独立打开 `/v2/` 点个股名 | `window.parent===window`，URL 不变、`#klineModal/#amvModal` 均不存在、无异常 ✅ |
| E. 点 chip 后概念卡未误触进详情 | `[data-back]` 前后均 false，`.card-host` innerHTML 长度 22589 → 22589 不变 ✅ |

## 5. 回退方法

功能是纯增量，回退任一侧即断开（两侧互不依赖，不会留半截状态）：

- **只关父页转发**：删 `app/web/v2bridge.js` 里 `frameEls` / `isOwnFrame` / `addEventListener("message")` 三块，以及 `mountFrame()` 里的 `frameEls.push(iframe)` 一行。回退后卡内点击无反应（消息无人接）。
- **只关卡内发送**：删 `app/web/shell.js` 里 `bindKlineBridge()` 定义与调用、`PM_KLINE`/`PM_AMV`/`parentOrigin()`。
- **整轮回退**：`app/web/v2bridge.js` 与 `app/web/shell.js` 均无历史改动依赖，`git checkout -- app/web/v2bridge.js app/web/shell.js` 即回 R4 状态（`legacy/` 与 `v2_map.json` 本轮未动，无需回退）。

## 6. 遗留 / 需要真机点验的项

1. **上下键（list）**：契约里 `list` 字段已实现并由父页透传给 `KLineApp.open`；但 shell.js 侧目前只发 `code`+`name`，卡内没有「当前列表」的数据来源，故用户点卡内个股时不会出现 `1/N`。要补需卡自己按渲染顺序算出有序列表（旧页 `kListFrom` 的做法是扫 `.k-name`），属另一轮工作。
2. **`el.stage` 之外的可点元素**：委托挂在 `#stage` 上，抽屉 / 搜索面板里的 `data-open-*`（本轮不存在）不在覆盖范围；若后续卡片把内容渲染到 body 层浮层，需改挂 `document`。
3. **父页 origin 判定**：iframe 内只能拿到 `document.referrer`。若将来父页以 `no-referrer` 策略嵌卡，会退到 `'*'`（仍可用，只是发送侧不再限定目标 origin）。
4. **未验证**：跨进程形态（8801 外壳嵌 8810）下的实点，本轮只在 8810 直开做过实测。
5. 未 commit、未重启服务（8810 为静态文件热读取，刷新即生效）。
