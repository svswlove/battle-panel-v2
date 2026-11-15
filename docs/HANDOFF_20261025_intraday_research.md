# 盘中照旧页搬 · 第二步（板块与个股研究）交接

2026-10-25 · 01 · 任务书「盘中第二步主菜：把旧页「板块与个股研究」卡照旧页搬进 v2 intraday 插件」
前置：`docs/HANDOFF_20261025_intraday_port.md`（第一步 板块轮动 · 当天，已验收）

## 一、结论

研究卡（旧页 4306–4531，`_rs*` 12 个函数组）**已完成并逐字节对齐旧页**。

- 与旧页 `_rsHtml()` 在真实载荷上 **逐字节相同**（5 组参数：3 排序键 × 板块/全A），
  只规范化掉任务书四条约束强制造成的 4 类属性改写。
- 行数 **90 / 183**（全A 档中栏 300）、列数 **6**、表头文本、缩略线 273 条、
  贡献分项 5 条、成分股 183 个可点名字 —— 全部与旧页一致。
- 交互全集在真 DOM（jsdom）上跑通：排序 3 键 / 点行选板块 / 返回全A / 回成分股 /
  层级 3 档 / 右键属性 / 滚动位置保持 / 卸载后不再响应。
- **发现并修掉旧页一个真 bug**：点排序按钮状态改了但表不重排（见第五节）。

## 二、文件结构（白名单内）

| 文件 | 内容 | 行数 |
|---|---|---|
| `app/plugins/intraday/frontend/research.js` | 渲染 + 取数 + 交互（旧页 12 个函数组搬入） | 475 |
| `app/plugins/intraday/frontend/research.css.js` | 旧页 1483–1537 + 1331–1335 样式逐条搬入 + `.it-card` 作用域 | 137 |
| `app/plugins/intraday/frontend.js` | 仅编排接入：import / 建 secResearch / createState+bind / unmount / refresh | +25 行 |
| `app/plugins/intraday/tests/test_research.py` | 13 条守门测试 | 353 |
| `app/plugins/intraday/tests/research_align.mjs` | 与旧页逐字节对照运行器（node:vm 跑旧页源码） | 122 |
| `app/plugins/intraday/tests/research_dom.mjs` | 交互全集运行器（jsdom） | 167 |
| `app/plugins/intraday/tests/fixtures/amv_research_l{2_board,2_all,3}.json` | 真实载荷快照（140KB / 203KB / 146KB） | — |

未动：`frontend/sector.js`、`frontend/caliber.js`、`frontend/format.js`、`frontend/card.css.js`、
`backend.py`、`plugin.json`、其它插件、`app/core/`、`engine/`、`data/panel/*`。**8810 未重启**（静态文件）。

## 三、四条约束的落实

### 1. 一次性布尔 / 模块级状态 → 每实例 AbortController

旧页状态全是模块级 `let _rs/_rsLvl/_rsSel/_rsSrt/_rsBusy/_rsErr/_rsAt/_rsLive/_rsBusyAt/_rsScope`
（4306–4310）+ 模块级 `_rsScroll`（4312）+ 模块级 `_rsNode/_rsSig`（4367）+ **模块级 `setInterval` 看门狗**（4374）。
搬进卡片后全部下放到 `createState()` 返回的 `st`：

| 旧页 | 卡片 |
|---|---|
| `_rs` / `_rsErr` / `_rsAt` / `_rsBusy` / `_rsBusyAt` | `st.rs` / `st.err` / `st.at` / `st.busy` / `st.busyAt` |
| `_rsLvl` / `_rsSel` / `_rsSrt` / `_rsScope` / `_rsLive` | `st.lvl` / `st.sel` / `st.srt` / `st.scope` / `st.live` |
| `_rsScroll`（模块级对象） | `st.scroll`（每实例） |
| `_rsNode` / `_rsSig` | `st.node` / `st.sig` |
| `setInterval` 看门狗（4374，永不清理） | `st.watch`，`unbind()` 里 `clearInterval` |
| 无卸载概念 | `st.ac = new AbortController()`，`unbind()` 一次 abort 全解 |

