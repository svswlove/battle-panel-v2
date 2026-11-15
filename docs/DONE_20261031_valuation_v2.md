# DONE 2026-10-31 · 估值页 v2（删两个 tab · 面板联动 · 六维打分挂载）

**结论**：估值页按主人新口径改完三件事——删掉「市场环境」「个股研究」两个 tab（口径先核对后归并）、
点击联通面板现成的 K 线卡与板块卡（产物自身零 fetch、单开静默降级）、把 score 插件的六维打分在
**生成时**挂进产物（两份 asof 各标各的，差 >1 个交易日一律显示「—」）。
valuation 插件 `/meta /view /files` 三端点口径未动，core 未动，score 插件未动。

## 1. 改了哪些文件

| # | 文件 | 类型 | 说明 |
|---|---|---|---|
| 1 | `夏天-activity/工具/peg/peg_dashboard.py` | 改 | ① 删两个 tab 的 HTML/JS/初始化；② 新增市场背景一行、联动桥 `postMessage`、表格行点击、六维打分读取与挂载、降级渲染；③ 顺手修 `window.__calc` 引用局部 `pool` 的 ReferenceError（🔍 弹窗此前打不开） |
| 2 | `夏天-activity/工具/peg/output/peg_dashboard.html` | 重生成 | 212,837 字符（含 120/120 打分匹配） |
| 3 | `projects/panel-v2/app/plugins/valuation/frontend.js` | 改 | 加 `message` 监听：source/origin/字段三重校验 → 转 `panel:open-kline` / `panel:open-card`；unmount 时移除监听 |
| 4 | `projects/panel-v2/app/plugins/valuation/plugin.json` | 改 | 0.2.0 → 0.2.1（前端行为变更，缓存键要变） |
| 5 | `projects/panel-v2/app/plugins/valuation/tests/test_valuation_plugin.py` | 改 | 版本断言同步 + 新增联动守门（只走现成通道 / 三重校验 / 不 fetch） |
| 6 | `projects/panel-v2/app/plugins/sector/frontend.js` | 改 | 补入参通道 `ctx.card.args.industry` → `_focusIndustry()`：归一化精确匹配 → 切层 → 定位行并描边；不在榜单则填进该卡搜索框 |
| 7 | `projects/panel-v2/app/plugins/sector/plugin.json` | 改 | 0.6.5 → 0.6.6 |
| 8 | `projects/panel-v2/app/plugins/sector/tests/test_sector_contract.py` | 改 | 新增入参定位守门（读过 args.industry、精确比对、未命中要说明） |

未动：`app/core/**`、score 插件任何文件、`/meta /view /files` 三端点实现与契约。

## 2. 两个 tab 删掉后，各自口径的去处

删之前逐个核过（面板内有无同口径）：

| 原 tab | 内容 | 判定 | 去处 |
|---|---|---|---|
| 市场环境 | 四大指数快照（点位 + 当日涨跌幅） | **面板内无同款**（盘中卡只有指数演化曲线与广度 880，没有「指数点位/涨跌幅」卡片） | **搬到页面头部一行**：`#marketLine`（在标题副行下，纯文本：指数名 点位 涨跌幅 + 全市场涨跌/平盘/涨停/跌停/样本数）。原来那段"市场宽度偏强/偏弱"的判词不搬（属解释性文案，不是口径） |
| 市场环境 | 全市场涨跌宽度（上涨/下跌/平盘 + 涨停/跌停） | **面板内重复**：盘中卡 `breadthHtml` 已有「涨跌家数 + 涨停/跌停」（源为 `/api/breadth_880.json` 广度 880） | 判重，不单独保留；数字仍并入上面那一行（附加口径说明「涨停≥9.8% 近似、样本 N 只」，与广度 880 的口径差异在页面上可见） |
| 个股研究 | 低估池 Top10 近 60 日 spark + 区间统计（近20日/区间位置/RSI14/MA5/MA20/均线多空） | **面板内有更强同口径**：K 线卡（日/周/分时 + 指标 + F10）可看任意标的同口径走势 | 判重，删；页面不再渲染 `D.klines`（数据管道未动，采集照旧，只是不再出这一屏） |

页面头部实测文本（2026-10-31）：

```
市场背景｜上证指数 3651.43 ▼ 0.82% · 深证成指 11138.20 ▼ 2.92% · 创业板指 2517.59 ▼ 4.45% ·
沪深300 4041.84 ▼ 1.03% · 全市场 上涨 764 / 下跌 4466 / 平盘 72（涨停 46 · 跌停 123，样本 5302 只，涨停口径≥9.8% 近似）
```

