# 任务书 · 周末冲刺 D1 板块域 10 端点（compat_sector）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 D1/4
> 背景：周末冲刺「API 全覆盖」，让 `8810/legacy/shell.html` 旧界面 11 Tab 零功能损失。你是 4 个并发执行手之一，只碰自己的目录。

## 1. 目标

在 `app/plugins/compat_sector/` 补 10 个板块端点（读为主 + 1 写代理），全部行为对齐旧 8002/8800。

## 2. 端点清单（已核实 8810 缺失）

读（9）：
- `GET /api/sector_card.json`、`GET /api/sector_intraday.json`、`GET /api/sector_spark.json`、`GET /api/sector_track.json` —— 数据源 `data/panel/` 同名 JSON（panel_read 白名单已有 etf_sector_map/amv_live_sector_track；缺的按同名补白名单 + manifest `data.panel` 声明）
- `GET /api/sector/health` —— 旧 8002 `/health` 语义（ok/version）；插件内组装，不代理
- `GET /api/etf_sector_map.json` —— 已有（确认即可，不重复实现）
- `GET /api/breadth_880.json`、`GET /api/temperature_latest.json` —— `data/panel/` JSON 转发
- `GET /api/sector_track_backfill_status` —— 旧 8800 语义（GET 只读状态）

写代理（1）：
- `POST /api/sector_track_backfill` —— 同款 `_proxy_engine_write` 模式（见 app/core/main.py:379 起的 /api/shell/kill 与 /api/sign）：urllib POST 8800 同路径、超时 8s、回执原样；8800 不可达回 `{ok:false,error:'panel_down'}` HTTP 200
- `POST /api/sector/restart` —— 同上代理

（共 10 端点：9 读 + 1 写代理两路由）

## 3. 环境事实（已验证，直接用）

- 8810 运行中 core 0.3.1；compat_sector 现版 0.1.1（manifest `data.read` 已有 amv_board.db；`data.panel` 有 amv_live_sector_track/etf_sector_map）
- 生产 JSON 目录：`OH-WorkSpace\data\panel\`；写白名单 `WRITABLE_PANEL` 只含 watchlist 等，本批不新增写白名单
- 代理参考实现：`app/core/main.py` 的 `_proxy_engine_write`（读它，别自己发明）
- 旧端参照：8800 = `OH-WorkSpace\engine\server.py`；8002 = `OH-WorkSpace\夏天-activity\工具\dashboard\`（板块服务）；**旧文件只读**
- 对齐工具：`tools/compare_legacy.py`（结构一致、值允许差异）；验收基线：逐端点响应 JSON 结构与旧端一致

## 4. 改动白名单

```
app/plugins/compat_sector/backend.py
app/plugins/compat_sector/plugin.json        # version 0.1.1→0.2.0；data.panel 补声明
app/plugins/compat_sector/tests/             # 每端点至少 1 测试（mock 代理）
app/core/paths.py                            # 仅当需要补 READABLE_PANEL 名字（最小 diff）
docs/compat_sector.md                        # 端点登记追加
docs/HANDOFF_20261024_D1_sector.md           # 交接（新建）
```

禁止：不动其他插件目录/core 其他文件/engine/web/旧服务；不真调写代理落盘（测试全 mock）；不引依赖。

## 5. 自测（你沙箱若禁子进程，照批 2a/2b 办法静态检查+等价验证，命令留 01）

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/compat_sector/tests -q
python -m pytest -q
python tools\cutover_gate.py
# 对齐抽查（真实请求，读端点）：
# 8810 /api/sector_card.json vs 8800 同路径 → 结构一致（tools/compare_legacy.py 或手工比对键集）
```

## 6. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit。8810 重启由 01 统一做（并发组全部完成后一次重启，避免互踩）。
