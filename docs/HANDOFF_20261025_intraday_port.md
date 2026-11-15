# 盘中照旧页搬 · 第一步（板块轮动 · 当天）交接

2026-10-25 · 01 · 任务书 `任务书/20261025_C_盘中照旧页搬.md`

## 一、结论

第一步（`_st*` 板块轮动·当天）**已完成并验收通过**。第二步（`_evo*` 日内演化 55 函数）尚未开始。

- 四象视图与旧页**逐值一致**：气泡 20 / 尾迹 20 / 右侧标签 20，板块码集合完全相同，
  20 条标签文本（板块名 + 超额）逐条相同。
- 网格视图 **24 格逐格一致**（板块名 | #名次 | 涨幅 | Δ占比 | 活跃涨跌），diffCount = 0。
- 真实鼠标事件 15 项全通过，含「切走再切回 3 次事件仍有效」。
- `pytest app/tests app/plugins/intraday/tests` → **194 passed / 1 skipped**。

## 二、文件结构

`app/plugins/intraday/frontend.js` 从 599 行单文件拆开：

| 文件 | 内容 | 行数 |
|---|---|---|
| `frontend.js` | 卡片骨架、取数、其余四块（冰点/AMV/广度/轨迹/脉冲） | ~650 |
| `frontend/format.js` | esc / num / pct / pp / clsUp / clsIt / code6 / fmtAmount / dShort | ~50 |
| `frontend/caliber.js` | 板块轨迹口径层（纯函数，零 DOM） | ~130 |
| `frontend/sector.js` | 板块轮动·当天（`_st*` 20 函数 → 本模块） | ~600 |
| `frontend/card.css.js` | 旧页样式规则逐条搬入 + 作用域限定 | ~180 |

拆法理由：口径层（算）与渲染层（画）分离后可单测——`tests/test_caliber.py` 用 Node
加载真实模块跑断言，测的是真代码，不是源码字符串。

## 三、四条血泪约束的落实

### 1. 一次性布尔 → 每实例状态（最重要）

旧页 `_evoBind` / `_stBindQuad` 用模块级 `let _stDelegated = false`，
`if(!host || _stDelegated) return`。搬进卡片后**没有沿用这个写法**，原因是它在卡片里必然坏：

- 旧页需要它，是因为 `#panel-intraday` 每 3s 被整体重建、委托会跟着丢；
- 卡片里宿主是 mount 时新建的节点，`paint()` 只重建 `.st-body` 的 innerHTML，
  事件委托绑在**不参与重绘的** `.it-card` 上 —— "只绑一次"由「mount 几次绑几次」天然保证。

每实例自带 `AbortController`（`createState` 里建），`unmount` 时 `abort()` 一次解掉全部监听
（含 window 上的 scroll / blur）。第二张卡挂载时绑的是它自己的节点，不存在残留布尔。

**实测证据**：切走再切回 3 次，每次都用真实鼠标重新悬停 + 点击，
浮层每次都出现、钉住每次都成功（`_st_result.json` 的 `10.1/10.2/10.3`）。

### 2. `document.getElementById` → 容器内查询

| 旧页 | 卡片 |
|---|---|
| `getElementById('panel-intraday')`（`_stBindQuad` 宿主） | 传入的 `st.el`（= `.it-card`） |
| `getElementById('stBody')`（`_stPaint` / `_stApplyHl`） | `st.body`（= `.st-body`） |
| `getElementById('stTip')`（`_stTipEl` / `_stTipHide` / `_stTipMove`） | `st.tip`（容器子节点） |

`_stHitCode` / `_stApplyHl` 的 `querySelectorAll` 作用域也从 `#stBody` 换成 `st.body`。

### 3. 越界挂载 → 容器内子节点

- `_stTipEl`：旧页 `document.body.appendChild` + `position:fixed` → 这里 `st.el.appendChild`
  + `position:absolute`，坐标用 `clientX - cardRect.left` 换算成容器坐标，并夹在容器矩形内。
