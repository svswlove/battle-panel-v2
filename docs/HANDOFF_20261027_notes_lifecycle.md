# 标注生命周期契约（2026-10-27）

> 归属：`ui_notes` 的唯一写口在 `app/plugins/ops/backend.py`（ops 0.3.2）。
> 前端：页面点选 `app/web/v2_note.js`（不改），列表与流转 `app/web/v2_notes_panel.js`。
> 数据：`engine/state/ui_notes.json`（`ctx.state_write("ui_notes")`，带备份/原子/审计）。

## 1. 字段

| 字段 | 说明 |
|---|---|
| `status` | **兼容字段**，由 stage 派生：verified/rejected → `resolved`，其余 → `open`。旧读法不用改 |
| `stage` | `open` / `doing` / `fixed` / `verified` / `rejected`；老数据缺省按 status 推导（resolved→verified） |
| `stage_at` | 最近一次流转时间；老数据回退 `resolved_at` → `created_at` |
| `owner` | 最近处理人 |
| `history` | `[{at, by, from, to, note, ref}]`，**只追加**；重开不清历史 |
| `resolved_at` / `resolve_note` | 仅在 verified/rejected 时有值；重开（→open）清空，结论仍留在 history |

读接口返回的 `stage/history/owner/stage_at` 是**读时视图**，`GET /ui_notes/list` 不写盘。

## 2. 端点

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/api/ui_notes/list?status=&stage=` | 清单 + `stats{total,open,by_stage,by_page}`；`stats` 恒为全量 |
| POST | `/api/ui_notes/add` | 新增，起点 stage=open，history=[] |
| POST | `/api/ui_notes/update` | 只改 text + edited_at，不动 stage |
| POST | `/api/ui_notes/resolve` | **旧调用兼容**，等价 stage→verified（写 history） |
| POST | `/api/ui_notes/stage` | 生命周期唯一写口：`{id,to,note,ref,by,expected}` |

## 3. 并发与错误

- `expected` 非空且与当前 stage 不一致 → **409**，不写。
- `to` 不在枚举 → 422；id 不存在 → 404。
- 写失败 → 503（读文件坏时不静默覆盖，沿用既有纪律）。

## 4. 前端约定

- 阶段推进按钮携带展开时的 `stage` 作 `expected`；收到 409 提示「阶段已变化」并重拉。
- `by` 默认 `01`。
- 列表按 `page` 分组；`v2:card:<key>` 解析到卡，非 `v2:` 旧页标注只标记、不定位。
- 已完成 = verified/rejected；doing/fixed 仍算未完成，页面 pin 继续显示。

## 5. 迁移记录

2026-10-27：N20261027-011947-26 ~ 101349-33（8 条）走 `POST /ui_notes/stage` 迁到 `verified`，`ref` 指向对应 DONE 文档；`-34`（成交额预估失真）保持 open。