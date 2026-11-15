# K 线弹窗 P1b：叠加对比 + 纸面只读标记 + 分时共轴（2026-10-27）

> 负责人：01（执行器）｜权限 write，白名单见任务书｜8810 换血由主人统一做
> 回退入口：HANA 文件历史；无 `.bak`、无新依赖、未启停任何常驻服务

## 0. 一句话

叠加对比按分时开放（行业/大盘/自定义 + 清除），分时价格轴收敛成唯一源并让主图刻度与叠加线共用；
paper 两个只读口接上买卖点/成本/委托/条件单；失败、0 条、无持仓三态分开说；下单与条件单写入仍未做。

## 1. 改动清单

| 文件 | 性质 | 内容 |
|---|---|---|
| `frontend/marks.js` | 修改 | 新增 `intradayAxis / pctFromIntraday / alignPctToSlots / overlayExtent / paperMarkData`；`availability` 支持原因覆盖；菜单加叠加子菜单；`scaleFor` 增 `extra` 参数 |
| `frontend.js` | 修改（只动 drawIntraday 轴/X/Y + 叠加绘制） | `drawIntraday(canvas, d, overlay)` 改用 `intradayAxis`；末尾画叠加线与图例；`dailyAxis` 引用保持不变 |
| `frontend/overlay.js` | 修改 | 叠加状态机（加载/替换/清除/重试）、行业映射、自定义输入、纸面数据拉取与三态原因、日线切换清叠加、底部叠加状态条、竞态序号 |
| `tests/js/p1a_marks.test.mjs` | 修改 | +14 条 P1b 用例（分时轴/对齐/叠加/菜单/纸面适配），共 41 项 |
| `tests/test_kline_overlay_p1a.py` | 修改 | 守卫随口径更新（分时也走唯一轴、纸面只读口白名单），共 39 项 |
| `docs/DONE_20261027_kline_p1b.md` | 新建 | 本文 |

## 2. 分时共轴（修 P1a 遗留的同类问题）

P1a 只把日线收敛了；分时还是「drawIntraday 自算一份、scaleFor 复制一份」，且 X 用 `nSlots => i/(nSlots-1)`，
标记层却按点数算，槽位口径不一致。P1b 一并收敛：

- **唯一源** `marks.js::intradayAxis(d, w, h, extra)`：以昨收为轴心、上下等距，`0%` 落在竖向中点；
  返回 `{pre, spanPct, lo, hi, rect, yOf, priceAtY, yOfPct, pctAtY, xAt, slots, n}`。
- `drawIntraday` 只从 `AX` 取 `padL/padR/padT/plotH/lo/hi/xAt/yOf`，不再自算；`frontend.js` 其它函数未动。
- 标记层与右键反算走 `scaleFor('intraday', cache, w, h, extra)` → 同一个 `intradayAxis`。
- 叠加线用 `AX.yOfPct(pct)`，与主图刻度同一条轴 ⇒ 不会「线按 %、轴按价格」。

## 3. 叠加对比

| 项 | 口径 |
|---|---|
| 入口 | 主图右键 → 叠加对比 ▸：行业走势 / 大盘走势（默认 `000001.SH`）/ 自定义品种… / 清除叠加 |
| 周期 | **仅分时**。日线时三项 disabled 并写原因「仅分时支持叠加（日线无昨收基准）」；切到日线会把已有叠加清掉（不留错画面） |
| 换算 | 各自 `(close / pre_close - 1) * 100`；主图与叠加各自用自己的昨收 |
| 对齐 | 只按时间标签对到主图 `session.slots`；**对不上/无标签/脏点留空不画**（不按序号硬塞） |
| 轴 | 叠加线的 % 极值并进同一根对称轴（`extent`），否则线会画出图外 |
| 行业 | `/api/watchlist.json`：先看老格式 `sector_of`，再用 `items[].sector.l2.code`；**缺映射 → 菜单项 disabled 并写明「watchlist 无该票行业映射（不猜行业）」** |
| 自定义 | 沿用旧版 `window.prompt` 输入代码；取消有 toast 反馈 |
| 失败 | 底部状态条变红「叠加失败 · 重试」（title 带原因），点击重试；**主图数据不动、不清空** |
| 清除 | 状态条点一下或菜单「清除叠加」→ 回到纯主图并 toast |
| 竞态 | 每次请求带 `oseq` 序号，切周期/连续切换时晚到的响应直接丢弃 |

## 4. 纸面只读标记（paper）

| 标记 | 数据源 | 适配口径 |
|---|---|---|
| 买卖点 | `GET /api/trades/{code}?days=120` | `points[]`：`date`(YYYYMMDD)、`t`(HH:MM)、`price`、`side`；日线按 `date` 对 bars，分时按 `t` 对 slots，**未匹配不画** |
| 持仓成本线 | 同上 `cost.paper` | **只认 paper，绝不回退 `cost.live`** |
| 委托线 | `GET /api/order/list` | 只留本票 + `REPORTED/PARTIAL`，价取 `limit_price` |
| 条件单线 | 同上 `conditions[]` | 只留本票 + `ACTIVE`，价取 `trigger.price`，标签取 `label` |

