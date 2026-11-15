# HANDOFF · M1 补 8 个缺口端点（P0×4 / P1×2 / P2×2 → 实际交付 7 个 + 1 取消）

> 执行：01 ｜ 2026-10-24 ｜ 任务书 `docs/task_M1_fix.md`（已按主人裁定改：A5 取消）
> 上游：`docs/audit_20261024_sprint.md`（嗅嗅对抗审查）
> 状态：**全部落地并实测**；三条自测命令退出码 0；4 个 GET 端点活体对齐 PASS；不 commit / 不重启 8810。

---

## 0. 一句话结论

旧前端在 8810 上报 404 的 8 个端点里，**7 个已原生补齐**（A5 physical_ai 按主人裁定取消），
其中 4 个 GET 与 8800 **同参逐字节一致**，3 个触发类走 core 白名单通道 + 全程 mock 测试。
`battle_plan_rw/backend.py:70` 自认的写域缺口（模块开关批量）已补上，并加了「与旧仓枚举逐项比对」的漂移锁。

---

## 1. 端点清单与归属（最终落点，含一处偏离任务书的裁定）

| # | 端点 | 级别 | 落点 | 旧 handler（只读参考） | 状态 |
|---|---|---|---|---|---|
| A1 | `GET /api/panic` | P0 | `ops/backend.py` + `core/panic_calc.py`（新） | `server.py:1268` / 装配段 1196–1265 | ✅ 活体一致 |
| A2 | `POST /api/amv_refresh` | P0 | `ops/backend.py` + `core/ops_run.py::_amv_refresh` | `server.py:2235` | ✅ mock 全档 |
| A3 | `POST /api/sector_track_backfill` | P1 | `ops/backend.py` + `core/ops_run.py::_sector_backfill` | `server.py:2292` | ✅ mock 全档 |
| A4 | `POST /api/sector/restart` | P0 | `ops/backend.py` + `core/ops_run.py::_sector_restart` | `server.py:2172` | ✅ mock 全档 |
| A5 | `GET /api/physical_ai.json` | P2 | — | `server.py:873` | ⛔ **主人裁定取消**（旧端新功能，后续再迁） |
| B1 | `GET/PUT /api/kline-indicator-settings` | P1 | `ops/backend.py` + `core/contract.py::misc_write`（新） | `server.py:719/732` | ✅ 活体一致 |
| B2 | `GET /api/kline/{code}/xdxr` | P1 | `ops/backend.py` + `core/quotes.py::xdxr`（新） | `server.py:672` | ✅ 活体一致 |
| C1 | `POST /api/module_switches_batch` | P0 | `battle_plan_rw/backend.py` | `server.py:7400` | ✅ 行为测试 14 条 |

### 1.1 偏离任务书 §2 的一处（B 组落点）

任务书写 B1/B2 落在 `app/plugins/kline/`（0.7.0 → 0.8.0）。**实际落在 `ops` 插件里**，原因：

- 两个端点都是**根路径**（`/api/kline-indicator-settings`、`/api/kline/{code}/xdxr`），
  而 `kline` 插件是 `mount=plugin`（`/api/p/kline/*`），旧前端根本不走那个前缀；
- core 现机制是「一插件一路由表、挂一个前缀」（`main.py:322-345`），
  **不支持同一插件挂两前缀**。N1 批已就此裁过一次（`HANDOFF_20261024_N1_bpwrite.md §2`：
  「同插件挂两前缀会让边界变模糊」→ 建 `battle_plan_rw` 承载根路径）；
- 改 `main.py` 挂载区不在本单白名单（任务书 §3 禁止）。

所以按 N1 的同一先例：**HTTP 壳放已在 root 上的 `ops`**，语义全在 core
（`quotes.xdxr()` / `misc_write()`），`ops` 不持有任何 K 线业务逻辑。
`kline` 插件本次**零改动**（保留 0.7.0），`ops` 从 0.2.0 → 0.3.0。
若后续允许改 `main.py`，把这三条路由搬进 `kline` 即可（§6 遗留 1）。

---

## 2. 逐端点实现说明

### A1 `GET /api/panic`

**装配在 core**（`app/core/panic_calc.py`，新文件），三条理由与 `ops_run.py` 同构：

1. 算法唯一源在旧仓 `engine/collectors/panic_breadth.py` → `importlib` 动态加载，
   只调 `raw_from_snapshot` / `score_row` / `state_of` / `WINDOW`，**没抄一行公式**。
   单测 `test_panic_calc_uses_legacy_module` 静态扫描 `panic_calc.py`，禁止出现
   `less + 0.5 * equal`（分位本体）与 `100.0 - (q_limit_down`（恐慌广度本体）。
