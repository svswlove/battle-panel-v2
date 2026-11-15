# -*- coding: utf-8 -*-
"""交易日时钟门面：交易日判定 / 期望数据日 / 盘后窗口。

为什么要有这一层（2026-11-07）：
    「盘后直接用算好的数据」需要知道两件事——**今天是交易日吗**、**现在该显示哪一天的数据**。
    此前 compat / compat_kline / kline 三个插件各写了一份 `hm >= 16*60+10` 的时钟，
    只看周末、不判节假日，而且三处各改一次容易漂。这里收成唯一入口。

唯一源：`engine/common/trading_calendar.py`（懒引入；不可用 → 工作日兜底并标 degraded）。
    日历不可用时**不猜节假日**：工作日一律当交易日，宁可多算一次，也不把该更新的数据当休市跳过。

边界：
    · 本模块只回答「时间上该看哪一天」，**不判断数据是否真的到了**——
      数据就绪由后台看板（`data/panel/ops_status.json`）与源库 mtime 判断（见 core.prewarm）。
    · `postclose()` 只是保守兜底（16:40 = 收盘链 READY_DEADLINE）；有看板信号时优先用看板。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import sys
from pathlib import Path
from typing import Any, Optional

from app.core import paths

# 盘后保守兜底钟点：链在 16:10 启动、16:21 才写完主库（11-06 实测），16:40 是链的 READY_DEADLINE。
POSTCLOSE_HHMM = (16, 40)

_CAL: dict[str, Any] = {"tried": False, "mod": None, "error": None}
_DEGRADED: dict[str, Any] = {"active": False, "reason": None}


@contextlib.contextmanager
def _engine_on_path():
    """临时把 `<WS>/engine` 放进 sys.path，退出时只删本次新增的条目（同 amvlive 手法）。"""
    engine = str(Path(paths.WS) / "engine")
    inserted = engine not in sys.path
    if inserted:
        sys.path.append(engine)
    try:
        yield
    finally:
        if inserted:
            with contextlib.suppress(ValueError):
                sys.path.remove(engine)


def _calendar_module():
    """懒加载交易日历模块；失败返回 None（不抛，调用方兜底）。"""
    if _CAL["tried"]:
        return _CAL["mod"]
    _CAL["tried"] = True
    try:
        with _engine_on_path():
            import common.trading_calendar as tc  # noqa: PLC0415 - 懒引入，避免启动期依赖
        _CAL["mod"] = tc
    except Exception as exc:  # noqa: BLE001 - 日历缺失不拖垮面板
        _CAL["error"] = "%s: %s" % (type(exc).__name__, exc)
    return _CAL["mod"]


def _mark_degraded(reason: str) -> None:
    _DEGRADED["active"] = True
    _DEGRADED["reason"] = reason


def degraded() -> dict[str, Any]:
    """当前是否在用工作日兜底（给 /health 或页面标注用）。"""
    return dict(_DEGRADED)


def _to_date(d: Any) -> _dt.date:
    if d is None:
        return _dt.date.today()
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d
    s = str(d).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError("无法解析日期: %r" % (d,))


def is_trading_day(d: Any = None) -> bool:
    """是否交易日。日历不可用 → 工作日兜底（周一~五 True）并标 degraded。"""
    day = _to_date(d)
    if day.weekday() >= 5:
        return False
    tc = _calendar_module()
    if tc is None:
        _mark_degraded("trading_calendar 不可用，按工作日兜底：%s" % (_CAL["error"],))
        return True
    try:
        return bool(tc.is_trading_day(day))
    except Exception as exc:  # noqa: BLE001 - 日历故障不改变结论方向
        _mark_degraded("is_trading_day(%s) 抛错，按工作日兜底：%s: %s"
                       % (day, type(exc).__name__, exc))
        return True


def _weekday_before(day: _dt.date) -> _dt.date:
    x = day - _dt.timedelta(days=1)
    while x.weekday() >= 5:
        x -= _dt.timedelta(days=1)
    return x


def prev_trading_date(day: Any = None) -> _dt.date:
    """day 之前最近的交易日（不含 day 本身）。"""
    d = _to_date(day)
    tc = _calendar_module()
    if tc is not None:
        try:
            ds = tc.get_trading_dates((d - _dt.timedelta(days=45)).isoformat(),
                                      (d - _dt.timedelta(days=1)).isoformat())
            if ds:
                return _dt.datetime.strptime(ds[-1], "%Y-%m-%d").date()
        except Exception as exc:  # noqa: BLE001
            _mark_degraded("get_trading_dates 失败，按工作日兜底：%s: %s" % (type(exc).__name__, exc))
    return _weekday_before(d)


def latest_trading_date(day: Any = None) -> _dt.date:
    """day 当天或之前最近的交易日（day 是交易日就返回 day）。"""
    d = _to_date(day)
    return d if is_trading_day(d) else prev_trading_date(d)


def expected_data_date8(now: Optional[_dt.datetime] = None) -> str:
    """当前「应该看到」的数据日（YYYYMMDD），不是文件时间。

    · 交易日 15:00 后 → 当天（收盘了，数据日就是今天；是否已写入由调用方/看板再校验）
    · 交易日 15:00 前 → 上一交易日
    · 非交易日 → 最近交易日
    """
    cur = now or _dt.datetime.now()
    d = cur.date()
    if is_trading_day(d):
        if (cur.hour, cur.minute) >= (15, 0):
            return d.strftime("%Y%m%d")
        return prev_trading_date(d).strftime("%Y%m%d")
    return latest_trading_date(d).strftime("%Y%m%d")


def postclose(now: Optional[_dt.datetime] = None) -> bool:
    """数据固定窗口（保守兜底）：非交易日，或交易日 >= 16:40。

    只用于「允许把结果落盘/直读」的保守判定；精确就绪请看板（ops_status.json）。
    """
    cur = now or _dt.datetime.now()
    d = cur.date()
    if not is_trading_day(d):
        return True
    return (cur.hour, cur.minute) >= POSTCLOSE_HHMM


def status(now: Optional[_dt.datetime] = None) -> dict[str, Any]:
    """一处汇总：给 /health、页面标注、预热器共用。"""
    cur = now or _dt.datetime.now()
    return {
        "now": cur.strftime("%Y-%m-%d %H:%M:%S"),
        "today": cur.strftime("%Y%m%d"),
        "is_trading_day": is_trading_day(cur.date()),
        "expected_data_date": expected_data_date8(cur),
        "postclose": postclose(cur),
        "postclose_hhmm": "%02d:%02d" % POSTCLOSE_HHMM,
        "degraded": degraded(),
    }
