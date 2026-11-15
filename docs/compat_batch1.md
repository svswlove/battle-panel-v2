# compat 兼容层 · 第 1 批交付报告

- 任务书：`docs/task_compat_batch1.md`（派单 01 → DSHana）
- 实施日期：2026-10-23
- 插件：`app/plugins/compat/`（新插件，`id=compat`，`backend.mount="root"` → 端点就在 **`/api/...`**）
- **状态：14/15 端点已实现；1 个（concept）因数据源不在只读白名单未做。运行期验证未跑（见 §5）**

---

## 0. 一句话结论

15 个端点里 **14 个已落地**（8 个纯透传 + 6 个组装），响应结构与旧 8800 逐字段对齐（依据是
`engine/server.py` 源码，不是猜测）。**1 个未做**：`/api/concept.json`——它的数据源是 Markdown
知识库，不在任何只读白名单，按契约插件读不到，故返回明确 503 而非伪造结构。

**两个必须先知道的现场变化**：
- 本仓库在我开工期间被**并发改动**：`app/core/contract.py` / `main.py` 新增了
  `backend.mount="root"`（挂到 `/api` 而非 `/api/p/<id>`），另有 `compat_sector`（板块域 9 端点）
  与 `compat_kline`（行情域 8 端点）两个同批插件已落地。我按新机制配置了 `mount="root"`，
  **因此任务书 §4 的「注意 8810 插件路径前缀是 `/api/p/<插件id>/`」已过时——本插件端点在 `/api/...` 根路径**。
  三方路径已逐个比对，**零重叠**（§6 表）。
- **本会话没有 shell/exec 工具**，无法 `curl` / `pytest` / 重启 8810 / 跑 `cutover_gate.py`。
  任务书 §5 的四项自测（HTTP 200、逐字段比对、pytest ≥196、gate 8 项 PASS）全部「未验证」，
  需由 01 或下一轮有执行环境时补齐。（详见 §5）

---

## 1. 与任务书假设不符的 4 处（先看这个）

任务书有些描述基于「旧端大概是怎样」，实读 `engine/server.py` 后与事实有偏差。**我按源码实现，
不按任务书的猜测实现**，逐条列明：

### 1.1 `/api/dashboard.json` 不需要组装，直读即是

任务书 §2.3 说「不是单文件，要组装」，并要求先 curl 下来当契约再组装。

实际：`engine/server.py:71` 的 `WHITELIST` **明确包含 `"dashboard"`**，走
`@app.get("/api/{name}.json") → _serve_doc("dashboard") → _read_json(data/panel/dashboard.json)`。
即旧端就是把 `data/panel/dashboard.json` 整个吐出去（任务书 §2.3 实测 5.1KB；本机文件 241 行，
含 `{meta,engine,account,positions,orders_today,environment,alerts,risk_locks,executor,watchdog}`）。

**实现：原样透传。** 重新组装是负收益——同一份文件的字段顺序/取整/时间戳全部会漂移，
反而破坏「逐字段一致」这个验收口径。已确认 `data/panel/dashboard.json` 存在
（`meta.generated_at = 2026-10-23 17:41:07`）。
注意任务书列的 8 个段少了 `executor` / `watchdog` 两个，旧端实际有，透传后自然一致。

### 1.2 `/api/push_types.json` 是常量端点，不是文件端点

任务书 §2.1 把它列进「直接透传文件内容」，数据源写 `data/panel/push_types.json`。

实际：**磁盘上没有这个文件**（已确认 `data/panel/` 下 125 个文件里无 push_types）。旧端 `api_push_types()`
（`engine/server.py:2862`）是从三份**代码常量**现场拼的：
`NOTIFY_TYPE_KEYS`(9) + `PUSH_TYPE_LABELS`(11) + `EVENT_WHITELIST`(12) + 一段 `channel` 文案。

