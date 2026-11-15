# 任务书：compat_misc 插件（其余只读端点）

- 派单：01 → DSHana
- 日期：2026-10-23
- **并行执行**：另两个任务在写 `compat_watchlist/` 和其他插件。**只写 `app/plugins/compat_misc/`**。

## 1. 目标

旧面板还剩一批只读端点没接。本插件负责它们，响应格式与旧 8800 逐字段一致。

| 路径 | 说明 | 优先级 |
|---|---|---|
| `GET /api/ops.json` | 后台任务监控（后台 Tab 首屏）| 1 |
| `GET /api/order/list` | 委托列表（盘中 Tab + K线弹窗用）| 1 |
| `GET /api/trades/{ts_code}?days=` | 买卖点标记（K线图上的 B/S 点）| 1 |
| `GET /api/condition/list` | 条件单列表 | 2 |
| `GET /api/paper_account` | 纸面账户 | 2 |
| `GET /api/ledger` | 台账 | 2 |
| `GET /api/selection` | 选股页主数据 | 2 |
| `GET /api/selection_board_options` | 选股板块选项 | 3 |
| `GET /api/selection_pool` | 选股池 | 3 |
| `GET /api/selection_prefs` | 选股偏好 | 3 |
| `GET /api/stock_amv/{ts_code}` | 个股 AMV 序列 | 2 |
| `GET /api/sector_amv/{code}` | 板块 AMV 序列 | 2 |
| `GET /api/sector_kline/{code}` | 板块 K 线 | 2 |
| `GET /api/ui_notes/list` | UI 备注 | 3 |
| `GET /api/review.md` | 复盘 markdown | 3 |
| `GET /api/review_chart.json` | 复盘图数据 | 3 |

**全部只读，不要实现任何 POST。**

## 2. 关键做法

1. **先探旧端**：每个路径 `curl http://127.0.0.1:8800/api/...` 存样本，按真实响应建结构。
   找不到的（404）在报告里标注「旧端不可用」并跳过。
2. 再读旧仓源码（`engine/server.py`）看它从哪取数，判断可用的白名单接口。
3. 数据源优先顺序：
   - `data/panel/*.json` → `ctx.panel_json(名)`（先跑
     `python -c "from app.core import paths; print(sorted(paths.READABLE_PANEL))"` 看有什么）
   - `data/*.db` → `ctx.db_read(名)`（同样先看 `READABLE_DB`）
   - 行情 → `ctx.quotes`
4. **缺数据源时**：返回旧端同构的空骨架（键名齐、值 null/空列表），并在报告里列出「需要 01 补的白名单」。
   **不要自己改 `app/core/paths.py`**（会和其他并行任务冲突）。

## 3. 边界

**允许**：只写 `app/plugins/compat_misc/`
**禁止**：改 core、改其他插件、改 `engine/`、写任何生产文件、插件互 import、发 HTTP、新依赖

## 4. 环境事实

- 8810 在跑，旧 8800 在跑
- `backend.mount` 必须 `"root"`
- 重启 8810：
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*port 8810*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  schtasks /Run /TN "PanelV2_KeepAlive"
  ```
- **你没有 shell**：不要 curl/pytest/重启，只写代码并在报告标「未运行验证」，01 会验。

## 5. 交付

- `app/plugins/compat_misc/{plugin.json, backend.py}`
- `app/plugins/compat_misc/tests/test_compat_misc.py`
- `docs/compat_misc.md`（逐端点：旧端样本、数据源、实现方式、缺什么白名单）
- 回执 **≤200 字 + 路径**

## 6. 做不完时

按 §1 的优先级从 1 往 3 做，先交高优先级。
