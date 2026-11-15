# HANDOFF · 审计 Rfix（2026-10-24 夜）

被审对象：`docs/audit_20261024_R124.md`（批次 1/2）与 `docs/audit_20261024_R35.md`（批次 2/2）。
本单范围：两份报告的**全部 P1（3 项）+ 2 项 P2**。其余 P2 未动，见 §5。

## 0. 结论

| 审计项 | 级 | 处置 | 证据 |
|---|---|---|---|
| R124 P1 ① `breadth_880.prev == today`（周末窗口） | P1 | 已修 | `compat_sector/backend.py::_prev_880` 锚改「日线末根日期」；2 条新用例 |
| R35 P1-1 review 卡 CSS 漂移（3 条 @media 丢失 + v6 正文块重复注入两遍） | P1 | 已修 | `review/frontend.js` 补 3 条 @media、删 52 行重复块、删 1 行重复垫片；新断言锁规则头集合等值 |
| R35 P1-2 concept 卡占位芯片点击被详情委托吞掉 | P1 | 已修（卡内闭环） | `concept/frontend.js` 委托前置判 `[data-open-kline],[data-open-amv]`；jsdom harness + 静态断言 |
| R35 P2-1 `.rv-gap` 类名撞车 | P2 | 已修 | 卡自有卡头 spacer 改名 `.rv-sp-gap`；旧页 `.rv-gap` 告警条保持原样 |
| R35 P2-2 STATUS.md 过期陈述 | P2 | 已修 | `docs/STATUS.md` 按磁盘现状重写「已知边界 / 周一清单」 |

自测：`python -m pytest -q` → **1227 passed / 4 skipped，exit 0**（44.98s）；`python tools\cutover_gate.py` → **PASS 8/8，exit 0**（45.1s）。
活体：临时实例 **8821**（`PANEL_V2_FEED=0`，本单自起自停），8810/8800 未碰、未重启、未 commit。

## 1. breadth_880 prev（R124 P1）

**根因（与审计定位一致）**：旧实现 `_pick` 用「自然日 today」当锚（`row[0] < int(today8)`）。非交易日里
880005/880006 快照返回的是最近交易日（周五 20261023）的收盘值，远端日线末根也是同一根 → 末根满足
`< today` 被判成「昨日」，prev 与 today 全等。

**修法**：锚改为「日线末根日期」（`anchor = max(min(末根, today))`，两源取较新者），再取**严格早于 anchor**
的最后一根。只有一个根（= 快照交易日）或没有更早的 → prev 全 null，宁可缺也不拿 today 冒充。
不改数据源、不改字段结构（prev 仍无 `total` 键）、不动 `BREADTH_880_STALE_DAYS` 停旧判据。

**为什么不用交易日历**：`common.trading_calendar` 未登记为口径库（`app/core/panic_calc.py` 里是懒引入 + 工作日
兜底），compat_sector 的 manifest 也没有该 lib 权限；引入要扩白名单。日线末根本身就是「数据源认定的最近
交易日」，语义更贴近旧端 `.day` 行为，且零新依赖。

**活体（8821 vs 8800 同参，2026-10-24 20:5x 周六）**：

| 端 | today.date / up / limit_up | prev.date / up / limit_up |
|---|---|---|
| 8821（新代码） | 20261024 / 4526 / 84 | **20261022 / 535 / 33** |
| 8800（旧） | 20261024 / 4526 / 84 | 20261022 / 535 / 33 |
| 8810（生产，旧代码未重启） | 20261024 / 4526 / 84 | 20261023 / 4526 / 84（仍是 today 同值） |

prev 段与 8800 **逐字段相等**（`amount` 也相等）。8810 要等重启才生效。

**测试**：`test_breadth_880_prev_skips_snapshot_trade_day`（末根=快照交易日 → prev 取更早那根，并断言 prev ≠ today）、
`test_breadth_880_prev_null_when_only_snapshot_bar`（只有一根 → 全 null）。原有用例全绿。

## 2. review 卡 CSS（R35 P1-1 / P2-1）

**实测确认**：旧页第一个内联 `<style>` 里复盘作用域规则 304 条（脚本解析，含 3 条 @media）；卡内 CSS 只有
301 条对应 + 34 个规则头被注入两遍。

**改动**（`app/plugins/review/frontend.js`，CRLF 保持）：
1. 补 3 条 @media，源序照旧页（紧跟各自基础规则之后——同特异性靠源序覆盖，放前面会被基础规则盖掉）：
   - `@media (max-width:760px){ #panel-review .rv-md { padding-left/right: var(--d-gap) } }`
   - `@media (max-width:760px){ #panel-review .rv-topline { padding-left/right: var(--d-gap) } }`
   - `@media (max-width:900px){ .rv-cols-wide { grid-template-columns:1fr } }`
2. 删掉 B 段里重复注入的 52 行（v6 正文块 33 个选择器 ×2 的第二份）；另删 1 行重复的面板兼容垫片
   `#panel-review .rv-root section.rv-sec{display:block}`（旧页只有一份）。