**实现：内置快照**（`backend.py::_PUSH_TYPE_KEYS/_PUSH_TYPE_LABELS/_PUSH_EVENT_WHITELIST/_PUSH_CHANNEL`），
逐字抄自旧仓。**这是本批唯一有漂移风险的端点**：旧仓改枚举/改中文标签时这里不会自动跟随。
→ 建议第 2 批把 `push_queue.py` 的这三个常量登记成 `READABLE_LIB` 的导出符号（纯常量、无副作用），
彻底消除副本。

### 1.3 `/api/alerts` 不是 `alert_todo.json` 的 alerts 段

任务书 §2.2 写「`data/panel/alert_todo.json` 的 alerts 段」。

实际：`engine/server.py:3596 api_alerts()` 调 `build_alerts_payload()`，是**内存态三源合一评估**，
返回 `{version,updated_at,alerts[],worst,near_triggers,pnl,source_health,holdings}`；
`alert_todo.json` 的 alerts 是另一个东西（盘中防线告警快照），跟这个端点无关。

**实现：按旧端 `build_alerts_payload` 逐字段复刻**（三源 = 持仓触线 / 买点信号触发 / 数据源连续降级）。
`alert_todo.json` 那段由 `/api/p/compat/alert_todo.json` 透传，是另一个端点。

### 1.4 `/api/search/all` 是 POST，不是 GET `?q=`

任务书 §2.2 写 `GET /api/search/all?q=`。

实际：`engine/server.py:5128` 是 `@app.post("/api/search/all")`，用 JSON body
（源码注释：中文经 URL query 会乱码）。旧前端 `index.html:2569` 也是
`fetch("/api/search/all", {method:"POST", body: JSON.stringify({q, limit})})`。

**实现：两个方法都注册**——`GET ?q=&limit=` 与 `POST {q,limit}`，共用同一个 `build_search()`，
响应结构完全一致。这样任务书的验收写法（GET）和旧前端实际写法（POST）都能过。

---

## 2. 逐端点：格式 / 实现 / 比对

响应结构全部以 `engine/server.py` 为准（只读参考，**未改旧仓一行**）。

### 2.1 静态 JSON 转发（9 个中 8 个已做）

| # | 端点 | 旧端实现（源码行） | 本插件实现 | 结构对齐 |
|---|---|---|---|---|
| 1 | `/api/dashboard.json` | `_serve_doc("dashboard")`（6837/71） | `ctx.panel_json("dashboard")` 透传 | ✅ 同文件即同结构 |
| 2 | `/api/battle_plan.json` | `_serve_doc("battle_plan")` | `panel_json("battle_plan")` 透传 | ✅ |
| 3 | `/api/amv_live.json` | 同上 | `panel_json("amv_live")` | ✅ |
| 4 | `/api/amv_regime_latest.json` | 同上 | `panel_json("amv_regime_latest")` | ✅ |
| 5 | `/api/position_cap_latest.json` | 同上 | `panel_json("position_cap_latest")` | ✅ |
| 6 | `/api/alert_todo.json` | 同上 | `panel_json("alert_todo")` | ✅ |
| 7 | `/api/alert_plan.json` | `_alert_plan_doc()`（167） | `build_alert_plan()`：缺失→200+空骨架 | ✅ 含「非 404」口径 |
| 8 | `/api/push_types.json` | `api_push_types()`（2862） | 常量快照 `build_push_types()` | ⚠️ 结构对齐，值靠人工同步（§1.2） |
| 9 | `/api/concept.json` | `api_concept()`（1766） | **503 + 明确原因** | ❌ 未做（见 §4） |

透传端点**逐字原样**返回，不重排键、不补字段、不删字段；文件缺失 → `404 + {"detail": "..."}`
（与旧端 `HTTPException(404)` 形状一致）。
`alert_plan` 是唯一例外：旧端**故意不 404**，缺文件回 `{"meta":{"persistent":true,"generated_at":"","source":"server"},"plans":[]}`
（§4.6 契约，让前端渲染「无长期预警」），本插件照做，并有单测钉住。

### 2.2 组装型只读（6 个）

#### `GET /api/positions.json`
旧端同样走 `/api/{name}.json` 白名单（`WHITELIST` 含 `"positions"`）→ **透传即一致**。
任务书列的 `{meta,date,updated_at,holdings,selection,track_signals,source_audit}` 全部在文件里，
不存在「需要组装」的部分。

