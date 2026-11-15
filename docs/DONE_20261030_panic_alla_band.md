# DONE 20261030 · 标注 58：恐慌图叠全A 880008 涨跌（背景层）

## 需求（主人 2026-10-30，两次纠正后定稿）

- 不是把恐慌图原有两条线调浅 —— 是**把全A 880008 的涨跌加上去**。
- 呈现：**一条浅色折线**（全A 日涨跌幅）+ **折线下方的淡色面积**。
- 数据：880008 一直在取（盘中实时、盘后落数据），不要等"历史序列落盘产物"。

## 改动

| 文件 | 改动 |
|---|---|
| `app/plugins/intraday/plugin.json` | `data.quotes: false → true`；version `0.1.4 → 0.1.5` |
| `app/plugins/intraday/backend.py` | 新增 `GET /index-series?n=`：`ctx.quotes.daily_bars("sh880008", n+10)` → `{date, close, pct}` 升序；进程内缓存 120s；取不到 `ok:false + reason`，不造数 |
| `app/plugins/intraday/frontend.js` | `panicChart(p, allA)`：全A 走独立映射 `Y2`（±M% 自适应满量程，M=⌈max|pct|⌉≥2），浅色虚线 `var(--fg) 0.38`，线下到 0 轴铺 `var(--up|--down) 0.10` 面积；`panicHtml(p, allA)` 图例加"全A 880008 ±x.xx%（虚线 · 淡色面积）"；`load()` 第一批多取 `/index-series?n=160`（`S.allA`） |
| 回滚 | 上一轮误改的两条线 opacity（0.55/0.6）与中性面积已全部撤掉，恢复 0.9 / 1.0 |

## 口径

- 主轴仍是 0–100、低值=恐慌，两条原线不动；全A 只占图高做背景，**不参与主轴刻度**。
- 缺失日断开不连线（不插值、不拿 0 填）。
- 面积着色按**最新一个有值交易日**的涨跌取色（红=涨 `--up`、绿=跌 `--down`），不按日分段。

## 验证

- `GET /api/p/intraday/index-series?n=160` → `ok:true`，rows 160 条，末行 20261030，pct `-3.03`。
- 实机（headless，新 profile 无缓存）：svg 子节点依次为 `area(fill=var(--down) fo=0.1)` / `alla line(stroke=var(--fg) so=0.38)` / `ldc(so=0.9)` / `panic(无 opacity)`；图例文本 `全A 880008 -3.03%（虚线 · 淡色面积）`。
- 截图：`_vis/verify_20261030/panic_alla_band.png`。
- `intraday` 测试：11 failed / 111 passed，失败项均为既有环境红灯（node harness 执行失败、与旧页逐字对照的 evo/quotes/research），本次未新增失败。

## 第二版（21:0x，主人「把折线图下方部分全部渲染」）

- 面积由「曲线→0 轴」改为「曲线→**图底**」，`fill-opacity` 0.07。
- 全A 线 opacity 0.38 → **0.5**，线宽 1 → 1.2，仍用虚线。
- 实测：面积 path 末端 `L 574.0 130.0 L 40.0 130.0 Z`（130=图底）；线 `stroke=var(--fg) so=0.5`。
- 截图：`_vis/verify_20261030/panic_alla_full.png`。

## 留痕

- 修 `app/plugins/intraday/backend.py` 硬约束注释里"不自连行情源"一条：本插件现在经 `ctx.quotes` 取 880008 日 K（仍是 core 持有的 eltdx 单例，插件不自己连）。
