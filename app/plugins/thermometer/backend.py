# -*- coding: utf-8 -*-
"""thermometer 插件后端：情绪温度计的「口径元信息」（只读）。

## 分工（2026-10-29）

| 事 | 谁做 | 页面怎么拿 |
|---|---|---|
| 情绪指数 / 六因子 / 盘面原始值 / stage | `emotion` 插件（口径唯一源，v1.1） | 前端 `ctx.api.get('/api/p/emotion/payload')` |
| 恐慌广度 / 四项 / 成交额 / 量能 | core `panic_calc` → 旧仓 `engine/collectors/panic_breadth.py` | 前端 `ctx.api.get('/api/panic')` |
| 恐慌口径断点 + 序列库行数 | **本模块** `/meta` | 页面图例与章节注脚 |

**本插件不算任何指标**：不重算情绪分、不重算恐慌广度、不碰 `track_pool.db`、不写
`data/panel/`。它只做三件事：拼版面、把两个已有读数放在同一屏、把两个口径的分母
（60 日 / 250 日）与一个历史断点标清楚。

**为什么读 `panic_breadth.json` 也要走白名单**：断点日期（哪一天起加了量能项）只能从
序列库里「哪些行带 amount 字段」看出来；`/api/panic` 只回今日的 volume，历史行不带。
读它用 `ctx.panel_json("panic_breadth")`（manifest `data.panel` 已声明），**只读、不写**，
也不重算任何分位——纯粹数行。

**为什么断点日期是常量而不是「自动取第一行带 amount 的日期」**：今日的恐慌值是快照现算
（`mode=close`），还没落库，所以「第一行带 amount 的日期」今天是空集。常量 `CALIBER_BREAK`
= 主人 2026-10-29 定下的口径切换日；`/meta` 同时回 `store.four_item_first` 与 `drift`：
等哪天序列库真的落了四项行，若与常量不一致会在这里显式报出来，不静默改口。

拆卸：删 `app/plugins/thermometer/` 整个目录 + 删 `app/web/shell.js` NAV 里
`thermometer::thermometer.main` 一行。core 零改动。
"""
from __future__ import annotations

import importlib.util as _ilu
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.core import paths

# 兄弟模块加载（与 emotion / concept 同款三级兜底）：生产时 core 用 spec_from_file_location
# 加载 backend，本模块没有父包；pytest 里走 app.plugins.thermometer 包导入。
try:                                        # 1) 包导入
    from . import dims as _dims  # type: ignore
except ImportError:                             # pragma: no cover - core 按文件加载时走这里
    try:                                    # 2) 项目根在 sys.path 时的绝对导入
        from app.plugins.thermometer import dims as _dims  # type: ignore
    except ImportError:                     # 3) 按路径加载同目录兄弟模块
        _here = Path(__file__).resolve().parent
        _spec = _ilu.spec_from_file_location("_panel_v2_thermometer_dims", _here / "dims.py")
        if _spec is None or _spec.loader is None:
            raise ImportError("无法加载 thermometer 的 dims 模块")
        _dims = _ilu.module_from_spec(_spec)  # type: ignore
        sys.modules[_spec.name] = _dims
        _spec.loader.exec_module(_dims)

#: 恐慌广度四项口径（含量能）首日。此处是唯一一处写死，`/meta` 与页面图例都取自它。
CALIBER_BREAK = "20261029"
#: 主力资金：**降级路径**——一次请求拿全A指数 880008 的 5 日「净额 / 占成交」（eltdx money_flow.daily）。
#: 正常路径改读 L2 历史序列（panel 白名单 `FLOW_SERIES_NAME`）：序列在就不用发这个请求。
INDEX_FLOW_CODE = "sh880008"
#: 本进程内缓存秒数（页面刷新不重复打服务器；日频指标 60s 足够）
INDEX_FLOW_TTL = 60.0
#: 单次取数超时秒（请求路径上的硬上限，超时就把该项标缺，不让页面挂着）
INDEX_FLOW_TIMEOUT = 5.0
_INDEX_FLOW_CACHE: Dict[str, Any] = {"at": 0.0, "doc": None}