## 3. 联动：只走现成通道

协议（事件名与字段是本次定的）：

| 方向 | 报文 | 校验 | 落到哪个现成入口 |
|---|---|---|---|
| 产物 → 面板 | `{type:"peg:open-stock", payload:{code:"600028", name:"中国石化"}}` | `ev.source === frame.contentWindow` 且 `ev.origin === location.origin` 且 `code` 必须 `/^\d{6}$/`，name 截断 24 字 | `panel:open-kline`（shell.js `bindCardJump` → `ctxOpenKline` → K 线弹窗） |
| 产物 → 面板 | `{type:"peg:open-sector", payload:{name:"工业金属"}}` | 同上（name 截断 40 字） | `panel:open-card {pluginId:'sector', args:{industry}}`（shell 卡间跳转 → 板块卡 `ctx.card.args`） |
| 面板内点击 | — | — | 不新建弹窗/页面/接口；板块卡无入参通道 → 本次补了 `args.industry` |

产物侧覆盖的点击点：表格行（新增）、PE-G 散点、市值×增长气泡、低估 TOP10、行业中性榜、增速陡峭、防御溢价、四宫格、预测分歧、行业估值走廊圆点；行业侧：行业低估分布、行业平均 PEG、估值走廊色带。按住 Shift/Ctrl 点行业 = 只在本页过滤（保留原有联动），不跳板块卡。

产物自身：`fetch(` 0 处、`XMLHttpRequest` 0 处（脚本扫描确认），报文只带 code/name 与行业名，不带任何数据快照。

## 4. 六维打分（生成时关联）

- 关联文件：`projects/panel-v2/data/panel_v2/score/latest.json`（`SCORE_LATEST`，可用 `--score` 覆盖；score 插件一行不动）
- 关联时机：`peg_dashboard.py` 生成 payload 时由 `attach_scores()` 读一次，按 **6 位代码**精确匹配写入 `pool[].score_total / score_grade`，同时写 `meta.score`（asof / peg_asof / gap_days / cutoffs / universe / weights / n_matched / reason）。页面运行时不再取数。
- 本次实测：`asof=2026-10-30`，PEG 数据 `2026-10-29`，`gap_days=1`，匹配 `120/120`。
- asof 不一致时实际显示：
  - 差 7 个交易日（构造样本实测）：卡头徽标 = `六维打分：—（与本期差 7 个交易日）`；主表两列 = `—`；口径说明末句 = `本期分数不可用：打分 asof 2026-10-20 与本期数据 2026-10-29 相差 7 个交易日。所有打分列显示「—」，PEG 数据不受影响。`
  - 产物缺失（构造样本实测）：徽标 = `六维打分：—`；两列 = `—`；说明末句 = `本期分数不可用：打分产物不存在。…`
  - 差 1 个交易日（当前真实状态）：正常显示，徽标写明 `（全 A 口径 · 与本期差 1 日）`。
- 两列加在：**主表**（低估成长股池，`今年EPS` 之后、`透视` 之前）+ **弹窗明细列表**（各图点击弹出的个股清单，`stockTableHTML`）+ **PEG 计算明细弹窗**（🔍，新增「六维打分（score 插件 · 全 A 口径）」区块，含总分与评级两格）。
- 排序与筛选：两列表头不输出 `data-k`（点它不排序），`sortKey` 仍只由 PEG 列决定；打分不参与低估名单筛选（该名单只看 PEG 分位）。
- 评级口径标注原文：
  - 列头 title：`全 A 口径评级（S）：按全 A 5577 只的分数分布分档，不是本池内排名`
  - 页面说明：`六维打分（资金30/板块20/趋势20/量价15/估值10/质量5）由 score 插件生成，口径是全 A 5577 只一起排横截面百分位再分档（S≥70.19 · A≥57.49 · B≥40.11 · C≥27.98），不是本池内排名——两个名次的分布不同，不能横着比。`
- 估值维与 PEG 的关系（页面原文）：`打分与 PEG 不是同一个口径：打分的「估值」维是 PE-TTM 与 PB 的横截面百分位（贵不贵），PEG 是前向PE ÷ 预期增速（成长是否已被定价），两者互不验证；分数不参与 PEG 排序，也不参与低估名单筛选。`

## 5. 两种场景实测

**A. 双击直开（无父页）** `http://127.0.0.1:8810/api/p/valuation/view`

```
inPanel=false；tabs=估值扫描/预期修正/事件资讯（3 个）；JS 错误 0
点行业柱 → 本页过滤（indFilterBar 出现「工业金属」）
点表格行 → PEG 计算明细弹窗（中国石化）——原行为保留
点散点   → 填搜索框 + 联动表格（原行为保留）
点击全程新增网络请求 = 0
```

