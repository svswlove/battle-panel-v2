# 任务书 4.1.2 · 把纸面撮合引擎迁进 panel-v2 core

> 执行者：DSHana｜主会话验收｜2026-10-23
> **你没有 shell，不要尝试运行 pytest / python / curl。只写代码 + 自检语法，交付时明确写「未运行验证」。**
> 所有路径都在 `OH-WorkSpace\projects\panel-v2`（你的 cwd）内。

---

## 0. 先读这些文件（只读，不要改）

参考源（旧引擎，已复制到 `_vendor_src/`，**只读**）：

| 文件 | 作用 |
|---|---|
| `_vendor_src/paper_broker.py` | 旧 `PaperBroker`（1406 行），撮合/账本/费用/估值 |
| `_vendor_src/manual_orders.py` | 旧 `ManualOrderService`（1247 行），手工单/条件单/冻结 |
| `_vendor_src/filelock.py` | 跨进程文件锁（msvcrt） |
| `_vendor_src/test_paper_broker.py` | 旧 broker 单测（36KB） |
| `_vendor_src/test_manual_orders.py` | 旧 order service 单测（31KB） |

项目现状（只读）：`app/core/paths.py`、`app/core/contract.py`、`docs/CONTRACT.md`、`docs/paper_trading_architecture.md`、`app/plugins/compat_misc/backend.py`。

---

## 1. 目标

把旧纸面交易引擎**搬进 panel-v2 的 core**，做成**只服务纸面、绝不接真实通道**的模块：

```
app/core/paper/__init__.py
app/core/paper/filelock.py     # vendored，纯文件锁
app/core/paper/state.py        # 账本路径/锁/原子写/备份/审计（唯一写文件的地方）
app/core/paper/broker.py       # vendored PaperBroker（paper-only）
app/core/paper/orders.py       # vendored ManualOrderService（paper-only，删实盘）
app/core/paper/service.py      # 单例装配 + wanted_codes()
app/core/paper/tests/test_broker.py
app/core/paper/tests/test_orders.py
```

并修改 `app/core/paths.py`：新增 `PAPER_STATE_DIR`（默认 `PANEL_V2_DIR/data/panel_v2/paper`，支持环境变量 `PANEL_V2_PAPER_DIR` 覆盖）。

**这一步不接 QMT、不写 HTTP 端点、不改插件。** feed 和插件在 4.1.3 做。

---

## 2. 硬约束（违反任何一条即验收不通过）

1. **零 xtquant**：`app/core/paper/` 下任何文件不得出现字符串 `xtquant` / `xttrader` / `xtdata` / `qmt_trader`。纸面通道绝不允许有真实下单路径。
2. **零旧依赖**：不得 `from common.*` / `from core.*` / `import track_data`；凡是需要的逻辑就地 vendoring 或改成注入。
3. **写文件只允许在 `state.py` 内部**：其他模块不得出现 `open(..., "w")`、`os.replace`、`os.remove`、`Path.write_text` 等写操作；必须调用 `state.py` 的 API。
4. **账本目录隔离**：所有读写都通过 `PaperPaths`（来自 `paths.PAPER_STATE_DIR`），**绝不碰 `engine/state` 或 `data/panel`**。
5. **保留旧口径**：费用、撮合、条件单 9 种类型、OCO、T+1、冻结、急停、手工优先锁，逐字移植，不"优化"。
6. **中文注释保留**：这是给后续维护者看的，不要精简掉旧文件里有价值的口径说明。
7. **不使用第三方新依赖**；只用标准库 + pytest（测试）。
8. **不改** `_vendor_src/`、`app/plugins/`、`app/core/contract.py`、`docs/`。

---

## 3. 模块接口（必须按这个签名写，后续任务依赖）

### 3.1 `app/core/paper/state.py`

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

@dataclass(frozen=True)
class PaperPaths:
    root: Path
    account: Path        # paper_account.json
    trades: Path         # paper_trades.json
    conditions: Path     # conditional_orders.json
    freeze: Path         # manual_orders_state.json
    manual_lock: Path    # manual_lock.json
    engine_state: Path   # engine_state.json
    kill_switch: Path    # kill_switch.json
    audit: Path          # audit.log
    backups: Path        # backups/

