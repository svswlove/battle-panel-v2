# 板块/详情 DOM 照抄 8002 · 交接

2026-10-25 · 01 · 任务书 `任务书/20261025_A_板块DOM照抄.md`

## 一、结论

**在榜格已从圆点改成 8002 的 10×3 网格（日期 + 当日排名 + 分位色阶 + 描边）。**
表格 / 网格 / 四象三视图与详情卡，DOM 结构与类名已按 8002 逐字照抄。
与 8002 同日期（2026-10-23）同层级（L2）同周期（3 天）**逐格比对 diffCount = 0**。

## 二、改了哪些文件

| 文件 | 行 | 改动 |
|---|---|---|
| `app/plugins/sector/frontend/board.js` | 388 | 整文件重写：照抄 `renderList` / `heatGrid` / `hitMapHtml` / `HM_BG` / `HM_FG` / `mvHtml` / `scoreTip` / `boardScoreTip` / `renderCrumb` / `renderDirectory` |
| `app/plugins/sector/frontend/views.js` | 559 | 整文件重写：照抄 `vizGrid`（含 `spark` / `badge` / `vizTipHtml` / `vizBind`）+ `vizQuad`（含尾迹 `ribbon` / 钉住 / 就地标签避让 / 引导线 / Top N / 多空一键） |
| `app/plugins/sector/frontend/detail.js` | 590 | 整文件重写：照抄 `cardHtml`（四块左栏 + `.card-tbl` 右栏）+ `bindCard`（含 `cardSortRows` / `secRankTip` / `scoreTip` / `boardTag` / `sigBadges`） |
| `app/plugins/sector/frontend/legacy.css.js` | 439 | 追加 8002 缺失的选择器（见第四节），并修了 token 自引用循环 |
| `app/plugins/sector/frontend.js` | 627 | 编排适配：视图容器 `.vizbox > .viz-cap + #vizStage`、指数层走 `.panels.idx`、搜索下拉改 `.sd-item` 结构、详情卡接浮层 |

未动：`compute.js`（验收基线）、`backend.py`、`data/panel/*`、`engine/*`、其它插件。

## 三、逐格比对证据（不接受自报，这是实测）

口径：`2026-10-23 / L2 / 3 天 / 默认按权重变化降序`，8002 与 8810 各开一页，取每行每个单元格的文本逐格比对。

| 视图 | 比对项 | diffCount |
|---|---|---|
| 表格 | 25 行 × 11 字段（名次变化/序号/名称/评分/权重/变化/活跃涨跌/真实涨跌/主力/在榜/徽标/退出态）= 275 格 | **0** |
| 网格 | 2 栏标题 + 25 个格子名 + 41 个徽标（5 类：bg-st/bg-rk/bg-new/bg-ex/bg-re） | **0** |
| 四象 | 20 气泡 / 20 尾迹 / 20 右侧标签 / 4 象限计数（领先×7 改善×3 落后×7 走弱×3）/ x 轴 6 刻度 / y 轴 6 刻度 / 图例条全文 | **0** |
| 详情卡 | 9 列表头 / 5 个分块标题 / 4 个 `.pin-k` / 5 个 `.ctb-k` / 首行 9 个单元格 | **0** |

在榜格的实测结构：
```html
<i class="hm-c" style="background:var(--hm-b6);color:var(--fg)"><s>0904</s><b>90</b></i>
```
30 格、grid 轨道 `32px × 10`、每格 32px 高、`<s>` 日期 + `<b>` 排名 —— 与 8002 `heatGrid` 逐字一致。

## 四、修掉的两个真缺陷（不是照抄能解决的）

### 1. `legacy.css.js` 只搬了 8002 约 219/487 条选择器

自动比对 8002 `style.css` 的 487 条选择器，v2 侧缺 268 条，其中影响 DOM 照抄后能否显示的有：

- 表头列定位 `.col-hd .ch-nm/.ch-sc/.ch-wt/.ch-dl/.ch-ap/.ch-rl/.ch-mn/.th-hits`（`grid-column` 分配）
- 视图容器 `.vizbox` / `.viz-cap` / `.vizbox svg`
- 网格徽标 `.viz-cell .b` / `.bg-new` / `.bg-re` / `.bg-ex` / `.bg-st` / `.bg-rk`
- 四象交互 `.vq-trail` / `.vq-tg` / `.vq-tf` / `.vq-ring` / `.vq-hid` / `.vq-blab` / `.vq-lead` / `.vq-x` / `.vq-hint` / `.vz-grp`
- 色格图 `.hm-hd` / `.hm-grid` / `.hm-c>s` / `.hm-c>b` / `.hm-ft`
- 详情卡全套 `.sector-modal .card-*` / `.ctb-*` / `.card-tbl` / `.sg-*` / `.bd`
- 搜索下拉 `.sd-lv` / `.sd-lv.lv-l3` / `.sd-lv.lv-concept`
- 窄屏兜底三档断点（1100 / 900 / 700）

已按 8002 原值补齐，选择器统一加 `.sector-card` / `.sector-modal` 作用域（8002 是整页独占，v2 是卡）。