**B. 面板 iframe 内**（rail → 估值）

```
点散点 → 父页 K 线弹窗打开（中国石化 600028.SH），产物内搜索框未被填（没走降级分支）
点行业柱 → 切到板块卡（rail active = sector::sector.board），提示「已定位到板块「工业金属」」
关 K 线：Esc 可关
父页 JS 错误 0
```

板块卡定位细节：catalog 名称归一化（去空白、去罗马数字尾缀、全角括号转半角）后**精确**匹配；
命中 → 切层级 + 设一级行业筛选 + 滚动到 `li.row` 并描边 2.4s；不在前/后 10% 榜单 → 填进该卡搜索框并说明
（实测「工业金属」因设置了一级行业筛选后落入榜单，直接定位成功）。
名称对不上（如「炼化及贸易」「航运港口」这类申万/东财与 AMV 口径差异，池内 6 个行业、覆盖 11.7% 股票）→
只在工具条下留一行「未找到同名板块…（口径不同，未定位）」，不做模糊猜到错板块。

## 6. 回归

```
pytest app/plugins/sector/tests app/plugins/valuation/tests -q  → 91 passed
产物 JS 语法：node --check 通过
产物自包含：fetch( 0 / XMLHttpRequest 0 / 外链仅新闻与公告 URL（数据）
```

## 6.1 右键菜单 + 可点反馈 + 点击入口收窄（2026-10-31 追加）

主人反馈三件：① 要右键菜单；② 看不出哪里能点；③ 点行内任意位置都跳 K 线不合理。

| 项 | 做法 | 实测 |
|---|---|---|
| 右键菜单 | 产物只发 `peg:context-menu {kind:'stock', code, name, x, y}`（坐标是 iframe 内 clientX/Y）→ 插件校验后**合成一个 contextmenu 事件**到临时节点（带 `data-ctx/data-code/data-name`）→ 触发 shell 现有的 `ctxResolve → ctxOpen`。不新写菜单、不改 shell.js | 右键表格行 / 散点：`#ctx-menu` 弹出，菜单项 = 加入自选（自选/追踪/走坏/权重/新建分组…）、打开 K 线、复制代码；坐标跟随点击点 |
| 行业右键 | 行业元素只有名称没有板块代码 → 不伪造 code，右键等同左键（`panel:open-card` 定位） | 右键行业柱 → 切板块卡并提示「已定位到板块「工业金属」」 |
| 单开降级 | `inPanel()` 为假时完全不拦 contextmenu | 单开右键：`preventDefault` 未发生，浏览器原生菜单保留 |
| 可点反馈 | SVG 元素 `cursor:pointer`；hover 白描边 + 提亮 + 4px 外发光（**无 transition**，单元素一次重绘，不会像旧版那样扫过百点时每帧重建滤镜）；`:active` 再提亮；表格行 hover 背景加深；股票名 hover 变青色+下划线 | 规则已落盘（computed 检查到 `.panel svg circle:hover` / `.nm-link:hover` / `tbody tr:hover`） |
| 勾选态（多选对比） | 勾选的行加 `.is-sel`：底色 + 左侧蓝色高亮条；`toggleSel` 里同步 DOM，排序重绘时 `tr` 生成也带回 class | 勾选 → `rgba(88,166,255,.13)` + `inset 3px 0 0` 蓝条、drawer 弹出；取消勾选 → class 清空；换排序后仍保持勾选态 |
| 点击入口收窄 | 删除整行点击委托；只有股票名（`.nm-link`）可点开 K 线；行内其它区域、checkbox、排序、文本选中不受影响 | 点名称 → K 线弹窗；点行业列 → 不跳；单开点名称 → PEG 明细 |

注意：donut 环是 `fill="none" + stroke` 画出来的，hover 用 `:not([fill="none"])` 排除，避免描边把环变细。

### 6.1.1 三处回修（2026-10-31 第二轮反馈）

