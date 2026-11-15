# 全量 pytest 证据（批次 0 复核，2026-10-24 03:0x）

嗅嗅审核 P1-1 要求：721 passed 需落盘证据。

```text
命令：OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
目录：OH-WorkSpace\projects\panel-v2
时间：2026-10-24 03:0x（批次 0 commit 02023fc + shell.js post 补丁之后）

721 passed, 4 skipped, 105 warnings in 34.86s
```

基线 706（commit a76b950 时）→ 721：净 +15 = compat 5 条 shell/search 测试 ×1 +
core 10 条 shell_endpoints 测试 −（被替换的旧 501 用例 0 条）。目录覆盖含
`app/plugins/compat/tests` 与 `app/core/tests`（gate 的 pytest 清单不含这两处，
已在 tools/cutover_gate.py 同步补齐，见同日修改日志）。
