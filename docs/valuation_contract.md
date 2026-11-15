# 估值页契约（valuation 插件 · v0.2.0 · 2026-10-29）

> v0.2.0 变更（2026-10-29 返工）：托管从 core（`/peg` 静态挂载 + `paths.PEG_OUTPUT_DIR`）
> **整体搬进插件**。理由：8810 已是插件化结构，主人要求「这部分也要做成插件化，便于拆卸」；
> core 多一行就是拆卸多一个必改文件。现在拆卸 = 删插件目录 + 删 NAV 一行，core 零改动。

## 1. 定位

面板 v2 侧栏「估值」= **现有 PEG 估值选股仪表盘原样**。不重做、不裁剪、不重排信息架构。

```
夏天-activity/工具/peg/peg_dashboard.py
        └─ 生成 → 夏天-activity/工具/peg/output/peg_dashboard.html   （自包含单文件，232KB）
                         │
        valuation 插件 GET /api/p/valuation/view → FileResponse（原样字节，不复制、不加工）
                         │
        valuation 卡：一张铺满主区域的 <iframe src="/api/p/valuation/view?v=<mtime>">
```

页面内容 = 产物的全部：KPI 指标卡、PE-G 散点象限、可排序低估名单表、12 张横截面画像图、
PEG 分布/分层、行业中性榜、防御溢价、估值×质量四宫格、预测分歧度、行业估值走廊、
数据覆盖透视、新闻/公告/K 线区间、预期修正雷达。

## 2. 边界（硬约束）

| 允许 | 禁止 |
|---|---|
| 只读托管产物字节，原样返回 | 解析产物、重算指标、二次加工数据 |
| `stat` 报 mtime/大小 | 复制产物进 panel-v2（复制 = 双源） |
| iframe 展示 | 与板块 / 持仓 / K 线弹窗联动（主人后续再定） |
| 插件内推导产物路径 | **往 `app/core/` 加任何 peg 相关代码或常量**（有测试锁） |
| — | 改 `peg_dashboard.py` 取数与算法、动 `peg/cache/` |
| — | 写 `data/panel/`、新建常驻服务或计划任务 |

iframe 切断右键菜单与 K 线弹窗，是当前形态的已知取舍。

## 3. 路由（全部在插件内，core 只负责 `/api/p/<id>` 前缀）

| 路由 | 语义 | 异常 |
|---|---|---|
| `GET /api/p/valuation/meta` | 产物元信息：`available / file / dir / mtime / mtime_text / size / age_seconds / stale / url` | 产物缺失仍 200，`available=false` + `reason`（刷新命令） |
| `GET /api/p/valuation/view` | 仪表盘本体，`text/html; charset=utf-8`，`Cache-Control: no-cache, must-revalidate` | 产物缺失 → 503 + `hint` |
| `GET /api/p/valuation/files/{name}` | 同目录其他产物（`peg_study_report.html` 等），只服务 `.html` | 越界 / 非 `.html` / 不存在 → 404 |

- `meta.url` 是前端 iframe 地址的**唯一来源**；前端不拼路由、不认产物文件名。
- 目录穿越防护：`resolve_product()` 用 `Path.resolve()` 归一后判断「是否仍在产物目录内」，
  不做字符串前缀比较；`..`、绝对路径、符号链接跳出一律 404。
- 产物路径在插件内推导：`paths.SUMMER_TOOLS_DIR / "peg" / "output"`（调用时求值，测试可 patch）。

## 4. 前端

`valuation.main`（slot=main，order=75，rail 位于「选股」与「后台」之间）。

- 顶栏：`产物 <时间> · <大小> · <多久之前>`（`stale` 标黄）+「重新载入」；
- 主体：iframe 铺满 `#stage`（实测 clientHeight + 负 margin 抵消内边距，ResizeObserver 跟随）；
- 不声明 `refreshEvery`：产物按需生成，自动重载只会打断阅读；
- `unmount()` 断开 ResizeObserver 并置空 iframe（shell 调用时不传参，句柄存模块作用域）。

## 5. 刷新产物（唯一更新方式）

```powershell
OH-WorkSpace\夏天-activity\工具\peg\refresh_dashboard.ps1
```

约 140 秒、会联网。跑完**不用重启 8810**：面板点顶栏「重新载入」（`?v=<mtime>` 变即重下）。

## 6. 拆卸（可执行步骤，2026-10-29 已实际演练一遍并复原）

```powershell
cd OH-WorkSpace\projects\panel-v2

# 1) 备份（回滚用）
$bk = "$env:TEMP\valuation_teardown"; New-Item -ItemType Directory -Force $bk | Out-Null
Copy-Item -Recurse -Force app\plugins\valuation "$bk\valuation"
Copy-Item -Force app\web\shell.js "$bk\shell.js"

# 2) 删插件目录
Remove-Item -Recurse -Force app\plugins\valuation

# 3) 删 app/web/shell.js 的 NAV 里这一段（valuation::valuation.main 那一项）
#    { key: 'valuation::valuation.main', label: '估值', icon: '...' },

# 4) 重启 8810（面板设置抽屉的「重启面板」，或 POST /api/shell/restart）
```

拆卸后应满足：`/api/health` 200、`plugins` 19→18（无 `valuation`）、rail 9 项无「估值」、
`/api/p/valuation/view` 404、`/peg/*` 404、`app/core/` 一个字节没动。

复原：把 `$bk\valuation` 拷回 `app\plugins\`、`shell.js` 拷回 `app\web\`，再重启 8810。

**本次演练实测（2026-10-29 21:1x）**：拆卸后 `health ok=true plugins=18 has_valuation=false`、
`/api/p/valuation/view` 404、`/peg/peg_dashboard.html` 404、首页 200、rail 9 项、错误卡 0；
复原后 `plugins=19`、`/api/p/valuation/view` 200、237068 字节。

## 7. 验收

1. `curl /api/p/valuation/view` → 200，字节数 = 磁盘文件字节数，含 `<title>PEG 估值选股仪表盘…</title>`；
2. `curl /api/p/valuation/files/../../app/core/main.py`（含 `--path-as-is` 与 `%2e%2e%2f` 两种）→ 404，不返回源码；
3. `curl /peg/peg_dashboard.html` → 404（core 不再托管）；
4. rail 出现「估值」，点开 iframe 铺满主区域，产物章节全部在；
5. 产物缺失：`/view` → 503 带刷新命令，卡片显示「产物不存在」而不是红屏；
6. 测试：`app/plugins/valuation/tests`（18 项，含 core 无 peg 字样的防回潮断言）。

## 8. 已知项（不在本次范围）

- 缺 `baostock` → PE 历史分位字段全空（产物内显示「—」），脚本侧问题；
- 产物是深色单主题，不随面板主题切换（产物自带配色，面板不注入）；
- 既有失败 `test_undefined_tokens_not_used`（`sector/frontend/board.js:43` 用了主题表未定义的
  `--mono`，写了 fallback 所以视觉无碍）与本次无关，按边界未处理。
