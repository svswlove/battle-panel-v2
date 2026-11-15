# HANDOFF 20261026：旧端口下线（8800 / 8002 / 8801）

> 上一轮（01 会话 `sess_0mvo773zz_84d9b1cb9aa8a0dbeb05`）已完成前端侧的全部解耦，
> 前端对这三个端口**零请求**。剩下的是「拆自愈链 → 停进程」，本文是唯一交接面。
> 不需要回读那个会话；需要原始细节时看 `OH-Works/文件修改日志/2026-10-26.md`。

---

## 1. 结论先行

| 端口 | 是什么 | 前端是否还依赖 | 能否直接停 |
|---|---|---|---|
| 8800 | 旧引擎 `engine/server.py`（面板 + 条件单线程 + 大量采集端点） | **否**（全 Tab 472 请求全在 8810） | **不能**，要先拆 3 处 |
| 8002 | 0AMV 板块工作台（`夏天-activity/工具/dashboard`） | **否**（板块 Tab 与详情卡均 v2 原生，实测 0 请求） | **不能**，要先拆 2 处 |
| 8801 | 面板外壳宿主 `engine/shell_server.py` | **否** | **不能**，要先拆 2 处 |

「零请求」的证据：`Hubagent/01/_vis/_portaudit_all_tabs.js`（9 个 Tab 全量审计）、
`_vis/_sector_tab_audit.js`（板块单页 5/5，8002 = 0）。

---

## 2. 上一轮已经做完的（不要重做）

1. **板块详情改 v2 原生**：`app/web/shell.js` 的 `bindKlineBridge()` 两套委托合并成一套；
   `ctxOpenSector()` 改走「卡内 `_openDetail` → 壳层 `openSectorOverlay`（复用 `sector/frontend/detail.js`）
   → 父页 AmvCard 兜底」，**不再切卡**（单卡模式切卡会换掉用户当前卡，而父页 Tab 不跟着变）。
   `sector/frontend.js` 导出 `openDetailStandalone()`，自己 `injectCardCss(document)`。
2. **卡内跨卡事件补齐**：`panel:open-kline` / `panel:open-sector` 原本卡内在发、shell 从未监听
   （点了没反应）。`bindCardJump()` 已补两个监听。
3. **板块 Tab 不再拉 8002**：`app/core/main.py` 首页 `/` 在 `<head>` 注入 `window.__V2_OWNED`；
   `engine/web/index.html` 的 `renderSector()` 见到归属就让位；`v2bridge.js` 的 `dropLegacyPanel()`
   切卡后兜底拆残留 `#sectorFrame`。**已重启 8810 并实测 8002 请求 0。**
4. **急停/重启改原生**：`POST /api/shell/kill` 转发目标由 8800 改为本进程 `/api/kill`（rw 插件原生）；
   `POST /api/shell/restart` 不再拉起已退役的 8800，返回 `ok=False, engine=retired`。
5. **条件单/挂单撮合迁出 8800**：新建 `engine/executor/cond_watch.py`，已登记进
   `run_daemons.DAEMONS` + `SESSION_BOUND_DAEMONS` 与 watchdog 的 `CHECKS` / `SELFHEAL_TARGETS`。
   `engine/server.py` 里原来那两条线程（cond-push / order-watch）已删除，只留注释指引。

---

## 3. 剩下的活（按顺序做，每步可独立验收）

### 第 1 步：拆 watchdog 对三个旧端口的探活

文件：`engine/executor/watchdog.py`

- `SERVER_HEALTH_URL = "http://127.0.0.1:8800/api/health"`（约 72 行）—— 8800 存活检查。
  8800 停掉后会**持续告警 + 触发自愈拉起**。
- `CHECKS` 里 `("server", check_server)`、`("shell", check_shell)`（约 1751 行）、
  以及 8002 那一项（约 288 / 896 行附近，`check_*` 无状态文件型服务）。
- `SELFHEAL_TARGETS` 里对应的拉起项。
- `engine/tests/test_daemon_monitoring_coverage.py` / `test_watchdog.py` 会因删项变红，
  需同步调整（这两个文件里「清单必须齐全」的断言要按新清单改）。

**验收**：`pytest engine/tests/test_watchdog.py engine/tests/test_daemon_monitoring_coverage.py -q`
全绿，且 `watchdog` 的 checks 快照里不再出现 server_8800 / shell / 8002。

### 第 2 步：拆 run_daemons 的启动项

文件：`engine/executor/run_daemons.py`

- `DAEMONS` / `SESSION_BOUND_DAEMONS` 里的 `server`（`_launch_server.vbs`）与 `shell`（`_launch_shell.vbs`）。
- 注意 `test_run_daemons.py` 里有一条**硬编码清单**的断言（上一轮刚改过一次，加了 `cond_watch`），
  同步改。