三态必须分开说（菜单项右侧 + 原因行）：

- **有数据** → `N 条`
- **真没有** → `无数据` + `近 120 日无纸面成交` / `paper 无持仓成本` / `当前无 REPORTED/PARTIAL 委托` / `当前无 ACTIVE 条件单`
- **请求失败** → `无数据` + `成交请求失败：HTTP xxx（右键→刷新行情可重试）`，不与「没有」混谈

数据格式已用真实样例核过：`/api/trades/002812.SZ` 的 point 是 `{"date":"20261021","t":"09:30","price":46.45,"side":"BUY","mode":"paper"}`，
bars 的 `date` 同为 YYYYMMDD；`/api/order/list` 条件单 `trigger.price` 可能为 null（价格型才有），null 一律不画。

## 5. 快捷键

**没加。** 核过旧版 `kline_lc.js` / `kline_order.js`：键盘只处理 Esc / Enter / ArrowUp-Down（面板内导航）与输入框，
**没有分时↔日线切换键**。按任务书「无旧版对应则不加」，本阶段只保留 P1a 的 Esc 分级。

## 6. 实测

```
pytest app/plugins/kline/tests -q                → 39 passed
node app/plugins/kline/tests/js/p1a_marks.test.mjs → 41 项全过
```

一次性浏览器检查（临时静态服务 + 合成数据 + fetch/api 桩；验完已停服务、删临时目录）：

| 检查 | 结果 |
|---|---|
| 纸面标记（1 成交 / paper 成本 1590 / 1 委托 / 无 ACTIVE 条件单） | 标记子菜单：`买卖点 ✓ 1 条`、`持仓成本线 ✓ 1 条`、`委托线 ✓ 1 条`、`条件单线 当前无 ACTIVE 条件单`；标记层有实际落笔（223 像素） |
| 点「叠加大盘走势」 | 底部出现 `叠加：上证指数 ×`，toast `已叠加 上证指数`；主图落笔 11272 → 11608（多出叠加线与图例） |
| 清除叠加 | 状态条隐藏，落笔回落至 11339（≈叠加前） |
| 切日线 | 叠加被清除（状态条隐藏），叠加子项全部 disabled 并写明「仅分时支持叠加（日线无昨收基准）」 |

## 7. 未验证 / 已知限制（必须让主人知道）

1. **行业走势取不到数（口径冲突，不是 bug）**：`/api/watchlist.json` 的行业代码是 THS 板块后缀 `881126.TI`，
   而 v2 后端 `/api/p/kline/intraday/{code}` 只认 `SH/SZ`（实测 `非法 ts_code: '881126.TI'`；同号段 `881126.SH` 能取到）。
   按「不猜行业、不新造口径」，本轮**没有自动换后缀**：行业项在有映射时可点，但请求会失败并以红条「叠加失败 · 重试」+ 原因呈现。
   要真能用，需要主人拍板一条口径（后端放行 `.TI`，或前端明确 `TI→SH` 映射），这是数据源层的事，不在本档白名单内。
2. 行业映射字段与任务书假设不同：任务书写 `sector_of`，实际文件里是 `items[].sector.l2`；两形状都兼容了，但**文件本身没有 `sector_of`**。
3. 叠加与主图都只到「按秒/分钟槽对齐」这一层，没有做多日分时（`intraday_multi`）叠加。
4. 实时行情下的叠加刷新依赖「刷新行情」或切周期；没有接定时轮询（旧版是 3s 定时器，本阶段不引入常驻定时器）。
5. 浏览器检查用的是合成数据与桩，**真实 8810 上的叠加与纸面标记仍待主人换血后验收**。
6. `drawIntraday` 的空态文案多了一条「缺昨收，无法定涨跌幅轴」——这是新增的显式失败，不是绘制退化。

## 8. 口径同步点

- 日线轴：`marks.js::dailyAxis`（P1a 已收敛）
- 分时轴：`marks.js::intradayAxis`（本轮收敛）
- 所有坐标口径只在这两个函数里；`frontend.js` 与 `overlay.js` 只取用，不得再写第二份。
  守卫：node 用例「日线轴只有一份 / 分时也走唯一轴」。

---

# 验收返修（第三轮，2026-10-27 03:3x~04:0x）

> 打回 4 项：① 行业叠加必须可用（`.TI` 适配）② `drawIntraday` 要有真装配用例 ③ `alignPctToSlots` 的 `own[k]` 错配 ④ 变更集中 + P1a 过时标题。

## F1. 行业叠加：`.TI` 身份码 vs eltdx 分时通道

权威事实（已核 `engine/server.py::_eltdx_history_minute_bars` 注释）：eltdx 分时只认交易所前缀
（`sh000001 / sz399001 / sh880008 / **sh881101**` 实测可取）；`compat_kline` 的 `.TI` 只用于 THS DB 日线/周线，
分时没有 THS 实时源。因此适配层做一次**单向、窄口径**映射：