测试守门：`test_research_has_no_module_level_state`（禁模块级 `let`）+ jsdom 用例断言
`unbind` 后 `alive=false / watch=0 / 点击不再响应`。

### 2. `document.getElementById` → 容器内查询

旧页 `_rsTarget()`（4383–4390）是**面板级双槽位**：整页里有两个 `id=rsBody` / `id=rsBodyIntraday`，
靠 `section.active` 选当前可见那个（2026-10-21 根因注释：`getElementById` 只拿到隐藏的那个，
卡片永远空着）。卡片只有一个宿主 → **双槽位逻辑整体去掉**，`paint(st)` 直接写 `st.body`
（`mount` 时建的一个容器节点，由 `frontend.js` 传入）。
`_rsPaint` 的 `el.querySelector('[data-rs-col=...]')` 作用域也随之收到 `st.body` 内。

### 3. 浮层 / 子节点挂容器内

本卡旧页**没有浮层**（无 tooltip 节点），唯一的「越界」是两处内联
`onscroll="_rsScroll['b']=this.scrollTop"`（4520 / 4526）往模块级对象写值。
改为容器内 **capture 阶段 scroll 委托**，写回 `st.scroll`（scroll 不冒泡，capture 仍会经过祖先）。
滚动位置保持行为与旧页一致（4403–4406）：重绘后 `box.scrollTop = st.scroll[k]`。

### 4. 内联 `onclick` → `data-act` 委托

旧页共 8 处内联事件：

| 旧页 | 位置 | 卡片 |
|---|---|---|
| `onclick="rsSet('lvl',k)"` × 3 | 4501 层级按钮 | `data-act="rs-lvl" data-v="L2|CONCEPT|L3"` |
| `onclick="rsSet('srt',k)"` × 3 | 4504 排序按钮 | `data-act="rs-srt" data-v="contrib|px_pct|amv_pct"` |
| `onclick="rsSet('srt',k)"` × 3 | 4427 表头三列 | 同上（**`data-k` 保留**：旧页 CSS `.rs-tbl thead th[data-k]` 靠它做 hover/cursor，故动作参数另开 `data-v`） |
| `onclick="rsSet('sel',c)"` × N | 4434 板块行 | `data-act="rs-sel" data-v="<code>"` |
| `onclick="rsSet('scope','all'|'board')"` | 4515/4516 返回全A/回成分股 | `data-act="rs-scope" data-v="all|board"` |
| `onclick="kNameClick(this,event)"` × 183 | 2880 个股名 | `data-open-kline` / `data-open-name` 占位（跨 iframe 桥） |
| `onscroll="_rsScroll[...]=..."` × 2 | 4520/4526 | 容器内 capture 委托 |

全部由绑在 `.it-card`（不参与重绘的卡片根）上的**一个 click 委托**处理，
`unbind()` 用同一个 AbortController 解掉。

## 四、偏离清单

| # | 旧页 | 卡片 | 理由 |
|---|---|---|---|
| 1 | `_rsTarget()` 双槽位（`#rsBody` / `#rsBodyIntraday` + `section.active`） | **去掉**，`paint` 直接写 `st.body` | 任务书点名「卡片里不需要」。整页才有多槽位；卡片一个宿主，保留只会引入查错节点的风险 |
| 2 | 8 处内联 `onclick` / `onscroll` | `data-act` / `data-v` + 容器内 capture 委托 | 约束④；旧页写法在卡片里会跨实例打架 |
| 3 | 模块级 state 与 `setInterval` 看门狗 | 每实例 `st` + `AbortController` + `clearInterval` | 约束① |
| 4 | `kName` 挂 `onclick="kNameClick(...)"` 直调 `KLineApp` | 换成 `data-open-kline` / `data-open-name` 占位 | 卡在 iframe 里；与第一步 sector.js 同款处理，由 shell 捕获阶段发 `v2:openKline` 给父页 |
| 5 | **签名不含 `_rsSrt`**（旧页 4395） | **`srt` 计入签名** | 旧页 bug，见第五节。这是本单唯一一处「不照抄」的代码语义 |
| 6 | `.src` / `.muted` / `.rs-*` / `.k-*` 全局类名 | 全部加 `.it-card` 前缀 | 卡片共存，不加前缀会污染别的卡（与 card.css.js 同策略） |
| 7 | 单行 `@media(max-width:900px){ .rs-split{...} }` | 内层选择器也补 `.it-card` | card.css.js 那版对 `@` 开头整行跳过，本卡有单行 @media 会漏成全局。已在 research.css.js 里补上（未改 card.css.js：第一步已验收） |
| 8 | 口径行 `new Date()` 直取 | `asofText(rs, now)` 可注入 | 对照测试要可复现；运行时行为不变（默认 `new Date()`） |

