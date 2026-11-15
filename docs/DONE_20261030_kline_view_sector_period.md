# 2026-10-30 标注 42（K 线视口）/ 53（板块详情内切周期）

## 1. 标注 42：K 线打开默认看最新 —— 复验通过，未改代码

锚点：`v2:card:selection::selection.main` 里打开的 K 线弹窗画布。

实测（headless，8810，日线 240 根）：

| 指标 | 值 |
|---|---|
| bars | 240（20251104 ~ 20261030） |
| visibleLogicalRange | from **180** / to **243** |
| 右对齐判定 | `|to - (n+3)| ≤ 2` → **true** |
| 切周线再切回日线 | from 180 / to 243，仍右对齐 |

即：打开时就显示最后 60 根（`VISIBLE_BARS=60`），to 多出的 3 根是 `rightOffset` 余量。
当前版本已满足「默认看到最新」，无需改动；主人的观感可能来自更早版本或别的入口（如从列表切票后视口继承）。

## 2. 标注 53：板块详情内切周期只切详情 —— 已改

**改前**：详情头部点周期 = `close()` + `onPeriod(n)` → 板块页全局 `_setWindow(n)`，整页重算，
关掉详情后板块页周期已被改掉，回不到原来的。

**改法**（`sector/frontend/detail.js`）：

- `openDetail` 里新增详情自己的 `st.winN` 与 `oi`（`o` 的副本）；
- 新增 `reloadIn(n)`：更新 `st.winN` → 清 `box.dataset.bound`（innerHTML 重建后卡头按钮是新节点，必须允许重绑）
  → 重拉 `/series /members /contrib`（带新 period）→ `paintCard` 重画；
- `bindCard` 里周期按钮：有 `o.onPeriodIn` 就只调它并 return，不再 `close() + onPeriod()`；
  没有（内联指数详情等旧入口）才退回旧行为。

**验证**（headless）：打开详情（周期 3日）→ 点「5日」→ 详情**保持打开**、头部周期变 **5日**、
表格与走势按 5 日重画；关闭详情后板块页不受影响（`reloadIn` 路径不调用 `onPeriod`）。

pytest `app/plugins/sector/tests`：**71 passed**。

## 3. 边界

- 只改 `detail.js` 一个文件；未动 `frontend.js` 的全局周期 setter，未动后端契约。
- 内联指数详情（`mountDetail`）仍走旧的「关窗 + 全局切」路径 —— 它没有独立窗口语义，
  要一起改需要给它单独的周期状态，属下一轮。
- 截图：`_vis/verify_20261030/sector_period_inmodal.png`。
