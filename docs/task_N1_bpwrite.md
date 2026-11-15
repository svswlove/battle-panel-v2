# 任务书 · 周末冲刺 N1 battle_plan 原生写域（8 端点）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 N1/4
> **方向变更（主人裁定）**：写操作全部原生插件化，不走转发。旧系统的签收/启停/择机单/预警/手动仓位都是 battle_plan.json 的受控写，执行器 5s 重读生效（文件即消息总线）。插件原生写它，旧执行链照常工作。

## 1. 目标

把旧 8800 的 8 个写端点在 battle_plan 插件原生实现（不再转发，core 里也没有转发层）。语义唯一源 = engine/server.py 同名 handler（只读参考）。

## 2. 端点与语义（已核实）

全部**读整份 battle_plan → 改内存 → ctx.panel_write 整份写回**，未知字段原样保留：

| 路径 | 语义（engine/server.py 行号） |
|---|---|
| `POST /api/sign` | 逐项/全部签收（7144）：_sign_item 写 sign 子对象+status_history；_sync_plan_sign 刷顶层 sign（265，空 buy_plan 不降级！）；同步 engine_state（见 §3）；回 {ok, sign, engine_state, signed_items, total_items}（batch_cap 不搬，如实少该键并在契约登记） |
| `POST /api/kill` | 急停总停（7646）：快照 module_switches 到 engine_state.module_switches_snapshot（on→on 不覆盖快照）→ 全置 false；解除时恢复快照+清除；kill_switch.json 写 active/reason/history（append 一条） |
| `POST /api/toggle` | 条目启停（7232）：buy_plan 按 plan_id / operations 按 ts_code 更新 enabled + status_history 追加；不动 sign/status |
| `POST /api/cancel` | 条目取消（7180）：先读旧 handler 全文，语义照搬（状态流转+留痕） |
| `POST /api/signal_watch/toggle` + `/cancel` | 择机单启停/取消（7541/7569）：toggle 只改 enabled；cancel 状态流转，都不动 status_history（S3 契约） |
| `POST /api/manual_position` | 手动仓位（7342）：pct∈[0,100]，amount = pct% × battle_plan.position_cap（缺失回退总资产，再缺失 0）；落 manual_position 键进 battle_plan |

注意路径：这些是旧前端直调的根路径。battle_plan 是 mount=plugin 插件——**本批同时给插件加 root 挂载**（见 §4）。

## 3. engine_state / kill_switch 写法

- sign 完成后同步 engine_state.json：signed→ARMED/battle_plan_signed=true；否则 READY/READ_ONLY；kill 激活时 FROZEN（_apply_engine_state 298 行语义照搬，注意保留未知键）
- kill 写 kill_switch.json：active/triggered_at/reason/updated_at + history append
- 全部走 `ctx.state_write("engine_state"/"kill_switch", payload)`（新机制，见 docs/CONTRACT_STATE.md）；**禁止 open('w')**（gate 扫描会拦）

## 4. mount=root 双挂载

battle_plan 现在是 mount=plugin（/api/p/battle_plan/*）。本批需要 /api/sign 等根路径。core 已支持 root 挂载（一插件一路由表挂两前缀**尚未支持**——你去 app/core/main.py:328 看现状：mount==root 时挂 /api，否则 /api/p/<id>）。**方案**：改 main.py 挂载逻辑，新增 manifest 字段 `backend.mount_root_extra: ["sign","kill",...]`（或等价机制），root 挂载只暴露列出的子路径。这是 core 改动，你只改这一处挂载区，**改动前把方案写进 HANDOFF**。备选（若你判断更稳）：新建 `battle_plan_rw` 兼容插件 mount=root 承载根路径写端点，battle_plan 保持不动——**选备选则不动 core**，我更倾向备选（零 core 改动），你评估后在 HANDOFF 里定并执行。

## 5. 改动白名单

```
app/plugins/battle_plan/          # 或新增 app/plugins/battle_plan_rw/（按 §4 你定的方案）
app/plugins/battle_plan/tests/
docs/battle_plan_contract.md      # 写域登记（含与旧端差异：batch_cap 不搬）
docs/HANDOFF_20261024_N1_bpwrite.md
```

禁止：不动 core（选备选方案时）；不动其他插件/engine/web/旧服务；测试不写真文件（tmp_path 模拟 PANEL_V2_WS 覆盖到临时目录——paths 全部支持环境变量）。

## 6. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/battle_plan/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

行为对齐抽查（对照 8800 响应结构）：sign 的响应键集、toggle 后 battle_plan.json 的 status_history 形状。**不许真签收/真急停生产文件**——冒烟用「重复签收已签条目」（幂等，无变更）与 404 探针。

## 7. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810。
