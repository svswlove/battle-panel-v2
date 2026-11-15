# 任务书 · 批2a battle_plan 卡升级（签收+仓位门）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 项目：作战面板 v2 · 插件化重构
> 前置事实已由 01 验证：8810 运行中（core 0.3.1）；`/api/sign` 代理已挂 core main.py:396（POST，body 透传 8800）；battle_plan 后端 5 端点只读全绿；git 干净（HEAD 8f8e70e）。

## 1. 目标

把 v2 壳的 battle_plan 卡从"基础只读表"升级为对照旧 index.html `renderBattlePlan` 的全功能对等卡，重点是签收交互闭环。

## 2. 行为基准（唯一事实源）

`engine/web/index.html` 的 `renderBattlePlan(d)`（约 3533 行起）+ 相关交互（8700~9030 行段）：
- 作战表卡头：`plan_date · plan_version · 签收状态`（`sg.status==="SIGNED"` → "✔ 已全部签收"；否则 "待签收 N/M" + `sg.signed_at`）
- 段落顺序（照抄旧版信息层级）：meta 卡（签收进度/引擎状态/仓位口径徽标 paper-live/总仓上限）→ buy_plan 预案表（逐项签收按钮）→ watch_levels → risk_warnings → pending_todos → check_log → 择机单在场表（终态单折叠为计数）→ module_switches（**只读展示**，不搬编辑）
- 签收交互：
  - 逐项：`POST /api/sign` body `{signer:'主人', plan_id: <id>}`
  - 一键全部：`POST /api/sign` body `{signer:'主人', all:true}`，按钮在 allSigned 时 disabled
  - 提交后以服务端回执为准重拉数据（不乐观更新；失败原样显示 toast/行内错误）
- stale 提示保留：`battle_plan.json` 的 AMV 文字是旧值时卡片顶部显示提示（现有逻辑已在用，别丢）
- 刷新：不声明 refreshEvery（签收后手动刷新即可），或如旧版有轮询节流（值变才重渲）可平移同款防抖

## 3. 改动白名单（只许碰这些）

```
app/plugins/battle_plan/frontend.js
app/plugins/battle_plan/plugin.json        # version 0.2.0 → 0.3.0
app/plugins/battle_plan/tests/             # 新增前端交互相关 pytest（见 §5）
docs/battle_plan_contract.md               # 追加签收交互约定（一节即可）
docs/HANDOFF_20261024_fe_batch2a.md        # 交接文档（新建）
```

禁止项：
- 不改 backend.py / 任何 core 文件 / 任何其他插件 / engine/web/（旧端一行不动）
- 不新增后端路由（后端已就绪，本任务是纯前端）
- 不写 data/panel/（签收写发生在 8800 侧，由代理转发）
- 不引任何框架/构建步骤（原生 ES module，现有契约 §5）

## 4. 已验证协议/线索（直接用，不要反推）

- `POST /api/sign`：挂 core（app/core/main.py:396），body 透传 `http://127.0.0.1:8800/api/sign`；旧壳传 `{signer, all}` 或 `{signer, plan_id}`；返回 8800 原样回执
- 卡片注册契约：plugin.json `frontend.cards` 现有一张 `battle_plan.main`（slot main / order 40），改内容不用动结构
- `ctx.api.post` 已存在（批 0 加的，见 commit 9c9fd41），写操作一律走它
- 前端挂载升级缓存破坏：import URL 已带 `?v=manifest.version`，version 升 0.3.0 即自动生效
- 旧版择机单终态集合：`SW_TERMINAL = new Set(["DONE","CANCELLED","EXECUTED","VOID","EXPIRED","ABANDONED_EOD"])`
- 旧版模式徽标口径：`_tradeMode==="paper"` → 纸面徽标 + title 说明"仓位/金额按纸面账本总资产计"；新版 `_tradeMode` 来源可从 `/api/order/list` 的 `mode` 字段拿（一次请求顺带），或先按 paper 常量（当前就是 paper）——**选后者，少一个请求**，在交接文档记一笔

## 5. 自测要求（全部要跑，贴退出码）

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/battle_plan/tests app/core/tests -q          # 半径内
python -m pytest -q                                                        # 全量（新前端不动后端，但按门禁跑一次）
python tools\cutover_gate.py                                               # 8 项 PASS
```

浏览器实测（用你环境里可用的 CDP/playwright）：
1. 打开 `http://127.0.0.1:8810/`，切到作战表卡，对照旧 `http://127.0.0.1:8800/`（作战表 Tab）同屏截图两张
2. **签收冒烟**：纸面模式下点"逐项签收"一条 → 8800 侧刷新后该条变 SIGNED → 8810 重拉后同步。注意：真签收会写生产 battle_plan.json 的 sign 状态——**只签一条已 SIGNED 的不产生变更**；若无已签条目，改为只验"点按钮 → 请求发出 → 回执渲染"（devtools 抓请求），在交接文档标注"未真签（避免写生产）"
3. 控制台 0 报错

## 6. 交付格式

回执 ≤300 字：做了什么 / 改了哪些文件 / 自测结果（pytest 数字 + gate 8 项）/ 遗留。截图与明细落 `docs/HANDOFF_20261024_fe_batch2a.md`。**不要自行 commit**，01 验收后统一提交。