def get_paths(root: str | Path | None = None) -> PaperPaths: ...

def read_json(path: Path, default: Any = None) -> Any: ...

def atomic_write_json(path: Path, doc: Any, *, audit_event: str = "", detail: str = "") -> None:
    """tmp(pid+thread) → fsync → os.replace 重试 6 次；失败抛异常（账本不能静默丢）。"""

def backup_file(path: Path, tag: str = "write") -> Path | None: ...
def audit(event: str, detail: str = "") -> None: ...   # 追加到 paths.audit，失败静默
def locked(path: Path, timeout_s: float = 10.0): ...   # 复用 filelock.exclusive_lock

def reset_to_initial(root: str | Path | None = None, initial_cash: float = 500000.0) -> Dict[str, Any]:
    """归档当前账本到 backups/reset_<ts>/，重建空账户+空流水；返回摘要。"""
```

说明：
- `atomic_write_json` 写前调用 `backup_file`（同目录/`backups`），但**同一进程同一路径 60 秒内只备份一次**（防高频下单把磁盘打爆）；`backup_file` 返回备份路径或 None。
- `audit.log` 每行 JSON：`{"ts","event","detail"}`。

### 3.2 `app/core/paper/broker.py`

从 `_vendor_src/paper_broker.py` 移植 `PaperBroker` 及 `calc_fee` / `COMMISSION_RATE` 等常量。改造点：

```python
class PaperBroker:
    def __init__(self, state_dir: str | Path | None = None,
                 paper_trades_path: str | Path | None = None,
                 paper_account_path: str | Path | None = None,
                 initial_cash: float = 500000.0,
                 clock=None,
                 partial_ratio_override=None,
                 seq_start: int = 1,
                 close_price_provider: Callable[[list[str], str], dict[str, float]] | None = None,
                 name_resolver: Callable[[str], str] | None = None,
                 alert_sink: Callable[[str, str, str, str], None] | None = None):
```

- 默认路径 = `get_paths(state_dir)` 的 `trades` / `account`；`state_dir` 优先，显式路径其次。
- 删除：`PAPER_TRADES_DEFAULT/PAPER_ACCOUNT_DEFAULT/INTRADAY_DEFAULT` 对旧 `common.paths` 的依赖。
- 删除 `write_intraday_signal`、`_write_intraday_signal_locked`、`_load_intraday`、`_day_summary`（不再双写 `data/panel/intraday.json`）。这些方法不导出。
- `close_prices_from_db` 删除；`close_price_provider` 缺省 = `lambda codes, day: {}`。`mark_to_market` 保持原逻辑。
- `name_from_battle_plan` 删除；`name_resolver` 缺省 = `lambda code: ""`。`order()` 里原调用点改为 `self.name_resolver(code)`。
- `_alert` 改成 `self.alert_sink(level, event, title, body)`（缺省只 `audit(...)`），不再 import `common.progress` / `push_queue`。
- 保留 `paper_order_id` 自增、重复单检测、幂等键、账本锁、`reconcile_fill`、`mark_to_market`、`load_account`、`get_trade`、`load_trades`、`cancel`、`query_align`、`_bookkeeping`、`_roll_day`。
- 所有写 `paper_trades.json` / `paper_account.json` 的地方改为 `state.atomic_write_json` / `state.locked`。
- **不要保留** 1041 行之后的 `_real_positions_map` / `_paper_positions_map` / `protect_scope_positions` / `protection_coverage` / `unprotected_holdings` / `protect_backfill_hook`（自动引擎专用，本版不需要）。

### 3.3 `app/core/paper/orders.py`

从 `_vendor_src/manual_orders.py` 移植 `ManualOrderService`，改名 `PaperOrderService`，改造点：

```python
class PaperOrderError(Exception):
    def __init__(self, message, code="invalid", status=422): ...

class PaperOrderService:
    def __init__(self, broker=None, state_dir=None,
                 cond_path=None, account_path=None, trades_path=None,
                 engine_state_path=None, session_ok=None,
                 kill_switch_path=None, freeze_path=None): ...
