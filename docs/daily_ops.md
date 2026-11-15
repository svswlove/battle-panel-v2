# 每日操作（`tools/run_daily.ps1`）

**用途**：一条命令完成“确保 8810 在跑 + M4 切流门禁”。
**边界**：只读检查，不写生产数据、不改旧 8800/8002、不接交易。

## 何时跑

| 时点 | 命令 | 目的 |
|---|---|---|
| 开盘前 9:00 前 | `.\tools\run_daily.ps1` | 确认 8810 可用，门禁全过 |
| 盘中 11:30 / 14:30 | `python tools\shadow_check.py --once` | 双跑比对，不跑全量 pytest |
| 收盘后 15:30 | `.\tools\run_daily.ps1` | 收盘后门禁；再跑一次 shadow |
| 18:00 灌库后 | `python tools\shadow_check.py --once` | 确认 kline `last_bar_date` 推进 |

## 三个工具的关系

- `tools\ensure_8810.ps1 -Once`：只负责 8810 健康/拉起；不碰旧 watchdog。
- `tools\cutover_gate.py`：7 项门禁（健康、全部插件、shadow、AMV、无写接口、kline 边界、pytest）。
- `tools\run_daily.ps1`：先 ensure，再 gate；日志落 `data\panel_v2\logs\YYYYMMDD_HHMMSS.log`。
- `tools\shadow_check.py`：双跑比对本身；盘中单独跑，不跑 pytest。

## 失败处理

1. `ensure_8810` 失败：先看 `http://127.0.0.1:8810/api/health`，再手工 `.\run.ps1`；不要杀旧 8800/8002。
2. `cutover_gate` 的 `shadow` FAIL：看 `data\panel_v2\shadow\` 最新 JSON 的 `samples`，按域定位插件。
3. `pytest` FAIL：先修测试/插件，不切流。
4. 任何失败都不影响旧端口；停 8810 即回退。

## 不做的事

- 不注册计划任务、不建常驻服务。
- 不写 `data/panel`、不写生产库、不下单。
- 不把 `skew` / `precision_fp32` 静默吞掉；报告中单列。