2. 快照要走 core 行情门面：旧 `fetch_880_snapshot()` 自建 `TdxClient`；v2 改
   `ctx.quotes.call("quotes.get_snapshots", ["sh880005", ...])`，字段映射逐条照搬
   （`last_price/open_price/high_price/pre_close_price` → `last/open/high/pre_close`）。
3. 六阶段标签读 `data/track_pool.db::sentiment`（只读 + 60s 缓存），放 core 免得插件为一个旁注字段
   声明整张库白名单。

**缓存三件套照旧端口径**：`PANIC_SNAP_TTL_S=2.8` / `PANIC_STAGE_TTL_S=60.0` /
序列库按 mtime；并发取快照时短等 1.5s（`SNAP_LOCK_WAIT_S`）。

**与旧端的差异（1 处，已实测定性）**：
- **不跑「每日追加自愈」**。旧 `api_panic` 收盘后会起后台单飞线程跑
  `panic_breadth.append_day()` 往生产库补当日行（`server.py:1057-1106`）。
  v2 端点**纯只读**：append 要扫 kline 全表 + 写 `data/panel` 生产文件，属盘后链职责。
  影响：若某天盘后链没跑，8810 的序列会停在上一交易日，而 8800 会自己补。
  **取证依据**：`engine/state/logs/panic_append.log` 最近两条是
  `21:17 done appended` / `21:20 done upgraded` —— 旧端的自愈确实在工作，
  v2 不做这一步就是会旧一天，已在 §6 列为遗留 2（建议把追加挂进盘后链，而不是渲染路径）。

### A2 `POST /api/amv_refresh`

`core/ops_run.py::_amv_refresh`：动态加载 `engine/executor/amv_live.py` 调
`run_once(publish=True)`（**同进程函数调用**，不是 schtasks、不是子进程）。
锁非阻塞（连点 429）+ 20s join（到点 504，**不杀已在跑的那次**，与旧端 `t.is_alive()` 同）。
返回体逐键照搬旧端：`{ok, asof, all, coverage, stale, indices}`；
`run_once` 返回 None（非交易/行情陈旧/闸门未过）→ `ok:false` + reason + 现有值，**HTTP 200**。

### A3 `POST /api/sector_track_backfill`

旧端是「子进程 `--check-only` 同步 + `Popen --apply` 异步」。v2 调**同一份工具的 Python 函数**
`backfill_sector_track.run(check_only=True / apply=True)`：

- check-only → 同步，无缺口回 `{status:"ok", gaps:[]}`；
- 有缺口 → 后台线程跑 `run(apply=True)`，工具自己写
  `engine/state/sector_track_backfill_status.json`，前端轮询
  `/api/sector_track_backfill_status`（`compat_sector` 提供）**路径不变**；
- `_backfill_status_running()` 照旧端（30 分钟超时保护，进程异常死亡视为不忙）。

**实测踩到并修掉的坑**：旧仓工具在 `replay()` 里**函数体内延迟 import**
`from executor import amv_live`（`backfill_sector_track.py:230`），而 `amv_live` 又
`import amv_store` + `numpy`。第一次跑报 `ModuleNotFoundError: No module named 'executor'`。
修法：`ops_run._ensure_legacy_path()` 把四个旧仓 import 根（`engine/executor`、`engine`、
`夏天-activity/工具`、`envs/qmt312/Lib/site-packages`）**幂等常驻**加进 `sys.path`
（不能像口径库那样临时插-恢复：延迟 import 发生在模块加载之后）。
旧端本来就带这三个目录（`server.py` import 期插 engine，子进程 `cwd=WORKSPACE`），本函数只是把同环境补回。

### A4 `POST /api/sector/restart`

`_sector_restart()` 逐条照旧端：① 枚举 python 进程，**只杀命令行含 `dashboard\server.py`** 的
（精确串，不误伤 `engine\server.py`，也不碰 8810/uvicorn）②`run_daemons --only amv_dashboard`。
锁非阻塞（429）。单测 `test_restart_kills_only_dashboard_server` 用三条假命令行
（dashboard / engine\server.py / uvicorn 8810）验证只有第一条被杀，且拉起命令**不含 8810**。

### B1 `GET/PUT /api/kline-indicator-settings`