```

- **整类删除 `LiveManualBroker`、`_place_live`、`_live_account_view`、`live_broker()`、`_live` 参数**；任何 `trade_mode()=="live"` 分支删掉。
- `trade_mode()` 永远返回 `"paper"`；`live_order_enabled()` 永远返回 `False`。
- `place()` / `cancel_order()` / `add_condition()` / `evaluate()` 中所有 live 分支删除。
- `account_view()` 只保留 paper 分支。
- 保留：`manual_locked` / `mark_manual_lock`（路径改成 `state_dir/manual_lock.json`）、冻结资金/持仓、价格笼子、`_resolve_fill`、`match_pending`、条件单 9 类型、OCO、过期、确认次数 `confirm_count`、`breach` 等全部逻辑。
- 保留 `COND_TYPES`、`SIGNAL_NAMES`、`MIN_LOT`、`SINGLE_ORDER_CAP_RATIO` 常量。
- 所有 `_read_json` / `_write_json_atomic` 改为 `state.read_json` / `state.atomic_write_json`。
- `kill_switch.json` 缺省 = `state_dir/kill_switch.json`，初始不存在视为未急停。
- `engine_state.json` 只读，缺省 `trade_mode=paper`；不存在也返回 paper，不回退 live。
- 兼容别名：`ManualOrderError = PaperOrderError`（旧测试/调用方还能 import）。

### 3.4 `app/core/paper/service.py`

```python
_singleton = None

def get_service() -> PaperOrderService:
    """进程单例；首次调用装配 PaperBroker + PaperOrderService。
    环境变量 PANEL_V2_PAPER_DIR 覆盖账本目录（测试用）。"""

def reset_service() -> None:
    """测试用：丢弃单例（不删文件）。"""

def wanted_codes(svc: PaperOrderService | None = None) -> list[str]:
    """feed 订阅清单 = 持仓 + ACTIVE 条件单 + REPORTED/PARTIAL 挂单，去重排序。"""
```

`service.py` 不启动线程、不 import feed。

---

## 4. 测试移植

把旧测试改写成 pytest（可以保留 `unittest.TestCase` 风格），放在：

- `app/core/paper/tests/test_broker.py`
- `app/core/paper/tests/test_orders.py`

要求：

1. 所有用到 `tmp_path` 的地方把 broker/service 的 `state_dir` 指到临时目录，**绝不写真实 `data/panel_v2/paper`**。
2. 旧测试里 `from core.paper_broker import ...` → `from app.core.paper.broker import ...`；`from core.manual_orders import ...` → `from app.core.paper.orders import ...`。
3. 删除/跳过只测「intraday.json 双写」和「实盘通道」的用例，用 `pytest.mark.skip(reason="paper-only migration: intraday 双写已移除")` 标出，并在交付摘要里列出跳过了哪些、为什么。
4. 其余用例必须原样保留断言（尤其是费用、部分成交、T+1、条件单 OCO、价格笼子、冻结）。
5. 补 3 个新用例：
   - `test_state_dir_isolated`：默认路径在 `PAPER_STATE_DIR` 下，不出现 `engine/state`。
   - `test_no_xtquant_import`：读 `app/core/paper` 下所有 `.py`，断言 `xtquant`/`xttrader`/`qmt_trader` 不出现。
   - `test_reset_to_initial`：写几笔后 `reset_to_initial` 回到 50 万空仓，旧账本进 `backups/reset_*`。

---

## 5. 交付格式（回复给主会话）

1. 列出新建/修改的文件清单（绝对路径）。
2. 说明哪些旧逻辑被删除/跳过，以及为什么（一行一个）。
3. 明确写：**「未运行验证：本环境无 shell，pytest 由主会话执行」**。
4. 如果有任何你不确定的口径，单独列「需主会话确认」小节，不要自己猜着改。

---

## 6. 验收标准（主会话执行）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/core/paper/tests -q
```

期望：全部通过（允许 skip，但 skip 必须逐条有理由）；现有 412 项不回归。
