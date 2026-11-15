# 任务书 · 前端批次 1a：watchlist 卡升级（对照旧 Tab 全功能）

> 路线图 `docs/FRONTEND_ROADMAP.md` 批次 1。行为基准 = `app/web/legacy/index.html` 的 `#panel-watchlist`（约 5550-6015 行 + 拖拽事件 5918-5975）。
> 现状：`app/plugins/watchlist/frontend.js`（6KB）是只读简化版——一张统计条 + 一条表格。

## 目标

把 `watchlist.main` 卡做到旧 Tab 的功能对等（**除 K 线弹窗外**，见「不做」），数据全部来自已有端点（13 条全就绪）。

## 功能清单（逐项对齐，旧实现行号见上）

| # | 功能 | 旧实现要点 | 新卡要求 |
|---|---|---|---|
| 1 | 分组条 | `_watchGroupBar`：个股组/板块组两排标签，当前组高亮，组名可双击改名、右键/长按删除、`+` 新增 | 同款交互；分组操作调 `/watchlist/group/add|rename|remove|reorder` |
| 2 | 盘后/实时开关 | `watchMode` ∈ {eod, live}，切换后卡片主图从「收盘序列」换成「当日分时」 | 同款开关，状态存 localStorage（键 `wl_mode_v1`） |
| 3 | 卡片网格 | `.wc` 卡：代码/名称（可点开 K 线）/徽标（badges 前 2 个）/现价/涨跌幅/主图 SVG/副图量柱/四态点 + 板块 chip + 5日背离 + 主力净流入 | 同款信息密度；主图按 eod/live 画两种图 |
| 4 | 涨跌配色 | 红涨绿跌，走主题 token `--up`/`--down`（**禁裸 hex**，现有卡里的 `#d33`/`#178a4c` 必须换掉） | 同 |
| 5 | 拖拽排序 | 组内拖动改序 → `POST /watchlist/reorder {codes, group}` | 同款；拖动中不重建（防 5s 轮询打断） |
| 6 | 拖拽换组 | 拖到同类型分组标签 → `POST /watchlist/move {code, group}`；跨类型（个股↔板块）不给落点 | 同款，跨类型拒绝 |
| 7 | 搜索添加 | 顶部搜索框，输入即搜 `/watchlist/search`，结果点击加入 `/watchlist/add` | 同款；搜索中不重建 |
| 8 | 移除 | 卡片右上 ✕ → `POST /watchlist/remove` | 同款（需二次确认？旧版无确认，保持一致不弹） |
| 9 | 板块卡 | `.wc.sec`：板块名/涨跌/走势图/板块多空 | 同款 |
| 10 | 刷新 | 旧 Tab 5s 轮询；新卡 `refreshEvery: 5000`，且**搜索框聚焦/拖动中/输入非空时跳过刷新** | 同款三条跳过条件 |

## 不做（本批明确排除）

- **K 线弹窗**（`kName`/`kCode` 点开个股 K 线大图、`watchSeries` 拉序列）：属 kline 卡职责，批次 1b 之后统一做「跨卡跳转」。本批名称/代码只做可点击样式 + 派发 `CustomEvent('panel:open-kline', {code})`，无监听方时 console.debug。
- 板块详情卡（`watchSectorOpen`）：同上，派发 `CustomEvent('panel:open-sector', {code})`。
- 关键位（旧 Tab 无此列，现卡有）：**保留**现有 `/levels` 列（是已交付的增值，不是回归）。

## 契约与约束（硬性）

1. **只吃本插件端点**：`ctx.api.get/post`，前缀自动 `/api/p/watchlist`。
   **已核实（01 亲自验证，不要再猜）**：`app/web/shell.js` 的 `makeCtx` 里，路径以 `/api/` 开头时**直接使用不拼前缀**（`path.startsWith('http') || path.startsWith('/api/') ? path : base + path`）。所以：
   - 读：`ctx.api.get('/api/watchlist.json')` → 完整 27 条 + 5 分组（已实测）。也保留现有 `/summary` `/items` `/levels`（本插件自有）。
   - 写：`ctx.api.post` **目前只有 get**（shell.js 的 api 只有 get）。两条路：① 在 `app/web/shell.js` 的 `api` 对象里补一个 `post`（白名单允许，改 shell.js）；② 卡片内直接 `fetch('/api/watchlist/add', {method:'POST',...})`——**不推荐**（绕开契约）。**选 ①**：补 `ctx.api.post(path, body)`，与 get 同构（绝对 `/api/` 不拼前缀、非 2xx 抛错带 detail）。
   - 写端点归属：`compat_watchlist` 插件已有全部写逻辑（`build_add`/`build_remove`/`build_move`/`build_reorder`/`build_group_*`，走 `ctx.panel_write` 受控写）。**不迁后端**（不动生产写路径），前端按绝对路径 `/api/watchlist/add` 等调用——这是兼容层的既定消费方式（选股卡同模式）。
