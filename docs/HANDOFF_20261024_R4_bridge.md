# HANDOFF · R4 旧 Tab 隐藏与卡替换开关（v2bridge 升级）

> 2026-10-24 晚 ｜ 01 ｜ 任务书 `docs/task_R4_bridge.md`
> 结论：**已完成并通过实测**。默认九 Tab 行为不变；`v2_map.json` 把某 Tab 置 `v2` 后点该 Tab 显示卡视图；卡加载失败 3s 内回退旧界面并留一行提示；「v2 插件」总入口仍在。

## 1. 改了什么

| 文件 | 变更 | 说明 |
|---|---|---|
| `app/web/v2_map.json` | 新增 | 逐 Tab 映射，出厂全 `old`；`_doc` 字段自带取值说明 |
| `app/web/v2bridge.js` | 重写（1326B → 10.4KB） | 从「加一个 Tab」升级为「读映射 + 逐 Tab 替换 + 回退」 |
| `app/web/shell.js` | +18 行 | `?card=<id>` 只渲染指定卡；坏卡给错误卡（`.card.is-error`） |
| `app/core/main.py` | **未改** | 映射走 `/static/v2_map.json`（`/static` 已挂 `app/web/`），无需注入内联 |
| 插件目录 | **未动** | 遵守并发单边界 |

### 映射取值

```json
{ "watchlist": "v2", "sector": "v2:sector.board", "dashboard": "old" }
```

- `old`：走旧 section（默认，也是映射文件缺失/损坏时的行为）
- `v2`：v2 卡，卡 id 按约定 `<Tab key>.main`
- `v2:<cardId>`：指定卡 id
- 改完刷新页面生效（桥 `fetch` 带 `no-store`）

## 2. 桥怎么实现（为什么这样切）

**包装 `window.loadTab`，不包装 `activate`。** 旧页 `activate()` / `_tabTick()`（轮询） / `_resumeFresh()`（回前台） / `hashchange`→`bootTab` 四条路径最终都落到 `loadTab(key)`，一个包装点覆盖全部入口；包 `activate` 会漏掉轮询 tick 与 hash 直链。

- 命中 v2 的 Tab：不跑旧渲染（不拉旧 `.json`），插一个 `div.v2-frame` + iframe `/v2/?card=<id>`；旧 section 用行内 `display:none` 压住（`activate()` 仍会给它挂 `.active`）。
- 视图用 `div` 不用 `section`：旧页全局 CSS 有 `section{display:none}`，且 `activate()` 只切 section 的 `.active`，div 天然不受影响。
- 轮询幂等：同一 Tab + 同一卡直接返回，不重载 iframe。
- 回退提示挂在旧 section **前面**（兄弟节点），不放进 panel 内部——旧渲染是 `panel.innerHTML = ...`，放里面第一次渲染就被冲掉。
- 回退后 `failed[tabKey]` 上锁，3s 轮询不会把 iframe 反复挂回来；提示在切回该 Tab 时仍在。

### 三条回退判据（都实测过）

1. 映射的卡 id 在 `/_plugins` 清单里不存在 → **立即**回退（不等 3s）
2. iframe 同源可读后 500ms 轮询，`.card-host` 出现即收工；出现 `.card.is-error` → 立即回退；满 3s 无宿主 → 回退
3. iframe `error` 事件 / `contentDocument` 不可访问 → 回退

`/_plugins` 拉不到时跳过存在性校验，交给 iframe 自己试——校验失败不能变成「功能不可用」。

## 3. 验证

### pytest 全量

```
python -m pytest -q  →  1152 passed, 4 skipped（47.0s）
```

### cutover gate

```
python tools\cutover_gate.py  →  PASS 46.4s
8810 /api/health=OK  全部插件健康=OK  shadow 双跑=OK  AMV 原始 JSON 比对=OK
battle_plan 写域接管=OK  插件无写代码=OK  kline 边界声明=OK  pytest 全套=OK
```

### 浏览器实测（CDP，headless Chrome，1600×1000）

**A. 默认全 old（九 Tab 不变）**
- Tab 栏：仪表盘/作战表/盘中/板块/概念/复盘/自选池/选股/后台 + 「v2 插件」，共 10 个
- `window.__v2Bridge=true`，首屏 `section.active = panel-dashboard`，`.v2-frame` 数量 = 0
- 逐个点 8 个旧 Tab：均 `section.active` 命中自己、`.v2-frame`=0、`panel.style.display` 空、无提示；各 panel `innerHTML` 均非空（intraday 798KB / concept 68KB / review 37KB / selection 30KB / battle_plan 21KB / ops 17KB / dashboard 3.2KB）

