# 任务书 · 周末冲刺 D4 行情补口 2 端点（kline + compat 通配 JSON）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 D4/4
> 背景：周末冲刺「API 全覆盖」。两个小端点，一个在 kline 插件（逐笔），一个在 compat（通配静态 JSON）。工作量最小，先行收口。

## 1. 目标

补 `GET /api/volume_ticks/{ts_code}` 与 `GET /api/{name}.json` 通配。

## 2. 端点规格

### 2.1 volume_ticks（kline 插件，version → 0.7.0）

- 旧语义（先读 8800 server.py 或 8002 对应实现核实）：逐笔压缩 `[[minute_idx, price_milli, vol]]` 供分时图密度渲染
- 数据源：优先 `ctx.quotes`（eltdx 逐笔，kline 已有 quotes 声明与 TTL 缓存，20s 档）；旧端读不到时的空态行为照抄
- 路径参数校验：非法代码 400；无数据 200 + 空数组（以旧端实测为准，先 curl 8800 看空态形状再写）
- kline/tests 加测试：形状/代码校验/quotes 不可用时空态不抛

### 2.2 通配 `{name}.json`（compat 插件，version → 0.2.0）

- 旧语义：`/api/{name}.json` 对 `data/panel/*.json` 的通配静态转发（8800 有此路由，openapi 显示 `/api/{name}.json`）
- 实现：白名单校验 `name`（必须命中 `READABLE_PANEL` 已声明集合）→ `ctx.panel_json(name)`；未声明 → 404（不能变成任意文件读！）
- 这条路与 compat 现有的具体 JSON 端点（dashboard.json 等）共存：具体路由优先，FastAPI 路由顺序保证（具体在前通配在后）
- 测试：白名单内 200、白名单外 404、具体端点不被遮蔽

## 3. 环境事实

- kline 现版 0.6.0（manifest `data.quotes:true`、lib 有 indicators）；compat 现版 0.1.0
- `ctx.quotes` 用法参考：kline backend 的 quote5/intraday 实现（同插件内照抄模式）
- 旧端参照：8800 server.py grep `volume_ticks`、`{name}.json`（只读）
- READABLE_PANEL 现有名字见 app/core/paths.py

## 4. 改动白名单

```
app/plugins/kline/backend.py
app/plugins/kline/plugin.json        # 0.6.0 → 0.7.0
app/plugins/kline/tests/
app/plugins/compat/backend.py        # 通配路由
app/plugins/compat/plugin.json       # 0.1.0 → 0.2.0
app/plugins/compat/tests/
docs/HANDOFF_20261024_D4_quotes.md   # 交接（新建）
```

禁止：不动 core（paths.py 若需加白名单名字，登记到 HANDOFF 让 01 合并，不要直接改——D1/D2 也在动它）；不动其他插件。

## 5. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/kline/tests app/plugins/compat/tests -q
python -m pytest -q
python tools\cutover_gate.py
# 对齐抽查：8810 /api/volume_ticks/000001.SH vs 8800 同路径（结构一致）
```

## 6. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810。