颜色：旧页这段本来就全是 `var(--*)`，**零裸 hex、零自拼 rgba**，无需换色。
（`.k-nolink{opacity:.75}` 是旧页原样，不是 rgba。）

## 五、发现旧页一个真 bug（已修，需报备）

**现象**：旧页点「涨跌 / AMV」排序按钮，`_rsSrt` 变了、表**不重排**，表头箭头也不动。

**根因**：`rsSet('srt',k)`（4317）只调 `_rsPaint()`、不走取数；而 `_rsPaint` 的缓存签名（4395）是

    [_rs.asof, _rs.prev, _rsSel, _rsScope, _rsLvl, _rsLive, boards.length] + '#' + _rsErr

**不含 `_rsSrt`** → 签名不变 → `_rsNode` 被原样搬回（4399 `if (!_rsNode || sig !== _rsSig)` 不成立）→ DOM 不重建。

**实测证据**（node:vm 跑旧页源码 + jsdom 真 DOM）：连点三列，首行 6 个单元格文本与
`th` 的 class 串**逐字节不变**：

    contrib  firstCells= ['1','半导体881121','+2.391‰','+3.59%','+7.25%','']
    px_pct   firstCells= ['1','半导体881121','+2.391‰','+3.59%','+7.25%','']   sig same=true
    amv_pct  firstCells= ['1','半导体881121','+2.391‰','+3.59%','+7.25%','']   sig same=true
    th(class) px_pct : ['指数贡献↓.n on','涨跌.n','AMV.n', ...]   ← 箭头也没动

**处置**：卡片里把 `st.srt` 计入签名（`research.js::signature`）。修复后：三键排序结果互不相同
（+2.391‰ / +7.46% / +26.77%），表头 `.on` 跟随，且**不重拉数据**（`apiCalls` 仍为 1，与旧页一致）。
`engine/` 只读，**旧页本身未改**——是否回修旧页由 01 决定。

## 六、测试与对齐证据

### 测试（新增 13 条，`app/plugins/intraday/tests/test_research.py`）

| 组 | 用例 | 守什么 |
|---|---|---|
| 静态 6 条 | 无 DOM/网络捷径、无模块级状态、零裸 hex/rgba、CSS 全作用域 + token 存在、frontend.js 接入点唯一、常量对上旧页 | 四条约束与颜色门禁 |
| 纯函数 4 条 | `rsFmt`/`rsSort`（缺值排最后，不当 0）、`rsSp`/`kName`（不可跳显式 `.k-nolink`）、`asofText` 四态 + 15:00 边界、分项条 5/6 行随 `susp` 阈值 | 旧页口径逐项 |
| 自愈 1 条 | **8s 超时**（截获定时器断言 `delay==8000`、`err=='请求超时（8s）'`、busy 释放）、**15s 悬挂释放**、**实时→收盘回落**（第 2 次 URL 无 `live=1`）、TTL 30s/120s、看门狗建/清 | 任务书点名「必须保留」的三条 |
| 旧页对齐 1 条 | `_rsHtml()` vs `html()` 逐字节（node:vm 跑旧页源码，5 组参数） | 照抄不走样 |
| DOM 1 条 | 交互全集 + 占位属性 + 滚动保持 + 卸载 | 点得动 |

