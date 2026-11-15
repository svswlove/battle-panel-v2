# 任务书 · 周末冲刺 N4 行情补口 + amv_research（kline + compat）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 N4/4
> 方向：原生实现，不转发。

## 1. 目标

三个小端点：volume_ticks（kline 0.7.0）、通配 {name}.json（compat 0.2.0）、amv_research 双路径（compat）。

## 2. 端点规格

### 2.1 volume_ticks（kline 插件）

- 旧语义：逐笔压缩 `[[minute_idx, price_milli, vol]]`（先读 8800 handler / kline 前端确认字段）
- 数据源：ctx.quotes（eltdx 逐笔；kline 已有 quotes 声明与 20s TTL 模式，照抄 quote5 的取数法）
- 非法代码 400；无数据空数组（以旧端实测空态形状为准）

### 2.2 通配 {name}.json（compat）

- 白名单校验：name ∈ READABLE_PANEL 才 ctx.panel_json(name)；否则 404（防任意文件读）
- 具体路由在前，通配在后不被遮蔽（FastAPI 顺序保证，加测试锁死）

### 2.3 amv_research（compat，双路径 /api/amv_research 与 /api/amv_research.json）

- 旧 handler：engine/server.py:2776 起（GET，参数 level/code/days/live/scope，服务端组装板块排名+成分+贡献分项+缩略线，带 _RS_CACHE）
- 数据源：amv_board.db 等（handler 内 SQL 照搬走 ctx.db_read；缩略线/MA10 逻辑原样）
- **规模控制**：若旧 handler 依赖大量私有 helper（_rs_base 等 200+ 行），评估后允许「v1 降级：只实现参数校验 + 数据源组装主干，缩略线等次要段缺省 null 并在响应 note 标注 v2-partial」——如实登记，不算失败
- 缓存：TTL 照旧（收盘口径 _RS_TTL 值从旧代码抄）

## 3. 改动白名单

```
app/plugins/kline/backend.py + plugin.json(0.7.0) + tests/
app/plugins/compat/backend.py + plugin.json(0.2.0) + tests/
docs/HANDOFF_20261024_N4_quotes.md
```

禁止：不动 core（白名单要加名字→登记 HANDOFF 交 01）；不动其他插件。

## 4. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/kline/tests app/plugins/compat/tests -q
python -m pytest -q
python tools\cutover_gate.py
# 对齐抽查：volume_ticks 与 amv_research 同参 vs 8800
```

## 5. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810。