- **顺序**：先改 watchdog（第 1 步），再改 run_daemons。反过来的话，中间态会被 watchdog 拉回来。

**验收**：`pytest engine/tests/test_run_daemons.py -q` 全绿；`run_daemons --list`（若有）不再列这两项。

### 第 3 步：处理 8002 的最后一条路径

- `engine/server.py` 有 `/api/sector/start`（一键拉起工作台）与 `/api/sector/health`，
  前端已不再调用，但**旧页兜底面板**（`renderSector` 的 `_sectorDown` 分支）里还有按钮。
  因为 `renderSector` 现在会因 `__V2_OWNED` 直接让位，这条路径实际不可达 —— 可一并删，
  也可留着不删（不删的唯一代价是死代码）。**建议删，并同步删 `_sectorDown` 分支。**
- `app/web/v2bridge.js` 里仍有 `AMV_FRAME_ID` / `AMV_ORIGIN` / `pushThemeToAmvCard()` /
  `watchAmvFrameLoad()` —— 这些是**父页 AmvCard 的 iframe 主题推送**，AmvCard 是旧页功能。
  若决定连旧页 `AmvCard` 一起下（它只服务于 8002），这块整段可删；
  若保留 AmvCard 作为兜底，则保留。**这是个决策点，先问主人。**

### 第 4 步：停进程 + 验收

```powershell
# 停（确认 pid 后逐个停，别用通配）
foreach($p in 8800,8002,8801){ (Get-NetTCPConnection -LocalPort $p -State Listen).OwningProcess }
```

停完必须验：

1. `_vis/_portaudit_all_tabs.js` —— 9 个 Tab 仍全部正常、零异常；
2. `_vis/_sector_detail_verify.js` —— 12/12（板块详情、K 线链路）；
3. 等 5 分钟以上，确认 watchdog **没有**把进程拉回来（日志里无对应自愈记录）；
4. `pytest engine/tests/test_watchdog.py engine/tests/test_run_daemons.py -q`；
5. `pytest app/tests -q`（`projects/panel-v2/app`）—— 当前基线 **190 passed / 1 skipped**。

---

## 4. 明确未验证 / 有风险的点

- **急停改原生后没实测**。点一次会写 `kill_switch.json` / `engine_state` / 作战表（生产状态）。
  要验就在**非交易时段**点一次急停、再点回来，确认两端状态一致。不行就回滚
  `main.py` 的 `shell_kill` 到转发 8800（但 8800 停了就必然失败，回滚没意义，只能修）。
- **`cond_watch` 只在交易日 09:00–15:30 运行**。盘外不评估。明天开盘前必须确认它被拉起，
  否则条件单不触发且**不报错、不推送**。watchdog 已登记探活，但开盘那一刻值得看一眼。
- **8800 停掉后 `engine/server.py` 里的采集端点全部不可用**。前端已不请求，
  但如果有脚本/计划任务直接打 8800 的 API，会静默失败。**停之前先扫一遍计划任务与脚本**：
  `OH-Works/自动化治理/登记册/A0_现在在跑什么.md`。
- 上一轮遗留的既有缺陷（与本次无关，**不要顺手修**）：
  `test_kline_intraday_trades` 的 `toTsMinute`（`actual="9:0" expected="9:30"`）、
  `test_extreme_drill_d` 的 D4Kill、`test_kline_mf` 的 `mainOrder`（`kline_lc.js` 今天被人加了
  supertrend + chanlun，断言没跟上）、`test_side_effect_guard` 用 `unittest discover` 再跑全套
  （timeout 600s，本机跑不完）。

---

## 5. 环境事实（省去重新摸索）

- 8810 启动：`projects/panel-v2/run.ps1`（`uvicorn app.core.main:app --port 8810`），
  **无 `--reload`**，改 `app/**` 必须重启。
- 8810 保活：`projects/panel-v2/tools/ensure_8810.ps1`（健康则不动、不健康用 run.ps1 拉起）。
  **重启就走它**，不要手搓进程。
- 8810 首页服务的是 **`engine/web/index.html`**（旧仓原件，`main.py` 运行时注入脚本），
  **不是** `app/web/legacy/index.html`。改错文件会「看起来改了、线上没变」（上一轮踩过，
  `app/tests/test_sector_tab_cutover.py` 有专门守卫）。
- 行情源已全量迁 eltdx（QMT 因政策关停）：`engine/collectors/eltdx_adapter.py` 是适配层，
  生产代码里 QMT 行情导入为 0，守门测试 `engine/tests/test_qmt_migration.py`。
- 验证脚本都在 `Hubagent/01/_vis/`，用 CDP 起 headless Chrome，直接 `node xxx.js`。
- 测试解释器：`OH-WorkSpace\envs\qmt312\Scripts\python.exe`。
