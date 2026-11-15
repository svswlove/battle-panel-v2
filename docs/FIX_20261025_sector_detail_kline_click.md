# 修复：板块详情页个股左键点不开 K 线（2026-10-25 · 执行手交 01）

## 结论

根因与 01 诊断一致，按方案落码。**只改 2 个文件**：`app/web/shell.js` + `app/tests/test_fe_shell_bridge.py`。
未碰插件、`engine/`、`app/core/`、`data/`；git 无任何写操作。

## 一、shell.js 改动（+44 / −1 行，行号为改后）

### 1. 注释块 L756-761（原 L755 一行）
原注释「独立打开（window.parent === window）时不安装本监听：没父页可转，就不伪装成可点」已不成立，改为说明两种形态都装委托、独立形态走 ctxOpenKline / ctxOpenSector 本页路径。

### 2. 新增两个 helper（L775-793）
- `sectorModalOf(node)`：`node.closest('#sector-modal, .sector-modal')`。
- `closeSectorModal(modal)`：先点弹窗自己的 `.card-x`（走插件 `close()`，会摘掉它挂在 document 上的 keydown），再兜底 `remove()`（✕ 处理器尚未绑上的窗口期；插件 `close()` 内部有 `closed` 幂等标志，重复 remove 安全）。

**为什么需要它们**（诊断没写、落码时验证出来的第二半问题）：
`.sector-modal` 挂在 `document.body`（`sector/frontend/detail.js:325`），样式是 `position:fixed; inset:0; z-index:50` 的全屏遮罩（`legacy.css.js:315`），而板块卡 `unmount()`（`sector/frontend.js:84-97`）**不带走它**。只修「左键能切卡」而不收弹窗，新 K 线卡挂进 stage 后会被这层遮罩整个盖住 —— 用户看到的仍是那个弹窗，体感还是「点了没反应」。故：点击发生在弹窗内时，开卡成功（`ok`）后收掉弹窗。

### 3. `bindKlineBridge()` 独立形态（L794-818，替换原 L770 的 `if (window.parent === window) return false;`）
- 不再短路，改 `document.addEventListener('click', …, true)`（capture）。
- 命中 `[data-open-kline]` → `event.stopPropagation()` + `ctxOpenKline(code.trim(), name)`；
  否则命中 `[data-open-amv]` → `stopPropagation()` + `ctxOpenSector(code.trim(), name)`。
- `name` 取 `data-open-name`（`hasAttribute` 区分「未给名」与「空名」，与 iframe 分支逐字同口径）；code 空 → 不动作。
- 用 **document** 而非 `el.stage`：覆盖挂 body 的详情弹窗，与右键菜单 `ctxResolve` 的 document 级口径一致。
- 只 `stopPropagation`，不 `preventDefault`（保留文本选择/链接语义），与 iframe 分支一致。

### 4. iframe 形态（L819-841）**一行未动**
仍是 `el.stage` 上 capture 委托 + `postMessage(PM_KLINE/PM_AMV, parentOrigin())`，回归风险为零。

## 二、测试改动（app/tests/test_fe_shell_bridge.py，+88 行 / 5 条新用例）

新增 L298-388 一节，含 helper `_js_function_body()`（按大括号配对截函数体，避开注释干扰）：

| 行 | 用例 | 守什么 |
|---|---|---|
| 323 | `test_kline_bridge_does_not_short_circuit_in_standalone` | 源码中 `window.parent === window) return false` 不得出现在 bindKlineBridge 内 |
| 331 | `test_kline_bridge_standalone_uses_document_capture` | 独立分支必须有 document 级 capture click 委托，且调 `ctxOpenKline(` / `ctxOpenSector(` / 读 `data-open-name` / `stopPropagation` / 不得 `postMessage` |
| 351 | `test_kline_bridge_iframe_branch_kept` | iframe 分支 `window.parent.postMessage(` + `PM_KLINE`/`PM_AMV` + `parentOrigin()` 都还在 |
| 361 | `test_standalone_click_closes_sector_modal` | 弹窗选择器对上 `#sector-modal/.sector-modal`，且开卡后 `closeSectorModal(modal)` |
| 380 | `test_ctx_open_functions_exist` | 两个被复用的函数是真声明，本页路径（`findCardByPlugin('kline')` / `panel:open-sector`）在 |

定位 iframe 分支一律用 `"const host = el.stage"`，**不能用 `"el.stage"`** —— 独立形态的注释里也出现该词，会切错起点。

## 三、静态自查结果（沙箱禁子进程，pytest 未跑）

1. **语法**：shell.js 全文经 `new Function(src)` 解析（等价 `node --check`）→ OK。shell.js 无 import/export，经典脚本，该检查等效。
2. **断言复刻**：把 5 条新用例的判定逻辑用 TS 逐条复刻跑一遍 → 22 项检查全 true，0 fail。
3. **符号存在性**：`sectorModalOf` / `closeSectorModal` / `ctxOpenKline` / `ctxOpenSector` / `findCardByPlugin` / `bindKlineBridge` 均在 shell.js 有定义；`#sector-modal` 与 `.card-x` 在 `sector/frontend/detail.js` 真实存在（L322 / L339-340）。
4. **真 DOM 实跑（jsdom 30.1.3，用工作区已有 node_modules）** —— 不是静态推断：
   - 独立形态：`bind()` 返回 true；stage 内 `[data-open-kline]` 点击 → `ctxOpenKline("000001.SZ","平安银行")`，卡片自身冒泡处理器**未被触发**（stopPropagation 生效）。
   - body 挂的 `.sector-modal` 内 `.stk-click[data-open-kline="600519.SH"]` 点击 → `ctxOpenKline` 被调 + 弹窗被收掉（走 ✕，`closedByX=1`，节点从 DOM 移除）。**这条就是原 bug 的场景**。
   - `[data-open-amv]` → `ctxOpenSector("881121.TI","半导体")`。
   - 空码 `"  "` 与无属性点击：不动作、不抛错。
   - iframe 形态（代码跑在子 frame 里）：`postMessage` 收到 `{type:"v2:openKline", code:"000002.SZ", name:"万科A"}` + origin `http://127.0.0.1:8810`，本地 ctx 函数**零调用** —— 原路径未变。

## 四、需要 01 复跑

```
& 'OH-WorkSpace\envs\qmt312\Scripts\python.exe' -m pytest app/tests -q
```

预期：原基线 205 passed + 本单新增 5 条 → **210 passed**（若基线含 skip 则同数平移）。
浏览器验证：`/v2/` 直开 → 板块卡 → 开任一板块详情 → 点成分股名 → 应切到 K 线卡且详情弹窗消失。

## 五、回退

`git checkout app/web/shell.js app/tests/test_fe_shell_bridge.py`（本单未做任何 git 写操作，两文件均为工作区改动）。
