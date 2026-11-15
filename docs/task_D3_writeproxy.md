# 任务书 · 周末冲刺 D3 交易链写代理 12 端点（core）

> 派单：01 → DSHana ｜ 2026-10-24 ｜ 并发组 D3/4 ｜ **这是本冲刺唯一动 core 的单，优先级最高**
> 背景：周末冲刺「API 全覆盖」。旧界面全部写操作（签收/急停/开关/择机单/预警/策略/手动持仓）需要能在 8810 旧界面里完成，否则「零功能损失」不成立。

## 1. 目标

在 core main.py 现有代理区（/api/shell/kill、/api/sign 旁边）补 12 个写代理端点。**全部走 `_proxy_engine_write` 同款代理**，一行业务逻辑都不写——写入口仍在 8800，8810 只是转发（单一写入口不变，无双写风险）。

## 2. 端点清单（已核实 8810 缺失）

| 路径 | 旧语义（engine/server.py 核实） | 备注 |
|---|---|---|
| `POST /api/kill` | 急停（改三处状态） | 已有 /api/shell/kill 覆盖旧壳；此路径是 index.html 直调，补齐 |
| `POST /api/toggle` | 条目级模块开关 | body {item_type, enabled, signer} |
| `POST /api/module_switch` | 单模块开关 | |
| `POST /api/module_switches_batch` | 批量开关 | |
| `POST /api/cancel` | 撤单别名 | |
| `POST /api/alert_plan/toggle` | 预警启停 | |
| `POST /api/alert_plan/delete` | 预警删除 | |
| `POST /api/signal_watch/toggle` | 择机单启停 | |
| `POST /api/signal_watch/cancel` | 择机单取消 | |
| `POST /api/ticket_policy` | 工单策略写 | |
| `POST /api/ticket_policy/clear` | 工单策略清 | |
| `POST /api/manual_position` | 手动持仓写 | real_positions/manual 同款一并列上 |

（`/api/real_positions/manual` 若与 manual_position 同 handler 就一起，不同则登记不做并在 HANDOFF 说明）

## 3. 实现约束

- 复用/扩展现有 `_proxy_engine_write`：读 app/core/main.py:340~410 现状，若函数签名只支持 POST body 透传就够；需要 GET 代理再加（最小 diff）
- 超时分档：默认 8s（现有写代理锁同款）；`/api/kill` 沿用现有值
- **代理白名单常量**集中一处定义（列表化 12 条路径），别散落 12 个装饰器各写一遍
- 每端点挂 core 测试：mock 8800（现有 test_shell_endpoints.py 的桩模式照抄）——代理转发 URL/body 正确、8800 挂时回 `panel_down`、真 app 路由注册齐
- **不许有任何本地写盘逻辑**

## 4. 改动白名单

```
app/core/main.py                    # 代理区追加（最小 diff）
app/core/tests/test_shell_endpoints.py  # 或新建 test_write_proxies.py
docs/CONTRACT.md                    # 若写代理清单需登记（追加一节，与 D1/D2 无交集）
docs/HANDOFF_20261024_D3_writeproxy.md  # 交接（新建）
```

禁止：不改 core 其他区域（quotes/feed/paper/paths）；不动任何插件目录（那是别的并发手的）；不真调 8800 写端点（全 mock）。

## 5. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/core/tests -q
python -m pytest -q
python tools\cutover_gate.py
```

## 6. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit；不重启 8810（01 统一做）。
