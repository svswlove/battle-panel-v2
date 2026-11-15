# sector 双跑比对报告（旧 8002 → 新 8810）

**结论：PASS — 三个端点字段级零差异，sector 可进入切流。**

- 比对时间：2026-10-22
- 比对口径：`rank` 用 `date=2026-10-21`（两侧都有数据，未触发回退），`series` 用 `days=60&period=1`
- 比较总量：**260,026 个字段/元素**，`main_diffs=0`、`missing=0`、`extra=0`
- 唯一一组差异（`series` 的 `period=10/20`，12 处）**全部归因于旧端缺陷**，新版正确；详见 §5
- 证据：`tools/sector_diff_result.json`（890 KB，含全部 HTTP 尝试记录）

---

## 1. 方法与判定口径

只读 HTTP（GET），不连库、不写库、不写 `data/panel`。

| 项 | 口径 |
|---|---|
| 数值容差 | 两侧均为 `int` → **必须完全相等**；含 `float` → 绝对差 ≤ `1e-6`（未放宽） |
| 缺失 vs None | 键不存在记 `"<MISSING>"`，键存在但为 `null` 记 `None`，两者**不相等** |
| join 键 | `rank` 按 `code`；`tree` 按 `code`；`series` 按 `code` + 数组下标 |
| 排序 | `tree` 各层节点顺序**单独列出**（`ordering_diffs`），不混进字段差异 |
| 失败重试 | 5xx / 超时重试 2 次（共 3 次尝试）；4xx 不重试，按确定性结果记录 |
| 差异样例 | 每类最多 20 条（`diffs` 总数不截断） |

本次运行 **1533 个 HTTP 请求全部成功，0 次重试**。

## 2. 端点与字段映射

| # | 旧 8002 | 新 8810 | 响应顶层 keys | 是否同形 |
|---|---|---|---|---|
| 1 | `/api/sector_tree` | `/api/p/sector/tree` | `levels, l1, l2, l3, concept, index, tree, counts, note` | 完全同形 |
| 2 | `/api/sector_level?level=&date=&period=` | `/api/p/sector/rank?level=&date=&period=` | `date, level, period, n, rows[]` | 完全同形 |
| 3 | `/api/series?code=&days=&period=` | `/api/p/sector/series?code=&days=&period=` | `code, level, name, l1, ma_n, n_members, dates[], amv[], ma[], weight[], rank[], real_pct, amv_pct, diverge, flow_calc, flow_main, period, unit` | 完全同形 |

**字段映射：1:1 同名，无需改名。** 实测两侧顶层 keys 集合逐个相等：

- `tree` 节点字段：`code, name, level, l1, level_num, exclude, is_unmap, n_members`（两侧一致）
- `rank` 行字段：`code, name, l1, amv, weight, rank, amv_pct, real_pct, diverge`（两侧一致）
- `series` 标量 13 个 + 数组 5 个（两侧一致）

**路径差异（切流时前端需改的只有前缀）**：`/api/sector_tree` → `/api/p/sector/tree`，`/api/sector_level` → `/api/p/sector/rank`，`/api/series` → `/api/p/sector/series`。查询参数名未变。

## 3. 比较结果

| 类 | 比较项 | 结果 | 说明 |
|---|---|---|---|
| `/tree` | 5,080 | **pass** | `counts` + 5 层 724 节点 × 8 字段 + 键集合 + 顺序 |
| `/rank` | 28,960 | **pass** | 5 个 `period`（1/3/5/10/20）× 4 层 × 9 字段，另 2 个额外日期 |
| `/series` | 225,986 | **pass** | **全量 722 个 code** × `days=60`（非抽样） |
| `series` period 扫描 | 12（差异） | **旧端缺陷** | 见 §5，新版全部正确 |

### 3.1 `/tree`

| 层 | 旧 | 新 | join 成功 | 节点字段差异 | 排序差异 |
|---|---|---|---|---|---|
| L1 | 0 | 0 | 0 | 0 | 无 |
| L2 | 91 | 91 | 91 | 0 | 无 |
| L3 | 231 | 231 | 231 | 0 | 无 |
| CONCEPT | 390 | 390 | 390 | 0 | 无 |
| INDEX | 12 | 12 | 12 | 0 | 无 |

- `counts` 两侧一致：`{L1:0, L2:91, L3:231, CONCEPT:390, INDEX:12}`
- `note` 一致：`行业互斥（Σ==大盘）；概念/指数非互斥（一股可属多概念/多指数）`
- `tree`（L1 + children）两侧均为空数组——实库 `sector_meta` 无 L1 行，属数据现状，非新版缺陷
- 边界行 `BK9999`（`exclude=true, is_unmap=true, level_num=0, n_members=null`）两侧逐字段一致

