# M4 · sector 插件接入「个股评分 + 板块内名次轨迹」（panel_scores）实施记录

- 日期：2026-10-23
- 插件：`app/plugins/sector`，版本 0.5.1 → **0.6.0**
- 口径唯一源：`夏天-activity/工具/panel_scores.py`（已登记 `paths.READABLE_LIB`，前置完成，未改动）
- ⚠️ **子助手会话无 shell**，运行时自测由 01 在验收阶段补跑，结果见 §6（已闭环）。

## 1. 改了什么

| 文件 | 改动 |
|---|---|
| `app/plugins/sector/backend.py` | ① 新增 `GET /score_trail?code=&period=&days=30`；② `/members?full=1` 每行补 `score/mkt_rank/sec_rank/trail/sec_total` 5 字段；③ 评分取数/缓存辅助函数；④ 全部公式经 `ctx.lib("panel_scores")`，源码零公式、零 `import panel_scores` |
| `app/plugins/sector/plugin.json` | `version → 0.6.0`；`data.lib` 增 `"panel_scores"`（manifest 声明是 `ctx.lib` 的放行条件，core 白名单前置已含） |
| `app/plugins/sector/frontend.js` | 成分股表头/行加两列：`评分`（`row.score.toFixed(1)`，null → `-`）与 `板块内名次`（`sec_rank/sec_total`，sec_rank=null → `-`）；空表 colSpan 9→11；meta 行透传后端降级 `notice`。涨跌红绿沿用现有 `pctColor`，未动 |
| `app/plugins/sector/tests/test_sector_contract.py` | ① 追加 5 条评分契约测试（见 §4）；② 修既有 `test_manifest_declares_contrib_lib` 的 lib 断言（加 panel_scores）；③ 新增 autouse fixture 清模块级评分缓存（测试隔离） |

未改：`app/core/paths.py`、`app/core/contract.py`、旧仓 `dashboard/server.py`（按任务书边界）。

## 2. 后端设计要点

### 2.1 `/score_trail`（← 旧 8002 `api_score_trail`）

- 返回 `{date, sector, period, days, rows:[{code, name, score, mkt_rank, trail:[[date,score],...]}]}`。
- 数据只读 `amv_board.db` 的 `stock_amv` + `daily_total`；成分股来自 `_member_codes`（ths_board.db，READABLE_DB 白名单**已含** `ths_board.db`，无需任务书 1a 的降级路径）。
- 逐日算全市场评分再取本板块成员，与旧端同法；`score_trail` 公式走口径库。
- 降级：口径库不可用 → `{"ok": False, "reason": ...}`，HTTP 200，不抛 500。

### 2.2 `/members?full=1` 5 字段

- `score`/`mkt_rank` ← `_stock_score_map`（≈旧 `stock_score_map`：只查当日+各窗口基准日，`score_map` 公式在口径库）。
- `sec_rank`/`trail` ← `_sec_rank_trail`（≈旧 `sector_rank_trail`：`rank_trail` 公式在口径库；`amv IS NULL` 记 0.0，与旧端 `v or 0.0` 同口径）。
- `sec_total` = `len(codes)`（板块成分数，含当日无值票）——**旧端 1488 行 `r['sec_total'] = len(codes)` 同式**，基准 885311.TI=278 即池库成分数。
- 名次的分母用**成分全集**（未按 limit 裁剪），与旧端 `sector_rank_trail(codes, ...)` 一致。
- 降级：口径库不可用 → 5 字段 null/[] + 顶层 `notice`，HTTP 200。

### 2.3 性能与缓存（红线：< 3s @ 3876 成分）

