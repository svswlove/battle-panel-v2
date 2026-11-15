# 任务书 · 周末冲刺收尾 M1 补 8 个缺口端点（P0×4 + P1×2 + P2×2）

> 派单：01 → 子代理 ｜ 2026-10-24 ｜ 依据：docs/audit_20261024_sprint.md（嗅嗅对抗审查）
> 方向：全部原生实现，不转发；数据源用白名单机制，不绕过。

## 1. 目标

8 个端点，两个插件目录承载 + 一处旧壳常量不动（SECTOR_ORIGIN 由 01 另行处理）。

## 2. 端点规格（语义唯一源 = engine/server.py 行号，只读参考）

### A 组 · ops 插件（现 0.2.0 → 0.3.0，目录 app/plugins/ops/）

**A1 `GET /api/panic`（P0，server.py:1236 起 + 991 起实现段）**
- 历史 ← data/panel/panic_breadth.json（白名单补 "panic_breadth"）；实时 ← eltdx 快照 880005/880006/880009（走 ctx.quotes，插件 manifest 声明 data.quotes:true）
- 算法口径唯一源在 engine/collectors/panic_breadth.py——**不要抄算法**：与 ops/run 同一先例，core 侧动态加载该模块的纯计算函数（build_panic_payload / _panic_stage / _panic_session 若为模块级函数），core 新建 app/core/panic_calc.py 包装；无法纯函数化的部分（如 _panic_snapshot 连 eltdx）在 core 包装层用 ctx.quotes 重写，HANDOFF 登记
- 无 store → 503 {"ok":false,"error":"no_store",...}（旧语义）
- **此端点 3s 一刷，注意用 panic_breadth.json 的 doc 整体缓存 + 快照 TTL 2.8s（旧常量照搬）**

**A2 `POST /api/amv_refresh`（P0，server.py:2203）**
- 旧语义：线程跑 executor.amv_live.run_once(publish=True)，锁防并发（429），超时 504
- 原生化：走 ctx.ops_run 同款先例——core 扩 ops_run.py 支持 callable 加载 executor/amv_live.py 的 run_once；ops 插件声明 data.ops_run:true
- 注意：这不是 schtasks，是同进程 Python 函数调用（旧端就是 threading.Thread 包 subprocess-free 调用）；保持锁+超时语义

**A3 `POST /api/sector_track_backfill`（P1，server.py:2259 起）+ A4 `POST /api/sector/restart`（P0，server.py:2139 起）**
- 两个都是触发类：backfill 起线程跑回填；restart 拉起 8002 进程（subprocess）
- restart 走 ops_run 通道（与 run_manual 同性质：白名单+锁+跨进程授权在 core）；backfill 若旧实现是纯函数线程也走 ops_run 通道加载
- 先读旧实现再定，HANDOFF 登记「旧 handler 行号 + 处理方式」

**A5 `GET /api/physical_ai.json`（P2，server.py:97 起 + 搜 physical_ai handler）——已取消**
- 主人 2026-10-24 裁定：物理 AI 指数是刚加的新功能，暂不迁移，后续再迁
- 不实现、不登记白名单；旧前端 404 时整条不渲染，属设计内优雅降级，不需要处理

### B 组 · kline 插件（0.7.0 → 0.8.0，目录 app/plugins/kline/）

**B1 `GET/PUT /api/kline-indicator-settings`（P1，server.py:715 起）**
- GET：读 夏天-activity/工具/xiaoshi_cache/kline_indicator_settings.json（READABLE_JSON 补名）
- PUT：写同一文件——新机制：engine 缓存类小文件读写，建议 core 加 READABLE_JSON + WRITABLE_MISC 双白名单（或复用 state 通道思想新增 misc_write）；按最小改动选：core/paths.py 加 WRITABLE_MISC {"kline_indicator_settings": ...} + contract.py ctx.misc_write()（照 state_write 模式：备份+原子+审计）
- 前端 kline_lc.js:3453 调用，PUT body 是 dict

**B2 `GET /api/kline/{code}/xdxr`（P1，server.py:624 起）**
- 通达信除权除息事件（pytdx get_xdxr_info）；旧端缓存 xiaoshi_cache/xdxr/{code}_{YYYYMMDD}.json 当日有效
- 数据源：ctx.quotes 门面查有没有 xdxr 通道（读 app/core/quotes.py 现状）；有就走门面；没有就在 core/quotes.py 补一个 xdxr(code) 方法（eltdx/pytdx 按现有连接模式），缓存当日
- 响应形状照旧（先 curl 8800/api/kline/000001.SH/xdxr 看）

### C 组 · battle_plan_rw 插件（0.1.0 → 0.2.0）

**C1 `POST /api/module_switches_batch`（P0，index.html:9071 调用，旧 server.py:7367）**
- 模块开关批量写：读旧 handler 语义照搬（dirty 批量提交模式）；写 battle_plan.json 的 module_switches 键（受控写通道已有）
- 注意嗅嗅指出 battle_plan_rw/backend.py:70 自认不覆盖——现在补上

## 3. 改动白名单

```
app/plugins/ops/（backend/plugin.json/tests）
app/plugins/kline/（backend/plugin.json/tests）
app/plugins/battle_plan_rw/（backend/plugin.json/tests）
app/core/ops_run.py（扩 panic/amv_live/backfill/restart 加载）
app/core/panic_calc.py（新建，如需）
app/core/quotes.py（仅补 xdxr 通道，如需）
app/core/paths.py（白名单补名：panic_breadth/physical_ai/kline_indicator_settings + WRITABLE_MISC）
app/core/contract.py（仅 ctx.misc_write，如选此方案）
docs/HANDOFF_20261024_M1_fix.md
```

禁止：不动 engine/、不动其他插件、测试不真触发 backfill/restart/amv_refresh（mock）。

## 4. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/ops/tests app/plugins/kline/tests app/plugins/battle_plan_rw/tests app/core/tests -q
python -m pytest -q
python tools\cutover_gate.py
# 活体对齐（8800 在跑）：GET /api/panic、/api/physical_ai.json、/api/kline/000001.SH/xdxr、GET /api/kline-indicator-settings 四端点与 8800 同参比对
```

## 5. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810（01 统一做）。