- **滚动时坐标重算**（旧页 fixed 不存在这个问题）：补了 capture 阶段的 window `scroll` 监听，
  按上次记录的鼠标 client 坐标重算。实测：直接派发 scroll 后 `left/top` 从 `3px/3px`
  被重写为 `1092px/1190px`。
- 副作用说明：真实滚动会让 Chrome 重发 `mouseover/mouseout`，浮层被收起是正确行为
  （不留残影）。两条路径都验过。

### 4. 内联 onclick → 事件委托

`stSet` 的 2 处内联调用（`onclick="stSet('view','quad')"` / `stSet('chg','w')` 等 4 个按钮）
改成 `data-act="st-set" data-k="view|chg" data-v="quad|grid|w|e"`，
由 `.it-card` 上的一个委托处理，照抄旧页 `closest('[data-*]')` 模式。

气泡钉住本来就是委托（旧页 `_stBindQuad` 的 click），原样搬。

## 四、三处有意偏离旧页（都是「整页 → 卡片」的必然结果）

| 旧页 | 卡片 | 理由 |
|---|---|---|
| `.src` / `.muted` / `.up` 等全局类名 | 加 `.it-card` 前缀 | 旧页是整页独占，卡片里不加前缀会污染别的卡 |
| `font-family: var(--mono)` | 别名 `--mono: var(--font-mono)` | **旧页从没定义 `--mono`**（全文 6 处引用，themes.css 里没有），一直落到浏览器等宽兜底。这是旧页的隐性缺陷，卡片里补上别名让它真用主题等宽 |
| 区间带 `#9dc3f0`（区间选择用，第二步才用到） | `var(--accent)` | 2026-10-25 的「颜色只走 token」门禁（`app/tests/test_fe_shell_bridge.py`）拦裸 hex |
| `.st-tip` 阴影 `0 4px 14px rgba(0,0,0,.18)` | `var(--shadow-pop)` | 同上，且三套主题下阴影强度本就该不同 |

## 五、实测记录（真实鼠标事件，不用 `el.click()`）

探针：`Hubagent/01/_vis/_intraday_st_cdp.js`（15 项）、`_cmp_st_cdp.js`（8 项）、
`_st_scroll_cdp.js`（滚动专项）、`_theme_cdp.js`（16 项）。

### 一次踩到的探针自身缺陷（记录下来防复发）

第一版探针用 `window.scrollBy` 滚动，全部「悬停」类断言假失败。
根因：v2 壳的滚动容器是 `#stage`（`.stage{overflow:auto}`），不是 window；
元素在首屏下时 `elementFromPoint` 返回 null、鼠标事件全落空。
改用 `scrollIntoView` + 确认 `elementFromPoint` 命中自身后再派发事件。

### 交互（v2 卡，`/v2/` 全卡模式）

| 项 | 结果 |
|---|---|
| 侧栏切到盘中 | OK（rail 按钮 `data-card-key`，不是 `data-key`） |
| 四象渲染 | 20 气泡 / 20 尾迹 / 20 右侧标签 |
| 悬停气泡 | 浮层出现（`电机 881277.TI / 真实涨幅 +5.30% / 超额 +21.69pp / 权重 0.27% / 活跃涨跌 +26.77%`），其余 76 个元素 dim |
| 滚轮滚动 | 浮层不留残影；重新悬停仍出浮层 |
| 点气泡钉住 | `.bub.on` ×1 + `.st-ql.pin` ×1 + 就地标签 ×1；再点取消 |
| 网格视图 | 两栏榜 24 格 + 24 条迷你走势，四象已撤，按钮态正确 |
| 加权/等权 | 数值真变（+5.30% → +6.08%），按钮态跟随 |
| **切走切回 3 次** | 每次都要重新悬停出浮层 + 点击可钉住 → 3/3 通过 |
| 控制台 | 零 error / 零未捕获异常 |

### 与旧页逐屏对比

| 项 | 结果 |
|---|---|
| 四象气泡数 / 板块码集合 / 尾迹数 | 全等（20 / 同集合 / 20） |
| 右侧标签文本（板块名 + 超额） | 20 条**逐条相同** |
| 工具条按钮文案 | 四象 / 网格 / 加权 / 等权，全等 |
| 口径说明行 | `口径：x=流通市值等权真实涨幅 · y=超额 ｜ 归属日 20261023 ｜ 235 点` —— 逐字相同 |
| 网格 24 格 | 逐格 diffCount = **0** |