#### `GET /api/gates`
复刻 `build_gates_payload()`（3069）。返回
`{version:"v1-20260914", updated_at, gate1{name:"情绪门",state,reason,data_asof,data_stale}, gate2{...,items[],data_basis,trade_mode,holdings_source,holdings_stale,labels_available}, gate3{...,reason,pool_size,signals[]}}`。

- 情绪门：`intraday.json::market_breadth.limit_up/limit_down` 绝对阈值（涨停<30 或 跌停>25=RED；
  ≥70 且 ≤15=GREEN；其余 YELLOW；缺数 UNKNOWN）。
- 持仓门：持仓按 trade_mode 定权威源 + `positions.json holdings[].exit_watch` 关联触发线；
  武装 STOP_LOSS 最低线为判据（现价≤线 RED；距线 ≤1% YELLOW）。
- 选股门：`buy_signal.db::stock_signal` 当日产物（当日命中→YELLOW；有产物无命中→GREEN；
  未产出→UNKNOWN + reason），**不用停写的 pool_track 兜底**。

**已知差异（1 处）**：`gate2.trade_mode` 恒为 `"live"`。原因见 §3.1。

#### `GET /api/status`
复刻 `api_status()`（2894）。返回
`{code,label,state,mode,battle_plan_signed,kill_switch_active,kill_reason,trade_mode,updated_at}`
（任务书列的 8 个键全有，另有旧端也有的 `updated_at`）。

- `code/label`：急停 FROZEN/已急停 → 已签收 RUNNING/运行中 → 否则 READ_ONLY/只读未签收。
- 签收唯一源 = `battle_plan.json::sign.status`（旧端口径）。
- 急停态取 `dashboard.json::engine.kill_switch`（旧端读 `engine/state/kill_switch.json`，
  而 dashboard 是它的**同源即时快照**——`_refresh_dashboard()` 在签收/急停后立即重建）。

**已知差异（1 处）**：`trade_mode` 恒为 `null`。原因见 §3.1。

#### `GET /api/real_positions`
复刻 `api_real_positions()`（4373）。原样透出 `real_positions.json` + 追加
`positions_list[]`（15 个字段：`ts_code,name,qty,available_qty,cost_price,last_price,price_source,quote_asof,pnl_amount,pnl_pct,day_pnl_amount,day_pnl_pct,market_value,weight_pct,opened_at`），
`positions` 非空时再给 `market_value_total`。

- 现价档位与旧端同序：① `watchlist_quotes.json` ② `ctx.quotes`(eltdx) 补未命中。
- 缺实时价 → 回退成本价并标 `price_source="cost_fallback"`，当日盈亏置 `null`（**不编造**）。
- `positions` 为空 → **不多给** `market_value_total` 键（旧端同条件，单测钉住）。
- 文件未生成 → 404（前端渲染「实盘待连接」，不误报空仓）。

#### `GET /api/alerts`
复刻 `build_alerts_payload()`（3510）。三源合一，字段见 §1.3。
告警条目键集 `{level,source,tab,ts_code,name,message}` 与旧端逐字一致。

#### `GET /api/search/all`（GET + POST）
复刻 `api_search_all()`（5128）的返回结构：`{q, stocks:[{code,name,kind}], sectors:[{code,name,level,kind}]}`。
股票源 = `ctx.json_read("stock_names")`（东财全量表，`{date, names:{6位码:名称}}`），
`kind` 三分类（板块/ETF/股票）判据与旧端同式。

**已知差异（1 处）**：旧端 `sectors` 还支持 pypinyin 拼音首字母/全拼匹配；本批**不引入新依赖**
（任务书 §3 禁），只做代码+名称匹配。字段结构不变，差别是**召回面变窄**。
股票侧同样只有代码+名称匹配，无拼音。

---

## 3. 三个必然与旧端不同的值（都不是 bug，是取数边界）

### 3.1 `trade_mode` → `null` / `"live"`

