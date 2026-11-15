# 任务书 · 周末冲刺 N2 运维与备注原生域（ui_notes 4 + ops 2 + trade_mode 2）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 N2/4
> 方向：原生插件化，不转发。

## 1. 目标

新建 `ops` 插件（mount=root）承载 8 个端点：UI 备注四件套、运维两件、交易模式两件。

## 2. 端点与语义（已核实 engine/server.py 行号）

### ui_notes（4，存储=engine/state/ui_notes.json，23KB 生产文件）
- `GET /api/ui_notes/list`（828）：`?status=open` 过滤；旧前端 9 处调用
- `POST /api/ui_notes/add`（793）：先读旧 body 模型（UiNoteBody）与 id 生成规则，照搬
- `POST /api/ui_notes/update`（812）：部分更新语义照搬
- `POST /api/ui_notes/resolve`（836）：解决/归档语义照搬
- 写全走 `ctx.state_write("ui_notes", payload)`（白名单已开，见 docs/CONTRACT_STATE.md）；读走 ctx（需要 panel_json 不适用——state 文件在 engine/state，**读也走 state_write 同源？不行**。见 §3：给 ctx 加 state_json 读方法，或你在插件里经 core 白名单读——**core 已有 paths.WRITABLE_STATE 映射，你需要在 contract.py 给 PluginContext 补一个 `state_json(name)` 只读方法**（对齐 WRITABLE_STATE 白名单 + manifest data.state 声明），这是本单唯一 core 改动，最小 diff，与 N1/N3/N4 无交集（他们不碰 context.py））

### ops（2）
- `GET /api/ops.json`（2063）：读 data/panel/ops.json + ops_status 相关组装（refresh 参数语义照搬；panel 白名单没有 ops.json 名字——**paths.py READABLE_PANEL 补 "ops"**，与 N3 可能同时想加：以你为准，冲突时 git 层面无重叠行，后到者 rebase 一行即可）
- `POST /api/ops/run`（2081）：旧语义是调 ops_status.run_manual → schtasks /run。**这不是面板数据写，是触发运维任务**。原生化裁决：**不搬**（插件禁 subprocess，属红线）。实现为 405/501 + 明确 detail「运维触发请用计划任务/旧端」？——不。主人要零功能损失。**最终方案**：POST /api/ops/run 返回 {ok:false, error:'ops_run 不在 v2 范围'} 是功能损失。**改为：把 ops_status.run_manual 的白名单+锁逻辑以 ctx 方法暴露？超范围。** 落地裁决：**N2 只做读端点，ops/run 在 HANDOFF 登记为「待主人裁决项」**——理由：run_manual 起的是 Windows 计划任务（schtasks /run），跨进程系统操作，插件沙箱红线（gate 扫 subprocess），需要主人明确授权后单开任务书。回执里说清。

### trade_mode（2）
- `GET /api/trade_mode`：读 engine_state.json 的 trade_mode（缺省 live）——走你的 state_json
- `POST /api/trade_mode`（7628）：∈{live,paper} 校验 → engine_state.json 更新 trade_mode（ctx.state_write，保留其他键）
- alert 域顺手：`POST /api/alert_plan/toggle`（7587）+ `/delete`（7609）：读改写 data/panel/alert_plan_persistent.json（白名单已开 "alert_plan"）→ ctx.panel_write；语义照搬旧 handler

## 3. core 改动（你单专属）

1. `app/core/contract.py` PluginContext 补 `state_json(name)` 只读（对齐 WRITABLE_STATE + manifest data.state）
2. `app/core/paths.py` READABLE_PANEL 补 "ops"（一行）
3. `docs/CONTRACT_STATE.md` 补 state_json 一节

## 4. 改动白名单

```
app/plugins/ops/                  # 新插件（backend/plugin.json/tests）
app/core/contract.py              # 仅 state_json 方法
app/core/paths.py                 # 仅 READABLE_PANEL 加 "ops"
docs/CONTRACT_STATE.md
docs/HANDOFF_20261024_N2_ops.md
```

禁止：不动其他插件/main.py；测试用 PANEL_V2_WS/PANEL_V2_DATA_DIR 环境变量指临时目录，不写真生产文件。

## 5. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/ops/tests app/core/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

对齐抽查：/api/ui_notes/list?status=open 与 8800 同参响应逐字段；/api/ops.json 结构；trade_mode 幂等写（paper→paper 无变更，比对 mtime/内容）。

## 6. 交付

回执 ≤300 字（含 ops/run 的裁决请求）；明细落 HANDOFF；不 commit；不重启 8810。