- 命名纯函数 `marks.js::mapOverlayCode(code)`：`^(?:88[01456]\d{3})\.TI$ → 同 6 位 .SH`（覆盖 880/881/884/885/886），
  返回 `{input, req, mapped, ths}`——`input`（`.TI`）留作**展示与身份**，`req` 只用于请求。
- 其它 `.TI`（如 `887126.TI`、`899050.TI`）**不映射**，原样请求；非 `.TI` 原样透传。
- **没有**改全局 `normalize`，也没有把映射写进后端；只在 `overlay.js::loadOverlay` 一处取用。
- 失败仍如实上报：映射后拿不到数据 → 底部红条「叠加失败 · 重试」+ 原因，不谎报成功。

### 真实只读端点实测（一次性探针，只 GET，跑完删）

| 请求 | 结果 |
|---|---|
| `881126.TI`（原样） | **400** `非法 ts_code: '881126.TI'` |
| `881126.SH`（映射后） | **200** `ok=true`，`pre_close=586.76`，241 点，`last=590.49`，`pct=0.6357` |
| 对齐到 600519.SH 的 241 槽 | 非空 **241/241**，13:01（idx 121）有值 |
| 负例 `887126.TI` / `899050.TI` | `mapped=false`，请求原码 → 仍 400（拒绝猜测，符合预期） |

DOM 流验证（临时静态服务 + 桩）：行业项显示 `叠加行业走势 881126.TI`（THS 身份），点击后实际请求
`/intraday/881126.SH`，底部出现 `叠加：汽车零部件 ×`。

## F2. `drawIntraday` 真装配用例（补上 node 没拦住的那类）

上一轮漏 `avgs` 变量导致分时整体进 catch，纯函数测试全绿也发现不了。本轮加 6 条**记录型 canvas** 用例
（记录 `stroke` 的 color/width/起笔次数与全部坐标）：

| 用例 | 断言 |
|---|---|
| 有 avg | 主价格线（width 1.6）与均价线（width 1.1）都真的落笔；顶点数 > 241 |
| avg 全 null | 不崩、不画均价线、坐标无 NaN |
| 无昨收 | 早退、零 stroke，并写明「缺昨收，无法定涨跌幅轴」 |
| 叠加 null 断点 | 叠加线（width 1.3）落笔且**断成 ≥2 段**；主图线仍在；图例出现 |
| slots 13:01 | 13:01 的横坐标与 `AX.xAt(121)` 一致且位于 11:30 右侧（午休后不塌陷） |
| 单点 | 坐标有限、价格点落在绘图区中轴 |

顺带修掉一个真 bug：均价线判据原为 `avgs.length === pts.length`，而 `Number(null) === 0`，
**缺均价的点会被当成 0 价画出贴底假线**；改为「每点都有有效均价才画」。

## F3. `alignPctToSlots`：不再用 `own[k]` 猜

原实现 `own[k] != null ? own[k] : pts[k].t`：当 `own` 是**全量 241 槽表**而 `points` 只有午后几个点时，
会拿 `own[0]='09:30'` 去套午后第一点。改为三级、逐级降级：

1. 点位自带 `i`（compat_kline 形状，slot 索引）→ 优先；先拿 `own[i]` 的时间在主轴里再校一次，对得上就用；
   若 `own` 与主轴等长则直接认 `i`。
2. 点位自带 `t` → 用 `main.indexOf(t)`。
3. `own` 与点数**等长**（逐点时间表）时才允许按 `own[k]` 取。
4. 都不成立 → 该点留空（不画），一个有效点都没有则整体返回 null。

新增用例：**只有午后点**（i=121/122/123 → 落在 121/122/123，上午为空）、**points 缺槽**（i=0/5/11 → 只落 3 点、
中间留空）、**无 i 无 t 不猜**、**逐点时间表仍可用**。

## F4. 变更是否集中 / 标题

- `drawIntraday` 本轮只改了两处：均价线判据（F2 的真 bug）与既有轴/叠加绘制，没有扩写；`frontend.js` 其它函数未动。
- P1a 用例标题 `日线轴：…区间取 close+yellow+white` → `…区间取 OHLC+黄白线`（只改标题，断言未动）。

## F5. 复跑（本轮交付）

```
pytest app/plugins/kline/tests -q                  → 40 passed（P1b 的 39 + 新增 1 条适配守卫）
node app/plugins/kline/tests/js/p1a_marks.test.mjs → 52 项全过
  （其中 P1a 27 + P1b 14 + 本轮返修 11：R1×1、R3×4、R2×6）
```

新增 python 守卫 `test_overlay_code_mapping_is_local_and_narrow`：映射前缀只认 `88[01456]`、走命名常量
`THS_SECTOR_TI`、`overlay.js` 用 `map.req` 请求且不得调用 `normalize(`。

## F6. 仍未验证

- 以上真实端点验证是**只读 GET**（`/api/p/kline/intraday/*`），未触发任何 paper 写；8810 上跑的仍是旧前端，
  换血后叠加/标记的最终视觉仍待主人验收。
- 映射只覆盖 `88[01456]xxx.TI`；若将来出现别的 THS 板块号段，需重新核 eltdx 通道是否支持再扩，不默认套用。
