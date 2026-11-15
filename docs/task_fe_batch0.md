# 任务书 · 前端批次 0：v2 壳 chrome 补齐 + 旧壳缺失端点

> 路线图：`docs/FRONTEND_ROADMAP.md` 批次 0。行为基准：`app/web/legacy/shell.html`（旧壳）。

## 目标

v2 新壳（`app/web/`，入口 `/`）达到旧壳的 chrome 完整度：九主题 + 三档字号 + rail 侧栏 + 搜索面板；旧壳在 8810 下缺失的 4 个端点补齐。

## 关键架构事实（先读，不许推翻）

1. **主题唯一源** = `app/web/legacy/themes.css`（sync_vendor 从 `engine/web/themes.css` 同步，33KB，含 `--theme-manifest` 九主题清单）。批次 0 **不复制**这份文件到 `app/web/themes.css`——core 的 `/static` 挂载目录是 `app/web/`，所以做法是：在 `app/web/shell.html` 里 `<link>` 引用 `/legacy/themes.css`（core main.py 263 行已挂载 `/legacy` 静态目录，themes.css 就在其中）。**禁手写第二份色表**。
2. **旧壳的 kill/restart/search 不在 8800/8810 里**，由独立 8801 外壳宿主（`engine/shell_server.py`）承载。8810 的 `/legacy/shell.html` 里 ENGINE=origin(8810)，所以那 4 个端点请求的是 8810。**v2 的补法（已定，不许改成在 8810 内做进程管理）**：
   - `/api/shell/search`（POST）：**本插件实现**，转发语义=旧壳 `api_shell_search`，但目标改为**本进程**的 `/api/search/all`（compat 已有，`app/plugins/compat/backend.py` 的 `search_all_post`，POST `{q, limit}`）——同源直调 `build_search`，不做 HTTP 自环。挂到 `compat` 插件（root mount → 完整路径 `/api/shell/search`）。返回形状 `{q, stocks:[{code,name,kind?}], sectors:[...]}`（以 `build_search` 实际返回为准，先读 `compat/backend.py::build_search`）。
   - `/api/shell/kill`（POST）：**代理 8800** `/api/kill`（body 透传；8800 不可达 → `{"ok":false,"error":"panel_down","detail":"引擎服务未启动"}`，HTTP 200）。急停语义唯一源=旧端 engine/server.py，禁复制逻辑。挂 `compat`。
   - `/api/shell/restart`（POST）：**不实现为杀 8800**！8810 托管旧壳时，重启按钮的目标应是「拉起 8800 引擎」且**不许碰 8810 自己**。实现：检查 8800 `/api/status` 可达 → `{"ok":true,"killed":[],"stdout":"already up","stderr":"","ts":...}`；不可达 → 调 `engine/executor/run_daemons.py --only server` 拉起（qmt312 python，路径参考 `engine/shell_server.py::_launcher_python`，ENGINE_DIR=`OH-WorkSpace\engine`）。**绝对禁止**匹配/杀掉任何 `port 8810` 或 `app.core.main` 进程。挂 `compat`。若插件契约禁止 subprocess（读 `docs/CONTRACT.md` §8 + `cutover_gate.py` 的 banned tokens：`subprocess.`、`Popen(`、`os.system(`），则 **shell/restart 不进插件**，改为 core 路由（`app/core/main.py`，core 不受插件静态扫描约束），任务书按此定：**restart 放 core，kill/search 放 compat 插件**。
   - `/api/sign`（POST）：**代理 8800** `/api/sign`（body 透传；不可达 → panel_down 同上）。挂 `compat`。旧端实现 `engine/server.py:7144`（读它确认 body 形状，前端旧壳传 `{signer:'主人', all:true}`）。
3. **v2 壳侧栏改造**：保留现有 manifest 驱动卡片导航，改成旧壳同款左侧 rail（52px 图标列 + 悬浮提示），但 v2 的导航单元是**卡片**不是 9 个固定 Tab——rail 按卡片渲染（图标可用首字或简单 SVG 映射，不追求与旧壳逐字一致），顶部搜索按钮 + 底部主题/设置按钮照旧壳。搜索面板（`#sPanel` 样式与交互）照搬旧壳 ShellSearch，但 fetch 目标是 8810 自己的 `/api/shell/search`，选中后行为：若是板块 → 打开 sector 卡；个股 → 打开 kline 卡并传入代码（kline 卡现无入参则先 console.log + 顶部 toast 提示「已选中，K线入参下批接」，不硬做）。
4. **主题切换**：shell.html 挂 `data-theme`/`data-fs` 到 `<html>`；设置抽屉照搬旧壳（主题 select 读 `--theme-manifest`、字号三段、引擎状态 stat）。localStorage 键沿用旧壳（`shell_theme`/`shell_fs`），保证用户已有偏好无损。shell.css 现有硬编码深色值改走 themes.css 的语义 token（`--bg/--fg/--card/--line 等`，先读 themes.css 顶部 token 清单再改，保证无 token 可用的回落值写在 `:root`）。

## 改动白名单

- 改：`app/web/shell.html`、`app/web/shell.js`、`app/web/shell.css`
- 改：`app/plugins/compat/backend.py`（+kill/search/sign 三端点）、`app/plugins/compat/plugin.json`（如需声明）、`app/plugins/compat/tests/`（对应测试）
- 改：`app/core/main.py`（仅加 `/api/shell/restart` 一条 core 路由）、`app/core/tests/`（对应测试）
- 新建：`docs/HANDOFF_20261024_fe_batch0.md`

禁止：改 `engine/`、改 `app/web/legacy/`（sync_vendor 管辖）、改其他插件、引任何依赖包、git commit。

## 环境事实

- Python：`OH-WorkSpace\envs\qmt312\Scripts\python.exe`
- 8800 旧端在跑（代理目标）；8810 新端在跑（改动后重启：停 `port 8810` 进程 → `powershell -File run.ps1`，启动后等 `/api/health` ok=true）
- themes.css token 清单：读 `app/web/legacy/themes.css` 前 120 行
- 旧壳参考：`app/web/legacy/shell.html`（rail/抽屉/搜索全套 UI 与 JS）；`engine/shell_server.py`（端点语义）

## 自测要求（全部留下输出）

1. `python -m pytest app/plugins/compat/tests app/core/tests -q` → 全绿
2. `python -m pytest -q`（全套）→ 不低于 706 passed
3. 重启 8810 后：`/api/shell/search`（POST `{q:"银行",limit:5}`）→ 200 有结果；`/api/shell/kill`、`/api/sign` → 200（panel_down 或真实响应都可，报告实际值）；`/api/shell/restart` → 200 且 8810 自身进程 PID 不变（证明没自杀）
4. 浏览器（或 CDP）打开 `http://127.0.0.1:8810/`：九主题切换 `<html data-theme>` 变化、rail 渲染、搜索面板弹出可用；截图落 `docs/`（可用 engine/tests/panel/ 的 CDP 先例方式，截图文件名 `fe_batch0_shell.png`）

## 交付格式

回执 ≤300 字：做了什么 / 测试数字 / 端点实测结果 / 遗留 + 路径。差异如实列。