对比探针里旧页那侧有一处必须说明：旧页在无头浏览器里所有 `[id^=panel-]` 都是
`display:none`（`active=intraday` 但面板不显示），按钮 `getBoundingClientRect` 全 0×0，
真实鼠标点不到。所以**旧页网格视图用 `stSet('view','grid')`（就是它 onclick 走的同一个函数）
切过去**，只用于数据对比；交互本身用真实鼠标在 v2 卡里验（第 8 项）。
旧页面板切换机制不在本单范围（`engine/` 只读）。

### 三套主题

nightsail / moonwhite / warmpaper 各读一次计算样式：

- 四个关键 token（`--up` `--down` `--accent` `--dim`）都有值（空值 = 落浏览器兜底）；
- 气泡 fill、标签 fill、按钮背景/前景、图底、次级文字颜色**逐主题全不同**；
- 没有任何元素落到 `rgba(0,0,0,0)` / `none` 这种「var() 没解析」的兜底值。

截图：`_vis/_theme_{nightsail,moonwhite,warmpaper}.png`。

## 六、测试

```
pytest app/tests app/plugins/intraday/tests -q
→ 194 passed, 1 skipped
```

### 改了两条旧断言（不是为了过测而放松）

它们锁的是 v0.1.0 的**实现选择**，而本单明确推翻那两个选择：

| 旧断言 | 为什么必须改 | 新断言守什么 |
|---|---|---|
| `test_frontend_zero_imports`：不得有任何 `import` | 本单把单文件拆成四个子模块 | `test_frontend_no_third_party_imports`：只许相对路径导入、禁外部 URL；`test_frontend_submodules_exist`：四个子模块真实存在且非空壳 |
| `test_frontend_does_not_pull_full_track`：源码里不许出现 `sector_track.json` | 四象/网格的 x/y 与当日序列全在全量里，旧页就是拉全量 | 必须有 `TRACK_TTL_MS` 字面常量、必须真的参与缓存判断、全量只许一处取数 |

### 新增 `tests/test_caliber.py`（13 例）

用 Node 加载真实模块，守口径而不是守字符串：

- 盘中时段过滤（`08:59` / `09:29:59` / `15:30` 必须被剔，`15:00:56` 按 `HH:MM` 留在区间内）
- `rel` = 板块 `amv_pct` − 同快照 `all`
- `contrib` = (当前占比 − EOD 基线占比) × 10，占比 = 基线×(1+amv/100)/(1+all/100)
- **缺基线权重 → `null`，不是 0**（缺数据就标缺，不然会被读成「无贡献」）
- 断点断线：>3 分钟断（整 3 分不断、3 分 01 秒断）；缺值也断
- 格式化与旧页逐项对齐（`—` / `+x.xx%` / `up|down` / `亿·万亿` / `10/23` / 转义）
- **真实生产轨迹**（`data/panel/amv_live_sector_track.json`，235 点 × 90 板块）
  取末快照 12 个板块，`rel` 与手算值逐一对上

## 七、未验证 / 已知边界

1. **盘中实时跳动**：验收时是周日，轨迹是 2026-10-23 收盘口径（235 点）。盘中新增采样点的
   增量重绘没实测（只在静态全量上验过）。第二步会顺带验。
2. **`data-open-amv`（点板块看工作台详情）**：占位属性已照旧页写在气泡/标签上，
   但 v2 侧的真实处理在 `shell.js`（B 单在改），本轮只确认属性存在、没验点击效果。
3. **极端行情的四象重叠**：只验了 2026-10-23 一天、二级层 90 个板块（显示前 10 + 后 10）。
   三级层（更多板块）未逐层点。
4. **跨浏览器**：只在 headless Chromium 里验过，Firefox / Safari 未验。
5. **`_stLine` / `_stLineLab` 相关样式**：旧页 `.st-line` 只服务于已撤除的「轨迹」视图，
   样式随规则搬进来了但当前无 DOM 使用（旧页 `_stHitCode` 仍会命中它，故保留）。

