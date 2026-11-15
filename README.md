# 作战面板 v2 · A 股复盘与交易纪律工作台

[![tests](https://github.com/svswlove/battle-panel-v2/actions/workflows/tests.yml/badge.svg)](https://github.com/svswlove/battle-panel-v2/actions/workflows/tests.yml)

> A 股板块轮动、选股、K 线、复盘、预警的一体化面板。**薄核 + 插件**架构，23 个业务插件各管一块，口径只有一份实现。
>
> 定位：给个人交易者用的「看板 + 纪律工具」，不是通用行情终端，也不下单。

## 这是什么

一个跑在浏览器里的本地工作台（`127.0.0.1:8810`），把盘后到盘中的判断串成一条线：

| 域 | 看什么 | 插件 |
|---|---|---|
| 板块 | 板块树/排名/贡献分解/结构标签/分钟轨迹 | `sector` `compat_sector` |
| 选股 | 盘后快照 + 盘中刷新、筛选偏好、买点信号 | `selection` |
| 个股 | 日线/分钟 K、筹码（L2）、多空状态树、估值 | `kline` `compat_kline` `state_tree` `valuation` |
| 复盘 | 每日复盘 MD 渲染、六格摘要、纸面账本 | `review` `paper` `dashboard` |
| 纪律 | 作战表、预警条件单、急停、交易模式 | `battle_plan_rw` `alert` `ops` |
| 自选 | 自选池/分组/富化徽标/关键位 | `watchlist` `compat_watchlist` |
| 其他 | 市场温度、情绪、AMV 活跃市值、概念知识库 | `thermometer` `emotion` `amv` `concept` |

数据口径原则（本项目的第一原则）：

- **一个指标一个 owner**：产物归生产插件，消费方经 core 读，不重算、不直连别人的目录；
- **缺数据就标缺**：宁可 503「数据不可用」，也不编造或用近似值迁就；
- **插件只经 `ctx` 摸数据**：禁止自己 `sqlite3.connect`，禁止跨插件 import。

## 架构

```
app/
  core/        薄核：启动、插件扫描、路由挂载、生命周期、健康检查、静态壳、契约(ctx)
  plugins/     23 个域插件，每个目录一份 plugin.json + backend.py + frontend.js + tests/
  web/         壳页 shell.html / shell.js / shell.css
  lib/         随包口径库（5 个纯函数模块 + VENDOR.json 记来源哈希）
docs/          契约、任务书、迁移记录（CONTRACT.md 是插件总线唯一权威）
tools/         运维与构建脚本
```

插件通过 `PluginContext` 拿数据，读写全部走白名单（`app/core/paths.py`）：

```python
ctx.panel_json("watchlist")        # 只读生产 JSON（白名单内）
ctx.db_read("amv_board.db")        # 只读 SQLite（白名单内）
ctx.state_write("kill_switch", x)  # 受控写：备份 + 原子替换 + 审计
ctx.lib("amv_regime").sma          # 口径库，只暴露声明过的纯函数
```

单插件加载失败不影响整站：core 只 warning，其余插件照常挂载。

## 快速开始

需要 Python 3.10+。

```bash
pip install -r requirements.txt
python -m uvicorn app.core.main:app --host 127.0.0.1 --port 8810
# 打开 http://127.0.0.1:8810/v2/
```

Windows 也可以直接 `.\run.ps1`。

环境变量（都有默认值，独立部署时通常只需要第一个）：

| 变量 | 含义 | 默认 |
|---|---|---|
| `PANEL_V2_WS` | 工作区根（`data/`、`engine/`、`夏天-activity/` 所在） | 项目上溯两级 |
| `PANEL_V2_DATA_DIR` | 数据目录 | `<WS>/data` |
| `PANEL_V2_OUT_DIR` | 插件产物/缓存（纸面账本也在这里） | `<项目>/data/panel_v2` |
| `PANEL_V2_PLUGINS` | 插件目录 | `app/plugins` |

## 数据层：需要自备（重要）

**本仓库只含代码，不含任何行情数据与交易记录。** 面板是这条数据链路的消费端，源头是若干盘后脚本与行情客户端。要看到真实内容，需要自己准备：

| 数据 | 位置 | 说明 |
|---|---|---|
| 生产快照 JSON | `<WS>/data/panel/*.json` | 自选池、选股快照、作战表、预警、AMV 实时快照等 20 余份 |
| 板块/行情库 | `<WS>/data/*.db` | `amv_board.db`（逐股活跃市值日序）、`ths_board.db`（板块成分）、`ths_index.db`（板块指数日线）、`track_pool.db`、`buy_signal.db`、`amv_minute.db` |
| 知识库 | `<WS>/夏天-activity/概念追踪/`、`每日复盘/` | Markdown 目录，复盘页按需渲染 |
| 行情客户端 | `eltdx`（可选依赖） | 盘中快照/分钟线；不可用则相关字段留空或降级 |

**空手起也能跑**：没有上述数据时，核心与 22 个插件照常加载（`alert` 需要旧引擎模块，单独部署时缺），109 个 GET 接口的实测分布是

```
200 -> 50 个（不依赖数据的页面与元信息）
503 -> 37 个（如实报「数据不可用」，前端显示缺数据而不是弹故障）
400/404/422 -> 22 个（参数缺失或路径不存在）
500 -> 0 个
```

也就是说：clone 下来直接起服务，界面能打开、导航能用，只是各页面基本是空的。

## 测试

```bash
python -m pytest app/tests app/plugins app/lib -q     # 1785 passed, 3 skipped
```

测试按插件分目录（`app/plugins/<id>/tests/`），核心契约与跨插件口径在 `app/tests/`。
几个门禁值得知道：`test_single_source.py`（同一口径不许有第二份实现）、`test_web_single_source.py`（vendored 文件必须与源一致）、各插件的契约测试（返回字段逐字对齐）。

**CI**：每次推送 / PR，GitHub Actions 在干净的云端 Windows 机器上装依赖并跑同一套测试。
云端没有本机的生产数据，因此 41 条「对着真实数据断言」的用例会被自动跳过
（`app/conftest.py` 里的 `needs_data` 清单），其余 1744 条照常跑；本地有数据时一条都不跳。
实测：无数据环境 `1691 passed, 80 skipped`（其中 41 条是本机制跳过），有数据环境 `1785 passed, 3 skipped`。

## 目录与约定

- 插件四件套：`plugin.json`（manifest，含版本与数据声明）+ `backend.py`（`register(ctx) -> APIRouter`）+ `frontend.js`（页面卡）+ `tests/`。
- 插件目录内的文件只由该插件改；跨插件通信一律经 core。
- 前端零构建：原生 ES 模块 + CSS 变量，改完刷新即生效。
- 桌面外壳 `shell/` 是 .NET + WebView2，可选，不影响网页端使用。

## 已知边界

- `alert` 插件依赖旧引擎的 `add_alert` / `alert_store` 模块（条目结构唯一实现），单独部署时会加载失败（其余插件不受影响）。
- K 线弹窗复用一套旧页 K 线模块（`engine/web/` 下的 js），这些资源不随本仓库分发，缺它们时弹窗不完整。
- L2 筹码（主力动向）来自通达信协议客户端，需要本地 `.cah` 缓存与 7709 行情通道，公开环境不可用。
- 盘中实时字段依赖 `eltdx`；不可用时如实留空，不做近似替代。

## 许可

MIT，见 `LICENSE`。

**不构成投资建议。** 本项目是行情展示与纪律工具，收益与风险都由使用者自己承担。