新增写通道 **`WRITABLE_MISC` + `ctx.misc_write()`**（照 `state_write` 模式：双白名单 + 备份 +
tmp+replace 原子 + 审计日志 `backups/<name>/audit.log`）。
与旧端的差别只有一条：旧端 `json.dump(body, f, ensure_ascii=False)` **无缩进**，
`misc_write` 刻意保持无缩进（这个文件是前端 settings 快照，缩进会放大体积、且旧消费方按原样读）；
`panel_write`/`state_write` 用 `indent=2`，两者不通用。

GET 语义照旧端：无文件/坏 JSON → `{}`（不 404，前端 `mergeSettings` 当无设置）。
PUT：整分 dict 覆盖写，未知顶层键保留（前端是「GET 整分 → 只改 `kline_lc` → PUT 整分」，
嗅嗅 2026-10-06 就因此要求「GET 失败则跳过 PUT」）。

### B2 `GET /api/kline/{code}/xdxr`

core 新增 `quotes.xdxr(ts_code)`，数据源从旧端 pytdx `get_xdxr_info` 换成 eltdx
`corporate.capital_changes`（同一份通达信协议记录）。

**最容易搬错的一层，已用 7 只票校准**：pytdx 的 `fenhong/songzhuangu/peigu/peigujia`
与 eltdx 的 `c1..c4` **顺序不对应**——
`fenhong=c1`、`songzhuangu=c3`、`peigu=c4`、`peigujia=c2`。
按序号抄会得到「分红 6.0 元、送股 1.0 股」这种看着像对的错值。
2026-10-24 实测 **000001.SZ / 600519.SH / 300750.SZ / 688981.SH / 601318.SH / 002594.SZ / 000858.SZ
七只票 0 差异**；`test_xdxr.py` 用假 block 把映射钉死。

**市场判定逐字照搬旧端**（`_tdx_market`，`server.py:636`）：只看数字前缀、**不看后缀**。
所以 `000001.SH`（上证指数）被判成深市 → 实际拿 `sz000001`（平安银行）的记录。
这不是笔误而是旧端既有口径；8810 要与 8800 **同参对齐**就必须一致（实测两边 81 条 events 全等）。
口径修正应两边一起改（§6 遗留 3）。

**缓存**：旧端落盘 `夏天-activity/工具/xiaoshi_cache/xdxr/{code}_{YYYYMMDD}.json`；
v2 不写该目录（插件写通道受控），改**进程内内存缓存**，按 (标的, 自然日) 失效。
差异：重启进程后第一次请求要重取（旧端当日缓存还在磁盘上）。响应形状不变，前端无感。

### C1 `POST /api/module_switches_batch`

`battle_plan_rw/backend.py::module_switches_batch`，逐条照 `server.py:7400`：

1. `switches` 键名 ∉ 九键 → 422（同文案）；
2. `manual_position.pct` 存在且 ∈[0,100]（bool/非数字/越界 → 422）；
   amount = pct% × `position_cap`（缺失/负 → 回退 `dashboard.total_asset`，`0` 是合法值不回退）；
3. `notify_types` 每项 ∈ 9 枚举（非法 → 422），去重保序；
4. **空提交不写盘**（直接回现状，不刷 `updated_at`、不产生备份）；
5. 一次写盘（`ctx.panel_write` = 备份+原子+审计），补全九键，响应键与旧端逐键一致
   （`ok/module_switches/manual_position/notify_types`，**没有** `changed`）。

**这份单子补上了 `backend.py:70` 自认的写域缺口**，并把该行注释改成「写域已覆盖模块开关」。

**防漂移锁**：`NOTIFY_TYPE_KEYS` 是内置快照（插件不能 import 旧仓、core 无 py 常量通道，
同 `compat` 先例），但 `test_notify_type_keys_match_legacy` 直接 AST 读旧仓
`common/push_queue.py` 抽元组逐项比对，`MODULE_KEYS` 同理读 `server.py`——旧仓改枚举时测试直接红。

---

## 3. 改动文件清单

**白名单内（任务书 §3 允许）**