旧端读 `engine/state/engine_state.json::trade_mode`。该文件在 **`engine/state/`**，
而 `paths.READABLE_DB` / `READABLE_PANEL` / `READABLE_JSON` **都不覆盖 state 目录**，
任务书 §3 只允许往 `READABLE_PANEL` 加「已有生产 panel 文件」。

- `/status`：字段保留为 `null`（**不猜 paper/live**，符合「缺就 null」铁律）。
- `/gates` `/alerts`：按旧端「非法值/缺失 → live」的保守默认走 `live`
  （这是旧端自己的缺省语义，不是我们编的），所以持仓权威源 = `real_positions.json`。

**影响**：若当前真实 `trade_mode=paper`，`/gates` 的持仓门与 `/alerts` 的 pnl 会比旧端少
纸面持仓那几只。要修必须在 core 加 `engine/state/*.json` 只读条目（core owner 权限），
这是第 2 批的事。

### 3.2 `/gates` `gate2.holdings_source` 等派生字段

因 3.1，`holdings_source` 恒 `"real_positions.json"`，`holdings_stale` 来自
`positions.json` 的 `_label_stale`（空 holdings → `state:"empty"`，不当假陈旧）。

### 3.3 `/search/all` 的后缀推断

旧端名称表键是**完整 ts_code**（后缀来自 TDX 官方名表）；`stock_names` 只有 6 位码，
后缀只能**按号段推断**：`920xxx→BJ`、`880/881/884/885→SH`、`4/8→BJ`、`5/6/9→SH`、`0/1/3→SZ`，
推断不出 → 不出现在结果里（宁可少召回，不下发错后缀）。**未验证**推断表对全量 5 万条的覆盖率。

---

## 4. 未做的端点：`/api/concept.json`

**唯一没实现的端点。返回 `503 + {"detail": "..."}`，不伪造结构。**

原因（事实，非推断）：
1. 旧端 `api_concept()`（1766）的数据源是 `夏天-activity/概念追踪/概念与催化总表.md`
   —— 一个 **Markdown 知识库**，由 `engine/common/concept_source.py` 解析成
   `{ok,updated,active[],watch[],chain[],archived[],counts{},sector_map{},unresolved_targets[],sector_unresolved[]}`。
2. 磁盘上**没有** `data/panel/concept.json`（已枚举 `data/panel/` 125 个文件确认）。
3. 该 md 既不在 `paths.READABLE_PANEL` 也不在 `paths.READABLE_JSON`。
   任务书 §3 只允许往 `READABLE_PANEL` 加 panel 文件，**没给加 md 的授权**；
   而项目 `AGENTS.md` §2 明令「插件只能经 `ctx.*` 取数」——插件自己 `open()` 读 md 就是违规。

**为什么不硬做**：结构极其复杂（active 有 10 字段含 targets_detail/sectors_list，
watch 有 9 字段含 deltas，还有 sector_map 反查 ths_board.db），
照猜实现必然键名/嵌套对不上，违反「必须逐字段一致」的验收口径。
**宁可明确报未实现，不交一个看着像其实不对的端点。**

**建议解法（第 2 批）**：core 加 `READABLE_JSON["concept_master"] = SUMMER_DIR/概念追踪/概念与催化总表.md`
（只读、已存在、有生产者），或把 `concept_source.py` 的纯解析函数登记成 `READABLE_LIB`
（`load_index`/`load_detail` 内部自己读 md，需像 `amv_contrib` 那样先做 AST 副作用审查）。
两者都需 core owner 批准。

---

## 5. 自测：全部「未验证」——本会话无执行环境

**本会话只提供文件读写与检索工具，没有 shell / exec。** 因此任务书 §5 的四项一条都没实测：

