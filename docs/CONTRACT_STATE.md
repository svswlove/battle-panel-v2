# 契约 v1.4 · 引擎状态文件白名单（data.state / ctx.state_write / ctx.state_json）+ 任务触发（data.ops_run）

> 2026-10-24 主人裁定：**写操作全部原生插件化，不走转发**。旧系统的写操作（急停/签收同步/交易模式/UI 备注）落盘在 `engine/state/` 下的状态文件，旧执行器与旧壳直接读文件（5s 重读，文件即消息总线）。插件原生写这些文件 = 旧执行链照常生效，旧 server.py 彻底退出写路径。

## 1. 白名单（app/core/paths.py::WRITABLE_STATE）

| 名字 | 文件 | 旧消费方 |
|---|---|---|
| `engine_state` | engine/state/engine_state.json | 执行器、旧壳 /api/status、签收同步 |
| `kill_switch` | engine/state/kill_switch.json | 执行器、watchdog、面板徽章 |
| `ui_notes` | engine/state/ui_notes.json | 旧前端备注面板 |

## 2. manifest 声明

```json
"data": { "state": ["engine_state", "kill_switch"] }
```

与 `data.write`（data/panel 下的生产 JSON）分开，加载期两边都做白名单校验，含未知名字直接拒绝加载。

## 3. ctx.state_write(name, payload)

与 `ctx.panel_write` 同一套安全措施：
1. 双白名单校验（manifest + WRITABLE_STATE）
2. 写前备份到 `data/panel_v2/backups/<name>/<时间戳>.json`
3. 原子替换（tmp + os.replace + fsync）
4. 每次写追加审计行（`backups/<name>/audit.log`：时间/插件/文件/字节）

插件侧禁止 `open(...,'w')`（gate 静态扫描不变）；写只能走这一条通道。

## 4. 语义约束（写域插件必须遵守）

- **读-改-写整份**：state_write 收完整 dict；未知字段原样保留，只改自己负责的键。
- **锁**：同一文件的多步读改写用模块级 threading.Lock 包住（兼容层已有先例）。
- **不改 schema**：这些文件被旧执行器/旧壳消费，键名、嵌套、枚举一字不动（迁移的是写入口，不是数据格式）。

## 5. ctx.state_json(name)（只读，2026-10-24 N2 新增）

```python
doc = ctx.state_json("ui_notes")   # -> dict（顶层必须是对象）
```

读同一批引擎状态文件，接口形状照 `ctx.panel_json`：

| 项 | 行为 |
|---|---|
| 校验 | 双白名单：名字必须在 `manifest.data.state` **且** 在 `paths.WRITABLE_STATE`；不满足 → `PermissionError` |
| 缺失 | 文件不存在 → `FileNotFoundError`（不静默返回空文档） |
| 形状 | 顶层不是 JSON 对象 → `ValueError` |
| 写 | **无**。写入只能走 `ctx.state_write`（备份/原子/审计在 core） |

为什么读也复用 `WRITABLE_STATE`：一个状态文件只该被声明一次。分两份名单会出现
「能读不能写」「能写不能读」的半套声明，也会让同一文件出现在两个概念里。插件读自己
负责的状态文件，跨界（如 ops 读 kill_switch）由 manifest 声明拦住。

已用：`ops` 插件读 `ui_notes`（备注四件套）与 `engine_state`（trade_mode）。

## 6. 消费方边界（谁写谁读）

- `ui_notes`：`ops` 插件读写（旧前端备注面板）。旧 8800 不再写。
- `engine_state`：`battle_plan` 插件写签收/急停相关键（N1）；`ops` 插件只改 `trade_mode`；
  两边都整份读改写，未知键原样保留。
- `kill_switch`：`battle_plan` 插件写（N1）。

## 7. data.ops_run / ctx.ops_run(name)（2026-10-24 N2 追补）

```json
"data": { "ops_run": true }
```

```python
res = ctx.ops_run("OH_PostCloseChain")   # -> 旧仓 run_manual 原样 dict
```

| 项 | 行为 |
|---|---|
| 声明 | `data.ops_run` 布尔，默认 **false**（同 `data.feed` / `data.paper`）；非布尔 → 加载期 `ContractError` |
| 闸门 | 未声明调用 → `PermissionError` |
| 实现 | `app/core/ops_run.py` → `importlib` 加载旧仓 `engine/ops/ops_status.py` → `run_manual(name)` |
| 单源 | 白名单/冷却/任务状态/跨进程锁**全在旧仓**，core 与插件不复制 |
| 失败 | 旧仓缺失/导入失败 → `RuntimeError`（HTTP 503）；旧仓内部异常/超时 → `{ok:false, reason:…}` |
| 上限 | 30s，到点返 `reason:'timeout'`，**不杀**在跑的那次调用 |

为什么允许它出现在 core：底层是 `schtasks /run`（跨进程），插件沙箱静态禁子进程；core 不在 gate
静态扫描范围，先例是 `/api/shell/restart`。红线不在 core 里重建，而在旧仓 `run_manual` 里单点维持。
