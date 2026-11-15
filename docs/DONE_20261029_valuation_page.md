# DONE 2026-10-29 · 面板 v2 新增「估值」页（PEG 估值仪表盘 · 插件内只读托管）

**结论**：rail 出现「估值」，点开主区域铺满显示现有 PEG 估值选股仪表盘全部内容。
托管在 `app/plugins/valuation` 插件内（`/view` + `/files`），**core 零改动**；
拆卸 = 删插件目录 + 删 NAV 一行，已实际演练并复原。

> v0.2.0 返工（2026-10-29）：初版把托管写进 `app/core/main.py` 的 `/peg` 静态挂载 + `paths.PEG_OUTPUT_DIR`，
> 违反主人「8810 插件化、便于拆卸」的要求，也让拆卸必须动两个 core 文件。已全部撤回：
> `main.py`、`paths.py` 恢复原样（`grep -i peg` 零命中），托管搬进插件 `FileResponse`。

## 1. 改动文件清单（最终态）

| # | 文件 | 类型 | 说明 |
|---|---|---|---|
| 1 | `app/plugins/valuation/plugin.json` | 新建 | id `valuation` v0.2.0，卡 `valuation.main`（slot=main，order=75），零数据白名单、零写 |
| 2 | `app/plugins/valuation/backend.py` | 新建 | 托管三端点：`/meta`（stat 报新鲜度）、`/view`（仪表盘本体 FileResponse）、`/files/{name}`（同目录 `.html`，带穿越防护） |
| 3 | `app/plugins/valuation/frontend.js` | 新建 | iframe 壳：src 取 `meta.url`、铺满 `#stage`、顶栏产物时间 + 「重新载入」、unmount 断开 ResizeObserver |
| 4 | `app/plugins/valuation/tests/test_valuation_plugin.py` | 新建 | 18 项：manifest/路由/meta/view 字节一致/缺产物 503/兄弟产物/非 html 404/穿越 404/**core 无 peg 字样** |
| 5 | `app/web/shell.js` | 改 | `NAV` 在「选股」与「后台」之间加 `valuation::valuation.main`（准星图标） |
| 6 | `夏天-activity/工具/peg/refresh_dashboard.ps1` | 新建 | 手动刷新：`envs\qmt312\Scripts\python.exe peg_dashboard.py`，约 140 秒 |
| 7 | `docs/valuation_contract.md` | 新建 | 契约（数据源唯一、边界、路由表、拆卸步骤、验收） |
| 8 | `docs/DONE_20261029_valuation_page.md` | 新建 | 本文件 |
| 9 | `docs/valuation_page_20261029.png` | 新建 | 实际页面截图 |

`app/core/main.py`、`app/core/paths.py`：**最终零改动**（返工中新增的 `/peg` 挂载与 `PEG_DIR/PEG_OUTPUT_DIR` 已整段删除）。
未动：`peg_dashboard.py`、`peg/cache/`、`data/panel/`、现有 5 个业务插件、`CORE_VERSION`。

## 2. 路由

| 路由 | 语义 | 异常 |
|---|---|---|
| `GET /api/p/valuation/meta` | `available/file/dir/mtime/mtime_text/size/age_seconds/stale/url` | 缺失仍 200 + `reason` |
| `GET /api/p/valuation/view` | 仪表盘本体，`text/html; charset=utf-8`，`Cache-Control: no-cache, must-revalidate` | 缺失 → 503 + `hint` |
| `GET /api/p/valuation/files/{name}` | 同目录其他 `.html`（`peg_study_report.html` 已验证 200 / 61238 字节） | 越界/非 html/不存在 → 404 |

## 3. 刷新命令

```powershell
OH-WorkSpace\夏天-activity\工具\peg\refresh_dashboard.ps1
```

产物落盘后不用重启 8810：面板点顶栏「重新载入」（`?v=<mtime>` 变即重下）。

## 4. 验收证据（2026-10-29 21:1x–21:2x，全部实测）

**① 拆卸演练（实际删掉再恢复）**

```
拆卸前: health ok=True plugins=19 has_valuation=True
删 app\plugins\valuation\ + shell.js 那一条 NAV → POST /api/shell/restart
拆卸后: health ok=True plugins=18 has_valuation=False
        /api/p/valuation/view           → 404
        /peg/peg_dashboard.html         → 404
        /                               → 200
        浏览器 rail = 仪表盘/作战表/盘中/板块/概念/复盘/自选池/选股/后台（9 项，无「估值」），错误卡 0
        app/core/ 全程未改（grep -i peg → 0 命中）
复原: 插件目录 + shell.js 拷回 → 重启
        health ok=True plugins=19 has_valuation=True
        /api/p/valuation/view → 200
```

可执行步骤写在 `docs/valuation_contract.md` §6。

**② 防回潮测试**

`test_core_has_no_peg_reference`：扫 `app/core/**/*.py`，任何文件出现 `peg`/`PEG`（大小写不敏感）即失败；
`test_core_mount_route_is_gone`：`GET /peg/peg_dashboard.html` 必须 404。
当前 `Select-String app\core\main.py,app\core\paths.py -Pattern peg` 计数 = 0。

**③ HTTP（curl）**

```
$ curl -s -o NUL -w "status=%{http_code} bytes=%{size_download} type=%{content_type}\n" \
    http://127.0.0.1:8810/api/p/valuation/view
