# 文件修改日志正文（2026-10-25 · 板块详情左键开 K 线修复）待 01 搬

> 用途：本文件正文由 01 手工 append 到 `OH-Works/文件修改日志/2026-10-25.md` 末尾。
> 本会话沙箱只可写 `projects/panel-v2`，对 `OH-Works/` 的写入被拒（workspace-write）。
> 追加时请整段复制下面的内容（含标题行），不要改写措辞。

---

## 修复：板块详情页个股左键点不开 K 线

- 任务：01 任务书（根因已由 01 诊断，执行手按方案落码）
- 范围：只改 `app/web/shell.js` + `app/tests/test_fe_shell_bridge.py`；未碰插件、`engine/`、`app/core/`、`data/panel/*`、旧 8800/8002/8801
- 修改：
  - `app/web/shell.js`（1356 → 1400 行，+44 / −1）
    - `bindKlineBridge()` 独立形态（`window.parent === window`）不再 `return false` 短路，
      改为 document 级 capture 阶段 click 委托，命中 `[data-open-kline]`/`[data-open-amv]` 后
      `stopPropagation` + 直调 `ctxOpenKline` / `ctxOpenSector` 本页路径（右键菜单同一套实现）
    - iframe 形态（嵌入旧页，postMessage 转发父页）**一行未动**
    - 新增 `sectorModalOf()` / `closeSectorModal()`：点击发生在 body 挂的板块详情弹窗内时，
      开卡成功后把弹窗收掉（该弹窗 `position:fixed; inset:0; z-index:50` 且板块卡 unmount 不带走它，
      不收就会盖住新 K 线卡，体感仍是「点了没反应」）
    - 注释更新：删掉「独立打开时不安装本监听」，改为说明两种形态的分工
  - `app/tests/test_fe_shell_bridge.py`（311 → 399 行，+88）
    - 新增 helper `_js_function_body()` + 5 条门禁用例：不得短路 return false / 独立形态须有
      document capture 委托且调 ctxOpenKline+ctxOpenSector / iframe 分支保留 / 弹窗必须收 /
      被复用的两个函数真实存在
- 新增：
  - `projects/panel-v2/docs/FIX_20261025_sector_detail_kline_click.md`（修复说明 + 自查明细）
  - `projects/panel-v2/docs/APPEND_20261025_file_log_kline_click.md`（本文件）
- 验证：本机沙箱禁子进程，**pytest 未由执行手跑**（`spawn EPERM`）。已做静态自查：
  `new Function(shell.js 全文)` 语法 OK；5 条新用例判定逻辑复刻跑 22 项全通过；
  jsdom 30.1.3 真 DOM 实跑四种场景（stage 内点击 / 弹窗内点击 + 弹窗被收 / data-open-amv /
  空码与无属性 no-op）与 iframe 形态 postMessage 转发，全部符合预期。
  **01 需复跑** `pytest app/tests -q`（基线 205 passed，本单 +5 → 预期 210 passed）+
  浏览器验证 `/v2/` → 板块卡 → 板块详情 → 点成分股名应切 K 线卡且弹窗消失。
- 回退：`git checkout app/web/shell.js app/tests/test_fe_shell_bridge.py`（本单未做任何 git 写操作）
- 8810 未重启（静态文件）；git 未做任何写操作。