**B. watchlist 切 v2 生效**
- `{"watchlist":"v2"}` → 点自选池：`.v2-frame`=1，`src=/v2/?card=watchlist.main`，旧 `panel-watchlist.style.display=none`，iframe 内 `.card-host`=1、标题「自选池」、侧栏隐藏，正文是真实持仓/自选列表（23 只，行情 2026-10-23）
- 切回仪表盘：`.v2-frame`=0、`panel-watchlist.style.display` 还原为空

**C. 断卡回退（两条路径都验）**
- 映射写 `v2:no.such.card`：`.v2-frame`=0，提示「v2 卡（no.such.card）未就绪，已回退旧界面：v2 卡不存在：no.such.card（改完 v2_map.json 刷新页面重试）」，提示在 `panel-watchlist` **前一个兄弟节点**，旧 panel 已 `.active` 且 `innerHTML` 36KB
- 阻断 `/v2/` 主文档（Fetch 域 failRequest）：4.2s 后回退，提示「卡视图不可访问」，旧界面 36KB 渲染出来；再过 5s 复查仍无 iframe（未被轮询挂回）
- 阻断 `/static/shell.js`（壳能开、卡挂不出来）：4.5s 后回退，提示「3 秒内未渲染出卡片」；过 6s 复查 `.v2-frame`=0、提示仍 1 条

**D. 总入口 Tab**
- 点「v2 插件」：`.v2-frame`=1，`src=/v2/`（无 card 参数），iframe 内 8 个 rail 卡按钮、标题「仪表盘」

**E. `?card=` 单卡视图（直开 `/v2/?card=`）**
- `/v2/?card=watchlist.main`：`.card-host`=1、无错误卡、rail 收起、`main.left=0`
- `/v2/?card=no.such.card`：`.card.is-error`=1，标题 `no.such.card`，正文「v2 卡不存在：no.such.card / 请核对 card id（映射写 v2:<卡 id>）」

全程 `console` 无 error（除壳自身 HQChart 横幅日志）。

### 说明与未验证项

- 探针脚本放在 `%TEMP%`（`r4_bridge_probe*.js`、`r4_cardparam.js`），未进项目目录；每次跑完都复原 `v2_map.json`（探针里断言 `mapRestored: true`）。
- 实测期间 `v2_map.json` 曾被探针临时改写为 `v2` / `v2:no.such.card`，现已复原为全 `old`。
- **未验证**：`v2:sector.board` 这类「卡 id 与 Tab 名不同」的组合只在 `/_plugins` 清单层面确认 `sector.board` 存在，没有逐 Tab 实点（`sector` Tab 本身旧页是 iframe 嵌 0AMV 工作台，换卡属另一件事）。
- **未验证**：跨域 iframe（若将来 v2 壳换端口）时 `contentDocument` 不可读 → 会走「卡视图不可访问」回退，这条路径只在同源下模拟过。

## 4. 期间发现的并发单噪音（不在本单范围）

`python -m pytest -q` 首轮出现过 2 次偶发失败，均**不在**本单白名单内，且单跑/复跑均通过，判定为其他并发工单的中间态：

| 现象 | 定位 | 复跑 |
|---|---|---|
| `compat/tests/test_compat_contract.py::test_status_shape_and_values` 断言 `trade_mode is None` 但实得 `paper` | `app/plugins/compat/` 正被另一单改（`git status` 显示 `backend.py`/`plugin.json`/测试文件均 M，测试文件 19:20 刚改） | 单跑 pass；该目录单跑 97 passed |
| `selection/tests/test_selection_backend.py` 两条 `test_data_contract_documents_four_endpoints` / `test_backend_has_no_io_tokens` | `app/plugins/selection/` 属 R2 并发单 | 单跑 pass；目录单跑 56 passed |

最终一次全量 1152 passed 与 cutover gate PASS 均为干净结果。

## 5. 回滚

```
git checkout -- app/web/v2bridge.js app/web/shell.js
del app/web/v2_map.json
```

三个文件互不牵连：`v2bridge.js` 回到「只加 v2 插件 Tab」，`shell.js` 回到无 `?card=` 过滤（`/v2/` 整壳行为不变），删掉映射文件后桥按全 old 处理。