| 任务书 §5 要求 | 状态 | 说明 |
|---|---|---|
| 1. `python -m pytest -q` ≥196 passed | **未验证** | 新增 45 个用例函数（`app/plugins/compat/tests/`，parametrize 展开约 70 条），按算术应远超 196，但**没真跑** |
| 2. 每个新端点 curl 8810 → HTTP 200 | **未验证** | 无法 curl；且 8810 **也没有重启**（任务书 §4 的 schtasks） |
| 3. 逐字段递归比对 8810 vs 8800 | **未验证** | 无法访问两个端口；比对方法见 §5.2 |
| 4. `python tools/cutover_gate.py` → 8 项 PASS | **未验证** | 未跑 |

### 5.1 已做的**静态**核对（可复现，不是「跑过」）

- 逐端点读 `engine/server.py` 对应函数（行号已在 §2 标出），字段名/嵌套/数组元素键名逐字抄录。
- `backend.py` 全文扫描 `cutover_gate.py::_check_no_write_code` 的 40+ 个禁用 token
  （`open("w` / `.write_text(` / `.mkdir(` / `json.dump(` / `os.replace(` / `requests.post` /
  `subprocess.` / `INSERT INTO` / `UPDATE ` …）：**0 命中**。
- 全文扫描 `contract.FORBIDDEN_DB_PATTERNS`（`sqlite3.connect` 等）：**0 命中**。
- 全文扫描 `contract.FORBIDDEN_QUOTE_PATTERNS`（`from eltdx` / `TdxClient` / `xtquant`）：**0 命中**。
- manifest 每个 `data.panel` / `data.json` / `data.read` 名字都已在 `paths.py` 白名单里且文件存在。
- 单测里也把这几条钉住了（`test_backend_has_no_forbidden_io` 等 9 个用例），
  跑起来会替 `cutover_gate` 提前挡住。

### 5.2 逐字段比对怎么做（给执行者）

递归比对规则：**键名集合必须完全相等（含数组元素键名），数值允许不同**。

```python
# 伪代码，临时脚本，跑完即删（不要放 app/plugins/ 下——会被 cutover_gate 静态扫描误判为写代码）
def walk(a, b, path="", out=None):
    out = out if out is not None else []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a: out.append(f"{path}.{k}: 仅旧端有")
            elif k not in b: out.append(f"{path}.{k}: 仅新版有")
            else: walk(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b): out.append(f"{path}: 长度 {len(a)} vs {len(b)}（数据更新允许，标注即可）")
        for i, (x, y) in enumerate(zip(a, b)): walk(x, y, f"{path}[{i}]", out)
    elif isinstance(a, bool) != isinstance(b, bool):
        out.append(f"{path}: 类型 {type(a).__name__} vs {type(b).__name__}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        pass  # 数值差异允许
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out
```

端点映射（8810 → 8800）：
`/api/{dashboard,battle_plan,amv_live,amv_regime_latest,position_cap_latest,alert_todo,positions}.json`
→ `/api/{同名}.json`（**本插件 mount=root，前缀就是 `/api`，不是 `/api/p/compat`**）；
`/api/{alert_plan,push_types,concept}.json` → `/api/{同名}.json`；
`/api/{gates,status,real_positions,alerts}` → `/api/{同名}`；
`/api/search/all?q=<词>` → POST `/api/search/all`。

**预期会报差异的项（预先声明，不是事后找补）**：
`status.trade_mode`（null vs live/paper）、`gates.gate2.*`（holdings_source、trade_mode、
items 因纸面/实盘分流）、`alerts.pnl/holdings`（同上）、`search` 的拼音召回、
以及一切 `updated_at`/`asof`/`generated_at`/`age_seconds` 时间类字段。

---

## 6. 改动面清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `app/plugins/compat/plugin.json` | 新建 | manifest，`mount: "root"`，`data.write: []`，`quotes: true` |
| `app/plugins/compat/backend.py` | 新建 | ~1050 行，15 个路由，零写操作 |
| `app/plugins/compat/tests/test_compat_contract.py` | 新建 | 45 个用例函数（含 parametrize 展开约 70 条） |
| `app/core/paths.py` | **改（仅加 4 行条目）** | `READABLE_PANEL` 增 `dashboard` / `positions` / `intraday` / `alert_plan_persistent` |
| `docs/compat_batch1.md` | 新建 | 本报告 |

