# 首页收口：8810 `/` 从旧页切成 v2 壳

日期：2026-10-26（旧端口下线同一晚，接续 `HANDOFF_20261026_old_ports_retire.md`）
决定：主人拍板「首页切 v2，旧页留 `/legacy/` 作回滚，桥脚本+按钮一起下线」

---

## 1. 改前是什么样

`http://127.0.0.1:8810/` 返回的**不是 v2**，而是旧仓 `engine/web/index.html`（669KB，9 个 Tab），
`main.py` 往里注入两样东西：

- `<head>`：`window.__V2_OWNED={...}`（告诉旧页哪些 Tab 已归 v2，免得 boot 就去拉 8002）
- `</body>` 前：`<script src="/static/v2bridge.js">`

`v2bridge.js` 再往旧页 `#tabs` 加一个 Tab，标签文字 `V2_TAB_LABEL = "v2 插件"`，点它用 **iframe 嵌 `/v2/`**。
所以界面上看到的是「旧页外壳 + 里面套 v2 卡」，主人问的「侧边栏 V2 插件按钮」就是它。

这是绞杀者迁移的中间态，代价：首页读 669KB 旧页、每张卡多一层 iframe、主题靠 postMessage 推、
标注要跨 iframe 生效、9 个 Tab 的边界各写一遍。

## 2. 改后是什么样

| 路径 | 内容 |
|---|---|
| `/` | **v2 壳**（`app/web/shell.html`，4653 字节） |
| `/v2/` | 同一份 v2 壳（旧书签继续可用） |
| `/legacy/` | 旧仓前端原样（`engine/web` 只读直挂），回滚用 |
| `/static/v2bridge.js`、`/static/v2_map.json` | 404（已下线） |

切换前的覆盖核对：旧页 9 个 Tab（仪表盘/作战表/盘中/板块/概念/复盘/自选池/选股/后台）
在 v2 里都有对应卡，另多 K 线 / 活跃市值 / HCL / 纸面 4 张 → 13 张。

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `app/core/main.py` | `index()` 返回 v2 壳；删 `_v2_owned_tabs()` 与两处注入；删 `HTMLResponse` import；头部补路径表 |
| `app/web/v2bridge.js` | 移出静态区 → `docs/attic/20261026_v2bridge_retire/` |
| `app/web/v2_map.json` | 同上 |
| `app/tests/test_v2bridge_height.py` | 同上（整文件为 iframe 高度口径） |
| `app/tests/test_sector_tab_cutover.py` | 同上（整文件为「旧页 Tab 让位给 v2 卡」） |
| `app/tests/test_fe_shell_bridge.py` | 2 条 v2_map 用例换成「首页= v2 壳 / `/legacy/` 可回滚 / 桥文件不得回潮 / v2 覆盖 9 Tab」4 条守卫；主题消息用例改为只锁 v2 壳侧 |
| `app/web/shell.js` | 3 处注释：`v2bridge 转发` → `宿主页转发`（父页已不再是旧页） |
| `app/plugins/concept/frontend.js` | 1 处注释同上 |
| `_vis/_portaudit_all_tabs.js` | 切卡选择器从 `[data-tab]` 改成 `.rail-btn[data-card-key="pluginId::cardId"]`；点不到时明确报 MISS（不伪装测过） |

## 4. 验收（线上实测）

| 项 | 结果 |
|---|---|
| `GET /` | 200，4653 字节，含 `shell.js`/`rail-tabs`，无 `v2bridge`/`__V2_OWNED`/旧页标题 |
| `GET /v2/` | 200，同上 |
| `GET /legacy/` | 200，旧页完整（680KB 字符），标题「统一作战面板」在 |
| `/themes.css` 与 `/legacy/themes.css` | 均 200（旧页资源未断） |
| `/static/v2bridge.js`、`/static/v2_map.json` | 404 ✓ |
| 9 卡端口审计 | 9 张卡全部点到（0 MISS），83 个请求**全部** 8810，旧端口 0，未捕获异常 0 |
| `pytest app/tests` | 183 passed / 1 skipped（改前 190/1；净减 7 条 v2_map 用例、净增 4 条收口守卫） |
| v2 全量 `pytest` | 1484 passed / 13 failed，13 条与收口前**完全同一批**既有红灯（intraday 字节对齐 12 + panic source 1） |

## 5. 回滚

把 `app/core/main.py` 的 `index()` 改回：

```python
return FileResponse(legacy_web / "index.html", media_type="text/html; charset=utf-8")
```

再把 `docs/attic/20261026_v2bridge_retire/` 里的 `v2bridge.js`、`v2_map.json` 移回 `app/web/`，
并把那两段注入逻辑从 git 历史里恢复。旧页本身一直在 `/legacy/`，没动过。

## 6. 侧边栏图标修正（同日晚，主人指出后补做）

### 问题

侧边栏图标按 **pluginId** 映射，旧版却是按 **Tab** 分配，两者错位，实测四处：

| 现象 | 根因 |
|---|---|
| 仪表盘退化成「仪」字 | 它的「拱+指针」图标被 `amv` 占用了 |
| 盘中退化成「盘」字 | 它的折线图标被 `kline` 占用了 |
| 选股/后台无图标 | `selection` / `ops` 在 ICONS 里没条目 |
| 多出 4 个侧栏位 | `amv` / `amv.hcl` / `kline` / `paper` 在旧版**没有独立页面**（K 线是弹窗、amv/HCL 是仪表盘子块、纸面是口径切换） |

### 改法

`shell.js` 的 `ICONS`（按 pluginId）→ `NAV` 表（按旧版 9 个页面）：

- 每条 = card key + 旧版中文名 + 图标 path，图标**逐字节照抄** `engine/web/index.html:8548 RAIL_ICONS`
- `renderNav()` 只渲染 NAV ∩ 已装卡片，缺卡跳位不留空
- 初始卡改取「NAV 第一张」（仪表盘），不再依赖插件 order 决定的 `state.cards[0]`
- 导航表外的卡不进侧栏，仍可由搜索 / 卡片内跳转 / `panel:open-card` 打开

### 验收

- 9 条图标与旧版**逐条字节一致**（脚本比对，全部一致；旧版另有未启用的 `events` 图标，未取）
- 线上 rail 实测 9 个按钮，顺序 = 仪表盘/作战表/盘中/板块/概念/复盘/自选池/选股/后台，每个都有 SVG，**零文字图标**
- 回证「描掉 4 张卡仍可达」：搜索 600519 → 点结果 → 主区标题变「K线」，rail 保持无高亮
  （与旧版 K 线弹窗不占导航位一致），未捕获异常 0
- 9 卡端口审计：9 张全点到，88 请求全 8810，异常 0
- `pytest app/tests` 183 passed / 1 skipped；全量 1484 passed / 13 failed（与修正前同一批）

### 附带修的一处测试脆弱点

`test_open_card_event_has_listener` 用裸 `src.index("panel:open-card")` 定位，我在 NAV 注释里
提到该事件名后它就拿到了注释、报假红。改为锁 `addEventListener('panel:open-card'`，
守卫意图不变、定位不再靠字符串首次出现。

## 7. 遗留

- `engine/web/index.html` 里的 `__V2_OWNED` 守卫（`renderSector` 附近）现在恒为 false，
  旧页在 `/legacy/` 下自行渲染 —— 无害，未改动旧仓文件（回滚时需要它）。
- `app/web/legacy/index.html`（8801 旧壳副本）的注释仍提 v2bridge/v2_map，属历史说明，未改。