2. 颜色一律 `var(--up)`/`var(--down)`/`var(--dim)` 等 themes.css token；**禁裸 hex**（现有卡里的 `#d33`/`#178a4c`/`#e08a1e`/`#888` 必须换掉）。
3. 零写盘、零 `from common`/`from core`、仅本插件端点 + `/api/watchlist*` 兼容路径（门禁静态扫描）。
4. 卡片只操作自己的 `el`，不写全局变量；不依赖其他插件。
5. 无框架无构建，原生 ES module。
6. **刷新纪律**（旧 Tab 已踩过的坑，照搬）：`refreshEvery: 5000`，但刷新时跳过三种情况——① 搜索框聚焦或非空 ② 拖拽进行中 ③ 分组名编辑中。否则每 5 秒重建 DOM 会丢焦点、打断拖拽。

## 改动白名单

- 改：`app/plugins/watchlist/frontend.js`（主）、`app/plugins/watchlist/plugin.json`（若卡片声明需调整）
- 改：`app/web/shell.js`（仅：`api` 对象新增 `post` 方法，与 get 同构；不改其他逻辑）
- 改：`app/plugins/watchlist/tests/`（如已有前端测试则补；无则新建只读静态检查：无裸 hex、无跨插件 import、无写盘）
- 新建：`docs/HANDOFF_20261024_fe_batch1a.md`

禁止：改 `app/web/legacy/`、改 `engine/`、改其他插件后端、引依赖、git commit。

## 环境事实

- Python：`OH-WorkSpace\envs\qmt312\Scripts\python.exe`
- 8810 在跑；改动后重启（停 `port 8810` → `powershell -File run.ps1`）
- 现有 watchlist 卡：`app/plugins/watchlist/frontend.js`（可整体重写）
- 旧实现参考：`app/web/legacy/index.html` 5550-6015 行（卡片/渲染/拖拽）、5726-5890（分时/板块绘制）
- 端点清单：`app/plugins/compat_watchlist/backend.py`（13 路由）
- 主题 token：`app/web/legacy/themes.css`（`--up`/`--down`/`--card`/`--line`/`--dim`/`--fg`/`--accent` 等）
- 真实数据形状（实测 2026-10-24 02:1x）：
  ```
  /api/watchlist.json → {groups:[{id,name,kind}], items:[{kind,code,name,added_at,order,group,
    quote:{price,change_pct,source,asof}, quote_source, regime, sector:{l2:{code,name,regime},l3:{...}},
    div, flow, badges:[{label}]}], stats:{total,stocks,sectors,bull}, quote_asof, mode, updated_at}
  ```

## 自测要求

1. `python -m pytest app/plugins/watchlist/tests -q` → 全绿
2. `python -m pytest -q` → ≥721 passed
3. 重启 8810 → `/api/health` ok
4. CDP 实测（参考 `engine/tests/panel/_fe_batch0_cdp.js` 写法）：打开 `http://127.0.0.1:8810/` → 点 rail「自选池」→ 断言：卡片数=27、分组标签数=5、盘后/实时开关可切、搜索框输入「银行」出结果、控制台 0 错误；截图 `docs/fe_batch1a_watchlist.png`
5. **文件完整性自查**（上一批教训）：改完每个文件后回读并 `python -m py_compile`（py）/`node --check`（js，若是 module 用 `node --input-type=module --check`）；回执里报文件行数

## 交付格式

回执 ≤300 字：做了什么 / 测试数字 / CDP 断言结果 / 文件行数 / 遗留 + 路径。