def _index_flow() -> Dict[str, Any]:
    """【降级路径】取全A指数 880008 最近 5 日主力资金（**一次请求**，不做全市场循环、不落盘）。

    只在 L2 历史序列（panel 白名单 `FLOW_SERIES_NAME`）读不到时调用；正常路径不发这个请求。
    返回 `{"rows": [{date, main_ratio, main_net, amount}, ...], "reason": ""}`，行已按日期升序。
    eltdx 不可用 / 超时 / 空 → rows=[] + reason（页面把该项标「缺分位」，不编）。
    前提：这里只发**一次** 880008 的请求（金融数据获取卡 §4 禁的是全市场循环，不是单指数）。
    """
    now = time.time()
    cached = _INDEX_FLOW_CACHE.get("doc")
    if cached is not None and now - float(_INDEX_FLOW_CACHE.get("at") or 0) < INDEX_FLOW_TTL:
        return cached

    def _fetch() -> list:
        from eltdx import TdxClient  # noqa: PLC0415 - 只在取数时导入，插件其它路径不依赖
        blk = TdxClient().money_flow.daily(INDEX_FLOW_CODE)
        out = []
        for r in getattr(blk, "records", ()) or ():
            d = getattr(r, "date", None)
            out.append({
                "date": d.strftime("%Y%m%d") if hasattr(d, "strftime") else str(d),
                "main_ratio": getattr(r, "main_ratio", None),
                "main_net": getattr(r, "main_net", None),
                "amount": getattr(r, "total_amount", None),
            })
        return out

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            rows = ex.submit(_fetch).result(timeout=INDEX_FLOW_TIMEOUT)
        doc = {"rows": rows, "reason": "" if rows else "接口返回空"}
    except Exception as exc:  # noqa: BLE001 - 取不到就标缺，不让页面挂
        doc = {"rows": [], "reason": "eltdx money_flow.daily(%s) 失败：%s" % (INDEX_FLOW_CODE, type(exc).__name__)}
    _INDEX_FLOW_CACHE.update(at=now, doc=doc)
    return doc
#: 断点的展示文案（图例与竖线标注共用）
CALIBER_BREAK_LABEL = "此前三项口径 / 此后四项含量能"
#: 位置序列文件名（880008 全A指数日线，由 tools/index_880008_daily.py 写入）
INDEX_FILE = "index_880008_series.json"
#: 主力资金历史序列的 panel 白名单名（core `paths.READABLE_PANEL` 登记 + manifest `data.panel` 声明）；
#: 文件名本体只在 `dims.FLOW_SERIES_FILE` 定义一次，报错文案引用它。
FLOW_SERIES_NAME = "index_flow_series_880008"
#: 龙虎榜缓存目录名（夏天-activity/工具/xiaoshi_cache，hithink 只读产物）
DRAGON_CACHE_DIRNAME = "xiaoshi_cache"
#: 三项口径的历史行数（截至 2026-10-29 实测 1264 行；只用于页面注脚的填空，不参与判断）
THREE_ITEM_NOTE = "三项口径：跌停占比 / 上涨占比 / 全A中位"
FOUR_ITEM_NOTE = "四项口径：前三项 + 量能（当日全天额 ÷ 昨日全天额）"

#: 判定「这一行是四项口径」的字段：旧仓 `turnover_context` 落的是 amount/amount_ratio
FOUR_ITEM_FIELDS = ("amount", "amount_ratio")


def _row_has_amount(row: Dict[str, Any]) -> bool:
    """该行是否带量能原始值（= 四项口径行的充分标记）。"""
    return any(row.get(key) is not None for key in FOUR_ITEM_FIELDS)


def _dates(rows: List[Dict[str, Any]]) -> List[str]:
    out = []
    for row in rows:
        date = row.get("date")
        if date:
            out.append(str(date))
    return out


def caliber(doc: Dict[str, Any]) -> Dict[str, Any]:
    """序列库 → 口径元信息（纯函数，输入是 `panic_breadth.json` 解析后的 dict）。"""
    rows = [r for r in (doc.get("series") or []) if isinstance(r, dict)]
    four = [r for r in rows if _row_has_amount(r)]
    three_n = len(rows) - len(four)
    dates = _dates(rows)
    first_four = None
    if four:
        first_four = str(four[0].get("date") or "") or None

    # 序列库若已落四项行，首日必须与常量一致；不一致就上报，不静默改口
    drift = None
    if first_four and first_four != CALIBER_BREAK:
        drift = {
            "store_four_item_first": first_four,
            "declared_break": CALIBER_BREAK,
            "why": "序列库首条四项行与常量不同：要么序列库被回填、要么口径又变了，需人工确认后改常量",
        }

    return {
        "ok": True,
        "break_from": CALIBER_BREAK,
        "break_label": CALIBER_BREAK_LABEL,
        "three_item_note": THREE_ITEM_NOTE,
        "four_item_note": FOUR_ITEM_NOTE,
        "store": {
            "rows": len(rows),
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
            "three_item_rows": three_n,
            "four_item_rows": len(four),
            "four_item_first": first_four,
        },
        "drift": drift,
        "windows": {"emotion_days": 60, "panic_days": 250},
        "note": ("恐慌广度序列库只读；今日读数由 /api/panic 快照现算（四项口径首日），"
                 "断点左右不可直接连线比较"),
    }


