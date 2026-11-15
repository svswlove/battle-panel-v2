# 新会话交接（2026-10-25 傍晚）

> 上一会话已满。这份文件是接着干的唯一入口，读完就能接。
> 详细状态见 `docs/STATUS.md`。

---

## 一句话现状

三单并行交付：**A 通过、B 通过、C 第一步通过但有疑点**。
今天还完成了口径统一（个股四态缺失值）、名称表修复、追踪池删除、方向修正。

---

## 立刻要做的三件事（按顺序）

### 1. 查 C 单差的 14000 字 ⚠ 最重要

**现象**：v2 盘中卡内容 **2701 字**，旧页 `http://127.0.0.1:8810/` 盘中 Tab **16702 字**。

**已知**：`_st*` 20 个函数已搬进 `app/plugins/intraday/frontend/sector.js`（584 行），
板块轮动当天的数据已能出来（`/api/sector_track.json` 实测 3119KB / 235 snapshots / 358ms）。

**要查**：旧页盘中那 16702 字里，减去 `_st*` 覆盖的部分，剩下的是什么。
候选：冰点监控（`loadPanic`）、宽度 880（`loadBreadth880`/`_br880Paint`）、
盘中脉冲（`watchPaintLiveSector`）、AMV 实时（`_amv*`）、日内演化（`_evo*`）。

**查法建议**：拿旧页 `#panel-intraday` 的 `innerText` 按块切分（找 `card(...)` 标题），
与 v2 `.card-host` 的 `innerText` 逐块对，看到底缺哪几张卡。

### 2. C 单第二步：`_evo*` 55 个函数

任务书在 `任务书/20261025_C_盘中照旧页搬.md`，里面已有完整函数清单和四条约束。
**等第 1 步查清后再派**——如果差的 14000 字主要是 `_evo*`，那这步就是解药；
如果还缺冰点/宽度/脉冲，要先补那些。

### 3. 全量 pytest 复跑

```powershell
cd OH-WorkSpace\projects\panel-v2
& 'OH-WorkSpace\envs\qmt312\Scripts\python.exe' -m pytest app -q
```
确认三单合并后无交叉破坏。目前已知绿：`app/tests app/plugins/intraday/tests` = 194 passed。

---

## 三单的关键事实（验收已做，不用重验）

### A 板块/详情 DOM 照抄 — 通过

- 在榜格已从圆点改成 8002 的 10×3 网格：30 个 `hm-c`、`grid-template-columns` = `32px × 10`、11 个描边
- 表格 20 表头 / 25 行；网格 25 `.viz-cell`；四象 20 气泡 + 20 标签
- **顺手修的两个真缺陷**：
  1. `legacy.css.js` 只搬了 8002 的 219/487 条选择器，已补齐
  2. 别名层自引用循环 `--line: var(--line)`（CSS 判定无效）→ 改成 `var(--sc-line-src)`
     实测：`--line`=`#42506a` → 边框 `rgb(66,80,106)`

### B 右键菜单 — 通过

- 只改 `app/web/shell.js` + `shell.css`，未碰插件
- 六项真实鼠标实测全绿，含贴边翻转
- **v2 菜单节点 id = `ctx-menu`（连字符）**，旧页是 `ctxMenu`。用旧名字会误判「未实现」
- `intraday`/`amv` 无 `data-*` 属性 → 右键不生效，等 C 单补

### C 盘中第一步 — 通过但有疑点

- `_st*` 26 个符号 → `frontend/{sector,caliber,format,card.css}.js`
- 一次性布尔改成**每实例 `AbortController`**（这是最关键的改法）
- 见上面第 1 条疑点

---

## 今天踩过的坑（别再踩）

### 坑 1：改错了文件
`/` 服务的是 `engine/web/index.html`（**正本**），不是 `app/web/legacy/index.html`（旧快照，落后 540 行）。
我第一次删追踪池改的是 legacy，白做一轮。**改旧前端只改 `engine/web/index.html`。**