| 文件 | 改动 |
|---|---|
| `app/core/paths.py` | `READABLE_PANEL` +`panic_breadth`；`READABLE_JSON` +`kline_indicator_settings`；新增 `WRITABLE_MISC` + `misc_writable()` |
| `app/core/contract.py` | `Manifest.misc_write` 字段；`data.misc` 校验；`ctx.misc_write()`（照 state_write）；`ctx.ops_action()`（照 ops_run） |
| `app/core/ops_run.py` | 新增固定动作通道：`OPS_ACTIONS` / `_amv_refresh` / `_sector_backfill` / `_sector_restart` / `_ensure_legacy_path` / `run_action()` |
| `app/core/panic_calc.py` | **新建**：panic 装配（动态加载旧仓算法 + 快照走门面 + 阶段标签 + 三处缓存） |
| `app/core/quotes.py` | 新增 `xdxr()` / `_tdx_market()` / `_xdxr_event()` / `XDXR_CATEGORY` + 内存缓存 |
| `app/plugins/ops/backend.py` | +4 触发/只读端点 +2 K 线周边端点；`_run_action` HTTP 分档 |
| `app/plugins/ops/plugin.json` | 0.2.0 → 0.3.0；`panel`+panic_breadth、`json`+kline_indicator_settings、`misc`、`quotes:true`、`timeout_ms` 5000→30000 |
| `app/plugins/battle_plan_rw/backend.py` | +`module_switches_batch` + `NOTIFY_TYPE_KEYS` + `ModuleSwitchesBatchBody` |
| `app/plugins/battle_plan_rw/plugin.json` | 0.1.0 → 0.2.0 |
| `docs/HANDOFF_20261024_M1_fix.md` | 本文件 |

**测试（白名单「tests」项）**

| 文件 | 内容 |
|---|---|
| `app/plugins/ops/tests/test_ops_m1_endpoints.py` | 新建 34 条：panic 装配/缓存 TTL/session、amv 四档、backfill 四态、restart、B1 读写与白名单、B2 形状与降级 |
| `app/plugins/ops/tests/test_ops_actions.py` | 新建 32 条：core 动作通道（未知名、锁、超时、只杀 dashboard、sys.path 四个根、进程枚举降级） |
| `app/plugins/battle_plan_rw/tests/test_bp_batch_switches.py` | 新建 14 条：C1 全语义 + 枚举漂移锁 |
| `app/core/tests/test_xdxr.py` | 新建 12 条：字段映射（含 c1..c4 顺序）、市场判定照搬、缓存、分类表漂移锁 |
| `app/plugins/ops/tests/test_ops_plugin.py` | 改 4 条旧断言（版本/路由集/subprocess 边界改为 AST 调用级判定） |
| `app/plugins/battle_plan_rw/tests/test_battle_plan_rw_contract.py` | 改 1 条（路由集 +module_switches_batch） |

**辅助工具（白名单外，需 01 复核）**

| 文件 | 用途 |
|---|---|
| `tools/check_m1_alignment.py` | **新建**：8811(新) vs 8800(旧) 四个 GET 端点同参比对，退出码 0/1/2 |

---

## 4. 测试数字与自测命令

```
python -m pytest app/plugins/ops/tests app/plugins/kline/tests \
                  app/plugins/battle_plan_rw/tests app/core/tests -q
→ 250 passed, 17 warnings   exit=0

python -m pytest -q
→ 1063 passed, 4 skipped, 113 warnings   exit=0

python tools\cutover_gate.py
→ PASS 40.2s  8810 health=OK 插件健康=OK shadow双跑=OK AMV比对=OK
               battle_plan写域接管=OK 插件无写代码=OK kline边界声明=OK pytest全套=OK
   exit=0
```

基线对照：动手前同三条命令分别是 164 / 1025 / PASS，新增 **86 条测试**，零失败。

**触发类一条都没真跑**（任务书 §3 硬约束）：
`test_ops_actions.py` 全程 `monkeypatch` 掉 `_load_module`（假模块）与 `subprocess.run`
（假替身，不杀进程、不拉 8002、不跑 schtasks），`_list_python_procs` 换假列表。
`test_ops_m1_endpoints.py` 的三条触发端点额外在插件层再 mock 一次 `core_ops_run.run_action`。

---

## 5. 活体对齐（8811 临时实例 vs 8800，全程只读）

> 8811 = 同一份新代码另起端口（**没动 8810**，也没重启它）；跑完已关闭。

```
python tools\check_m1_alignment.py
→ PASS
| GET /api/panic                          | SAME | HTTP 200/200 value=90.6 state=过热 mode=closed series=120 |
| GET /api/kline/000001.SH/xdxr           | SAME | HTTP 200/200 events=81 |
| GET /api/kline/600519.SH/xdxr           | SAME | HTTP 200/200 events=45 |
| GET /api/kline-indicator-settings       | SAME | HTTP 200/200 keys=3 |
```

除 4 个规定端点，额外抽查了 4 组边界，**响应体逐字节相同**：