### 与旧页逐字节对齐（`tests/research_align.mjs`，node:vm 直接跑旧页源码）

    旧页片段 = 2842-2845(esc/num/cls) + 2856-2882(kName/kTsPlayable) + 4306-4321(state/rsSet) + 4327-4531(loadResearch.._rsHtml)
    vm.createContext(box); runInContext(片段 + 把 _rs* 指到输入上 + 冻结 Date)
    normalize() 只抹掉强制差异（data-act 改写 / 去掉 onscroll / 去掉 kNameClick / 去掉新增 data-open-name / 折叠模板缩进）

结果：

| 用例 | 结果 |
|---|---|
| L2/contrib/board | **逐字节相同**，左 90 行 / 中 183 行 |
| L2/px_pct/board | **逐字节相同** |
| L2/amv_pct/board | **逐字节相同** |
| L2/contrib/all | **逐字节相同**，中栏 300 行 |
| L2/amv_pct/all | **逐字节相同** |
| 空态（无数据） | `<p class="muted">数据加载中…（/api/amv_research.json）</p>` **完全相同** |
| 失败态 | 卡片输出「板块与个股研究数据不可用：<原因>」 |

规范化后比对长度 **250564 = 250564 字节**，`equal=true`。

### DOM 交互（`tests/research_dom.mjs`，jsdom）

| 项 | 结果 |
|---|---|
| 初始渲染 | 左 90 / 中 183 / 6 列 / 缩略线 273 / 可点名字 183 / 分项 5 条 / 选中行 1 |
| 排序 3 键 | 首行 → `+2.391‰` / `+7.46%` / `+26.77%`，`th.on` 跟随，**不重拉**（apiCalls=1） |
| 点第 3 行板块 | `sel=881281.TI`，URL `?level=L2&code=881281.TI&live=1` |
| 返回全A | `scope=all`、中栏 300 行、按钮变「← 回成分股」、分项条隐藏（旧页同） |
| 回成分股 | `scope=board`、按钮回「返回全A」 |
| 层级 L3 | `?level=L3`，左 230 行（真实载荷） |
| 占位属性 | `data-ctx=sector` 90 / `data-ctx=stock` 183 / `data-open-amv` 1 / `data-open-kline` 183 |
| 滚动位置 | 写 123 → 重绘后仍是 123 |
| 同签名重绘 | 复用同一个 `.rs-wrap` 节点（零重解析） |
| 卸载 | `alive=false`、`watch=0`、点击不再响应 |

### 整卡挂载（jsdom，真实 `frontend.js` + 真实 `/api/amv_research.json`）

7 个区块节点齐全，研究块夹在「板块轮动·当天」与「板块轨迹摘要」之间（旧页 4716 相对位置），
三份 CSS（card / legacy / research）都在，`refresh()` 不重拉研究卡（TTL 生效），unmount 后零异常。

## 七、验证环境限制（必须说明，未跳过）

**本机沙箱禁止子进程**：`spawnSync('node'|'python'|任何绝对路径)` 一律 `EPERM`
（已实测 `node.exe` / `python.exe` 绝对路径均被拒；申请提权一次被驳回）。
因此：

- `pytest app/tests app/plugins/intraday/tests -q` **未能由我执行**，需 01 复跑
  （基线 194 passed / 1 skipped，本单 +13 条，预期 207 passed / 1 skipped）。
- 替代做法（不是跳过）：**所有 node 载荷在我自己的进程内真实执行过**——
  `research_align.mjs`、`research_dom.mjs`、每个 `_expr()` 载荷、整卡 jsdom 挂载，
  结果见第六节；`app/tests` 的三类静态门禁（裸 hex / 未定义 token / 非相对导入）
  也按同样正则在本进程内逐文件扫过，intraday 6 个前端文件全部 **0 违规**。

