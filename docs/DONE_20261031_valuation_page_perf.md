# DONE 2026-10-31 · 估值页（PEG 仪表盘）卡顿优化 + 去广告

**结论**：改的是生成器 `夏天-activity/工具/peg/peg_dashboard.py`（不是产物 HTML），重渲染后同一滚动路径主线程耗时 153 ms → 68.6 ms（−55%），
大面积实时模糊节点 74 → 1，每帧重绘的常驻动画 27 → 0（剩下 26 个全是可合成属性），全屏 60fps 画布 → 静态。
移植前遗留的三处引流广告（B 站主页 / 微信领源码 / 粉丝福利）已清零。

## 1. 卡顿定位（先量后改，不是猜）

用 ego-browser 直连 `http://127.0.0.1:8810/api/p/valuation/view`，Chrome Performance 指标 + 逐项关闭实验：

| 指标（同一滚动路径，1400×707 视口） | 改前 | 改后 |
|---|---|---|
| 主线程 TaskDuration | 153 ms / 1.1 s | 68.6 ms |
| RecalcStyleDuration | 22.4 ms | 14.5 ms |
| 强制布局次数 | 30 | 0 |
| `backdrop-filter` 节点 | 74 | 1（仅弹窗遮罩） |
| 常驻动画 | 27（box-shadow 呼吸 ×8、全屏 canvas） | 26（全部 opacity/transform） |
| JS 长任务（>50 ms） | 0 | 0 |
| 单次 tooltip 更新 | 0.2 ms + 每次强制布局 | 内容比对 + 尺寸缓存，每帧最多一次 |

**结论**：主线程 JS 不是瓶颈（滚动 1.1 s 里 script 只占 4 ms、零长任务），瓶颈全在绘制/合成：
① 十几块 `.panel` 的 `backdrop-filter: blur(10px)` + 8 张 KPI 卡的 `blur(8px)` 随滚动反复重采样背景；
② KPI 的 `animation: breathe` 直接动 `box-shadow`，8 张卡每帧重算阴影并连带重做 blur；
③ 图表 hover 用 `filter: brightness() drop-shadow()` 且配 `transition: filter .15s`，光标扫过 120 点散点图会连续触发上百次模糊滤镜位图重建；
④ 星空 canvas 用 `requestAnimationFrame` 每帧 `clearRect` 全屏 + 重绘 60~150 颗星 + 鼠标视差，等于常驻一个全屏 60fps 画布；
⑤ tooltip 每次 `mousemove` 都 `innerHTML` + `getBoundingClientRect()`（强制布局）。

## 2. 改动清单（单文件，78 增 / 125 删）

`夏天-activity/工具/peg/peg_dashboard.py` → `render_html()` 内嵌模板：

| # | 位置 | 改动 |
|---|---|---|
| 1 | `.panel` | 去 `backdrop-filter: blur(10px)`，底色 alpha `.86/.72 → .94/.88` 补偿 |
| 2 | `.kpi` | 去 `blur(8px)`；`animation: breathe`（动 box-shadow）改为 `.kpi::after` 的 `opacity` 动画 `kpiGlow`（走合成器，主卡不再每帧重绘） |
| 3 | `.badge` / `th` / `.drawer` / `#tip` | 去 `backdrop-filter`（`#tip` 额外改 `left/top + will-change: transform`） |
| 4 | `.chart-box :hover` | `filter: brightness(1.3) drop-shadow(...)` → `filter: brightness(1.3)`；删 `transition: filter` |
| 5 | `#starfield` / `starfield()` | 逐帧循环 → 绘制一次 + resize 重绘（去掉闪烁、流星、鼠标视差） |
| 6 | `showTip/moveTip/hideTip` | 内容相同不重写 innerHTML；尺寸只在内容变化时测；位置用 `transform: translate3d`；`mousemove` 合并到每帧一次 |
| 7 | `window.__tipMove` | 修老 bug：原来 `= moveTip`，SVG 根节点把 event 对象当 x 传入，坐标恒为 NaN；改为解出 `clientX/clientY`，tooltip 现在真正跟随指针 |
| 8 | 表格搜索框 | `input` 事件加 120 ms 防抖（原来每敲一个字重排 120 行 × 24 列） |
| 9 | `.cta` 区块 + `.cta*` CSS + `@keyframes shine` | 删除（B 站 / 源码 / 粉丝福利三按钮） |
| 10 | `window.openWechat` + `.wechat` CSS | 删除（微信号 `YouDaChain703` 弹窗） |

**未改**：数据管道、PEG 计算、快照/出池记录、`refresh_dashboard.ps1`、插件 `app/plugins/valuation/*`（托管逻辑不用动，重载即读新文件）。

## 3. 验证（实测）

```
py_compile peg_dashboard.py                      → OK
rerender（load_latest_cache → render_html）       → 209699 chars, pool 120
浏览器（真实 Chromium，8810 读同一文件）
  滚动 1.1 s 主线程 68.6 ms / 样式重算 14.5 ms / 强制布局 0
  tooltip：hover 点 → display:block, translate3d(248,491)（pointer 234,476）；空白处移动 → 305,508；移出 → none
  点散点 → 搜索框填 600028，表格 120 → 1 条
  点四宫格点 → 弹窗「PEG 计算明细 · 中国石化」，点遮罩关闭正常
  五个 tab 全部可切（revision/market/news/stock/scan 均 visible 且有正文）
  搜索「600」→ 防抖后 37/120 条
  `.cta` 节点 0、vlink/bilibili 外链 0
  四宫格 SVG 在（109 个 circle），面板可见

面板内 iframe 路径（rail→估值，`/api/p/valuation/view?v=1793423375`，1440×625）
  标题对、节点 6141、`.cta` 0、正文 18290 字、四宫格 circle 109
  派发 mousemove → tooltip `display:block` 且贴合落点（截图核对偏移 = iframe 起点 + 14/16）
```

## 4. 已知取舍

- 星空不再闪烁/不再有流星、不再跟随鼠标：背景装饰不值得占一个 60fps 画布；要恢复观感请走「静态星点 + CSS 动画」而不是回到逐帧绘制。
- 图表 hover 从「发光」变「变亮」：`drop-shadow` 在 SVG 元素上代价高，观感差异小。
- 面板底色更实一点（补偿去掉模糊后的通透感丢失）。
- 视觉重排（配色、间距、四宫格文字与点重叠、四象限标签位置）按主人安排交知夏，本轮只做性能与去广告，未动版式。

## 5. 回滚

`git checkout -- 夏天-activity/工具/peg/peg_dashboard.py` 后重跑 `refresh_dashboard.ps1`（或 `rerender`）即恢复旧产物；
只回滚产物不回滚模板的话，下次刷新又会被覆盖，所以必须回退模板。