| 请求 | 8811 | 8800 | 体一致 |
|---|---|---|---|
| `/api/kline/notacode/xdxr` | 200 | 200 | ✅ |
| `/api/kline/920001.BJ/xdxr` | 200 | 200 | ✅（两边都空表） |
| `/api/kline/399001.SZ/xdxr` | 200 | 200 | ✅（两边都空表） |
| `/api/kline/688981.SH/xdxr` | 200 | 200 | ✅（173 条） |

`panic` 比对时只剥离 `asof`（请求时刻，必然不同），其余 `trade_date/mode/source/value/state/items/series/stage` 全等。

**非法代码的处理因此改过一版**：初版返回 400，实测 8800 对 `notacode` 回 **200 + 空 events**，
已改回 200（`test_xdxr_bad_code_matches_legacy_200` 锁住）。

---

## 6. 遗留与需 01 裁定的事

1. **B 组落点偏离（最需要拍板）**。B1/B2 现在挂在 `ops` 插件而不是 `kline`（理由见 §1.1）。
   若允许改 `app/core/main.py` 挂载区支持「一插件两前缀」，可把三条路由搬回 `kline`，
   `ops` 只留 A1–A4。搬迁成本很低（三个 handler 独立、无状态），但需要动 core + manifest 契约 +
   冲突检测，本单白名单不允许。

2. **panic 的每日追加自愈 v2 不做**（§A1）。现状：旧 `panic_append.log` 证明 8800 侧确实在自愈；
   8810 纯只读。若旧 8800 某天退役，追加得有人跑 —— 建议挂进 `postclose_chain`
   （`python collectors/panic_breadth.py --append`），而不是塞回渲染端点。

3. **`_tdx_market` 只看数字前缀是旧端既有口径**（`000001.SH` 拿到平安银行记录）。
   两边当前一致，但语义本身可疑。修要两边一起修，否则对齐立刻破。建议开一张小工单给旧仓 + 本模块同步改。

4. **xdxr 缓存从磁盘改内存**（§B2）：进程重启后首次请求要重取一次（旧端当日缓存仍在磁盘）。
   若要保住磁盘缓存，得给 `xiaoshi_cache/xdxr/` 开一个写白名单 —— 本单没做，因为「缓存目录」进写白名单
   会把写域边界撑开（每次请求都可能落一个新文件），收益不抵风险。

5. **`ops` 插件 `timeout_ms` 由 5000 → 30000**。`sector_restart` 内部 90s 上限、`amv_refresh` 20s、
   `backfill` check-only 60s，原 5000ms 会把插件层先掐死（返回 504「plugin timeout」而不是旧端语义）。
   注意：`plugin_guard` 只拦 `/api/p/` 前缀，root 挂载的插件**不过那层**，这个值主要防以后有人改挂载。

6. **`tools/check_m1_alignment.py` 在白名单外**。它是只读比对脚本（不写任何文件），
   但任务书 §3 没列 `tools/`。已如实登记，需要的话 01 决定留/删。
   每次运行要另起 8811 实例（脚本只比对、不启动），日常门禁没接它。

7. **不 commit、不重启 8810**（任务书 §5）。当前 8810 跑的是旧代码（PID 71048，14:34 启动），
   新代码要 01 统一重启才生效；重启后建议再跑一次 `tools\check_m1_alignment.py`
   （把脚本里的 `NEW` 改成 8810 即可，或加环境变量）。

---

## 7. 一次纪律事故（如实记录）

**15:13 我在 8811 真实实例上触发了 `POST /api/sector_track_backfill`**，
本应在 8811 上只验 4 个 GET（任务书要求触发类全部 mock）。

后果与处置：
- 它跑的是 `run(check_only=True)` → 检查阶段即抛 `ModuleNotFoundError: No module named 'executor'`，
  **没有进入下载/重放**；
- 唯一写入是 `engine/state/sector_track_backfill_status.json`（`status:error`，160 字节，
  文件原本不存在，git 里 `engine/state/*` 是 ignore 的）；
- **已删除该文件**（`Test-Path` 确认 False）；
- `data/panel/amv_live_sector_track.json`（10735270 B）与 `data/panel/amv_live.json` 的
  `LastWriteTime` 仍是 `2026/10/23 15:00:57` / `12:24:29` —— **未被改写**；
- 这次误触反而暴露了 §A3 那个真实 bug（`sys.path` 缺 `engine/executor`），已修并加了
  `test_ensure_legacy_path_adds_four_roots` 与延迟 import 链的实测验证。

后续动作约束：8811 上只跑 GET；触发类只走 mock 测试。