### 2. token 别名层有自引用循环（实测已暴露）

`legacy.css.js` 原文写着：

```css
.sector-card{
  --line: var(--line);
  --line-soft: var(--line-soft, var(--line));
  --accent: var(--accent);
  --accent-on: var(--accent-on, var(--card));
}
```

`--line` / `--accent` / `--accent-on` 在 v2 主题表里与 8002 **同名**，这样写是自引用循环，CSS 判定 invalid ⇒ 所有 `var(--line)` 失效，`border-color` 回退成 `currentColor`。
实测证据（夜航主题，修前）：`.panel` 的 `border-top-color = rgb(42, 38, 34)`（= 文字色，不是 `#42506a`），`seg-btn.active` 背景也不是 accent 色。

修法：在 `html` 上先把主题值取到不同名的 `--sc-line-src` / `--sc-accent-src` / `--sc-accent-on-src`，再由 `.sector-card` 映射。
修后实测：`border-top-color = rgb(66,80,106)`（= `#42506a` 主题线色）、`seg.active` 背景 = `rgb(77,163,255)`（= accent）。

## 五、与 8002 的有意差异

| 8002 | v2 | 原因 |
|---|---|---|
| 折线视图 `vizCompass` | 不做 | 任务书明确跳过 |
| 热力视图 `vizHeat` | 不做 | 同上 |
| `HM_BG` 写死 `rgba(139,44,31,.55)` 等 6 个裸色值 | 改 6 个 `--hm-b*` token，阈值逐字不变（.10/.22/.35/.55/.75） | 仓库守门测试禁裸 hex/rgba；且原值在夜航主题下失真 |
| 详情卡「概念 →」「☆ 自选」两个跨页按钮 | 不做 | v2 无对应入口，属跨插件跳转 |
| 详情卡「板块数据」的「实时」口径 | 纯收盘 | 主人 2026-10-22 已回退 |
| `.sector-card-tip` 浮层（8002 是 `#vizTip`） | 复用板块页那一个 | 一张卡只留一套浮层，禁两套 |
| `vizRows` 里 `weight / 1e4` | 保留同式 | compute 用 1e6 整数，`/1e4` 与 8002 同值 |

## 六、自测记录

| 项 | 结果 |
|---|---|
| `node --check` 6 个 js | 全过（含从 8810 实际下发内容复查一遍，排除「本地对、服务端错」）。**注意**：`legacy.css.js` 曾在模板字符串的 CSS 注释里写反引号，把模板截断成语法错误；已改掉，之后复查通过 |
| `pytest app/plugins/sector/tests app/tests` | **220 passed, 1 skipped** |
| 表格视图 | 25 行，11 格/行，`grid-template-columns` 表头与数据行**完全相同**（`32px 26px minmax(0,1fr) 42px 50px 54px 54px 54px 62px 46px 12px`，gap 7px），表头 10 个元素对 11 轨道（`.ch-nm` 跨 2/4，与 8002 一致） |
| 在榜浮层 | 30 格、10 列 × 32px、`<s>`+`<b>`、`hm-hd`/`hm-ft` 齐全 |
| 网格视图 | 25 格、25 条迷你走势、41 个徽标、2 栏标题 |
| 四象视图 | 20 气泡、20 尾迹（60 条 path）、20 标签、12 条网格线、2 条轴线、4 象限词、Top N 3 按钮、多空 2 按钮；点气泡 → pin 生效（ring 显 1、就地标签 1） |
| 详情卡 | 189 行成分股、9 列、2 条走势 SVG、4 个数据格、5 行贡献分项 + 合计、`grid-template-columns: 380px 735px`、表头 sticky |
| 三套主题（暖纸/夜航/月白） | 面板底色、边框、表头线、涨跌色、高亮色全部跟随；实测值见第四节 |
| 与 8002 逐格比对 | 见第三节，四个视图全 0 |

## 七、未验证 / 遗留

- **L3（231 个板块）与概念层的四象**：只验了 L2。L3 气泡数多，标签堆叠与避让未逐层点。
- **窄屏断点**：CSS 三档已补齐（1100/900/700），但只在 1440 宽视口实测过，未逐档截图核对。
- **跨浏览器**：只在 ego-browser（Chromium）验过。
- **盘中实时**：验收时是周日，板块数据为 T-1 收盘口径。
- **`backend.py` 有未提交改动**（`git status` 显示 M）：本轮未碰它，属前轮遗留，未深挖。
- **`app/tests/test_fe_shell_bridge.py` 里 `intraday` 卡**（`card.css.js` 裸 hex `#9dc3f0/#fff/#e6e6e6` 与未定义 token `--curve`）在本轮开工前就是红的，与本单无关，未修（范围外）。sector 侧两条守门测试均已转为绿。

## 八、回滚

改的都是 `app/plugins/sector/` 下文件，`git checkout -- app/plugins/sector/frontend app/plugins/sector/frontend.js` 即可回到本轮前状态（`frontend/` 目录本轮前已存在，只是内容不同）。
`compute.js` 全程未改，可作口径基准独立比对。
