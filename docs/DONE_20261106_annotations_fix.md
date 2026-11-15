# 2026-11-06 标注功能 6 条修复（01）

> 来源：`docs/AUDIT_20261106_annotations.md`（同日体检）+ 主人拍板「全改 1–6」。
> 范围：`app/web/v2_note.js`、`app/web/v2_notes_panel.js`、`app/web/shell.js`、`app/web/shell.css`、
> `app/plugins/ops/{backend.py,plugin.json}` + 三个测试文件。未动 `app/core/`、其它插件、`engine/`。

## 1. 六条与改法

| # | 问题 | 改法（文件） | 实测 |
|---|---|---|---|
| P1 | 换卡后圆点不同步（新卡不出现 / 旧卡残留） | `shell.js::selectCard` 在 mount 后调 `state.note.refresh()`；`v2_note.js::refreshNotes` 检测 page 变化先清旧点再拉（`_lastPage`），拉到后 120ms 补画一次 | 切选中→1 个点、切仪表盘→0 个、切回盘中→按位置出现；改前选股卡 0 个、切走后旧点残留 |
| P2 | 锚点失效后从页面消失 | ① 存盘改用 `uniqueSel()`：从元素往上逐层加，**第一次全页唯一就收工**（实测 5 段 → 1 段 `div.gate-lamp.g-UNKNOWN:nth-child(1)`）；层数到顶才退回老的 `path()`；② 读取用 `resolveTarget()` 逐段退化：完整 → 去 `:nth-child` → 逐段去外层（含去 nth 版），**每步都要求唯一匹配**，多处命中一律不认；③ 仍锚不上的计入入口按钮角标 | 盘中卡 4 条：改前 0 条可见，改后滚动到位 2 条可见、角标报「2」 |
| P3 | 圆点文字溢出 | `renderPins` 只写本页序号（1/2/3），完整 id + 内容进 `title`；CSS 补 `overflow:hidden` | 圆点 18×18 内显示「1」，`scrollWidth` 不再 55px |
| P4 | 滚出视口后粘在屏幕边缘 | `renderPins` 加视口判断（`r.bottom<0 / r.top>innerHeight / r.right<0 / r.left>innerWidth` 不画） | 元素在 825px（视口 667）时不画，滚进视口即出现 |
| P5 | 圆点层级压过抽屉 | `shell.css` 加 `body:has(.drawer.on) / body:has(.np-drawer.on)` 时 `.un-pin{display:none}`（圆点保持 z=110 以便盖过 K 线弹窗 z=100） | 开设置抽屉后 pin 的 `computed display=none`，关掉恢复 |
| P6 | 标注删不掉 | 后端 `POST /ui_notes/delete`（带 `expected`，409 保护）+ `POST /ui_notes/purge`（只许 closed 阶段、带 `expect_count`）；前端清单每行加「删除」、页脚加「清理已完成 N」，都用系统确认框 | 后端 12 项新测试全绿；面板实测行内显示「开始处理 / 定位 / 删除」、页脚「清理已完成 82」 |

## 2. 测试

```powershell
cd projects/panel-v2
node --check app/web/v2_note.js app/web/v2_notes_panel.js app/web/shell.js   # 全 exit 0
python -m pytest app/tests/test_v2_note.py app/tests/test_v2_notes_panel.py `
  app/plugins/ops/tests -q
# → 187 passed, 2 failed
```

新增：`app/plugins/ops/tests/test_ops_ui_notes_delete.py`（12 项）、`test_v2_note.py` 4 项（唯一性 selector /
锚点兜底 / 圆点序号与裁剪 / 切卡刷新）、`test_v2_notes_panel.py` 1 项（删除护栏）。

两条红灯是**既有**、与标注无关：`test_restart_touches_no_process` /
`test_ops_run_module_never_shadows_subprocess` —— 断言 `app/core/ops_run.py` 不得 import `subprocess`，
而该文件 2026-11-04 已为「按需脚本」功能加了 `run_script()`。属于功能与测试口径的冲突，
未在本次处理（不在标注半径内）。

其余既有红灯（`test_fe_shell_bridge.py` 4 条：K 线卡接线、裸 hex/rgba、未定义 token）在改动前即红。

## 3. 实机验证（8810 现网，ego-browser）

| 项 | 结果 |
|---|---|
| 切卡同步 | 选股 1 点 / 仪表盘 0 点 / 盘中按位置出现；改前对应 0 / 残留 / 残留 |
| 圆点内容 | `textContent="1"`、`title="N20261106-104611-82 · 设置是不是不能长久化保存"` |
| 视口裁剪 | 目标在 y=825（视口 667）不画；`scrollIntoView` 后出现在 y=302 |
| 失联角标 | 盘中卡 4 条中 2 条锚不上 → 按钮角标「2」 |
| 抽屉让位 | 开设置抽屉时 pin `display:none`，关掉恢复 |
| selector 生成 | 点仪表盘 gate-lamp → `div.gate-lamp.g-UNKNOWN:nth-child(1)`（单段、全页唯一；旧实现是 5 段全链） |
| 不污染数据 | 全程 `stats.total` 87 → 87 |

## 4. 边界 / 未做

- **删除接口需要重启 8810 才生效**（Python 插件在进程内，静态文件不用）。
- P2 只能救「结构变但特征还在」的旧标注。像 `-85` 的 `div.src:nth-child(1)`（同 class 元素全页多个）
  按「宁可不锚也不误锚」的口径放弃，改由角标提示 + 清单处理。
- `:has()` 需要 Chromium 105+（面板跑在本机 Chrome，满足）。
- 批量清理只支持已验证/驳回；未完成的不提供批量删（那是待办）。
- 未动既有 87 条标注数据；未改 `engine/state/ui_notes.json`。
