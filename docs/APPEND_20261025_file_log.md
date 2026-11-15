# 文件修改日志 · 2026-10-25 · 追加（待 01 搬入 OH-Works/文件修改日志/2026-10-25.md）

> 本单工作区在 `projects/panel-v2`，日志目录在工作区外（写入被沙箱拒绝：EPERM），
> 故正文先落 `projects/panel-v2/docs/`，请 01 原样 append 到当日日志。

---

## 01 · 盘中照旧页搬第二步：板块与个股研究（执行=DSHana 子代理）

**背景**：审计确认盘中字数缺口的 98.5%（旧页 10624 字）是这张卡。第一步 `_st*` 已验收。

**改动**（白名单内，未碰 engine / core / 其它插件 / 已验收的 sector.js·caliber.js）：

| 文件 | 动作 | 行数 |
|---|---|---|
| `app/plugins/intraday/frontend/research.js` | 新增 | 475 |
| `app/plugins/intraday/frontend/research.css.js` | 新增 | 137 |
| `app/plugins/intraday/frontend.js` | 改（仅编排接入 +25 行） | 719 |
| `app/plugins/intraday/tests/test_research.py` | 新增（13 条） | 353 |
| `app/plugins/intraday/tests/research_align.mjs` | 新增（旧页对照运行器） | 122 |
| `app/plugins/intraday/tests/research_dom.mjs` | 新增（jsdom 交互运行器） | 167 |
| `app/plugins/intraday/tests/fixtures/amv_research_l2_board.json` | 新增（真实载荷快照） | 140423 B |
| `app/plugins/intraday/tests/fixtures/amv_research_l2_all.json` | 新增 | 202924 B |
| `app/plugins/intraday/tests/fixtures/amv_research_l3.json` | 新增 | 146059 B |
| `docs/HANDOFF_20261025_intraday_research.md` | 新增 | — |
| `docs/APPEND_20261025_file_log.md` | 新增（本文件） | — |

**验证（我执行的部分）**：与旧页 `_rsHtml()` 逐字节对齐（node:vm 跑旧页源码，5 组参数，
规范化后 250564 = 250564 字节，equal=true）；jsdom 交互全集 13 项全绿；整卡挂载零异常；
app/tests 三类静态门禁按同款正则自扫，intraday 6 个前端文件 0 违规。

**验证（未执行，需 01 复跑）**：`pytest app/tests app/plugins/intraday/tests -q`。
原因：本机沙箱禁止子进程（`spawnSync` 一律 EPERM，node.exe / python.exe 绝对路径均被拒，
提权申请被驳回）。基线 194 passed / 1 skipped，本单 +13 条，预期 207 passed / 1 skipped。

**发现旧页一个真 bug（需拍板是否回修）**：`_rsPaint` 缓存签名（旧页 4395）不含 `_rsSrt`，
而 `rsSet('srt')` 只调 `_rsPaint()` → 点排序按钮状态变了但表不重排、箭头不动
（node:vm 实测连点三列，首行 6 格与 th class 逐字节不变）。卡片侧已把 `srt` 计入签名修好；
`engine/` 本单只读，旧页未改。

**回退**：删除 5 个新增源文件 + 3 份 fixture，`git checkout -- app/plugins/intraday/frontend.js`
（frontend.js 只加了 import / secResearch 节点 / createState+bind / unmount / refresh 五处）。
静态文件改动，8810 未重启，硬刷新即生效。

**未做**：任何 git 写操作；未改 `test_intraday.py`（其子模块白名单仍只列第一步 4 个，
新增文件由 `test_research.py` 守门；若要求统一，追加两个文件名即可）。