### 坑 2：`git` 作用域
仓库根是 `OH-WorkSpace`，不是 `panel-v2`。在 panel-v2 里跑 `git stash`/`git checkout`
会影响整个 OH-WorkSpace，C 单就这样误动了 `engine/web/kline_lc.js`。
**派活任务书必须写明：git 操作前先确认 `git rev-parse --show-toplevel`。**

### 坑 3：拿 `.click()` 和改样式测交互
`el.click()` 绕过命中测试；手动改 `style.left/top` 绕过布局逻辑。
两者都会得出假的「通过」或假的「失败」。**一律用 CDP `Input.dispatchMouseEvent` 真实事件。**

### 坑 4：grep 搜字符串当代码证据
搜 `sqlite3.connect` 会命中注释和 docstring。**用 AST 或只匹配 import 语句。**
我第一次报「三处绕过统一门面」，全是误报。

### 坑 5：闭包分析把模板串当调用
旧页大量 `onclick="fn()"`，分析器会把字符串里的函数名算成调用，
导致「142 个共用核心 / 1969 行」这种错结论。**分析前先剥引号内容，并把 `loadTab`/`activate` 列黑名单。**

### 坑 6：守门测试路径算错会假通过
`test_amv_regime_null_semantics.py` 第一版 `APP` 少上溯一级，扫描范围为空，6 项全绿但什么都没查。
**守门测试必须先证明它能抓到已知问题。**

---

## 文件地图

| 要改什么 | 改哪里 |
|---|---|
| 旧前端（8800 页面本体） | `engine/web/index.html`（11555 行，正本） |
| v2 卡片 | `app/plugins/<id>/frontend.js` + `frontend/` |
| v2 壳 | `app/web/shell.js` + `shell.css` |
| 口径库 | `app/lib/amv_regime.py`（四态唯一源） |
| 数据源门面 | `app/core/quotes.py` / `feed.py` |

| 要看什么 | 看哪里 |
|---|---|
| 当前状态 | `docs/STATUS.md` |
| 口径待办 | `docs/TODO_20261025_caliber_convergence.md` |
| 前端共用层设计 | `docs/DESIGN_fe_shared_core_20261025.md` |
| 路由归属盘点 | `docs/AUDIT_compat_routes_20261025.md` |
| 指标重复审计 | `docs/AUDIT_metric_duplication_20261025.md` |
| 今天的口径修复 | `docs/FIX_20261025_amv_null_semantics.md` |
| 三单交接 | `docs/HANDOFF_20261025_{sector_dom_copy,ctx_menu,intraday_port}.md` |

---

## 环境事实

- 8810 服务：`http://127.0.0.1:8810`，健康检查 `/_plugins/health`，18 插件
- 重启入口：`tools/ensure_8810.ps1 -Once`（**改 `frontend.js` 不用重启**，每次请求读盘；改 `plugin.json` 或后端才要）
- Python：`OH-WorkSpace\envs\qmt312\Scripts\python.exe`
- 旧服务 8800 / 8002 / 8801 仍在跑，只读参考，不动不重启
- 浏览器：`ego-browser nodejs -e "await import('file:///...')"`，脚本放 `Hubagent/01/_vis/`

---

## 待主人拍板（都属「两个值并存必须选一个」）

1. **止损分级**：已说「不要了」。待确认删到哪一层——只删档位标注（红·<5%/黄·<10%），
   还是连止损线、距离列一起删，甚至整个「临近触发预警」卡不要。
   位置：`engine/web/index.html` 的 `loadPnl`（5386 附近）与 `loadNearTriggers`（5425 附近）。
2. **`sector:952-955` 回退单轨**：嗅嗅建议删，我认为保留（是旧 8002 既有行为）。
3. **轨迹未定**：旧页何时停改（主人已说「随时可以停」）；
   v2 卡与旧页并存期，共用模块只能保证「v2 内部一份」。