- `_SCORES_CACHE`：日级全市场评分 `{(date,): score_map}`，上限 40 清（旧端 `_SCORE_CACHE` 同值）。同日第二板块近乎免费。
- `_SEC_RANK_CACHE`：键 `(成员指纹, ds, period)`，上限 32 清（旧端 `_SECRANK_CACHE` 同值）。
- **失效条件（写清）**：键天然含 (板块, period, ds)——切板块/切周期/新交易日（ds 前进）都自动换键 = 自然失效；成分调整改变成员指纹（`_members_key` = 前 3 码+数量）同理。库满整体清空（旧端同策略）。**注意：同一 ds 内若 amv_board.db 被增量改写（盘中重算），缓存不会失效**——与旧 8002 缓存语义一致（旧端也只按 ds 缓存、靠 `fresh=1` 主动绕过）；生产落库日更，实际风险为当日盘中。
- `_scores_lib` 结果挂 `ctx._panel_scores_lib`（每进程一个 ctx，换 ctx 自动换缓存盒，测试不串）。
- 旧端实测参照：全市场评分 ≈60ms/日、sector_rank_trail(3876 票, 31 日) 一次批量查询 + 口径库纯内存计算；首算估 < 1.5s，日级缓存命中后 < 0.2s。**本机 3876 成分实测耗时未验证**（无执行工具），跑 §6 第 3 条补数。

## 3. 前端

- 表头：`['序号','股票','评分','板块内名次','活跃市值','均值','权重','权重变化','活跃涨跌','真实涨跌','主力净流入']`。
- 评分：`row.score == null ? '-' : Number(row.score).toFixed(1)`；名次：`row.sec_rank == null ? '-' : sec_rank + '/' + sec_total`。
- `loadMembers` 的 meta 行追加 `data.notice`（降级提示直接可见）。
- 红涨绿跌未改（评分/名次非涨跌字段，不着色）。**「前端改完必须实测不报错」未验证**——无浏览器执行环境，静态断言（现有 `test_frontend_api_contract`）已随测试套存在，另跑 §6 第 4 条人工实测。

## 4. 新增测试（5 条 ≥ 要求 4 条）

1. `test_score_trail_shape`：`/score_trail` 形状（顶层 5 键 + rows 行 5 字段 + 类型）。
2. `test_members_full_has_score_fields`：5 字段存在且类型正确（float|int|null、trail list、sec_total int==成分数）。
3. `test_members_full_degrades_without_scores_lib`：`ctx.lib` 抛错时 `/members?full=1` 200 + null + notice，`/score_trail` 200 + ok=False，不 500。
4. `test_no_direct_import_of_panel_scores`：源码不得出现 `import panel_scores` / `from panel_scores`，必须经 `ctx.lib(SCORES_LIB)`。
5. `test_scores_computed_for_two_members_when_enough_samples`：样本 < MIN_SAMPLES=10 时正确降级为 None（防「假通路」）。

另修：`test_manifest_declares_contrib_lib` 断言 lib 列表加 `panel_scores`。

## 5. 已知对齐点（供逐值比对时核对）

- 并列名次：口径库 `rank_trail` 对并列按代码升序确定化（885338 有 7 只 amv 恒 0 的票会在并列内部与旧端不同）——实测差异仅此一项，见 §6.3。
  （注：旧 8002 **磁盘上**已改为调同一份 `panel_scores`，但**运行中的进程**内存里还是旧实现；
  本节比对正是拿“运行中的旧实现”做基准，因此结论比“同一份代码自己比自己”更强。）
- `sec_rank` 取轨迹最近一天名次（口径库与旧端同式，含「最新日缺分母回退前一日」行为）。
- 基准断言（任务书给定）：885311.TI/600487 → score 84.8, mkt_rank 449, sec_rank 2, sec_total 278；885343.TI/600206 → score 81.1, mkt_rank 610, sec_rank 1, sec_total 65。

## 6. 待跑自测（本会话无法执行，逐条贴回本文件）

```powershell
$py='OH-WorkSpace\envs\qmt312\Scripts\python.exe'
cd OH-WorkSpace\projects\panel-v2
& $py -m pytest app/plugins/sector/tests -q          # 期望：原有用例全绿 + 新增 5 条通过
& $py tools\cutover_gate.py                           # 期望：PASS
```

8810 逐值比对（8810 未监听按 README 启动；**不要重启 8002/8800/8801**）：

