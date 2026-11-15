# 2026-10-27 页面标注 8 条 · 汇总与验收（01 收口）

> 来源：主人在 8810 页面上留的 8 条标注（编号 `N20261027-*`）。
> 执行：子代理分插件落码，01 规划 + 独立复跑 + 真实浏览器核验。
> 分项记录：`DONE_20261027_sector_index_all.md`（26/27）、`DONE_20261027_annotation_ops_scroll.md`（28）、
> `DONE_20261027_annotations_intraday_evo.md`（29–32）、`DONE_20261027_annotations_research_live.md`（33）、
> `DONE_20261027_annotation_amv_forecast_live.md`（33 的预测实时源）。
> 回退入口：HANA 文件历史（`OH-Works/文件恢复指引.md`）；未留 `.bak`。

## 1. 八条标注与结果

| 编号 | 页面 | 现象 | 根因 | 结果 |
|---|---|---|---|---|
| `-26` | 板块 | 指数层只显示 6 个，右侧没有走势与详情 | 只渲染前 6 + 右栏缺失（不是标题写错数） | 12 个全列，右栏走势 + 成分 + 数据；点行只切右栏 |
| `-27` | 板块 | 「区间 N 交易日」提示多余 | 旧布局没有该行 | 删除；双滑块其余逻辑未动 |
| `-28` | 后台 | 卡片内部还有一条滑块 | 三张表各自 `.ops-wrap` 内滚，外壳反而滚不动 | 卡片内滚动容器取消，滚动唯一归属外壳 `stage` |
| `-29` | 盘中 | 预测不刷新，一直是 10-23 | 前端只 GET 静态 `amv_live.json`，生产端自 10-23 未再发布 | 接通已有 `POST /api/amv_refresh`（盘中 60s 一次、午休/盘后/周末不发）；实测文件已更到当天 |
| `-30` | 盘中 | 行情叠加开关无反应 | `/sector_track.json` 快照缺 `idx_chg` | 读路径按分钟用同源指数分时补齐；120 个快照全部带 5 个有源指数 |
| `-31` | 盘中 | AMV 信息行在行情模式仍显示 | 模式切换未隔离 | 行情模式不渲染预测浮层与全 A 信息行，标题改「行情涨跌」 |
| `-32` | 盘中 | 行情图空白 | 叠加数据缺失（同 `-30`），另缺昨收时曾用当日开盘顶替 | 补齐后可绘；缺独立昨收一律标无数据，不用开盘伪造涨跌基准 |
| `-33` | 盘中 | 研究区用昨日收盘数据 | 全市场快照按全库 6164 码取数（86s）→ 超 6s 超时 → 回落收盘 | 改用在市清单（暖路径 2.8s）；前端按 TTL 自动重试；实测返回今日实时 |

## 2. 独立验证（01 复跑，非子代理自报）

| 项 | 命令 | 结果 |
|---|---|---|
| 板块 | `node app/plugins/sector/tests/js/sector_index.cdp.mjs` | **27/27** |
| 后台 | `node app/plugins/ops/tests/js/ops_scroll.cdp.mjs` | **75/75**（三视口） |
| 盘中模式/叠加 | `node app/plugins/intraday/tests/js/evo_mode.cdp.mjs` | **21/21**，console 异常 0 |
| 盘中前端频次 | `pytest app/plugins/intraday/tests/test_amv_refresh.py` | **8 passed** |
| 盘中功能 | `pytest app/plugins/intraday/tests/test_evo_mode.py test_research.py` | 除 1 条既有 legacy 对齐例，其余通过 |
| 板块后端 | `pytest app/plugins/compat_sector/tests` | **63 passed** |
| AMV 取数范围 | `pytest engine/tests/test_amv_live.py` | **21 passed**（含新增 `TestRunOnceScope`） |
| 面板全量 | `pytest app -q` | **1572 passed / 13 failed / 3 skipped**；失败集 = 12 条旧页对齐（既有）+ `test_panic_store_mode_shape`（既有），无新增 |

### 生产接口实测（`127.0.0.1:8810`，只读 GET + 一次既有动作 POST）

- `GET /api/amv_research.json?live=1`：首轮 **4.2s**（< 6s 门限），随后 **0.0s** 缓存命中；`asof=2026-10-27 13:49:51`、`live_mode=true`、`live_err=null`。
- `GET /api/sector_track.json`：`date=20261027`，120 个快照 **120 个带 `idx_chg`**；上证 −0.062%、创业板 −0.349%、科创50 −1.401%、沪深300 −0.047%、中证1000 −0.552%。
- `GET /api/amv_live.json`：`trade_date=2026-10-27`、`asof=2026-10-27 12:19:35`、`forecast.stage=中午模型`、`anchor=11:30`；当日有来源。
- `POST /api/amv_refresh`：7.03s → `ok:true`、`coverage.pct=99.71%`、`stale:false`、`indices=9`（该动作是既有按需刷新，旧设计即此，不新增常驻）。
- 重启 8810 后 `/_plugins`：`sector 0.6.3`、`ops 0.3.1`、`intraday 0.1.3`、`compat_sector 0.2.1`。

## 3. 改动面

- 插件包内：`sector`（frontend + plugin.json）、`ops`（frontend + plugin.json）、`compat_sector`（backend 读路径 + 版本）、`intraday`（frontend.js / frontend/evo.js / frontend/research.js / frontend/amvrefresh.js 新增 / plugin.json）。
- 旧仓一处：`engine/executor/amv_live.py` 仅 `run_once` 的取数范围（v9 公式、覆盖闸、tick 时间闸、`_publish_stale` 未动）。
- 未动：`app/core/`、`app/web/`、旧页 `engine/web/`、其它插件、生产 state/账本；无新依赖、无跨插件 import。

## 4. 生效状态

- 8810 已按新代码重启（2026-10-27 12:22），健康检查通过；`/_plugins` 版本号已是新值。
- 页面刷新即可看到全部修复；旧简化 K 线仍只作回退，未改入口。

## 5. 遗留 / 边界

| 项 | 状态 |
|---|---|
| 12 条 `*_byte_identical_to_legacy` | **既有红灯**，跑旧页片段时 `cls is not defined` / `legacy-quotes.js` 语法错，属对齐脚本抽取问题；本批之前即红，未修（旧页只读） |
| `test_panic_store_mode_shape` | 既有红灯（panic 数据源形状），本批之前即红，未修 |
| 节假日（落在工作日） | 前端按工作日判交易，会各触发一次 `POST`，服务端返 `ok:false`，卡片显式标「刷新失败」；去噪需新接口，未做 |
| 触屏 / 移动端 | 未验证 |
| 盘中卡 20s 整体轮询 | 未改；预测刷新独立 60s 节流 |