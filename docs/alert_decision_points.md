# 预警卡「决策点」分区 + 唤醒总开关（2026-11-16）

> 归属：`app/plugins/alert/`（预警卡内一个分区，**不新开页面、不加 NAV**）。主人 2026-11-16 20:47 拍板。

## 1. 它解决什么

决策点链路原来完全无 UI：盘前 `build_wake_plan.py` 渲染当日计划 → `wake_eval.py`（交易日 09:00–15:30 常驻，60s/轮）
评估 → 命中写 `wake_events.jsonl` 并调 `wake_agent` 新建会话叫醒夏天。面板上看不到任何一环。
本分区把三件事摊开：**今天该守什么**、**今天真命中了什么**、**评估器还活着没有**，外加一枚**唤醒总开关**。

## 2. 数据源（三读一写）

| 用途 | 文件 | 谁在写 | 读法 |
|---|---|---|---|
| 当日计划 | `data/panel/wake_plan_{YYYYMMDD}.json` | `engine/tools/build_wake_plan.py`（盘前 09:24） | 只读 |
| 命中流水 | `engine/state/wake_events.jsonl` | `engine/executor/wake_eval.py`（触发一条追加一行） | 只读 |
| 评估器心跳 | `engine/state/wake_eval_status.json` | `wake_eval.write_heartbeat`（60s 一跳，出窗口落 `EXITED`） | 只读 |
| 唤醒总开关 | `engine/state/wake_switch.json` | **本卡写入**（唯一写操作，走 `ctx.state_write`） | 读写 |
| 开关审计 | `engine/state/wake_switch_audit.jsonl` | 同上（追加型） | 只读（排障用） |

装配实现在 `app/plugins/alert/decisions.py`（纯只读、无任何写动作）；
开关的**读取/降级口径/文档装配/审计追加**唯一实现是 `engine/common/wake_switch.py`（不含状态写）。

## 3. 口径（硬约束，测试守门）

1. **只展示，不判定。** 命中次数 / 最后触发时间 / `trigger_values` 全部取 `wake_events.jsonl` 原值；
   面板**不读** `intraday.json`、**不重算** condition。`condition_text` 只是把 `logic + terms` 字面翻成人话
   （`指数价(000001.SH) < 3600`），不做任何现值比较。
2. **计划条目不标实时状态。** 不出现「已满足 / 未满足」；今日是否触发只看流水。
3. **空态明确，不拿旧数据充今天。**
   | 情况 | 面板显示 | 字段 |
   |---|---|---|
   | 今天的计划文件不存在 | 今日计划未生成（＋「最近一份 XXXX-XX-XX」只作提示） | `plan.present=false, reason=not_generated` |
   | 文件在、`meta.trade_date` 不是今天 | 计划文件日期不符（打印文件里的日期） | `reason=trade_date_mismatch` |
   | 文件在、JSON 解析失败 | 计划文件无法解析 | `reason=unreadable` |
   | 今天没有命中 | 今日无命中（正常态，不是数据缺失） | `hits.count=0` |
   | 心跳文件缺失/坏 | 评估器状态未知 | `evaluator.state=UNKNOWN` |
4. **跨日对齐**：计划看 `meta.trade_date`、事件看 `trade_date`，两边都必须等于**今天**；
   文件名对、内容不对的，直接拒收。
5. **纯只读**（分区本身）：不写任何文件、不提供增删改；`decisions.py` 由测试静态扫描守（无 `open(w)` /
   `json.dump` / `os.replace` 等）。

## 4. 唤醒总开关（唯一写操作）

语义（主人定死，改口径先问）：

| 开关 | 评估器 | 事件落盘 | 叫醒夏天（`wake_agent`） | alert 的 QQ 推送 |
|---|---|---|---|---|
| 开 | 照常 | 照常 | 会 | 不受影响 |
| 关 | 照常 | 照常 | **不会** | 不受影响 |

关掉的是**唤醒通道**，不是评估器，也不是 notify 推送（两条独立链路）——证据链不能断。

**缺省与降级**：

