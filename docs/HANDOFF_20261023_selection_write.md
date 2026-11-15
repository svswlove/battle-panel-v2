# 选股页写端点交接（2026-10-23 · 4.7）

## 状态

`compat_misc` 新增三条 POST（旧 8800 同名接口）：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/selection_prefs` | 保存选股筛选偏好（覆盖写，None 不落盘） |
| POST | `/api/selection_pool/add` | 选股池 upsert（新加入排最前） |
| POST | `/api/selection_pool/remove` | 选股池移除（带/不带后缀均可命中） |

写通道：`ctx.panel_write`（`WRITABLE_PANEL` 新增 `selection_filter` / `selection_pool`），备份 + 原子替换 + 审计。
`selection_pool` 写成旧 8800 的 v2 结构 `{current, pools:{date:{items,saved_at}}}`；GET 同时兼容旧 `{date,items}` 结构。

## 验证

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/plugins/compat_misc/tests -q
# 39 passed
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
# 680 passed / 4 skipped
```

实际 GET 与旧 8800 仍全等（`/api/selection` / `/api/selection_pool` / `/api/selection_prefs`）。
写端点只在临时 FakeCtx 测试过；生产首次使用前建议先备份 `data/panel/selection_pool.json` / `selection_filter.json`（`panel_write` 本身也会备份）。

## 注意

- 旧 8800 仍会写同一份 `selection_pool.json` / `selection_filter.json`；两套界面同时改会互相覆盖。切换后建议只用 8810 的选股页。
- 回退：停 8810 → 从 `data/panel_v2/backups/selection_pool|selection_filter/` 恢复（如需）。