status=200 bytes=237068 type=text/html; charset=utf-8      # 磁盘文件 237068 字节，一致

$ curl -s --path-as-is -o NUL -w "%{http_code}\n" \
    "http://127.0.0.1:8810/api/p/valuation/files/../../app/core/main.py"
404
$ curl -s -o NUL -w "%{http_code}\n" \
    "http://127.0.0.1:8810/api/p/valuation/files/%2e%2e%2f%2e%2e%2fapp%2fcore%2fmain.py"
404                                                         # 不返回源码

$ curl -s -o NUL -w "%{http_code}\n" http://127.0.0.1:8810/peg/peg_dashboard.html
404
$ curl -s -o NUL -w "%{http_code} %{size_download}\n" http://127.0.0.1:8810/api/p/valuation/files/peg_study_report.html
200 61238
$ curl -s -o NUL -w "%{http_code}\n" http://127.0.0.1:8810/api/p/valuation/files/snapshots.csv
404
$ curl -s http://127.0.0.1:8810/api/p/valuation/meta
{"ok":true,"available":true,"file":"peg_dashboard.html","mtime":1793278169,
 "mtime_text":"2026-10-29 20:49","size":237068,"age_seconds":1522,"stale":false,
 "url":"/api/p/valuation/view"}
```

**④ 浏览器（ego-browser，真实 Chromium）**

- iframe `src=/api/p/valuation/view?v=1793278169`，尺寸 1440×625；`#stage` 1442×657（差 = 内边距，即铺满）；
- iframe 内 `document.title` = `PEG 估值选股仪表盘 · 低估成长股雷达`，`body.innerText` 18,356 字；
- 产物章节实读：PE-G 散点象限、低估成长股池（可排序表）、横截面画像 12 图、PEG 分布、分层占比、
  低估 TOP10、行业中性低估榜、行业低估分布、行业平均 PEG、市值×增长气泡、增速持续性预警、
  防御溢价监控、估值×质量四宫格、预测分歧度、行业估值走廊、数据覆盖透视、方法论与局限性、
  预期修正雷达、行业修正中位数、上修榜；
- 切走再切回：iframe 1→0→1（unmount 收尾正常）；「重新载入」可点。

**⑤ pytest**

```
pytest app/tests app/plugins/{sector,kline,amv,battle_plan,watchlist,valuation}/tests -q
→ 497 passed, 1 failed, 1 skipped
```

唯一失败 `test_undefined_tokens_not_used` = **既有问题，与本次无关**，证据：

- 报错内容只涉及 `sector\frontend\board.js` 的 `--mono`，本次新增的 `valuation/frontend.js` 不在 offenders 里；
- 定位 `app/plugins/sector/frontend/board.js:43`：`font-family:var(--mono,var(--font-mono))`；
  `--mono` 未在主题表定义（写了 fallback，视觉无碍，但静态扫描不认 fallback）；
- 该文件 mtime `2026-10-27 23:20:01`、git 状态 `??`（历史会话新增、从未提交），本次会话未触碰任何 sector 文件。

按边界未修，需另开工单。

## 5. 已知取舍

- 产物是深色单主题，不随面板主题切换（产物自带配色，面板不注入）；
- iframe 内无右键菜单、无 K 线弹窗联动（主人明确后续再定）；
- 缺 `baostock` → PE 历史分位字段空（产物显示「—」），脚本侧问题。

## 6. 回滚

删 `app/plugins/valuation/` + `shell.js` 那一条 NAV，重启 8810 即可（core 不用动）；
只想临时关掉：`plugin.json` 的 `enabled` 改 `false` 后重启（`navItems` 会跳过缺卡）。
