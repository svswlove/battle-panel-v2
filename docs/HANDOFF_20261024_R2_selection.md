# HANDOFF · R2 selection 选股卡（新插件）

> 任务书 `docs/task_R2_selection.md` ｜ 2026-10-24 晚 ｜ 契约 `docs/selection_contract.md`
> 结论：**已交付**。全量 pytest `1220 passed, 4 skipped`（exit 0）、`cutover_gate` PASS（exit 0）、
> headless Chrome 实测 8.2MB 首屏 **425–568 ms**（预算 2000 ms）。未 commit、未重启 8810。

## 1. 改了什么（白名单内）

| 文件 | 性质 | 说明 |
|---|---|---|
| `app/plugins/selection/plugin.json` | 新建 | id=selection，v0.1.0，卡 `selection.main` slot=main order=55，`backend.mount=root`，`data.*` 全空 |
| `app/plugins/selection/backend.py` | 新建 | `register(ctx)` 返回**空** `APIRouter()`（零后端改动；理由见 §3） |
| `app/plugins/selection/frontend.js` | 新建 | 68.9KB，原生 ES module，零 import；结果表 / 筛选偏好 / 选股池 / 板块选项 |
| `app/plugins/selection/tests/test_selection_backend.py` | 新建 | 13 项：manifest、空 router、不抢 compat_misc 路径、无 IO |
| `app/plugins/selection/tests/test_selection_frontend.py` | 新建 | 43 项：端点集合、主题 token、虚拟滚动纪律、筛选口径、写纪律、unmount |
| `app/plugins/selection/tests/harness_virtualscroll.mjs` | 新建 | jsdom harness：真实打四端点，21 项断言，**跑完复原生产偏好与池** |
| `app/plugins/selection/tests/perf_headless.mjs` | 新建 | headless Chrome + CDP 首屏性能实测（零第三方依赖，Node 自带 WebSocket） |
| `docs/selection_contract.md` | 新建 | 四端点载荷契约 + 前端卡契约 + 与旧 Tab 的差异表 |
| `docs/fe_R2_selection.png` | 新建 | 卡面截图（1440×900，2x） |

**白名单外零改动**：`compat_misc` 一行未动，`docs/CONTRACT.md` 未动（本插件不改 core 契约），
旧 `app/web/legacy/index.html` 只读。`git status` 里 `app/core/*`、`compat/*`、`shell.js` 等
`M` 项是本次任务之前就存在的（R1/R3/R4 并行批次遗留），非本卡改动。

## 2. 交付形态

- 入口：`/v2/?card=selection.main`（v2 壳单卡模式）；`/v2/` 整壳里排在自选池（order 50）之后。
- 未改 `app/web/v2_map.json`：`selection` 仍是 `"old"`，逐 Tab 切流由 R4 统一开关，不属本卡范围。
  要立即试卡，直接开 `/v2/?card=selection.main` 或把 v2_map 的 `selection` 改成 `"v2"`。

## 3. 关键决策（含与上游的偏离）

1. **`backend.py` 是空 router，不是没有 backend.py。**
   core 的 `load_plugin()` 硬性要求 `backend.py` 可 import 且 `register(ctx)` 返回 `APIRouter`；
   而 `main.py` 的根路径冲突检查是「同一 `/api/<path>` 被两个 root 插件注册 → 启动即 RuntimeError」。
   四端点归 `compat_misc`（同样 mount=root），所以本插件必须 `mount=root`（前端消费根路径）
   且**一条路由都不注册**。将来把选股端点收编成本插件自己的域时，在 `backend.py` 加路由 +
   从 `compat_misc` 删对应路径，一次只挪一条。

2. **虚拟滚动而非分页。** 任务书给了「分页 / 虚拟滚动」二选一。选虚拟滚动：固定行高 32px、
   DOM 只留可视区 ±6 行（实测 12–23 行），排序/过滤在内存数组上做。分页会把「2824 行里找一只票」
   变成翻页找，且旧 Tab 是连续滚动，行为等价性差。

3. **筛选浮层改为卡内联面板。** 旧端把浮层 `position:fixed` 挂 body；卡在 iframe 里，
   fixed 定位与裁切不可靠。功能等价（勾选 / 搜索 / 全选清空 / 二次点击删除），位置改到筛选条下方。

4. **筛选偏好只以服务端为准**（去掉旧端的 localStorage 双写快）。双写会出现两处不一致，
   而任务书要求「POST 后回读一致」。市值 min/max 仍走 localStorage（`sel_mcap_v2`）——
   `SelectionPrefsBody` 没有市值字段，属后端契约缺口，未扩接口。

5. **股票名不点开 K 线**（旧端 `KLineApp.open`）。CONTRACT §5 禁跨插件依赖，K 线是独立插件。
   本卡只给悬停显示 ts_code。要恢复此能力需 core 提供统一导航口，另开工单。

6. **不声明 `refreshEvery`。** 数据是 T-1 盘后快照，定时重拉 8.2MB 无意义；只有「刷新」按钮与写后重拉。

## 4. 验证证据

### 4.1 全量测试与门禁

```
python -m pytest -q
1220 passed, 4 skipped, 112 warnings in 43.37s        exit=0

python tools\cutover_gate.py
PASS  42.4s  8810 /api/health=OK  全部插件健康=OK  shadow 双跑=OK  AMV 原始 JSON 比对=OK
             battle_plan 写域接管=OK  插件无写代码=OK  kline 边界声明=OK  pytest 全套=OK   exit=0
```