| 问题 | 根因 | 修法 | 实测 |
|---|---|---|---|
| 右键菜单打开了收不起来 | 壳只监听**父页**的 click / Esc / scroll；菜单是在 iframe 里开的，iframe 内的这些事件不冒泡到父页 | 同源 iframe 可直挂监听：菜单打开时给 `frame.contentDocument` 加 `pointerdown`/`mousedown`/`keydown`(capture)，给 `frame.contentWindow` 加 `scroll`(capture)，任一触发就合成一次父页 `click`（走壳自己的 `ctxClose`）并自行解绑；卡卸载时也解绑 | 右键开 → iframe 内点空白关 ✓；再开 → Esc 关 ✓ |
| 悬浮卡片上多一条白底小条（内容是卡片里已有的） | SVG 数据点上的原生 `<title>` 与自定义 tooltip 重叠：浏览器先弹系统白底提示，下面才是 `#tip` | 删掉全部数据点 `<title>`（15 处，覆盖散点/四宫格/气泡/分布柱/环形/各榜单/走廊），只保留文档头部的 `<title>` | 3688 个可点节点中带 title 的 0 个，`svg title` 0 个，只剩 `document.title` |
| 象限图底下说明写反 | X 轴是增速（向右增）、Y 轴是前向 PE（向上增），原文案把「左上」当成了高增低 PE | 改为：**右下**=增速高+PE低→低估高增（重点）；**右上**=增速高+PE高→合理偏贵；**左下**=增速低+PE低→价值陷阱；**左上**=增速低+PE高→贵且不增 | 页面文案已对上坐标轴 |
| 四象限名位置 | 原先写在图下一行，视线要来回跳 | 直接画进画布四角（`pointer-events:none` 不挡散点 hover；带 `paint-order:stroke` 深色描边，压在点上仍可读）；下方 `.quad-hint` 行删除 | viewBox 0 0 880 400：左上(60,18)、右上(806,18)、左下(60,346)、右下(793,346)；散点 hover 仍正常弹 tooltip |
| 钉住名称 | 散点图工具栏加「📌 钉住名称」开关；开启后单击散点 = 标注/取消，点空白 = 清空，上限 30 只（避免钉满全图）；关闭后单击恢复原行为 | 钉住的点画白环 + 名称；避让：每个标签试 18 个候选位（上下左右 + 远位），与已放标签矩形不重叠且不出图才采用，全冲突则不画（宁缺不乱）；chip 实时显示「钉住 N（显示 M）」，不静默吞标签 | 8 只钉住 → 7 个标签、重叠对数 0；取消一只 → 7；点空白 → 0；关闭模式后单击 → 搜索框联动（600028） |


## 7. 回滚

- 产物侧：`git checkout -- 夏天-activity/工具/peg/peg_dashboard.py` 后重跑 `refresh_dashboard.ps1`
- 插件侧：`git checkout -- projects/panel-v2/app/plugins/{valuation,sector}` 后 `POST /api/shell/restart`

## 8. 排版改版验收（知夏 2026-10-31 20:4x）

知夏直接改了实现（未按「先交思路+预览」），产物 20:46 重生成、数据重跑到 10-31。验收结论：**可接受**。

| 项 | 结果 |
|---|---|
| Python 数据逻辑 | 对比 HEAD：51 个函数一个没少，只多了我加的 4 个（`attach_scores` / `load_score_snapshot` / `trading_day_gap` / `_parse_day`）—— 她的改动全在 `render_html` 模板内 |
| 整页高度 | 6019 → 4379（折叠 4 处） |
| 交互 | 点名称→PEG 明细 ✓；右键行→面板 ctx 菜单（加入自选/打开 K 线/复制代码）✓；钉住→标名 ✓；行业→板块卡 ✓ |
| 口径 | pool 120、打分 120/120、asof 10-30 / gap 0 ✓；市场背景行有 10-31 数据 ✓ |
| 性能 | backdrop-filter 1（仅弹窗遮罩）、动画 12、rAF 仅 tooltip 节流 ✓ 未退步 |
| 自包含 | 两个内联 script（189KB + 320B），零 link、零外链 ✓ |
| 四象限名 | 她改成「低增·还贵 / 增长已定价 / 双低观察 / 低估成长 ★」，位置仍在四角 |

**我补的一处**：主题跟随面板。她做了 `?mode=light|dark` 但 iframe 默认 dark，面板默认 frostglass 是浅色 → 浅色面板里嵌一块深色（就是主人说的「像两个软件」）。
插件改为量父页 body 底色定明暗（不维护主题名单，主题增删不会失效），iframe 拼 `&mode=`，并用 MutationObserver 盯 `data-theme`，切换时重挂 iframe。
实测：frostglass → mode=light、底色 rgb(245,245,247)；切 nightsail → 自动重挂 mode=dark、rgb(16,21,31)；切回 → light。

**待办**：`peg_dashboard.py.before-restore-20261031200454`（她 19:56 的回滚快照）先保留 —— git HEAD 还是 9/12 原版，删了就丢了「我改后/她改前」这一层；主人确认新版可用后再提交并删除。