## 八、已知边界 / 未验证

1. **盘中实时跳动未验**：验收时是周日，`live_mode=false`、载荷是 2026-10-23 收盘口径。
   `asofText` 的「实时 / 收盘定格 / 末帧（非今日）」四态用固定 `Date` 注入测过，
   但**真实盘中 `live=1` 拿到快照的端到端**要等开市（与第一步同一处边界）。
2. **`data-open-amv` / `data-open-kline` 只验属性，未验点击**：真实处理在 `shell.js`
   （捕获阶段委托 → `v2:openAmv` / `v2:openKline` postMessage 给父页），属 B 单范围。
   本卡按第一步同款处理，属性齐全（1 + 183 个）。
3. **右键菜单**：卡只负责输出 `data-ctx` / `data-code` / `data-name`；
   `ctx-menu` 节点由 shell 建（`document.body`，id 是连字符 `ctx-menu`，未改成驼峰）。
   本卡 90 + 183 行属性齐全，菜单本体行为未在本单实测。
4. **CONCEPT 档真实载荷**：`level=CONCEPT` 实测 200 但当前数据只有 1 个板块 / 28 只成分股
   （服务端 `_rs_base` 的结果，非本卡问题），已做逐字节对齐但样本小。
5. **跨浏览器**：jsdom 只近似 Chromium；`position:sticky` 表头、`box-shadow:inset` 选中条
   这类视觉项需真浏览器看一遍（节点与类名与旧页逐字节相同，风险主要在容器高度约束）。
6. **`max-height:520px` 未调整**：照抄旧页。卡内可用高度受限时两张表各自出现滚动条，
   与旧页一致；若 01 认为卡里应更高，改 `research.css.js` 一行即可（旧页无对应值，属设计取舍）。
7. **`test_intraday.py` 未改动**：`test_frontend_submodules_exist` 白名单里只列了第一步的 4 个子模块，
   本单新增 `research.js` / `research.css.js` 没有加进去。这是**有意不碰**已验收测试文件
   （白名单外无强制要求，新增文件的守门由 `test_research.py` 承担）；若 01 希望统一，
   在该断言里追加两个文件名即可。

## 九、改动清单

    M  app/plugins/intraday/frontend.js                     +30 行（仅编排接入）
    A  app/plugins/intraday/frontend/research.js             475 行
    A  app/plugins/intraday/frontend/research.css.js         137 行
    A  app/plugins/intraday/tests/test_research.py           353 行（13 条）
    A  app/plugins/intraday/tests/research_align.mjs         122 行
    A  app/plugins/intraday/tests/research_dom.mjs           167 行
    A  app/plugins/intraday/tests/fixtures/amv_research_l2_board.json   140423 B
    A  app/plugins/intraday/tests/fixtures/amv_research_l2_all.json     202924 B
    A  app/plugins/intraday/tests/fixtures/amv_research_l3.json         146059 B
    A  docs/HANDOFF_20261025_intraday_research.md           （本文件）
    A  docs/APPEND_20261025_file_log.md                     （待 01 搬的文件修改日志正文）

未动：`frontend/sector.js`、`frontend/caliber.js`、`frontend/format.js`、`frontend/card.css.js`、
`backend.py`、`plugin.json`、`app/web/*`、其它插件、`app/core/`、`engine/`、`data/panel/*`。
未做任何 git 写操作。8810 未重启（静态文件改动，浏览器硬刷新即生效）。

## 十、下一步建议

1. 01 复跑 `pytest app/tests app/plugins/intraday/tests -q`（预期 207 passed / 1 skipped）。
2. 浏览器硬刷新看视觉效果（尤其是窄屏两栏与 520px 高度约束）。
3. 开市后验 `live=1` 实时口径的端到端（第四态「末帧（非今日）」在盘中误显示会很难看，需确认服务端 `live_date` 字段）。
4. 旧页 4395 的签名 bug 是否回修 `engine/web/index.html`，需 01 拍板（`engine/` 本单只读）。