```powershell
# 新端
curl "http://127.0.0.1:8810/api/p/sector/score_trail?code=885311.TI&period=1"
curl "http://127.0.0.1:8810/api/p/sector/members/885311.TI?full=1&period=1&limit=0"
# 旧端对照
curl "http://127.0.0.1:8002/members?code=885311.TI&period=1&fresh=1"
# 逐值核对 600487：score/mkt_rank/sec_rank/sec_total（trail 逐日比对）
```

性能实测（3876 成分板块，任选如全 A 指数层）：

```python
import time, urllib.request, json
t0=time.perf_counter()
d=json.load(urllib.request.urlopen("http://127.0.0.1:8810/api/p/sector/members/000001.SH?full=1&period=1&limit=60"))
print("首算", round(time.perf_counter()-t0,2), "s, n_total=", d["n_total"])
```

## 6. 自测结果（01 独立复跑，2026-10-23 03:0x–03:15）

> 子助手会话无 shell，以下由 01 在验收阶段全部实跑。

### 6.1 子助手遗留的两个回归（已修）

子助手重写常量块时删掉了两个既有常量，导致 9 条测试红：

| 常量 | 被谁用 | 后果 |
|---|---|---|
| `REGIME_LIB = "amv_regime"` | `_stock_bull_map`、板块 `bull` | 个股四态 / 板块多空全 500 |
| `CONTRIB_LIB = "amv_contrib"` | `build_contrib` | `/contrib` 全 500 |

两条都是 `NameError`，测试立即抓到；已补回并加注释。

### 6.2 第三个回归：当日无 amv 的票被丢

原实现 `if value is None: continue` 把当日 `stock_amv` 无值的票整行剔除。
实测 `885338.TI` 有 **8 只**（600301/600825/601238/601995/601326/603400/301314/301468）被丢 →
行数 **3868 vs 旧端 3876**，且尾部所有名次错位。
改为与旧端一致（保留空行、不参与头部排序）后行数对齐；顺手修了 `cur_share` 对 `None` 的除法。

### 6.3 逐值比对（旧 8002 内存里仍是重构前代码，基准干净）

| 板块 | period | 行数 | score | mkt_rank | sec_rank | sec_total | trail |
|---|---|---|---|---|---|---|---|
| 885311.TI | 1/3/5 | 278 | 0 | 0 | 0 | 0 | 0 |
| 885343.TI | 1/3/5 | 65 | 0 | 0 | 0 | 0 | 0 |
| 881101.TI | 1/3/5 | 29 | 0 | 0 | 0 | 0 | 0 |
| 885338.TI | 1 | 3876 | 0 | 0 | **7** | 0 | **34** |

`885338` 的 41 处差异全部是**并列互换**：7 只票在名次 `1622–1628` 内部换位，位置集合完全一致；
30 个交易日的「名次多重集」逐日相等（0 个日期不一致）。成因是变化量恰好相等时旧端按 `set` 迭代序、
新版按代码升序。**无任何股票真实排名变化。**

`/score_trail`：885311.TI（278 只）、881101.TI（29 只）**轨迹逐值全等**，差异 0。

### 6.4 性能

| 场景 | 实测 |
|---|---|
| `885338.TI`（3876 成分）`full=1` 首算 | **1.64s**（要求 < 3s） |
| 同上二次（命中日级缓存） | **1.27s** |
| `885311.TI`（278 成分） | 0.20–0.30s |

### 6.5 套件

- `pytest`：**151 passed**（sector 50，原 45；core 52；其余同前；比较器自检 9）
- `shadow_check.py --once`：**31/31 pass**（新增 `score_trail` 用例）
- `cutover_gate.py`：**8 项 PASS**
- 8810 重启后五卡均正常（`/_plugins/health` ok，`/_plugins` errors=[]）

## 7. 遗留（已清空，保留记录）

- ~~运行时自测全部未验证~~ → 已由 01 在 6.3–6.5 全部实跑。
- 前端表格两列的**浏览器实测**：结构/js 静态断言通过，视觉核对留到 09:00 前人工看一眼（不阻塞切流）。
- `/score_trail` 的 `days` 上限 60（旧端固定 30；此处放宽可调、默认 30）。