- 文件不存在 → 按「开」（首次上线不用手工铺文件）；
- 文件存在但读不出 / `enabled` 非布尔 → 也按「开」，同时 `ok=false`，面板挂**警示横幅**写「状态异常，按「开」处理」。
  理由：误唤醒比漏唤醒安全；但绝不能让人以为关了、其实没关。
- 生效时延：`wake_eval` 每轮 `tick` 开头读一次开关 → **60s 内生效**，不用重启进程。

**落盘与审计**：

- `wake_switch.json`：`{enabled, updated_at, by}` 三个字段，独立文件，不塞进任何既有文件；
- **写走面板受控写通道**（2026-11-16 收口）：`manifest.data.state: ["wake_switch"]` +
  `paths.WRITABLE_STATE["wake_switch"]` + `ctx.state_write("wake_switch", doc)`，
  写前备份、原子替换（tmp + `os.replace`）、核心审计日志（`data/panel_v2/backups/wake_switch/audit.log`）
  全由 core 统一做；插件不自己 `open(w)`。
- `wake_switch_audit.jsonl`：**追加型**，每次切换一行
  `{ts, enabled, by, source, prev_enabled, prev_ok, prev_reason}`（切换前状态一并留痕，坏文件被修好时也看得出来）。
  它只是留痕，不是状态；由 `common/wake_switch.append_audit()` 追加。
- 为什么两套审计并存：核心审计记录“谁在何时改了受控文件”，切换流水记录“开关历史与切换前状态”；
  前者是契约，后者是运维证据，互不替代。

**面板显示**：关闭时是整行红框横幅（不是安静的小 toggle）：`唤醒总开关已关闭 —— 现在不会叫醒夏天` +
「关闭于 / 操作者」+ 「恢复叫醒」按钮；开启时只有一行小字。关闭前弹一次确认（防手滑），开启不拦。

## 5. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/p/alert/alert/decision_points[?date=YYYYMMDD]` | 决策点分区数据（计划 + 命中 + 心跳 + 开关）。`date` 只给排障用，前端不传 |
| POST | `/api/p/alert/alert/wake_switch` | body `{enabled: bool, by?: str}` → 写开关并回读；非布尔直接拒，不落盘 |

## 6. 已知边界

1. **盘中未命中 → 流水为空是正常态**，不是数据缺失；面板文案已写明「评估器跑满 60s 一轮也没触发」。
2. **过 0 点后**（自然日切换）会显示「今日计划未生成」，直到当天 09:24 渲染出新计划——这是真实的，不拿前一天顶。
3. **非交易日**同理：没有当日计划文件 → 未生成。不把周五的计划当成周六的。
4. `wake_events.jsonl` 是**追加写**，读侧可能读到半行；解析失败的行跳过并计入 `hits.bad_lines`（不报错、不补值）。
5. 心跳 `stale` 判定 = `RUNNING` 且心跳距今 > **180s**（3 轮）。这是**心跳新鲜度**染色，不参与任何条件判定。
6. 「最近一份可用计划」只在今天缺计划时探测（回溯 10 天，覆盖长假），明确标「仅提示，不是今天的数据」。
7. 计划条件阈值**按原值展示**：`zhaban_rate > 0.4` 就显示 0.4，不换算成 40%。
8. 落表里有、今日计划里没有的 dp（计划白天重渲 / 长期预警另跑）会单列一行，不改口径、不隐藏。

## 7. 测试

```powershell
cd projects\panel-v2
python -m pytest app/tests/test_alert_decision_points.py -q     # 22 项：分区装配/空态/开关/降级/受控写通道/写边界
python -m pytest app/tests/test_state_write.py -q                # 5 项：state_write 双白名单 + 备份 + 核心审计
python -m pytest app/tests/test_contract.py app/tests/test_alert_page.py -q
python -m pytest app -q                                         # 全量
```

```powershell
cd <WS>
python -m pytest engine/tests/test_wake_switch.py -q            # 8 项：缺省/降级/读写/审计追加
python -m pytest engine/tests/test_wake_eval.py -q              # 28 项：含开关关→不唤醒但事件照落
```