### 3.2 `/rank`（date=2026-10-21）

| 层 | 旧 rows | 新 rows | join | only_old | only_new | 字段差异 |
|---|---|---|---|---|---|---|
| L2 | 90 | 90 | 90 | 0 | 0 | 0 |
| L3 | 230 | 230 | 230 | 0 | 0 | 0 |
| CONCEPT | 390 | 390 | 390 | 0 | 0 | 0 |
| INDEX | 12 | 12 | 12 | 0 | 0 | 0 |

- 比较字段：`name, l1, amv, weight, rank, amv_pct, real_pct, diverge`，另比顶层 `date/level/period/n`
- 行数 91 → 90（L2）、231 → 230（L3）：`BK9999` 等被 `exclude/is_unmap` 过滤，**两侧一致**
- `period=1/3/5/10/20` 五组全部 `diffs=0`
- 额外日期 `2026-10-20`、`2026-10-19`：各层行数相同、`diffs=0`（稳定性抽查）

### 3.3 `/series`（全量 722 个 code，days=60）

覆盖：L2 90 + L3 230 + CONCEPT 390 + INDEX 12 = 722 个 code，逐 code 比较 313 项（13 标量 + 5 数组 × 60 元素），合计 225,986 项。

- `diffs=0`、`missing=0`、`extra=0`
- 每 code 的 `dates[]`、`amv[]`、`ma[]`、`weight[]`、`rank[]` 长度均为 60，逐元素相等
- `n_members` 在 `BK9999` 两侧同为 `null`（池库无该 code）

## 4. 差异表

**主比对（tree / rank / series@period=1）：0 条差异。**

补充 `period` 扫描（`881101.TI`，`days=60`）的 12 条差异全部集中在 `period=10/20`：

| period | 旧端回显 | 新端回显 | 旧 `amv_pct` | 新 `amv_pct` | 旧 `flow_calc` | 新 `flow_calc` | 差异数 | 归因 |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | -0.63 | -0.63 | 46.63 | 46.63 | 0 | — |
| 3 | 3 | 3 | -9.39 | -9.39 | -14.82 | -14.82 | 0 | — |
| 5 | 5 | 5 | -15.11 | -15.11 | -33.97 | -33.97 | 0 | — |
| 10 | **1** | 10 | **-0.63** | -16.66 | **46.63** | -56.26 | 6 | 旧端缺陷 |
| 20 | **1** | 20 | **-0.63** | -28.87 | **46.63** | 41.2 | 6 | 旧端缺陷 |

`period=10` 的 6 条差异：`period, amv_pct, real_pct, diverge, flow_calc, flow_main`；`period=20` 同。

## 5. `period=10/20` 差异归因（旧端缺陷，非新版 bug）

不靠「旧端有 bug」一句话豁免，用**旧端自己的另一个端点**做独立参照判定。

**证据链**：旧 8002 `/api/board_flow` 不经 `series` 的参数传递路径，同库同日同 period 取值：

| period | 参照 `amv_pct` | 参照 `real_pct` | 参照 `flow_calc` | 参照 `flow_main` | 新版 | 旧 `series` |
|---|---|---|---|---|---|---|
| 10 | **-16.66** | **-7.44** | **-56.26** | **-27302.0** | 全部等于参照 | 全部等于 `period=1` 的值 |
| 20 | **-28.87** | **-5.16** | **41.2** | **-27302.0** | 全部等于参照 | 全部等于 `period=1` 的值 |

- 旧 `/api/sector_level?level=L2&period=10` 对 `881101.TI` 同样返回 `amv_pct=-16.66`，与 `/api/board_flow` 一致
- 旧 `series` 返回 `-0.63`，等于它自己 `period=1` 的结果 → **旧 `series` 丢了 `period` 参数**
- `diverge` 用参照推导 `round(amv_pct - real_pct, 2)`：`round(-16.66 - (-7.44), 2) = -9.22` = 新版值
- `period` 回显应等于请求值 10/20，新版正确
- 归因结论：12 条差异中 **12 条判定为「新版正确、旧端错误」**，`unexplained=0`

**反向验证**（防止归因逻辑把新版 bug 洗白）：人为把新版 `period=10` 的 `amv_pct` 改成 `-99.0` 后重跑，脚本报 `unexplained=1` 且把该条标为 `attribution: unexplained`，其余 5 条仍归因旧端 → 归因逻辑能区分方向。