3. P2-1：卡自有卡头 spacer 由 `.rv-gap` 改名 `.rv-sp-gap`（CSS 与 `div('rv-sp-gap')` 两处）；旧页 `.rv-gap`
   告警条规则原样保留。

**测试**：新增 `test_scoped_css_rule_headers_match_legacy` —— 从旧页第一个 `<style>` 抽复盘作用域规则（304 条），
与卡内 CSS 按「(外层 @media, 选择器) → 声明归一文本」比对：**必须无缺失、无重复规则头、无声明差异**，并显式锁死
那 3 条 @media。新增 `test_own_spacer_not_colliding_with_legacy_gap`。

**变异验证**（确认断言真会红）：临时删 `@media (max-width:900px)` 那条 → 1 failed；临时把 `.rv-cols-wide`
基础规则复制一份 → 1 failed；两次都已原样恢复。

**活体**：8821 下发 `/plugins/review/frontend.js`（53,067B），jsdom 真实挂卡后读注入的 `<style>`：3 条 @media 均在、
`#panel-review .rv-md{font-size:.86rem…}` 只出现 **1 次**、卡头元素是 `.rv-sp-gap` 且无 `.rv-gap`。

## 3. concept 卡占位芯片（R35 P1-2）

**改动**（`app/plugins/concept/frontend.js`）：卡内点击委托**先**判
`t.closest('[data-open-kline],[data-open-amv]')`，命中即 `return`，不进 `.cp-c[data-slug]` 详情分支。
与旧页同口径（旧页靠捕获阶段 `stopPropagation`，`index.html:10936`；iframe 里父页捕获够不到，故卡内自己判）。
占位 title 由「点击看 K 线 / 点击看 AMV 走势」改为「K线跳转待桥接 / AMV 跳转待桥接」——卡内没有跳转能力，
不能留旧页文案。

**验证**：
- 新增 harness `app/plugins/concept/tests/harness_chip_click.mjs`（jsdom，无网络，假 ctx）：点个股芯片 / 板块芯片
  均 0 次详情请求、仍在列表；点卡片正文仍正常开详情；返回列表可用。**11 项 PASS，exit 0**。
- 新增静态断言 `test_placeholder_click_guard_precedes_detail`（守卫必须在详情分支之前且命中即 return）、
  `test_placeholder_titles_say_pending_bridge`。
- 变异验证：临时删掉守卫那行 → harness 4 项 FAIL（detail=1、详情视图被打开）、静态断言 1 failed；已原样恢复。
- 活体：8821 真实 `/api/concept.json` 数据（21 活跃 / 26 链位）上 jsdom 挂卡，点真实芯片 → 0 次 `?slug=` 请求、
  无返回按钮、title 含「待桥接」；点卡片正文 → 详情正常打开。**12 项 PASS，exit 0**。

## 4. 未做（明确登记）

**跨 iframe 的占位跳转桥**：本单只保证「不误伤」（芯片点击不再误开详情），**不实现**真跳转。卡内点芯片当前
是「什么都不发生」。真跳转需 v2bridge 在父页监听 iframe 的 postMessage 再调旧页 `KLineApp.open` / `AmvCard.open`
（旧页捕获委托够不到 iframe 内部）——属 R4 桥的后续增强，本单未开。

## 5. 本批未动的审计项（不属白名单 / 不属 P1）

- R124 P2-②：`tools/cutover_gate.py` 的 pytest 目录白名单缺 `selection`（`intraday` 已在）。文件不在白名单，未动。
- R124 P2-③：R2 HANDOFF 缺失 —— 该文件已存在（`docs/HANDOFF_20261024_R2_selection.md`），STATUS 已同步。
- R124 P2-④：并发自测打生产写端点的模式建议（`PANEL_BASE` 默认值）——观察项，未改。
- R35 P2-3：R3 自报测试计数偏差（36 vs 34）——已由本轮实测覆盖（`test_review_frontend.py` 现 36 条，
  `test_concept_frontend.py` 37 条）。
- R35 P2-4：降级链夹具未入库 —— 未动。
- R35 P2-5：HANDOFF 字符数口径差异 —— 未动。

## 6. 白名单核对

改动文件（全部在白名单内）：
- `app/plugins/compat_sector/backend.py`
- `app/plugins/compat_sector/tests/test_compat_sector.py`
- `app/plugins/review/frontend.js`
- `app/plugins/review/tests/test_review_frontend.py`
- `app/plugins/concept/frontend.js`
- `app/plugins/concept/tests/test_concept_frontend.py`
- `app/plugins/concept/tests/harness_chip_click.mjs`（新增，`tests/` 下）
- `docs/STATUS.md`
- `docs/HANDOFF_20261024_Rfix_audit.md`（本文件，新增）

未动：`app/core/*`、`tools/*`、`app/web/*`、`v2_map.json`、其它插件、8810/8800 进程。
临时探针脚本（`_tmp_*`）跑完已删。
