# 任务书 · 周末冲刺 N3 板块只读域 10 端点（compat_sector）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 N3/4
> 方向：原生实现只读端点，不转发。

## 1. 目标

compat_sector 补 10 个板块读端点（全部 GET，无写、无代理）。

## 2. 端点与数据源（已核实）

| 路径 | 数据源 / 语义 |
|---|---|
| `GET /api/sector_card.json` | 先读旧 8002（OH-WorkSpace\夏天-activity\工具\dashboard\server.py）同名 handler 定位数据源；大概率 data/panel/ 同名或 amv_board.db 组装——**以旧代码为准**，组装逻辑照搬（走 ctx.db_read，SQL 原样） |
| `GET /api/sector_intraday.json` | 同上 |
| `GET /api/sector_spark.json` | 同上 |
| `GET /api/sector_track.json` | data/panel/amv_live_sector_track.json（白名单已有 amv_live_sector_track） |
| `GET /api/etf_sector_map.json` | 已有（确认即可） |
| `GET /api/sector/health` | 组装 ok/version（语义照旧 8002 /health） |
| `GET /api/breadth_880.json` | 旧 8002 定位数据源照搬 |
| `GET /api/temperature_latest.json` | data/panel/temperature_latest.json（白名单补名字，一行） |
| `GET /api/sector_track_backfill_status` | 旧 8800（4040 行附近）：读回填状态文件；定位真实数据源照搬（多半是 data/panel 或 state 下的 json） |
| `GET /api/stock_amv/{ts_code}` / `GET /api/sector_amv/{code}` / `GET /api/sector_kline/{code}` | 旧 8800 handler 定位数据源（amv_board.db / track_pool.db 查询），SQL 照搬 |

（sector/restart 与 backfill POST 属触发类，N2 的 ops/run 同性质待主人裁决，**本单不做**，HANDOFF 登记。）

## 3. 环境事实

- compat_sector 现版 0.1.1，mount=root，data.read 已有 amv_board.db
- 白名单补名字在 app/core/paths.py READABLE_PANEL（temperature_latest 等按需；与 N2 的 "ops" 不同行，冲突后到者 rebase 一行）
- 对齐工具 tools/compare_legacy.py；基线：与 8800/8002 同参响应结构一致（值允许时间差）
- **先读旧代码再写**：每个端点在 HANDOFF 里登记「旧 handler 行号 + 数据源 + 新实现落点」

## 4. 改动白名单

```
app/plugins/compat_sector/backend.py
app/plugins/compat_sector/plugin.json      # 0.1.1 → 0.2.0；data 声明补齐
app/plugins/compat_sector/tests/
app/core/paths.py                          # 仅 READABLE_PANEL 补名（最小 diff）
docs/HANDOFF_20261024_N3_sector.md
```

禁止：不动其他插件/contract.py/main.py/engine；不写真文件；无 POST。

## 5. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/compat_sector/tests -q
python -m pytest -q
python tools\cutover_gate.py
# 对齐抽查（真实请求）逐端点 vs 旧端
```

## 6. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810。
