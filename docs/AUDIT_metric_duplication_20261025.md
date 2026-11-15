# 指标重复实现审计（2026-10-25）

审计人：嗅嗅（审核）｜委托：01｜范围：`projects/panel-v2/app/`

> 本文件由 01 代为落盘。嗅嗅会话为只读授权，无法写入。

## 一、违规确认：0 处

01 先前报的「三处绕过统一门面」**全部不成立**。以下位置都是**文档字符串**里写的「本插件严禁 sqlite3.connect / from eltdx」说明句，不是代码：

| 位置 | 实际内容 |
|---|---|
| `concept/backend.py:24` | docstring 禁令说明 |
| `concept/render.py:13` | docstring 禁令说明 |
| `concept/source.py:13`、`:414` | docstring 禁令说明 |
| `review/backend.py:33` | docstring 禁令说明 |
| `review/paper.py:35` | docstring 禁令说明 |
| `compat_misc/backend.py:67` | docstring 禁令说明 |

按 **import 语句**（AST）扫全部插件：生产文件零命中，命中全在 `tests/`（测试自连库做校验，正常）。
`concept` 实际走 `ctx.db_read(BOARD_DB)`（`backend.py:217`）。

**教训**：用 grep 搜字符串会把注释和 docstring 算进去，必须用 AST 或只匹配 import 语句。01 最初报错即源于此。

## 二、静态扫描测试：不是漏网，是原文判定错误

扫描器在 `core/contract.py:39-46`、`1046-1057`，**纯子串全文匹配**——真违规会命中，包括 docstring 里的字面量也会命中（这导致 `compat_misc/backend.py:67` 的说明文字本身会被扫到）。

真实缺口两条：

1. `concept/tests/test_concept_source.py:420-435` 只扫 `source.py`，`backend.py` / `render.py` 无扫描
2. `compat/backend.py:1090-1105`、`compat_misc/backend.py:164-180` 用 `importlib` 按路径加载 `ths_board_pool.py`（内含 `import sqlite3`、`def connect`），**扫描器看不见这条通道**

当前只调 `is_custom_code` 纯函数，不算违规；但这条通道是隐患。

## 三、同指标多实现：7 组，其中算法不一致 4 组（高危）

| # | 指标 | 实现 A | 实现 B | 算法一致 | 差异点 | 级别 |
|---|---|---|---|---|---|---|
| 1 | AMV MA10 | `lib/amv_regime.py:52 sma`（正本，前 9 根 None，÷n） | `compat_misc:666`（扩张窗÷count）、`sector:880-884`、`amv/backend.py:60-63`、`compat_kline:552-560`（严格 n=10）、`sector:952-954`（回退）、`compat_sector:212`（None→0.0） | **否（6 种写法）** | 窗头 None vs 部分平均；分母 n vs 可用点数；None 三态处理 | 高 |
| 2 | 板块权重 Δ | `sector:792/825/861`（小数，未 ×100） | `sector:1226`（×100）、`compat_sector:247-259`（×100，且改用 amv/total 现算，非 share 列） | 部分 | **两种量纲，混用错 100 倍**；数据路径也不同 | 高 |
| 3 | 个股四态 | `sector:436-451`（45 日窗，None 跳过） | `compat_watchlist:453-476`（全序列，**None→0.0**） | 主体同源，输入不同 | 0.0 假值压低 MA10/EMA，临界处翻转 | 高 |
| 4 | 880 快照映射 | `core/panic_calc.py:207-235`（3 码，原始 float） | `compat_sector:530-560`（单码，int 取整，失败即弃） | **否** | 取值集、归一化、失败判据全不同 | 中 |
| 5 | 板块 amv_pct | `sector:526`（基准 dates[0]） | `compat_sector:252`（基准 last−win） | 是 | 基准日/窗口定义不同 | 中 |
| 6 | `_is_custom_board` | `compat:1090-1105` | `compat_misc:164-180` | 是（逐字复制） | 纯复制，且依赖外部工具目录 | 中 |
| 7 | 上证 MA5/10/20 | `core/review_chart.py:731-733` | `core/review_chart.py:752-755` | 是 | 同函数内复制，多打一次库 | 中→低 |

## 四、单列口径分歧（最严重）

1. **`None→0.0` 假值注入两处**：`compat_sector:212`、`compat_watchlist:465`。会把 AMV 缺失日当成真实 0，压低 MA10/EMA，在临界处翻转多空判断。
2. **权重 Δ 两种量纲**：小数 vs ×100，混用时差 100 倍。
3. **MA10 窗头三选一**，且三处都写了注释为各自辩护：
   - `amv_regime.py:52` 给 None
   - `compat_misc:666` 明写「别改成不足 n 点给 None」
   - `compat_kline:558` 窗内有 None 即无值

## 五、顺带否掉的疑点

选股筛选**无重复**：`selection/backend.py` 是空占位（62 行，零路由），`compat_misc:444 build_selection` 明写零计算透传，筛选只在 `selection/frontend.js:560` 一处。

## 六、未核实项

落库的 `share` 是否严格等于 `amv/daily_total`——生产脚本在 `engine/`，本次未读。若不等，#2 的分歧比表中所写更大。

## 七、建议的修复顺序（01 补）

按「影响交易判断的程度 × 改动风险」排：

1. **#3 `None→0.0`**（`compat_watchlist:465`）：最危险。缺失日当 0 会直接翻转多空。改为「缺失跳过」，不置零。
2. **#2 权重 Δ 量纲**：差 100 倍是硬错误。先确认落库 `share` 口径，再统一到一处。
3. **#1 MA10 六种写法**：收敛到 `amv_regime.sma` 一种，其余改调用。`compat_sector:212` 的 None→0.0 同属此类。
4. **#4 880 快照解析**：抽到 `core`，两处共用。
5. **#6 逐字复制的 `_is_custom_board`**：进 lib，删副本。
6. **#7 同函数内复制**：算一次复用，顺手改。
7. **#5 amv_pct 基准日**：需要先定义 period 语义，属设计问题，最后做。