def _degraded(reason: str) -> Dict[str, Any]:
    """序列库读不到：断点常量照给（页面还要标断点），行数一律 None —— 不编 0。"""
    return {
        "ok": False,
        "reason": reason,
        "break_from": CALIBER_BREAK,
        "break_label": CALIBER_BREAK_LABEL,
        "three_item_note": THREE_ITEM_NOTE,
        "four_item_note": FOUR_ITEM_NOTE,
        "store": {
            "rows": None,
            "first": None,
            "last": None,
            "three_item_rows": None,
            "four_item_rows": None,
            "four_item_first": None,
        },
        "drift": None,
        "windows": {"emotion_days": 60, "panic_days": 250},
        "note": "序列库不可读：断点常量照用，行数显示「缺」",
    }


def register(ctx) -> APIRouter:
    router = APIRouter()

    @router.get("/meta")
    def meta() -> Dict[str, Any]:
        """口径元信息：断点日期、序列库行数与三项/四项计数。只读白名单文件。"""
        try:
            doc = ctx.panel_json("panic_breadth")
        except (PermissionError, FileNotFoundError) as exc:
            return _degraded("%s: %s" % (type(exc).__name__, exc))
        if not isinstance(doc, dict):
            return _degraded("panic_breadth.json 结构不符（顶层不是对象）")
        return caliber(doc)

    @router.get("/dims")
    def dims(promo_rate: Optional[float] = Query(None, ge=0.0, le=1.0, description="emotion 今日晋级率（比例，0.4 = 40%）"),
             promo_base: Optional[int] = Query(None, ge=0, description="emotion 今日晋级率分母（昨日 2 板家数）"),
             panic_median_q: Optional[float] = Query(None, ge=0.0, le=100.0, description="/api/panic 全A中位项的 250 日分位（百分数）"),
             panic_amount_q: Optional[float] = Query(None, ge=0.0, le=100.0, description="/api/panic 量能项的 250 日分位（百分数）"),
             panic_median: Optional[float] = Query(None, description="/api/panic 全A中位原始值（%）"),
             panic_amount: Optional[float] = Query(None, description="/api/panic 量能原始值（成交额/昨日，×）")) -> Dict[str, Any]:
        """四维拆解（广度/接力/资金/位置）：子项分位 + 方向统一 + 维内等权。

        上游事实由前端透传（它们各有 owner）：晋级率两项来自 emotion payload，
        全A中位与量能的原始值/250 日分位来自 `/api/panic`。不传 → 该项标「缺」不参与，**不填 0**。
        只读：`track_pool.db`（sentiment 收盘行，`PRAGMA query_only=ON`）；
        位置序列（`ctx.data_dir` 下的盘后产物）与主力资金序列（`data/panel/`，走 panel 白名单
        `index_flow_series_880008`）都是只读文件，插件不连行情源。
        """
        conn = ctx.db_read("track_pool.db")
        try:
            rows = _dims.load_sentiment(conn)
            if not rows:
                return {"ok": False, "reason": "track_pool.db::sentiment 里没有 ts='close' 行",
                        "dims": [], "missing": []}
            # 三个只读序列：龙虎榜（hithink 缓存）+ 位置（880008）+ 主力资金（L2 历史序列）；两融已移出
            dragon = _dims.load_dragon_series(Path(paths.SUMMER_TOOLS_DIR) / DRAGON_CACHE_DIRNAME)
            index = _dims.load_index_series(Path(ctx.data_dir) / INDEX_FILE)
            # 主力资金：优先读 panel 白名单里的 L2 历史序列（507 条 → 250 日分位）；读不到才退回一次实时请求
            try:
                flow_series = _dims.parse_index_flow_series(ctx.panel_json(FLOW_SERIES_NAME))
            except (FileNotFoundError, PermissionError, ValueError) as exc:
                # 白名单未登记 / 文件缺失 / 顶层不是对象 → 降级，不抛到页面
                flow_series = {"rows": [], "meta": {}, "latest": {},
                               "reason": "主力资金序列不可读（%s: %s）" % (type(exc).__name__, exc)}
            index_flow = None if flow_series["rows"] else _index_flow()
            return _dims.build_dims(
                rows, index,
                promo_rate=promo_rate,
                promo_base=promo_base,
                panic_median_q=panic_median_q,
                panic_amount_q=panic_amount_q,
                panic_median=panic_median,
                panic_amount=panic_amount,
                flow_series=flow_series,
                index_flow=index_flow,
                dragon=dragon,
            )
        finally:
            conn.close()

    return router
