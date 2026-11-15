# 任务书：compat_sector 插件（旧前端板块域端点）

- 派单：01 → DSHana
- 日期：2026-10-23
- **并行执行**：另有任务在写 `app/plugins/compat/` 与 `app/plugins/compat_kline/`。
  你**只写 `app/plugins/compat_sector/`**，其他目录一律不碰。

## 1. 目标

旧面板前端的「板块」Tab 与「概念」Tab 调用以下 7 个端点。响应格式必须与旧 8002 / 8800 逐字段一致。

| 旧路径 | 归属 | 说明 |
|---|---|---|
| `GET /api/sector_card.json` | 8002? | 板块卡片数据 |
| `GET /api/sector_intraday.json` | 8002? | 板块分时 |
| `GET /api/sector_spark.json` | 8002? | 迷你走势 |
| `GET /api/sector_track.json` | 8002? | 板块轨迹 |
| `GET /api/sector_track_backfill_status` | 8002? | 回填状态（只读）|
| `GET /api/sector/health` | 8002? | 板块服务健康 |
| `GET /api/etf_sector_map.json` | 8002? | ETF→板块映射 |
| `GET /api/breadth_880.json` | 8002? | 880 广度 |
| `GET /api/temperature_latest.json` | 8002? | 市场温度 |

（表里 9 行，实际开工前先确认每个路径在 **8002 还是 8800** 上——`curl` 两个端口各试一次，
把结果写进报告。找不到的标注「旧端不可用」并跳过。）

## 2. 关键做法

**先探再写**。对每个路径：

1. `curl` 旧端拿到真实响应，存成样本（放 `docs/sector_samples/`，供比对）
2. 判断它是「单文件静态转发」还是「要组装」
   - 静态：直接 `ctx.panel_json(名)` 或 `ctx.json_read(名)` 透传
   - 组装：按旧端结构算，数据源见 §3
3. 在 `compat_sector` 插件里实现，路径**完全照旧**（不带 `/api/p/` 前缀——见 §4 路由说明）

## 3. 可用数据源（全部已在白名单，不要改 paths.py）

| 用途 | 接口 |
|---|---|
| 板块树/成分/榜 | `ctx.db_read("amv_board.db")`（表：板块表、stock_amv 等）|
| 同花顺板块 | `ctx.db_read("ths_board.db")` |
| 全市场日线/收盘 | `ctx.db_read("track_pool.db")`（kline 表）|
| 分钟 AMV | `ctx.db_read("amv_minute.db")` |
| 板块轨迹 | `data/panel/amv_live_sector_track.json`（如已在白名单）|
| AMV 快照 | `ctx.panel_json("amv_live")` / `amv_regime_latest` / `amv_breadth_latest` |
| 实时行情 | `ctx.quotes`（eltdx）|

**先跑 `python -c "from app.core import paths; print(sorted(paths.READABLE_PANEL)); print(sorted(paths.READABLE_JSON)); print(sorted(paths.READABLE_DB))"`
看当前白名单到底有什么**，只用手上有的。缺的：如果是**已有生产文件**，可以在报告里申请，
但**不要自己改 paths.py**（避免与并行任务冲突）。

## 4. 路由说明（关键，别搞错）

新后端的插件端点在 `/api/p/<插件id>/...`。但旧前端请求的是 `/api/sector_card.json` 这种根路径。

**所以本插件要注册的路径必须是 `/api/...` 前缀，而不是插件前缀。**

看 `app/core/contract.py` 怎么挂载的：插件 router 默认挂到 `/api/p/<id>`。
要挂到根路径，需要在 `plugin.json` 里找有没有对应开关；
**如果没有，就在报告里说明「需要 core 支持根路径挂载」，并先用插件前缀实现 + 提供改写规则表**
（旧路径 → 新路径），让 01 决定怎么接。

不要为了这个去改 core —— 那是 01 的活。

## 5. 边界

**允许**：只写 `app/plugins/compat_sector/`
**禁止**：改 core、改其他插件、改 `engine/`、写生产文件、插件互相 import、发 HTTP 请求、新依赖

## 6. 环境事实

- 8810 在跑（`http://127.0.0.1:8810`），旧 8002 与 8800 都在跑
- 重启 8810：
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*port 8810*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  schtasks /Run /TN "PanelV2_KeepAlive"
  ```
- manifest 参考 `app/plugins/sector/plugin.json`

## 7. 自测

1. `python -m pytest -q` → 全绿，总数 ≥196
2. 每个端点在 8810 上 200
3. 与旧端递归比对 JSON（允许数值差异，不允许键名/结构差异）
4. `python tools/cutover_gate.py` → 8 项 PASS

## 8. 交付

- `app/plugins/compat_sector/{plugin.json, backend.py}`
- `app/plugins/compat_sector/tests/test_compat_sector.py`
- `docs/compat_sector.md`（逐端点：旧端样本、实现方式、比对结果、找不到的说明）
- 回执 **≤300 字 + 路径**
