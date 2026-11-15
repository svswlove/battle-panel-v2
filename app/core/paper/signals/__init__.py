# -*- coding: utf-8 -*-
"""app/core/paper/signals · 信号模板库（任务书 4.4 从旧端 vendor 进来）

【来源】旧 8800 `signals/library.py` + `signals/macd.py`，**逐字复制**（含全部 docstring 与
中文注释），只做 import 路径适配：`library.py` 内的 `.macd` 相对导入在包结构下即成立，
顶层调试兜底分支 `from signals.macd import ...` 保持原样（正式路径不会走到它）。

【硬约束】复制后**不改算法、不改阈值、不改参数**；本包只做门面导出。
判定口径与旧端 `engine/core/order_runtime.py::_signal_check` 完全同源（2026-10-30 之前该函数住在 `engine/server.py`，已下沉）﹐改动须回旧端同步。

【边界】纯 OHLCV 计算，零外部数据源：本包不连行情、不读文件、不写文件；
`app/core/paper/` 的「零真实行情/交易库关键字」静态扫描对它同样生效。

【用法】provider 见 `app/core/paper/signal_provider.py`：取当日分钟K → 转 `Candle` →
`cls().evaluate(candles)` → `bool(res.triggered)`。
"""

from .library import (
    BreakoutSignal,
    Candle,
    HookTurnSignal,
    MacdDivergenceSignal,
    PullbackSignal,
    SignalResult,
    StopAnchorPicker,
    StopSignal,
    VwapReclaimSignal,
    WeakToStrongSignal,
)

__all__ = [
    "BreakoutSignal",
    "Candle",
    "HookTurnSignal",
    "MacdDivergenceSignal",
    "PullbackSignal",
    "SignalResult",
    "StopAnchorPicker",
    "StopSignal",
    "VwapReclaimSignal",
    "WeakToStrongSignal",
]