**根路径冲突检查**（`main.py` 新增的 `claimed[]` 保护会启动即报错）：仓库里已有两个同批的
root 挂载兼容插件，逐个比对后**路径零重叠**：

| 插件 | 根路径端点 | 与本插件冲突 |
|---|---|---|
| `compat_sector` | `sector_card.json` / `sector_spark.json` / `sector_intraday.json` / `sector_track.json` / `sector_track_backfill_status` / `sector/health` / `etf_sector_map.json` / `breadth_880.json` / `temperature_latest.json`（9） | ❌ 无 |
| `compat_kline` | `kline/{ts_code}` / `kline_bg/` / `intraday/` / `intraday_multi/` / `quote5/` / `volume_profile/` / `volume_ticks/` / `f10_brief/`（8） | ❌ 无 |
| **`compat`（本批）** | 见 §2（15） | — |

`app/core/paths.py` 的 4 个新增条目**逐个确认过文件存在**（`glob` 只读枚举 + 实际读取内容）：

```
data/panel/dashboard.json               241 行   meta.generated_at=2026-10-23 17:41:07
data/panel/positions.json               638 行   updated_at=2026-09-28 10:05:11（holdings 恒空）
data/panel/intraday.json                4500+ 行 generated_at=2026-10-23 15:30:00
data/panel/alert_plan_persistent.json   10 行    updated_at=2026-10-20 13:49:44
```

**未改**：`app/core/contract.py`、`app/core/main.py`、现有 5 个插件、`engine/` 旧仓任何文件、
任何 `data/panel/*`（只读）。

---

## 7. 已知债 / 下一批

1. **推送类型常量副本**（§1.2）——旧仓改枚举时不跟随。建议登记 `push_queue` 常量到 `READABLE_LIB`。
2. **trade_mode 不可读**（§3.1）——要么 core 加 `engine/state/engine_state.json` 只读条目，
   要么让 amv/paper 插件提供 `trade_mode` 端点。影响 `/status` `/gates` `/alerts` 三处。
3. **concept 未实现**（§4）——需 core 授权读概念 md。
4. **search 无拼音**（§3.3）——pypinyin 在 `engine/vendor_py`，不引入新依赖的前提下可用
   `sys.path` 借用，但那属于「插件自己找依赖」，建议下批统一在 core 侧做。
5. **`cutover_gate.py::_check_pytest` 的目录列表**里没有 `app/plugins/compat/tests`
   （也没有 `app/plugins/compat_sector/tests`），所以门禁**不会**跑本插件的测试。
   建议 01 补两行（本批不动该文件——任务书未授权改 tools/）。
6. **本批四项自测全部未跑**（§5），下一轮有执行环境时必须补齐，尤其是一直没重启的 8810。
7. **仓库并发改动**（§0）：`contract.py`/`main.py` 已新增 `backend.mount`，`compat_sector` 已落地。
   若后续还有别的 agent 同时加 root 挂载插件，注意 `main.py::claimed[]` 会因路径撞车**启动即抛错**
   （这是设计如此，不是 bug）；新增 root 插件前先 `grep @router.get` 全插件路径去重。
8. **`app/core/paths.py` 的 4 个新条目**如果与别批 agent 的改动撞车（同一文件被并发写），
   请以 `git diff` / 文件历史核对，别静默覆盖。

---

## 8. 端点 → 代码位置速查

| 端点 | 路由函数 | 组装函数 |
|---|---|---|
| `/dashboard.json` … `/positions.json` | `register()` 内逐一 | `_panel_or_404()` |
| `/alert_plan.json` | `alert_plan_json` | `build_alert_plan()` |
| `/push_types.json` | `push_types_json` | `build_push_types()` |
| `/concept.json` | `concept_json` | `build_concept()`（503） |
| `/gates` | `gates` | `build_gates()` |
| `/status` | `status` | `build_status()` |
| `/real_positions` | `real_positions` | `build_real_positions()` |
| `/alerts` | `alerts` | `build_alerts()` |
| `/search/all` (GET/POST) | `search_all` / `search_all_post` | `build_search()` |