## 6. 是否可进入切流

**可以，建议进入切流观察期。** 依据：

1. 三个端点在**全量数据**（722 个 code 的 series、5 个 period × 4 层的 rank、724 个 tree 节点）上字段级零差异，缺失/多余均为 0
2. 唯一差异组已用独立参照证明是旧端缺陷，新版行为与旧端其他端点自洽
3. 前端需改的只有 URL 前缀（§2），查询参数与字段名不变

**切流注意（已知行为差异，非数据差异）**

| 项 | 旧 8002 | 新 8810 | 影响 |
|---|---|---|---|
| `series` 的 `days` 默认 | 80（实测） | 60，上限 250（实测 `days=500`→250） | 前端显式传 `days` 即可，当前卡片传 60，无影响 |
| `series?period=10/20` | 回落到 1（bug） | 正确返回 10/20 | 切流后数值会变，**这是修正不是回归**；若前端有依赖旧错值的逻辑需同步检查 |
| `level` 非法 | 200 + 空 `rows` | 400 | 前端有固定枚举，无影响；异常路径语义更明确 |
| 库不可用 | 500 + `{"error":...}` | 503 | 仅影响故障期。**本次未验证**（需停库，违反只读约束）；该行为来自 `docs/sector_contract.md` §5，非实测 |
| `/api/series_{code}.json` 静态文件 | 有 | 未实现（由 `/series` 覆盖字段） | 若有前端直连静态文件需改走 `/series` |

**回退**：停 8810 进程即可，旧 8002 全程未改。

## 7. 未比较项（明确列出）

**端点级未覆盖**

- 旧 `/api/rank`（`ep_rank`，当日流入/流出 TOP + new/exit）——新端无对应，**未比较**
- 旧 `/api/trend`、`/api/board_top`、`/api/board_flow`（`board_flow` 仅作归因参照，未做正式 diff）
- 旧 `/api/members`、`/api/catalog.json`、`/api/manifest.json`、`/api/search`、`/api/contrib`
- 旧 `/api/score_trail`、`/api/card_live`、`/api/board_kline`、`/api/minute_*`、`/api/hcl*`、`/api/index_closes`
- 旧 `/api/leaders_{code}.json`、`/api/series_{code}.json`（静态文件形态）
- 双轨多空 `bull`（`amv_regime`）——新端未接，**未比较**

**维度级未覆盖**

- `series` 的 `days` 非 60（如 20/80/120/250）**未比较**；本次只跑 `days=60`（仅抽查了默认值与 `days=500` 的上限行为）
- `series` 的 `period=10/20` 只对 `881101.TI` 一个 code 验证，其余 721 个 code **未做 period 扫描**
- `rank` 只覆盖 `2026-10-21/20/19` 三个日期，更早历史**未比较**
- `rank` 的 `period` 5 组只在 `date=2026-10-21` 上比较，其他日期 × period 组合**未比较**
- 非 `exclude/is_unmap` 的 `L1` 层：实库 `L1=0` 行，**无数据可比**
- HTTP 头、状态码细节（除 4xx/5xx 错误路径外）、响应字节序、JSON 键顺序**未比较**（只比语义值）
- 性能（延迟/并发）**未比较**——本次只判字段一致性
- 故障路径（库不可用 → 503）**未实测**：需停库，超出只读约束
- 前端渲染结果**未比较**（本任务只比 API）

## 8. 复现

```powershell
cd OH-WorkSpace\projects\panel-v2

# 比较器自检（不联网）
python tools\test_diff_sector_selfcheck.py

# 默认：5 个代表 code + rank period=1
python tools\diff_sector.py

# 本次报告口径（全量）
python tools\diff_sector.py --rank-periods 1,3,5,10,20 --rank-dates 2026-10-20,2026-10-19 --full-series
```

退出码：`0`=PASS，`1`=FAIL，`2`=PARTIAL/ERROR。产物：`tools/sector_diff_result.json`。

## 9. 发现的新版问题

**无。** 本次比对未发现新版 8810 的字段级 bug。上面 §6 的 5 项属有意的行为差异，不构成缺陷。

旧端问题（不在本次修改范围，仅记录）：

- 旧 `api_series2` 把 `period` 以字符串传入 `ep_board_flow`，`'10'→1`、`'20'→2→1`，导致 `series?period=10/20` 静默回落到 `period=1`。修法：`int(q['period'][0])`，一行。建议主会话决定是否修旧端。