`app/plugins/selection/tests` 单独跑：`56 passed`。

### 4.2 首屏性能（headless Chrome 实测，非静态检查）

`node app/plugins/selection/tests/perf_headless.mjs`（临时 8811 实例，跑完已停）：

```json
{
 "ok": true,
 "navToPaintMs": 549, "paintMs": 425,
 "vp": { "clientHeight": 384, "domRows": 12, "heapMB": 20 },
 "switchBigGroup": { "ms": 22, "domRows": 12,
   "stat": "分组 砖形图 · 原始 2824 行 · 显示 12 行 · 过滤掉 2812" },
 "scroll": { "ms": 347, "domRowsBefore": 23, "domRows": 23, "minTop": 224, "scrollTop": 438 },
 "net": { "bytes": 8223653, "contentLength": 8223523, "ttfbMs": 290 },
 "checks": { "首屏 < 2000ms": true, "DOM 行数受控（<=60）": true,
   "最大分组 2824 行仍只渲染可视行": true, "切最大分组 < 500ms": true,
   "滚动 20 帧后 DOM 行数受控": true, "滚动确实位移了（minTop>0）": true,
   "无错误卡": true, "8.2MB 载荷真实过网": true }
}
```

读数口径：`paintMs` 是卡片自报的「mount 开始 → 首屏 DOM 落地」（`root[data-paint-ms]`），
`navToPaintMs` 含导航与外壳启动。网络层 `contentLength=8223523` 证明 8.2MB 真的过网，
不是走了缓存或假数据。**首屏 425ms / 预算 2000ms。**

### 4.3 交互与写（jsdom harness，真实打四端点）

`node app/plugins/selection/tests/harness_virtualscroll.mjs` → `ok: true`，21 项全 PASS，含：

- 首屏 DOM 12 行 / 当前分组原始 81 行；切「砖形图」原始 2824 行 → DOM 仍 12 行（19–22ms）
- 滚动后 `minTop` 跟随 `scrollTop`（窗口真的移了），DOM 行数仍 ≤60
- 点「涨幅」表头 → 排序生效、箭头出现、缺值沉底
- 取消科创/北交所 → 显示行数 30 → 12，且 `/api/selection_prefs` 走了 POST + 回读，状态条显示「回读一致」
- 池 ★：第一次点击只点亮「确认入池？」**不发请求**；第二次才 POST，端点按当前池态选 add/remove
- `unmount()` 后 `_sel === null`
- **生产态复原**：偏好 `pools=["mainboard"]`、`sigs={"砖形图":["ZHU_P","ZHU_O"]}`、
  池 9 只（2026-10-21）全部与 harness 开始前一致

> 注：harness 会真实写生产偏好/池，故内置「开始前快照 → 结束复原 → 断言复原」三段；
> 我另在 harness 之外手工核对过一次服务端回读，值一致。

### 4.4 静态检查

`python -m pytest app/plugins/selection/tests -q` 的 43 项前端断言覆盖：零 import / 零裸 fetch /
零裸 hex / 零写盘 / 零跨插件依赖 / 端点集合恰为 4 读 3 写 / `ROW_H=32`+`BUFFER=6` / 行绝对定位 /
`paintRows` 只遍历 `[first,last)` / rAF 节流 / `sparkSvg` 仅可见行 / 偏好 9 字段全量回传 /
`data-arm` 二次点击 / 非 ok 回执当失败 / unmount 释放 4 类资源。

## 5. 遗留与风险

| 项 | 状态 | 说明 |
|---|---|---|
| 卡片视图（表格/卡片切换） | **未做** | 任务书 §1 只点名结果表；旧 Tab 有卡片视图，属增量 |
| 股票名点开 K 线 | **未做** | 需 core 统一导航口（跨插件），见 §3.5 |
| 市值 min/max 不落服务端 | 沿用旧口径 | 后端 `SelectionPrefsBody` 无该字段；要落盘需扩 `compat_misc` 契约 |
| `v2_map.json` 未切 | 有意留 | 逐 Tab 切流归 R4；本卡只保证 `/v2/?card=selection.main` 可用 |
| 类型筛选 UI 与旧右键菜单 | 简化 | 旧端是「右键选项卡弹菜单」，本卡是筛选条上的「类型」chip + 面板；功能等价 |
| `mcap_src="snapshot"` 非 PIT | 如实透传 | 列头悬停显示「快照口径（非 PIT）」，不重算 |
| 节假日新鲜度判定 | 沿用 compat_misc | `freshness.state` 只认周末，节假日判 `unknown`（不假告警）；要精确需登记交易日历口径库 |

## 6. 复跑方式

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/selection/tests -q
python -m pytest -q
python tools\cutover_gate.py

# jsdom 交互 harness（会写生产偏好/池，但内置复原；务必在 8810 在跑时执行）
$env:NODE_PATH='OH-WorkSpace\node_modules'
node app/plugins/selection/tests/harness_virtualscroll.mjs

# 首屏性能（默认打 8810；也可指向临时实例）
node app/plugins/selection/tests/perf_headless.mjs
```

两个 `.mjs` 不参与 pytest，也不被 core 加载（core 只按 manifest 的 `frontend.module` 发一个文件）。
`harness` 需要 jsdom（在 `OH-WorkSpace/node_modules`，故用 `NODE_PATH`）；`perf_headless` 只用
Node 内置 WebSocket + 本机 Chrome/Edge，无额外依赖。
