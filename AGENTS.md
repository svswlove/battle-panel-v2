# panel-v2 协作规则（给人类与子 Agent）

本文件只写“不会变”的边界；具体任务、接口细节看 `docs/CONTRACT.md` 和对应插件 README/测试。

## 1. 目录所有权

- `app/core/`：只由 core owner 改。任何 manifest 契约变更必须先改 `docs/CONTRACT.md` 并升 `CORE_VERSION`。
- `app/plugins/<id>/`：一个插件一个 owner；owner 只改自己的目录与自己的 `docs/<id>_contract.md`。
- `docs/`：任务相关文档可改；不要覆盖别人的报告，新增文件用清晰前缀。
- `data/panel_v2/`：插件输出，随项目走；不得写生产 `data/panel/`。
- `projects/panel-v2/` 外部：旧 8800 / 8002 / 8801 一行不动。

## 2. 数据规则

- 插件只通过 `ctx.db_read()` / `ctx.panel_json()` / `ctx.json_read()` / `ctx.lib()` 访问数据与口径；禁止 `sqlite3.connect`、`open(...,'w')`、`json.dump` 写生产。口径库必须登记 `paths.READABLE_LIB` 且 import 期无副作用。
- 一个指标一个 owner：AMV / regime / position_cap 归 `amv`；K 线归 `kline`；作战表归 `battle_plan`。别的插件只读，不重算。
- 生产 JSON 只读白名单在 `app/core/paths.py::READABLE_PANEL`；新增名字要同时改 manifest 的 `data.panel`。

## 3. 改动面

- 先写“改动面”：要改哪些文件、为什么、怎么验收。
- 超过 3 个模块或跨插件，拆子任务，不混在一个提交里。
- 旧代码不推倒重写；用“新插件 + 旧路径保留 + shadow”的方式迁移。
- 不新建常驻服务/计划任务；需要定时就用前台 `--loop` 或外部调度器。

## 4. 测试与提交

```powershell
cd <panel-v2>
python -m pytest app/tests app/plugins/sector/tests app/plugins/kline/tests app/plugins/amv/tests app/plugins/battle_plan/tests app/plugins/watchlist/tests -q
python tools\shadow_check.py --once
python tools\cutover_gate.py
```

- 改插件必须跑该插件测试；改 core 必须跑全套 + 契约测试。
- 每个提交只包含一个域；提交信息用 `feat(panel-v2): ...` / `fix(panel-v2): ...`。
- 不提交 `data/`、`__pycache__`、`.pytest_cache`、密钥。

## 5. 当前边界（2026-10-22）

- 8810 只读五插件：`sector` / `kline` / `amv` / `battle_plan` / `watchlist`；不接 QMT、不下单、不写 `data/panel`。
- `kline` 是历史日线子集：`intraday=false`，不含当日 bar 与 yellow/white/AMV 指标。
- `battle_plan` 永久只有 GET；签收/改单/急停仍在旧 8800。
- 切流评审见 `docs/M4_CUTOVER.md`；每日操作见 `docs/daily_ops.md`。
