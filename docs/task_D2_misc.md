# 任务书 · 周末冲刺 D2 AMV/运维/备注 10 端点（compat + compat_misc）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 D2/4
> 背景：周末冲刺「API 全覆盖」。你是 4 个并发执行手之一，只碰自己的目录（compat 与 compat_misc 两个目录归你）。

## 1. 目标

补 10 个端点：AMV 研究 2、运维 2、UI 备注 4、交易模式 2。

## 2. 端点清单（已核实 8810 缺失）

compat 目录（AMV/模式，4）：
- `GET /api/amv_research` 与 `GET /api/amv_research.json` —— 同一处理（旧端两路径都开）；数据源 `data/panel/amv_research.json`（若文件名不同，读旧 server.py 确认实际来源；缺文件回 404 与旧端一致）
- `POST /api/amv_refresh` —— **写代理**（触发 AMV 重算，8800 落盘）：`_proxy_engine_write` 同款（app/core/main.py:379 起）；超时 30s（重算慢）
- `GET /api/trade_mode`、`POST /api/trade_mode` —— 读：从 `/api/status` 同源（battle_plan_signed 等已就绪，mode 字段在 8800 /api/status 里，代理 GET）；写：代理 8800（切换 paper/live 是全局状态，必须单一写入口在 8800）

compat_misc 目录（运维/备注，6）：
- `GET /api/ops.json?refresh=1` —— 读 `data/panel/ops.json`（refresh 参数旧端语义：强制重扫；读旧 server.py 对齐，做不到的重扫部分如实降级为直接返回 + 标注）
- `POST /api/ops/run` —— 写代理 8800（触发运维任务）
- `GET /api/ui_notes/list`（已有确认）/ `POST /api/ui_notes/add` / `POST /api/ui_notes/update` / `POST /api/ui_notes/resolve` —— 备注四件套：**先读旧 server.py 的存储方式**（大概率 data/panel/ui_notes.json 或 sqlite）；若 JSON → 受控写（`ctx.panel_write` 白名单 + 备份，参考 compat_watchlist 的 watchlist 写实现）；若 sqlite/其他 → 全部走代理 8800，不重写

## 3. 环境事实

- 代理参考：`app/core/main.py` `_proxy_engine_write`；受控写参考：compat_watchlist backend 的 `ctx.panel_write` 用法
- 旧端：`OH-WorkSpace\engine\server.py`（只读）；先 grep `ui_notes`、`ops.json`、`amv_research` 找到旧实现再动手
- compat 现版 0.1.0（15+2 路由）；compat_misc 现版 0.1.0
- **写纪律**：所有 POST 先弄清旧端写哪、怎么写，再决定代理还是受控写；拿不准一律代理（宁可多一跳，不要双写）

## 4. 改动白名单

```
app/plugins/compat/backend.py          # amv_research / amv_refresh / trade_mode
app/plugins/compat/plugin.json         # version → 0.2.0；data 声明补齐
app/plugins/compat/tests/
app/plugins/compat_misc/backend.py     # ops / ui_notes
app/plugins/compat_misc/plugin.json    # version → 0.2.0
app/plugins/compat_misc/tests/
app/core/paths.py                      # 仅补 READABLE_PANEL/WRITABLE_PANEL 名字（最小 diff，与 D1 冲突时以先合并者为准、后者 rebase）
docs/compat_batch2.md                  # 端点登记
docs/HANDOFF_20261024_D2_misc.md       # 交接（新建）
```

禁止：不动 compat_sector/compat_kline/compat_watchlist/其他插件（那是别的并发手的地盘）；不真触发 amv_refresh/ops/run（测试 mock）。

## 5. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/compat/tests app/plugins/compat_misc/tests -q
python -m pytest -q
python tools\cutover_gate.py
# 读端点对齐抽查 vs 8800
```

## 6. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810（01 统一做）。
