# 任务书 · 周末批次 R2 selection 选股卡（新插件 selection）

> 派单：01 → 子代理 ｜ 2026-10-24 晚 ｜ 并发组 R2/5
> 方向：旧「选股」Tab 升级为 v2 插件卡。行为基准 = renderSelection（index.html 7115 行起）。

## 1. 目标

新建 `app/plugins/selection/`（v0.1.0，mount=root，卡 selection.main）：

1. **选股结果表**：GET /api/selection（8.2MB 大 JSON——前端分页/虚拟滚动，或按需渲染 TOP N + 展开加载；旧 Tab 的列集照搬）
2. **筛选偏好**：GET/POST /api/selection_prefs（写走 compat_misc 已有的受控写端点）
3. **选股池**：GET /api/selection_pool + POST add/remove（已有端点）；行尾「+池/已入池」态
4. **板块选项**：GET /api/selection_board_options

写操作纪律：POST 走 ctx.api.post；池增删用行内确认（同 paper 卡的二次点击模式）。

## 2. 环境事实

- 四端点全就绪（compat_misc 实现，8.2MB 全量 JSON 与旧端逐字段全等已验证过）
- 写参考：paper 卡的行内二次点击；battle_plan 卡的 ctx.api.post 模式
- 卡骨架参考同 R1

## 3. 改动白名单

```
app/plugins/selection/（plugin.json；frontend.js；tests/）
docs/selection_contract.md
docs/HANDOFF_20261024_R2_selection.md
```

零后端改动（端点全在 compat_misc）。

## 4. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q
python tools\cutover_gate.py
```

实测：/v2/ 卡渲染 8.2MB 数据不卡死（首屏 <2s，分页/虚拟滚动）；筛选表单改一项 POST 后回读一致；池增删按钮态正确。

## 5. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit 不重启。
