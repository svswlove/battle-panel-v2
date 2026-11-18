"""核心路径常量。

所有路径都从本文件位置推导，允许环境变量覆盖（测试用）：
  PANEL_V2_WS        工作区根（默认 OH-WorkSpace）
  PANEL_V2_DATA_DIR  数据目录（默认 <WS>/data）
  PANEL_V2_PLUGINS   插件目录（默认 app/plugins）
  PANEL_V2_OUT_DIR   插件输出目录（默认 <DATA>/panel_v2）
  PANEL_V2_PAPER_DIR 纸面交易账本目录（默认 <panel-v2>/data/panel_v2/paper）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# panel-v2/app/core/paths.py -> panel-v2
PANEL_V2_DIR: Path = Path(__file__).resolve().parents[2]
APP_DIR: Path = PANEL_V2_DIR / "app"
WEB_DIR: Path = APP_DIR / "web"
DOCS_DIR: Path = PANEL_V2_DIR / "docs"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if raw:
        return Path(raw).expanduser().resolve()
    return default


# 工作区根：默认从项目位置推导（panel-v2 -> projects -> OH-WorkSpace），
# 搬家/换机器用 PANEL_V2_WS 或 PANEL_V2_DATA_DIR 覆盖，不写死盘符。
_default_ws = PANEL_V2_DIR.parents[1] if len(PANEL_V2_DIR.parents) >= 2 else PANEL_V2_DIR.parent
WS: Path = _env_path("PANEL_V2_WS", _default_ws)
DATA_DIR: Path = _env_path("PANEL_V2_DATA_DIR", WS / "data")
# 夏天知识库/工具目录（复盘、概念、温度等只读源；2026-10-23 review_chart 迁移新增）
SUMMER_DIR: Path = WS / "夏天-activity"
SUMMER_TOOLS_DIR: Path = SUMMER_DIR / "工具"
PLUGINS_DIR: Path = _env_path("PANEL_V2_PLUGINS", APP_DIR / "plugins")

# 插件唯一可写输出目录：默认放在项目内部，保证打包时自带数据；
# 需要写到外部时用 PANEL_V2_OUT_DIR 覆盖。
OUT_DIR: Path = _env_path("PANEL_V2_OUT_DIR", PANEL_V2_DIR / "data" / "panel_v2")

# 纸面交易账本目录（4.1.2 新增，见 docs/paper_trading_architecture.md §3.1）。
#
# - 默认落在项目内 `data/panel_v2/paper/`，**与旧 engine/state、data/panel 完全隔离**：
#   旧 8800 的 cond-push 线程还在写 engine/state/paper_account.json，双写同一文件必炸。
# - 测试/临时演练用 PANEL_V2_PAPER_DIR 覆盖到临时目录（app/core/paper/state.py
#   在**每次调用**时重读该环境变量，import 之后设置也生效）。
# - 本目录**不登记**进 WRITABLE_PANEL：纸面账本不是生产 panel JSON，旧 data/panel 一字节都不写。
PAPER_STATE_DIR: Path = _env_path("PANEL_V2_PAPER_DIR", OUT_DIR / "paper")

# v1 允许插件只读的库白名单（库名 -> 绝对路径），见 docs/CONTRACT.md §4
READABLE_DB: dict[str, Path] = {
    "amv_board.db": DATA_DIR / "amv_board.db",
    "ths_board.db": DATA_DIR / "ths_board.db",
    # 2026-10-24 S2：板块/概念指数日线（旧 8800 `/api/kline/*.TI` 的唯一数据源）。
    # 生产者 `夏天-activity/工具/ths_index_fetch.py`（盘后 sync/gap_fill），表 index_daily/index_meta。
    # 只读：compat_kline 只 SELECT，周线不走本库（原生 1w 源，不可用则如实标 unavailable）。
    "ths_index.db": DATA_DIR / "ths_index.db",
    "track_pool.db": DATA_DIR / "track_pool.db",
    "buy_signal.db": DATA_DIR / "buy_signal.db",
    "amv_minute.db": DATA_DIR / "amv_minute.db",
}

# 只读 panel JSON 白名单：由生产脚本写入，插件只能读，不能写。
PANEL_DIR: Path = DATA_DIR / "panel"
READABLE_PANEL: dict[str, Path] = {
    "amv_live": PANEL_DIR / "amv_live.json",
    "amv_regime_latest": PANEL_DIR / "amv_regime_latest.json",
    "position_cap_latest": PANEL_DIR / "position_cap_latest.json",
    "amv_breadth_latest": PANEL_DIR / "amv_breadth_latest.json",
    "battle_plan": PANEL_DIR / "battle_plan.json",
    "real_positions": PANEL_DIR / "real_positions.json",
    "alert_todo": PANEL_DIR / "alert_todo.json",
    "watchlist": PANEL_DIR / "watchlist.json",
    "watchlist_quotes": PANEL_DIR / "watchlist_quotes.json",
    "focus_levels": PANEL_DIR / "focus_levels.json",
    "all_a_latest": PANEL_DIR / "all_a_latest.json",
    # 2026-10-23 compat 兼容层（第 1 批）：旧 8800 `/api/{name}.json` 白名单原样透传所需的
    # 四个生产文件。只登记**磁盘上已存在、已有生产脚本在写**的文件，不新造产物。
    # 旧端来源逐一对齐 engine/server.py::WHITELIST（dashboard/positions/intraday）
    # 与 ALERT_PLAN_FILE（alert_plan_persistent）。
    "dashboard": PANEL_DIR / "dashboard.json",
    # 2026-10-23 compat 兼容层（第 3 批）：板块域两个大字典端点。
    # 旧 8800 `/api/sector_track.json` 读 `amv_live_sector_track.json`（10.5MB，tick_feed 盘中写），
    # `/api/etf_sector_map.json` 读 `etf_sector_map.json`（163KB，日更）。
    # 两者都是**已有生产文件**，只是之前没登记。
    "amv_live_sector_track": PANEL_DIR / "amv_live_sector_track.json",
    "etf_sector_map": PANEL_DIR / "etf_sector_map.json",
    # 2026-11-11 选股页 ETF 模式（主人：主板买不了，实际只能买 ETF）：
    #   ETF 日度快照 1,535 只 × 20 字段（含分类/涨幅/量比/距均线/尾盘占比）。
    #   生产者 = 盘后链 `etf_snapshot` 步（engine/collectors/etf_snapshot.py，每日覆盖 _latest）。
    #   **只读**：v2 不跑取数脚本，也不写这份文件。
    "etf_snapshot_latest": PANEL_DIR / "etf_snapshot_latest.json",
    "positions": PANEL_DIR / "positions.json",
    "intraday": PANEL_DIR / "intraday.json",
    "alert_plan_persistent": PANEL_DIR / "alert_plan_persistent.json",
    # 2026-10-23 compat 兼容层（第 4 批，任务书 docs/task_selection_4_3.md §1）：选股页三份
    # 生产文件。都是**盘后链/旧 8800 已在写**的既有产物，只登记、不新造：
    #   selection_latest 8.2MB 盘后快照（/api/selection 主体，生产脚本 selection_snapshot_builder）
    #   selection_pool   1.8KB 选股池（/api/selection_pool + /api/selection 的 pool 段）
    #   selection_filter 561B  筛选偏好（/api/selection_prefs）
    # 三者的写入口仍只在旧 8800（`WRITABLE_PANEL` 不登记它们），panel-v2 只读透传。
    "selection_latest": PANEL_DIR / "selection_latest.json",
    # 2026-11-04 盘中选股刷新：主人尾盘选股用（手动触发，非盘后链）。
    #   生产者 = engine/collectors/selection_live_refresh.py（面板“刷新”按钮→core run-script 拉起）；
    #   只读。有效性判定在 compat_misc.build_selection（当日、且盘后快照还没拿到今天）。
    "selection_live": PANEL_DIR / "selection_live.json",
    "selection_pool": PANEL_DIR / "selection_pool.json",
    "selection_filter": PANEL_DIR / "selection_filter.json",
    # 2026-10-24 周末冲刺（N2 ops 插件）：后台监控快照。旧 8800 `/api/ops.json` 由
    # `engine/ops/ops_status.build()` 现场聚合后**落盘**，产物文件名是 `ops_status.json`
    # （ops_status.py::OUT_FILE）；白名单名字按任务书取短名 "ops"。v2 只读这份快照，
    # 不跑 schtasks/WMI 探测（插件禁子进程）。
    "ops": PANEL_DIR / "ops_status.json",
    # 2026-10-24 M1 补缺（A1 `/api/panic`）：恐慌广度序列库。生产者
    # `engine/collectors/panic_breadth.py`（盘后 --append / 一次性 --backfill），旧 8800
    # `_panic_store_doc()` 读的就是这份。**只读**：v2 不在渲染路径里跑追加（见 HANDOFF）。
    "panic_breadth": PANEL_DIR / "panic_breadth.json",
    # 2026-11-06 面板 v2 资金维度：880008 全A 主力资金历史序列（L2 口径，**覆盖写、不累积**，约 100KB）。
    # 生产者 `夏天-activity/工具/build_index_flow_series.py`（读 .cah 缓存 → 四档 0.3/0.7 加权）；
    # 用途：给资金维度算「占成交」的 250 日历史分位。序列可直接重建（.cah 是源，本文件是缓存），
    # 因此必须先于面板查询刷新，否则序列陈旧；插件只在 ".cah 直连不可用"时退回实时接口。
    # **只读**：v2 不跑生成脚本、不写这个文件。
    "index_flow_series_880008": PANEL_DIR / "index_flow_series_880008.json",
}

# 其他生产 JSON 白名单（不在 data/panel，但同样只读、同样需要 manifest.data.json 声明）。
# 只登记“已有生产脚本在写”的文件，不新造产物。
READABLE_JSON: dict[str, Path] = {
    # 代码→名称（eltdx 全量表，主链 stock_names 步日更；2026-10-31 从 _tmp 迁到 data/——
    # 原先放在临时区，被清理/误删就断供。板块详情 / 打分引擎 / 拼音索引共用的唯一源）。
    "stock_names": WS / "data" / "stock_names.json",
    # 拼音索引（2026-10-28）：tools/build_pinyin_index.py 生成，股票+板块名称 → 全拼/首字母。
    # 只读；运行时**不依赖 pypinyin**（构建期生成，见 tools/build_pinyin_index.py）。
    "pinyin_index": WS / "engine" / "state" / "pinyin_index.json",
    # HCL 高切低（行业口径）：夏天-activity/工具/hcl_calc.py 落盘，旧 8002 /api/hcl 同源
    "hcl_latest": WS / "OH-Works" / "临时文件" / "hcl_latest.json",
    # 市场温度 J0（2026-10-24 N3）：旧 8800 `/api/temperature_latest.json` 唯一源
    # `engine/server.py:93 TEMPERATURE_FILE`（夏天-activity/工具/temperature_data/，不在 data/panel）。
    "temperature_latest": SUMMER_TOOLS_DIR / "temperature_data" / "temperature_latest.json",
    # K 线指标设置（2026-10-24 M1 B1）：旧 8800 `GET/PUT /api/kline-indicator-settings`
    # 的落盘目标（`engine/server.py:715 KLINE_SETTINGS_FILE`，投资科学同款文件的 v2 副本）。
    # 读走本白名单，写走 WRITABLE_MISC（同一路径，见下），读写各一条声明。
    "kline_indicator_settings": SUMMER_TOOLS_DIR / "xiaoshi_cache" / "kline_indicator_settings.json",
    # 自选池富化磁盘缓存（2026-11-07）：日更库类结果（个股/板块多空、板块归属、背离、资金、徽标）。
    # 不是生产产物，可按需重算；登记只为让插件走 ctx 白名单读写，不直连文件系统。
    # 失效口径：amv_board.db / ths_board.db / buy_signal.db 的 mtime+size 变化即整份作废。
    "watchlist_enrich_cache": SUMMER_TOOLS_DIR / "xiaoshi_cache" / "watchlist_enrich_cache.json",
}


def db_path(name: str) -> Path | None:
    """白名单内的库名 -> 绝对路径；不在白名单返回 None。"""
    return READABLE_DB.get(name)


# 只读**文本目录**白名单（2026-10-23 新增，任务书 docs/task_concept_4_2.md §2）。
#
# 背景：旧 8800 `/api/concept.json` 的数据源不是 JSON，而是 `夏天-activity/概念追踪/`
# 的 **Markdown 知识库**（`概念与催化总表.md` + `{slug}/概况.md`）。这类目录既不进
# `READABLE_DB`（不是库），也不进 `READABLE_JSON`（不是单文件），需要一个**目录级**白名单。
#
# 边界：
#   • 只读：核心只给 `PluginContext.text_dir(name)` 返回 Path，不提供任何写方法；
#   • 只登记已有生产内容在维护的目录，不新造产物（同 READABLE_JSON 口径）；
#   • 插件仍须在 `manifest.data.text` 里显式声明名字，两边对不上就拒绝加载。
READABLE_TEXT_DIR: dict[str, Path] = {
    # 概念追踪知识库（夏天维护）：总表 + 每个概念的概况/阶段 MD
    "concept_tracking": WS / "夏天-activity" / "概念追踪",
    # 每日复盘 MD（4.5 新增，任务书 docs/task_review_md_4_5.md §2）：
    # 旧 8800 `/api/review.md` 的唯一事实源，文件名严格 `日复盘 YYYY-MM-DD.md`。
    # 复盘流程只落 MD，面板按需渲染，本目录只读、插件不写任何文件。
    "daily_review": WS / "夏天-activity" / "每日复盘",
}


def text_dir(name: str) -> Path | None:
    """白名单内的只读文本目录名 -> 绝对路径；不在白名单返回 None。"""
    return READABLE_TEXT_DIR.get(name)


def panel_path(name: str) -> Path | None:
    """白名单内的 panel JSON 名 -> 绝对路径；不在白名单返回 None。"""
    return READABLE_PANEL.get(name)


def json_path(name: str) -> Path | None:
    """白名单内的其他生产 JSON 名 -> 绝对路径；不在白名单返回 None。"""
    return READABLE_JSON.get(name)


# 可写白名单（2026-10-23 新增）：插件能**修改**的生产文件。
#
# 背景：迁移旧后端全部端点，自选池增删改/签收/急停/下单这些写操作必须能落盘。
# 旧的「插件零写入」不可能保留，所以改成**受控写**：
#   • 只允许写这里登记的文件；
#   • 写前备份到 `data/panel_v2/backups/`；
#   • 原子替换（tmp + os.replace），不会写出半个文件；
#   • 每次写记一条审计日志（谁/何时/哪个文件/多大）。
# 初始只放 watchlist（自选池）—— 其余写操作（交易链）逐个评估后再加。
WRITABLE_PANEL: dict[str, Path] = {
    "watchlist": PANEL_DIR / "watchlist.json",
    # 2026-10-23（4.7）：选股页筛选设置/选股池写入口（旧 8800 同名端点迁移）
    "selection_filter": PANEL_DIR / "selection_filter.json",
    "selection_pool": PANEL_DIR / "selection_pool.json",
    # 2026-10-24 周末冲刺（主人裁定：写操作全部原生插件化，不转发旧 8800）：
    # 旧系统的签收/条目启停/择机单/预警/手动仓位都是 battle_plan.json 的受控写，
    # 执行器 5s 重读该文件生效（文件即消息总线）。插件直接写它 = 旧执行链照常工作，
    # 旧 server.py 退出写路径。备份+原子+审计由 ctx.panel_write 统一提供。
    "battle_plan": PANEL_DIR / "battle_plan.json",
    # 预警持久层（旧 /api/alert_plan/{toggle,delete} 的落盘目标 alert_plan_persistent.json）
    "alert_plan": PANEL_DIR / "alert_plan_persistent.json",
}

# 引擎状态文件白名单（2026-10-24 新增）：旧系统的 engine_state.json / kill_switch.json
# 在 engine/state/（不在 data/panel）。急停/签收同步/交易模式切换都写这两个文件。
# 语义同 WRITABLE_PANEL：白名单 + 备份 + 原子替换 + 审计，经 ctx.state_write() 访问
# （见 contract.py）。旧端消费方：执行器 5s 重读、旧壳 /api/status 轮询——文件格式不变。
STATE_DIR: Path = WS / "engine" / "state"
WRITABLE_STATE: dict[str, Path] = {
    "engine_state": STATE_DIR / "engine_state.json",
    "kill_switch": STATE_DIR / "kill_switch.json",
    # UI 备注（旧 /api/ui_notes/* 的落盘目标；在 state 目录、历史归属运维域，随本批一并开写）
    "ui_notes": STATE_DIR / "ui_notes.json",
    # 决策点唤醒总开关（2026-11-16 补登）：`wake_eval` 每轮读它决定要不要调 `wake_agent`。
    # 为什么进白名单：开关的**唯一写入口是面板**（预警卡里的总开关）——属于“插件写 engine/state”
    # 那一类，就必须走「manifest 声明 + 本白名单 + ctx.state_write」三道，不能因为模块自带了锁/原子/审计
    # 就绕过通道（否则下一个插件一句“alert 也这么写”就把契约掏空）。
    # 读侧：`common/wake_switch.py` 只读（口径与降级缺省唯一在此），`wake_eval` 每轮一次；
    # 写侧：只有 `ctx.state_write("wake_switch", doc)`，备份/原子替换/核心审计由 core 统一做。
    "wake_switch": STATE_DIR / "wake_switch.json",
}


# 引擎缓存类小文件可写白名单（2026-10-24 M1 新增）：**只读 JSON 之外的写通道**。
#
# 背景：旧 8800 有一类端点写的是「工具缓存/界面设置」这类旁路小文件
# （`夏天-activity/工具/xiaoshi_cache/kline_indicator_settings.json`），
# 既不是 data/panel 生产 JSON（WRITABLE_PANEL），也不是 engine/state 引擎状态（WRITABLE_STATE）。
# 硬塞进那两个白名单会把语义搅乱（一个生产、一个引擎状态），所以单开一张。
#
# 语义与 WRITABLE_PANEL/WRITABLE_STATE 完全一致：白名单 + 写前备份 + 原子替换 + 审计，
# 经 `ctx.misc_write()` 访问（contract.py）。消费方只有前端（跨会话保留设置），
# 引擎/执行器都不读这些文件 ⇒ 写坏了不影响交易链。
WRITABLE_MISC: dict[str, Path] = {
    # 旧 server.py:715 同路径，读写同一文件；GET 走 READABLE_JSON，PUT 走本名单
    "kline_indicator_settings": SUMMER_TOOLS_DIR / "xiaoshi_cache" / "kline_indicator_settings.json",
    # 自选池富化缓存（2026-11-07）：同 READABLE_JSON 同名条目；写坏了只会多算一次，不影响交易链。
    "watchlist_enrich_cache": SUMMER_TOOLS_DIR / "xiaoshi_cache" / "watchlist_enrich_cache.json",
}


def misc_writable(name: str) -> Path | None:
    """缓存类小文件可写白名单内的名字 -> 绝对路径；不在白名单返回 None。"""
    return WRITABLE_MISC.get(name)


def panel_writable(name: str) -> Path | None:
    """可写白名单内的名字 -> 绝对路径；不在白名单返回 None。"""
    return WRITABLE_PANEL.get(name)


def state_writable(name: str) -> Path | None:
    """引擎状态文件可写白名单内的名字 -> 绝对路径；不在白名单返回 None。"""
    return WRITABLE_STATE.get(name)


# 口径库白名单（v1.2）：纯函数库，只算口径不碰数据源。
# 登记条件见 docs/CONTRACT.md §8.1：import 期无副作用 + 插件在 manifest.data.lib 声明。
#
# **同时登记允许导出的符号**：口径库往往同时带数据加载函数（如 `market_regime_payload()`
# 内部自己 `sqlite3.connect`）。若把整个模块交给插件，插件就能绕过 `READABLE_DB` 白名单直连库。
# 因此 `ctx.lib()` 只返回这份白名单里的函数/常量，其余一律不可见。
_TOOLS = WS / "夏天-activity" / "工具"


@dataclass(frozen=True)
class LibSpec:
    """一个口径库：文件路径 + 允许导出的符号（其余不向插件暴露）。"""

    path: Path
    exports: tuple[str, ...]


READABLE_LIB: dict[str, LibSpec] = {
    # AMV 五档分解（涨跌/流通股/活跃度/新股/停牌 + 大盘基准），逐股恒等于 ΔAMV。
    # 旧 8002 `/api/contrib` 与 engine/server.py 共用此模块，禁第二份实现。
    # `board_parts(conn, ...)` 收调用方传进来的连接——插件用自己的 ctx.db_read，不绕过白名单。
    "amv_contrib": LibSpec(
        path=_TOOLS / "amv_contrib.py",
        exports=("parts", "board_parts", "PART_KEYS", "PART_NAMES"),
    ),
    # AMV 双轨多空状态机（MA10 + pct±4%/−2%；共振当天/单轨第二个）。
    # 板块 `bull`（旧 8002 `_dual_bull`）与个股四态徽标（旧 `_stock_bull`）共用此模块。
    # 注意：**不导出** `_load_total` / `_load_sector` / `market_regime_payload`——
    # 它们内部自己 sqlite3.connect，导出就等于开了绕过白名单的后门。
    "amv_regime": LibSpec(
        path=_TOOLS / "amv_regime.py",
        exports=("compute_regime", "compute_regime_single", "stock_state",
                 "stock_state_series", "latest_state", "sma"),
    ),
    # 板块结构标签（2026-10-27 新增）：基于 amv_contrib 五档分解，判「量价齐升 / 纯换手虚火 /
    # 结构偏弱」等形状，供 8810 板块列表的徽标与筛选。
    # **纯函数**：收已算好的 parts dict 与截面分位，不连库、不读文件（取数在调用方 backend.py）。
    # 本模块只做判定，不做任何收益预测——配套研究已表明单项效应量不足以覆盖交易成本。
    "amv_struct_tag": LibSpec(
        path=_TOOLS / "amv_struct_tag.py",
        exports=("classify", "display", "compute_row", "ALL_TAGS", "FILTER_GROUPS"),
    ),
    # 个股评分与板块内名次轨迹（2026-10-23 从 dashboard/server.py 抽出）。
    # 旧 8002 的 `stock_score_map` / `sector_rank_trail` 已改为调本模块，口径单源。
    # 纯函数：收「已取好的数据」（amv_by_date / total_by_date），不连库、不读文件。
    # 取数留在调用方（旧 8002 用自己的 conn()，插件用 ctx.db_read()）。
    "panel_scores": LibSpec(
        path=_TOOLS / "panel_scores.py",
        exports=("SCORE_W", "MIN_SAMPLES", "score_map", "score_trail", "rank_trail"),
    ),
    # 黄白线（2026-10-23 新增）：白线=EMA(EMA(C,10),10)，黄线=(MA14+MA28+MA57+MA114)/4。
    # 旧 8800 `/api/kline` 的 `_kline_payload_out()` 调本模块，`engine/common/indicators.py`
    # 是**口径唯一源**（文件头写明“新消费端一律 import 本模块，禁止自建第二份”）。
    # 纯函数、零 IO、零第三方依赖；黄线在 i<113 时输出 None（B1 降级口径，不编代理值）。
    "indicators": LibSpec(
        path=WS / "engine" / "common" / "indicators.py",
        exports=("ema", "ma_series", "yellow_white_lines",
                 "YELLOW_PERIODS", "WHITE_PERIOD"),
    ),
    # 「主力动向」L2 副图（2026-10-24 S1 补登记）：口径唯一源 = 01 自己的 L2 工具。
    # 旧 8800 `/api/kline` 的 `mf` 段走 `server.py::_stock_mf()` → 本模块 `build_mf()`，同源。
    #
    # **与其它口径库的差别**：本模块是「数据客户端 + 口径」一体的（`fetch_cah_live()` 内部
    # 自己连 7709 取 `.cah`，`default_free_float()` 走 eltdx）。任务书已裁定允许导出取数函数
    # ——它是 L2 数据源客户端，不是口径重写；且 import 期零副作用（AST 扫描通过：顶层只有
    # 常量/函数定义，连接全在函数体内懒建）。
    #
    # 导出面**只给装配入口 + 常量**：
    #   `build_mf` 是唯一需要的装配函数（内部串起取数/解析/口径，全部参数可注入）；
    #   LOOKBACK_* 供插件校验回顾周期边界（与旧端 MF_LOOKBACK_MIN/MAX 同值）。
    # 不导出 `load_cah` / `fetch_cah_live` / `default_free_float` 这类底层取数原语：插件拿不到
    # 独立取数句柄，只能整段装配，避免出现「插件自己拼一半口径」的第二条路径。
    "l2_mf": LibSpec(
        path=WS / "Hubagent" / "01" / "_tools" / "l2_mf.py",
        exports=("build_mf", "DEFAULT_LOOKBACK", "LOOKBACK_MIN", "LOOKBACK_MAX"),
    ),
    # AMV 多空判定状态树（2026-10-30 由 8002 迁入 8810）：口径唯一源 =
    # `夏天-activity/工具/amv_state_tree.py`。旧 8002 `/api/state_tree` 与本插件同源同码。
    #
    # **只导出纯函数与常量，不导出 `build_payload` / `load_daily` / `load_bars`**：
    # 那三个是「自带 sqlite 连接 + 自带 eltdx 客户端」的取数装配入口，交出去等于给插件
    # 一条绕过 READABLE_DB / ctx.quotes 的后门（CONTRACT §8.1 同一条理由）。
    # 取数一律由插件走 `ctx.db_read` / `ctx.quotes` / `ctx.amv_live`，本库只做判定。
    # import 期零副作用：顶层只有常量、`import amv_regime`、和一次 sys.path 守卫（AST 扫描通过）。
    "amv_state_tree": LibSpec(
        path=_TOOLS / "amv_state_tree.py",
        exports=(
            "evaluate", "pattern_verdict", "trigger_flags", "macd_dead_cross",
            "clean_minute_points",
            "S_MISS", "S_LIVE", "S_CLOSE", "S_VETO", "S_VOID", "S_NA", "S_HOLD",
            "R_IDLE", "R_PENDING", "R_NO_LIGHT", "R_LIT", "R_VOID",
            "DEADLINE_T", "SESSION_MINUTES",
        ),
    ),
}


def _vendored_lib(name: str) -> Path:
    """随包口径库副本（`app/lib/<name>.py`）。公开部署时它是唯一可用的那份。"""
    return APP_DIR / "lib" / (name + ".py")


def lib_spec(name: str) -> LibSpec | None:
    """白名单内的口径库名 -> 规格；不在白名单返回 None。

    2026-11-15 公开化：外部正本（工作区 `夏天-activity/工具`、`engine/`）**优先**，
    不存在时回退到随包的 `app/lib/<name>.py`。生产里正本总在，行为不变；
    独立部署（只有本仓库）时靠副本自包含。两边同源，VENDOR.json 记哈希。
    """
    spec = READABLE_LIB.get(name)
    if spec is None:
        return None
    if spec.path.is_file():
        return spec
    local = _vendored_lib(name)
    if local.is_file():
        return LibSpec(path=local, exports=spec.exports)
    return spec          # 都没有：原样返回，调用方据此报 FileNotFoundError（带真路径）


def lib_path(name: str) -> Path | None:
    """白名单内的口径库名 -> 模块文件路径；不在白名单返回 None。"""
    spec = lib_spec(name)
    return spec.path if spec else None


def plugin_out_dir(plugin_id: str) -> Path:
    """插件输出目录 data/panel_v2/<id>/（按需创建）。"""
    p = OUT_DIR / plugin_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def plugin_cache_path(plugin_id: str, name: str) -> Path:
    """插件落盘缓存路径 `data/panel_v2/_cache/<plugin>/<name>.json`（按需建目录）。

    2026-11-07 新增（盘后结果落盘）：与 `plugin_out_dir` 分开成 `_cache/` 子树，
    清缓存不影响业务产物；缓存可重算，**不做备份/审计**（与 panel_write/misc_write 不同）。
    边界：名字只允许字母数字与 `._-`，防路径穿越。
    """
    pid, nm = str(plugin_id or ""), str(name or "")
    pat = r"[A-Za-z0-9_.-]{1,64}"
    if (not re.fullmatch(pat, pid) or not re.fullmatch(pat, nm)
            or not pid.strip(".") or not nm.strip(".")):
        raise ValueError("plugin_id / cache name 只允许字母数字与 ._-（≤64，不能是纯点）")
    p = OUT_DIR / "_cache" / pid
    p.mkdir(parents=True, exist_ok=True)
    return p / (nm + ".json")


def plugin_output_file(plugin_id: str, name: str) -> Path:
    """读另一个插件输出目录里的文件路径（**不建目录、不创建文件**）。

    2026-11-07：跨插件只读通道。一个指标一个 owner——产物归生产插件，
    消费插件经 core 读，不重算、不直连别人的目录。
    名字白名单同 `plugin_cache_path`；文件不存在由调用方处理。
    """
    pid, nm = str(plugin_id or ""), str(name or "")
    pat = r"[A-Za-z0-9_.-]{1,64}"
    if (not re.fullmatch(pat, pid) or not re.fullmatch(pat, nm)
            or not pid.strip(".") or not nm.strip(".")):
        raise ValueError("plugin_id / output name 只允许字母数字与 ._-（≤64，不能是纯点）")
    return OUT_DIR / pid / (nm + ".json")


def ensure_out_dir() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR
