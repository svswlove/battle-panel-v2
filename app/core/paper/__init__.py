# -*- coding: utf-8 -*-
"""app/core/paper · 纸面交易 core 服务（4.1.2）

**只服务纸面，绝不接真实通道**：本包任何文件都不出现真实行情/交易库的导入，
不存在任何真实下单调用路径
（验收会静态扫：真实行情库 / 真实交易库 / QMT 交易模块 / 柜台客户端，四类关键字零命中）。

模块分工（见 docs/paper_trading_architecture.md §2/§3）：
  state.py     路径 / 文件锁 / 原子写 / 备份 / 审计 —— **唯一允许写账本文件的地方**
  filelock.py  跨进程文件互斥锁（纯标准库，旧工程逐字 vendoring）
  broker.py    PaperBroker：撮合 / 记账 / 费用 / 估值（paper-only）
  orders.py    PaperOrderService：手工单 / 条件单 9 类型 / OCO / 冻结 / 急停（paper-only）
  service.py   进程单例装配 + feed 订阅清单 wanted_codes()

账本目录：paths.PAPER_STATE_DIR（默认 <panel-v2>/data/panel_v2/paper，PANEL_V2_PAPER_DIR 可覆盖）。
与旧 engine/state、生产 data/panel 完全隔离，一个字节都不写。

本步（4.1.2）不含：HTTP 端点（4.1.4 的 paper 插件）、行情订阅（4.1.3 的 app/core/feed.py）。
"""

from app.core.paper.state import (
    PaperPaths,
    atomic_write_json,
    audit,
    backup_file,
    get_paths,
    locked,
    read_json,
    reset_to_initial,
)

from app.core.paper.broker import (
    COMMISSION_RATE,
    INITIAL_CASH,
    MIN_COMMISSION,
    STAMP_RATE,
    TRANSFER_RATE,
    PaperBroker,
    calc_fee,
)
from app.core.paper.orders import (
    COND_ACTIVE,
    COND_TERMINAL,
    COND_TYPES,
    MIN_LOT,
    SIGNAL_NAMES,
    SINGLE_ORDER_CAP_RATIO,
    ManualOrderError,
    PaperOrderError,
    PaperOrderService,
    manual_locked,
    mark_manual_lock,
)
from app.core.paper.service import get_service, reset_service, wanted_codes

__all__ = [
    "COMMISSION_RATE",
    "COND_ACTIVE",
    "COND_TERMINAL",
    "COND_TYPES",
    "INITIAL_CASH",
    "MIN_COMMISSION",
    "MIN_LOT",
    "ManualOrderError",
    "PaperBroker",
    "PaperOrderError",
    "PaperOrderService",
    "PaperPaths",
    "SIGNAL_NAMES",
    "SINGLE_ORDER_CAP_RATIO",
    "STAMP_RATE",
    "TRANSFER_RATE",
    "atomic_write_json",
    "audit",
    "backup_file",
    "calc_fee",
    "get_paths",
    "get_service",
    "locked",
    "manual_locked",
    "mark_manual_lock",
    "read_json",
    "reset_service",
    "reset_to_initial",
    "wanted_codes",
]