## 八、第二步（`_evo*` 55 函数）待办

按任务书建议，先搬 `_st*` 验证方法可行再搬 `_evo*`。方法已在第一步验证：
口径层分离 + 每实例状态 + 容器内查询 + 委托 + 真实鼠标探针。

第二步要特别处理：

1. **`_evoBind` 是全单最复杂的一处（约 130 行）**，含滚轮缩放、Shift 平移、右键拖拽量区间、
   Esc 清区间四套手势。`_evoDelegated` 同样的处理（每实例 AbortController）。
2. **`_evoCardUpdate` / `_evoPlaceCard` / `_evoPlaceFc`** 三处依赖 `#evoCard` / `#evoPaneL`，
   全部换成 `box.querySelector('.evo-pane-l')` 一类容器内查询（CSS 里已备 `.evo-pane`）。
3. **`_evoBfReload` 调 `_stAt = 0` 强制重拉轨迹**：第一步已经把轨迹缓存放在卡片状态 `S.trackAt`，
   第二步要让回填按钮能清它（现在是模块级私有，需要给 sector.js 开一个 `invalidate()` 出口）。
4. **`evoBackfill` 的两个端点已有且实测通**：`POST /api/sector_track_backfill`（ops 插件）、
   `GET /api/sector_track_backfill_status`（compat_sector 插件）。
5. **`_evoFitH` 是空函数，必须原样保留**（旧页 4063 注释：调用点不删）。
6. **三处 canvas 都没有**：旧页 AMV/行情/贡献三张图都是 SVG，`palette()` 不适用，
   颜色全是 `var(--accent)` / `var(--up)` / `_EVO_PAL`。`_EVO_PAL` 是 10 色裸 hex 调色板
   （旧页标注 `theme-lint-allow: 折线多色区分`），搬过来要向 `test_no_raw_hex_in_any_card`
   登记例外，或改成主题可见的派生色——这个要拍板。

## 八·五、一次误操作与恢复（必须记下来）

我为了对比「改动前测试是否已红」跑了 `git stash push -- app/plugins/intraday`，
失败（`Cannot save the current status`），随后 `git stash pop` **误弹出了一个 2026-10-16 的旧 stash**
（`stash@{0}: WIP on main: 688ffde`），它动到了仓库根下的 `engine/`：

| 文件 | 影响 | 处置 |
|---|---|---|
| `engine/web/kline_lc.js` | 被写入 2 处冲突标记（`<<<<<<< Updated upstream`） | `git checkout HEAD --` 恢复，现与 HEAD 逐字节相同（本会话之前它就是 clean） |
| `engine/web/index.html` | **未受影响** | 校验：本会话开始时 Python 读到 663770 字符 / 11555 行；恢复后仍 663770 / 11555，差 0。该文件的 `M` 状态早于本会话（不是本单改动） |

`stash@{0}` 仍完整保留（未 drop），内容与操作前一致。

**教训**：本项目仓库根是 `OH-WorkSpace`，不是 `projects/panel-v2`——
在 `panel-v2` 下跑无路径限定的 `git stash`，作用域是整个工作区（含 `engine/`）。
需要「回退看基线」时用 `git stash push -- <具体路径>` 并先确认作用域；失败的 stash push 不要跟 pop。

## 九、改动清单

```
M  app/plugins/intraday/frontend.js          599 → ~650 行（拆出四个子模块 + 接板块轮动块）
M  app/plugins/intraday/tests/test_intraday.py 2 条断言按新形态重写 + 2 条新增
A  app/plugins/intraday/frontend/format.js
A  app/plugins/intraday/frontend/caliber.js
A  app/plugins/intraday/frontend/sector.js
A  app/plugins/intraday/frontend/card.css.js
A  app/plugins/intraday/tests/test_caliber.py
```

未动：`app/web/shell.js`、其它插件、`app/core/`、`engine/`、`data/panel/*`。
8810 未重启（静态文件改动，浏览器硬刷新即生效）。
