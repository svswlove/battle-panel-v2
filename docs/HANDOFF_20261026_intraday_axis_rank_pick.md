# 盘中卡 · 2026-10-26 三条截图改动 + 「返回全A」忙碌态

> 交接对象：后续维护 `app/plugins/intraday/frontend/` 的人（含 DSHana）。
> 白名单：只动 intraday 插件 `frontend/` + `tests/`；`engine/` 只读参考，未改。

## 一、主人截图三条（原话 → 落地）

截图：`db875299f86bd644df249423/粘贴图片_mvo1ztdy_971bf6d8.png`（1920×1030，三处红框）

### ① 固定时间轴 / 取消滚轮缩放

> 「这两张图表的滚轮缩放功能取消。固定时间轴，盘中不再拉伸K线。例如11点30K线就只能走到一半。」

- 旧实现（= 旧页 8800）：`X(i) = ML + i*(W-ML-MR)/(n-1)` —— **按采样点索引**映射。盘中只有半天数据时，那半天被拉满整幅，看上去像「一天已经走完」。
- 新实现：**交易日分钟轴**。`09:30–11:30 + 13:00–15:00 = 240 分钟`，午休压缩（11:30 与 13:00 共用同一个 X＝半场）。所以 11:30 恰好 50%，线随真实时间生长。
  - `evo.js`：`SESS_OPEN/SESS_NOON/SESS_AFTER/SESS_CLOSE/SESS_MIN` + `minOfDay()` + `tradingMin()`；amv / quote / contrib 三张图 X 全部改 `ML + tradingMin/240*plotW`。
  - `evoXl()` 改为死写 `09:30 / 11:30 / 15:00`（轴不动，标签也不该飘）。
  - `idxAt()` 由「索引比例反算」改为「clientX → 交易日分钟 → 最近采样点」；新增 `pxOf()` 给 `drawCross()` / `drawRange()` 同源取 X（否则光标竖线会压不到线上）。
- 连带删除（都是「改缩放比」的东西，与「固定」互斥）：
  - 滚轮监听整段（含 90ms `wheelLock` 节流、`createState` 里的 `wheelLock` 字段）；
  - Shift + 左键平移（顺带消掉旧页那个 `data-n` 当全局点数的 bug）；
  - **右键量区间松手不再收窄窗口**：区间改成纯量具（带子 + 区间卡留在屏上，Esc / × 清）。若保留收窄，轴立刻不再是「固定」。
- 未删：右键拖拽画区间带 + 区间涨幅卡、Esc 清除、`.evo-hit` 点曲线钉住。

### ② 榜单 8 → 10

> 「板块由8个增加为10个。」

- `pick()`：`const K = 8` → `10`。正向 10 行、负向 10 行；「正负对比」仍是 top5 + bottom5 = 10（本来就没变）。
- 恰好对上 `EVO_PAL` 的 10 色，不需要新增颜色（`test_evo_palette_shape` 已守 10 色不重复）。
- CSS 一行未改：`.evo-rank{max-height:300px;overflow-y:auto}`，实测 10 行 `scrollHeight == clientHeight == 300`，不出现滚动条。

### ③ 点板块 → 跳到下方研究卡并选中

> 「点击板块时，不再默认打开板块详情，而是跳到下方显示，同时板块内排名内对应板块出现选中效果。」

- 榜行：去掉 `data-open-amv` / `data-open-name`（宿主不再弹板块详情），改 `data-act="evo-goto" data-goto="<code>"`。
- `evo.gotoBoard(st, code)` → `st.hooks.pickBoard(code)`。
- `frontend.js` 新增 `pickBoard`：
  1. `research.rsSet(S.research, 'sel', code)` → 左栏该行 `tr.pk.sel`（选中效果）+ 中栏切该板块成分股；
  2. `S.el.secResearch.scrollIntoView({behavior:'smooth', block:'start'})` → 跳到下方。
- `research.js` `paint()` 末尾：选中项变化时把该行滚进左栏可视区。
  **坑（已修）**：一开始用 `row.scrollIntoView({block:'nearest'})`，它会把外层滚动容器 `section.stage` 一并带走，把 evo 刚滚到研究区头的位置顶回 883px（实测）。现改为只改左栏自己的 `scrollTop`。
- 未改动范围：板块轮动（sector 四象标签/气泡）仍带 `data-open-amv` 打开详情 —— 截图箭头只指日内演化右榜，未擅自扩散。

## 二、「返回全A / 回成分股」忙碌态（主人拍板执行）

- 症状：点下去到数据回来有 1~1.5s 静默（盘中数据大 / 网络慢更久），按钮毫无反馈。
- 形态（与卡内「补齐缺口 → 检查中…」同一套语气，不弹框、不遮表）：
  - `disabled` + `aria-busy="true"` + `class="busy"` + 文案 `返回全A…` / `回成分股…`；
  - `::before` 一个 6px 圆形脉冲点（`animation: rs-pulse .9s`），`cursor:progress`，边框走 `--accent`；
  - `prefers-reduced-motion` 下停动效，只留静态点。
- 实现：
  - `research.js` 新增 `backBtn()`（两个标签写进 `data-idle` / `data-busy-txt`，忙碌时渲染禁用态）与 `setBackBusy(st, on)`（**只改属性/文本，不重绘整块**——重绘会重置两栏滚动位置、丢焦点）。
  - `load()` 里 `st.busy = true` 之后 `setBackBusy(st, true)`；`finally` 里 `st.busy = false` 之后 `setBackBusy(st, false)`（失败/超时也复原，不把人锁在忙碌态）。
  - `research.css.js` 加 `.rs-back.busy` 规则；`@keyframes rs-pulse` 放 `card.css.js`（research.css.js 的守门要求每行带 `.it-card` 前缀，`@` 规则不过关）。

## 三、验证

| 项 | 结果 |
|---|---|
| `pytest app/plugins/intraday/tests app/tests -q` | **255 passed / 1 skipped**（改前 250/1，净 +5） |
| 真浏览器探针 `Hubagent/01/_vis/_c2c_verify.js` | **24 / 24 PASS**（真实鼠标 / 滚轮 / Shift+拖拽 / CDP 900ms 网络延迟） |
| 固定轴像素 | 09:30 → x=46（贴左轴）、11:30 → x=313（正中间＝半场）、15:00 → x=580（贴右轴） |
| 滚轮 | 三次滚轮后 path `d` 逐字不变、`data-off/data-n` 不变 |
| 榜单 | 10 行、无 `data-open-amv`、全带 `data-act="evo-goto"` |
| 点榜 | 研究区 top=50（进视口）、`tr.pk.sel=881281.TI`、页签数 13→13（未弹详情） |
| 忙碌态 | disabled / aria-busy=true / class=busy / `::before` animation=`rs-pulse` / 文案「返回全A…」；忙碌中再点不多发请求；2.6s 后复原为「← 回成分股」且中栏 300 行 |
| 与旧页对齐 | `evo_align.mjs`：`sameStruct=true`（刨掉 path d / X 标签 / 榜单块后逐字节一致）、`yEqual=true`（曲线 Y 值未走形） |

## 四、有意偏离旧页（规格变更，非走形）

1. X 轴：索引映射 → 交易日分钟映射（主人 2026-10-26）。
2. 榜单：8 → 10（主人 2026-10-26）。
3. 滚轮缩放 / Shift 平移 / 右键收窄窗口：全部删除（与「固定时间轴」互斥）。
4. 「返回全A」按钮多 `data-idle` / `data-busy-txt` / `aria-busy` 三个属性（旧页没有忙碌态，这正是主人要补的缺陷）。

`tests/evo_align.mjs` 的 `stripChanged()` 与 `tests/research_align.mjs` 的 `normalize()` 已把上面 1/2/4 单列，其余仍要求逐字节对齐。

## 五、仍然没验的

- 盘中实时增量（边跑边来新点时段，时间轴右端是否平滑延伸）——需要交易时段真跑。
- 窄屏 <1100px 下 10 行榜 + 两图的实际排布。
- 「点榜跳转」在卡片滚动位置很深时的顺滑度（当前 smooth，headless 下测到 top=50）。
