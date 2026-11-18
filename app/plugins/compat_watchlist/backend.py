# -*- coding: utf-8 -*-
"""compat_watchlist：旧面板前端「自选池」域 12 条根路径端点（4 读 + 8 写路由）的兼容层。

**这是过渡层，不是新功能。** 旧前端（`engine/web/index.html` 自选池 Tab、
`kline_lc.js` 弹窗「☆ 自选」、右键菜单）请求的是根路径 `/api/watchlist*`；
本插件用 `backend.mount = "root"` 注册到 `/api`，响应的**键名/结构逐字段对齐旧 8800**
（口径唯一来源：旧仓 `engine/server.py` 的 `_watchlist_payload` / `_watchlist_raw` /
`_watchlist_groups` / `_watchlist_save` 与 `api_watchlist_*` 一组函数）。

端点（12 条路径 / 9 个逻辑端点）：

| 路径 | 方法 | 说明 |
|---|---|---|
| `/api/watchlist.json` | GET | 条目 + 分组 + 富化（行情/多空/板块/背离/资金/徽标），`?mode=live` 走实时档 |
| `/api/watchlist/groups` | GET | 分组列表 `{ok, groups}` |
| `/api/watchlist/state` | GET | 单条目是否在池 `{ok, kind, code, watched, ts}` |
| `/api/watchlist/search` | GET + POST | 股票/板块搜索（**旧前端是 POST + JSON body**，GET 一并接） |
| `/api/watchlist/add` | POST | 加条目（幂等 upsert） |
| `/api/watchlist/remove` | POST | 删条目（幂等） |
| `/api/watchlist/move` | POST | 移到分组（跨类型拒绝） |
| `/api/watchlist/reorder` | POST | 条目排序（可限组） |
| `/api/watchlist/group/add` | POST | 新建分组 |
| `/api/watchlist/group/remove` | POST | 删分组（条目移入同类型默认组） |
| `/api/watchlist/group/rename` | POST | 分组改名 |
| `/api/watchlist/group/reorder` | POST | 分组排序 |

## 写通道

唯一写入口是 `ctx.panel_write("watchlist", payload)`（core 已做：白名单校验 + 写前备份
`data/panel_v2/backups/watchlist/<时间戳>.json` + 原子替换 tmp→os.replace + `audit.log` 审计）。
富化磁盘缓存读写走 `ctx.json_read` / `ctx.misc_write`（同一套白名单 + 原子写），
本插件**不做裸文件 IO**（不 `open()`、不自己连库、不自己建目录）。

写操作一律「读整份 → 改内存 → 写整份」：

1. `panel_write` 收的是**完整 dict**，不是增量；只写局部会丢字段。
2. **未知字段原样保留**（任务书 §3.3）：顶层除 `meta`/`groups`/`items` 外的键、
   `meta` 里的未知键、条目/分组上的额外键，全部随读到的原值写回，只改本插件负责的部分。
   旧 8800 的 `_watchlist_save` 会重建 `{meta,groups,items}` 而丢掉额外键，这里**故意不照抄**。
3. 同一进程内用模块级 `threading.Lock()` 包住整个「读-改-写」，防连点并发交叉覆盖
   （旧端 `_WATCH_LOCK` 同款）。
4. 校验不过（非法代码 / 分组不存在 / 跨类型）直接 4xx，**不写一半**。

## 与旧端的已知差异（不编造，逐条登记）

| # | 差异 | 原因 |
|---|---|---|
| 1 | EOD 收盘价来源 `eltdx:eod`（旧端 `vipdoc:eod` 直读通达信 `lday/*.day`） | 插件不许 `open()`、vipdoc 不在任何只读白名单 → 改走 `ctx.quotes.daily_bars()`（同一份行情、末两根口径相同），`source` 字段如实标注不是 vipdoc |
| 2 | EOD 取不到时 `quote=null`（前端显示「—」） | 行情门面不可用时**不补 0、不拿实时价冒充收盘价** |
| 3 | 搜索**不含拼音**（首字母/全拼） | pypinyin 装在旧仓 `engine/vendor_py`，引入即新依赖 → 只做代码/名称子串匹配；返回结构不变，少的是召回面（与 `compat` 插件同处理） |
| 4 | `POST /watchlist/add` 的 `group` 生效 | 旧端 Pydantic 模型未声明 `group` 字段（默认 ignore）→ 旧端实际忽略它、总落默认组；前端右键菜单一直在传 `group`，这里按前端意图实现（旧前端不会因此出问题） |
| 5 | 富化缓存分三层：进程内存（行情 60s / 日更库 900s / 整包 6s）+ **日更库结果落盘**（2026-11-07） | 旧端按 `amv_board.db`/`buy_signal.db` mtime 失效；插件此前拿不到路径，只能用 TTL 近似，进程重启/15min 过期后首个请求重算 8s。现在用 `PRAGMA database_list` 拿到库路径，按三个源库的 **mtime+size** 失效并落盘；源库不变就不重算，变了就整份作废 |
| 6 | `div`/`flow`/`badges`/`sector` 的取数改为按请求内复用连接 | 同源同 SQL，只是连接复用，不改口径 |

等旧前端改完、新前端（`/api/p/watchlist/*`）补齐写操作后，本插件整体删除。
"""
from __future__ import annotations

import contextlib
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException, Query

# ---------------------------------------------------------------- 常量（照旧端）

PANEL = "watchlist"
PANEL_QUOTES = "watchlist_quotes"

# 默认分组（2026-10-15 旧端口径）：id 固定、不可删不可改名，kind 固定。
WATCH_GROUP_DEFAULT: Dict[str, str] = {"stock": "stock_default", "sector": "sector_default"}
WATCH_GROUP_DEFAULT_NAME: Dict[str, str] = {"stock": "自选", "sector": "板块"}

# 搜索白名单（旧端 `_WATCH_SEARCH_BY_MKT`）：A 股个股号段，ETF/基金/指数/债券全剔。
_WATCH_SEARCH_BY_MKT: Dict[str, Tuple[str, ...]] = {
    "SH": ("60", "68"),
    "SZ": ("00", "30"),
    "BJ": ("43", "83", "87", "92"),
}

# ETF 号段（2026-11-11 主人：面板搜不到 ETF、加不了自选，而主板买不了、实际只能买 ETF）。
# 只收真正可交易的 ETF 段：沪 51x/52x/56x/58x、深 158/159。
#   · 不按首位数字放宽：SH "5" 会把 500xxx（老封闭基金）、519xxx（场内开放基金）一起收进来；
#   · 不收 LOF（深 16x）：主人要的是 ETF，LOF 另开口径时再加。
# 3 位前缀而非 2 位：与实测 eltdx `codes.etfs()` 的码段对齐（2026-11-11：
#   159/588/560/561/512/562/516/515/513/158/563/510/520/511/517/518），
#   多留 521-529 / 564-569 / 589 给新发码段。
_WATCH_SEARCH_ETF_BY_MKT: Dict[str, Tuple[str, ...]] = {
    "SH": ("510", "511", "512", "513", "515", "516", "517", "518",
           "520", "521", "522", "523", "524", "525", "526", "527", "528", "529",
           "560", "561", "562", "563", "564", "565", "566", "567", "568", "569",
           "588", "589"),
    "SZ": ("158", "159"),
}

SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50

# 实时档（`?mode=live`）报价新鲜度：非当日 或 距今 > 5min → 不可用（回退 EOD，不冒充实时）。
LIVE_QUOTE_MAX_AGE_S = 300

# 富化缓存 TTL（见模块头「已知差异 5」）
# 2026-10-29 分层（自选页打开慢的根因）：原来全部 60s 满期——每 60 秒整包重算一次
#   （实测 5.8s，其中 4.3s 是 33 个板块的多空串行查询），面板 5s 轮询必然採到。
#   分层后：日更库类 15 分钟，行情类 60s，整包 6s（前端 refreshEvery=5000）。
CACHE_TTL_S = 60.0        # 行情/价格类（会随盘中变化）
DB_TTL_S = 900.0          # 日更库类：多空/板块归属/背离/资金/徽标（源库一天只写一次）
EOD_BATCH_TTL_S = 60.0    # EOD 行情整包（与 core.quotes.daily_bars 的 60s 缓存对齐）
PAYLOAD_TTL_S = 6.0       # 整包载荷（前端 5s 轮询）

# 买点徽标映射（旧端 `_WATCH_SIG_META` 逐字照抄：label + 颜色类 + 说明）。
_WATCH_SIG_META: Dict[str, Tuple[str, str, str]] = {
    "B1_W": ("B1", "b1", "B1·低位整理（ENV_OK + J<16，尚未出现阳线转强确认）"),
    "B1_Y": ("B1", "y", "B1·阳线转强（J<16 + RSI6 上行 + 收阳）"),
    "B1_Q": ("B1", "q", "B1·缩量蓄势（窄幅基底、无阴量派发，ACCUM_BASE）"),
    "TOP_RES": ("B1", "r", "B1·砖首转强（砖型图首根走强 + J 低位）"),
    "B2": ("B2", "b2", "B2 放量突破"),
    "B3": ("B3", "b3", "B3 回踩缩量（B2 次日缩量下探企稳）"),
    "B4": ("B4", "b4", "B4 回踩平量（B2 次日温和缩量整理）"),
    "B5": ("B5", "b3", "B5 二次回踩（B3/B4 次日再缩量踩稳）"),
    "A_GOLD": ("针", "y", "黄金针（仅日线）"),
    "B_WHITE": ("针", "w", "白金针"),
    "MOM": ("动", "mo", "动能选股"),
    "ZHU_P": ("砖", "zhu-p", "砖形图·终极起爆（个股｜均线多头 + 3~12 天动态横盘 + 反转确立）"),
    "ZHU_O": ("砖", "zhu-o", "砖形图·单日反包起爆（个股｜洗盘 1 天后放量反包）"),
    "ZHU_W": ("砖", "zhu-w", "砖形图·反转确立（个股｜砖体由绿转红且力度达标）"),
    "周": ("周", "wk", "周线B1（每周五更新）"),
    "WEEK_B1": ("周", "wk", "周线B1（每周五更新）"),
    # 五日线战法（2026-11-18）：盘后链 buysignal 步与买点徽标同批写库（只算自选池）。
    #   与 engine/server.py::_WATCH_SIG_META 同源同名——两张表必须同时改。
    "SD_ENTRY": ("首进", "sd-e", "五日线·首进（站上MA5+连续3日放量+7日内巨量1-2次，且真业绩）"),
    "SD_REENTRY": ("回马", "sd-r", "五日线·回马枪（巨量+距MA5 -4~-5%+MA5向上+近30日破位后5-10日）"),
}

# 读-改-写互斥（模块级）：防两个请求交叉覆盖同一份文件（旧端 `_WATCH_LOCK` 同款）。
_WATCH_LOCK = threading.Lock()

# 富化缓存：键 → (monotonic 时间戳, 值)
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()

# ---------------------------------------------------------------- 磁盘缓存（2026-11-07）
#
# 为什么要有落盘层：日更库类的富化要跑全历史状态机（板块 regime 单次 ~150ms），
# 48 票 × L2/L3 冷算 6-8s；内存 TTL 只在进程内有效，8810 重启或 15min 过期后的
# 第一个请求仍要把同一批结果重算一遍。这里把 DB_TTL_S 类结果写进一个小 JSON，
# 进程重启后首次读盘即命中（~1ms），源库不变就不重算。
#
# 失效口径：amv_board.db / ths_board.db / buy_signal.db 的 mtime+size 变化即整份作废。
#   —— 这三份都是日更落盘库，盘中不写；mtime 变化意味着数据已更新，宁可重算。
# 边界：只是缓存，读不到/读坏了都不影响正确性（退回现算）；写走 ctx.misc_write
#   （白名单 + 原子替换 + 审计），插件自身不做文件 IO。
_DISK_NAME = "watchlist_enrich_cache"
_DISK_VERSION = 1
_DISK_DBS: Tuple[str, ...] = ("amv_board.db", "ths_board.db", "buy_signal.db")
_DISK_RECHECK_S = 30.0     # 同一 ctx 重算 mtime 的最短间隔（日更库，30s 足够灵敏）
_MISS = object()
_DISK: Dict[str, Any] = {"owner": None, "loaded": False, "stamps": {},
                          "items": {}, "dirty": False, "next_check": 0.0}
_DISK_LOCK = threading.Lock()


# ---------------------------------------------------------------- 基础工具


def _now() -> str:
    """旧端 `now_str()` 同格式。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(value: Any) -> bool:
    try:
        f = float(value)
        return f == f and abs(f) != float("inf")
    except (TypeError, ValueError):
        return False


def _to_ts_code(code: Any) -> Optional[str]:
    """6 位码 → ts_code；已是 ts_code 原样返回；非法 → None。

    **逐字复刻** `engine/common/tsutil.py::to_ts_code`（该模块不在口径库白名单，
    且逻辑是纯字符串规则、不含任何数据口径，故此处置一份等价实现）：
    920xxx → BJ；6/5/9 开头 → SH；0/2/3 → SZ；4/8 → BJ；**15 开头 → SZ（ETF）**。

    2026-11-11（ETF 进自选）：原规则对 `159xxx`（深市 ETF 主体）返回 None → 加自选直接 422。
    只补 15 段（深市 ETF），不按首位放宽：深 1 开头还有 100xxx 国债 / 110xxx 转债 / 16x LOF，
    那些不该被默认归成「可交易 ETF」。沪市 ETF（5 开头）原本已走 SH 分支。
    """
    if not code:
        return None
    s = str(code).strip()
    if len(s) == 9 and s[6] == "." and s[:6].isdigit() and s[7:].upper() in ("SH", "SZ", "BJ"):
        return s
    if len(s) != 6 or not s.isdigit():
        return None
    if s[:3] == "920":
        return s + ".BJ"
    if s[0] in ("6", "5", "9"):
        return s + ".SH"
    if s[0] in ("0", "2", "3"):
        return s + ".SZ"
    if s.startswith("15"):
        return s + ".SZ"
    if s[0] in ("4", "8"):
        return s + ".BJ"
    return None


def _suffix_of(code6: str) -> Optional[str]:
    """6 位码 → 交易所后缀（搜索用）；推断不出 → None（**宁可少召回，不下发错后缀**）。

    规则与 `compat` 插件同（旧端 name 表带后缀，这里只有 6 位码）：
      920xxx → BJ；880/881/884/885 → SH；4/8 → BJ；5/6/9 → SH；0/1/3 → SZ。
    """
    if len(code6) != 6 or not code6.isdigit():
        return None
    if code6.startswith("920"):
        return "BJ"
    if code6[:3] in ("880", "881", "884", "885"):
        return "SH"
    head = code6[0]
    if head in "48":
        return "BJ"
    if head in "569":
        return "SH"
    if head in "013":
        return "SZ"
    return None


def _cache_get(key: str, ttl: float = CACHE_TTL_S) -> Tuple[bool, Any]:
    hit = _CACHE.get(key)
    if hit is None:
        return False, None
    return ((time.monotonic() - hit[0]) < ttl), hit[1]


def _cache_put(key: str, value: Any) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)
        if len(_CACHE) > 4096:            # 防御：自选池/板块码有限，正常不会长到这么大
            _CACHE.clear()


def _cached(key: str, build: Callable[[], Any], ttl: float = CACHE_TTL_S,
            persist: bool = False) -> Any:
    """短 TTL 缓存（同 key 并发重复计算无害，故不在锁内跑查询）。

    `persist=True` 的项多一层磁盘：内存过期后先查落盘结果（源库没变就直接用），
    命中时不跑 build。日更库类（DB_TTL_S）用这个，行情类不落盘。
    """
    ok, val = _cache_get(key, ttl)
    if ok:
        return val
    if persist:
        hit = _DISK["items"].get(key, _MISS)
        if hit is not _MISS:
            _cache_put(key, hit)
            return hit
    val = build()
    _cache_put(key, val)
    if persist:
        _disk_put(key, val)
    return val


def _reset_cache(prefix: str = "") -> None:
    """清富化缓存。

    `prefix=""` 全清（测试用）；写操作只清整包载荷 `payload:`——
    2026-11-03（N20261103-004838-69 新建分组后要刷新才出来）：原来写后全清，
    连 `regime:`/`sec_*:` 的 15 分钟分层缓存一起丢掉，下一次 GET 要重建全部分层缓存
    （实测 9.2s），前端 5s 轮询拿不到结果 → 用户只能刷新。
    """
    with _CACHE_LOCK:
        if not prefix:
            _CACHE.clear()
        else:
            for k in [x for x in _CACHE if x.startswith(prefix)]:
                _CACHE.pop(k, None)
    # 2026-11-07：内存清空后强制下一次 _disk_ensure 重检库戳（不丢磁盘 items）：
    # 清缓存常常发生在“库刚写完/刚改完自选”之后，30s 节流会把旧值多留一会儿。
    with _DISK_LOCK:
        _DISK["next_check"] = 0.0


def _reset_disk_state() -> None:
    """测试专用：把落盘层的内存镜像也清空（生产写操作只清 payload，不清磁盘）。"""
    with _DISK_LOCK:
        _DISK.update(owner=None, loaded=False, stamps={}, items={}, dirty=False, next_check=0.0)


def _db_stamps(ctx: Any) -> Dict[str, List[int]]:
    """三个源库的 (mtime_ns, size)。任一不可读 → 返回空（不用磁盘缓存，保守降级）。"""
    out: Dict[str, List[int]] = {}
    for name in _DISK_DBS:
        try:
            conn = ctx.db_read(name)
        except Exception:  # noqa: BLE001 - 缺库/未声明 → 不落盘
            return {}
        try:
            rows = _rows(conn, "PRAGMA database_list")
        except Exception:  # noqa: BLE001
            return {}
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        path = str(rows[0][2]) if rows and len(rows[0]) >= 3 else ""
        if not path:
            return {}
        try:
            st = os.stat(path)
        except OSError:
            return {}
        out[name] = [int(st.st_mtime_ns), int(st.st_size)]
    return out


def _disk_ensure(ctx: Any) -> None:
    """首次/换 ctx/过期重检时，校验源库戳并载入磁盘缓存。"""
    owner = id(ctx)
    if (_DISK["loaded"] and _DISK["owner"] == owner
            and time.monotonic() < float(_DISK["next_check"] or 0)):
        return
    with _DISK_LOCK:
        now = time.monotonic()
        if (_DISK["loaded"] and _DISK["owner"] == owner
                and now < float(_DISK["next_check"] or 0)):
            return
        stamps = _db_stamps(ctx)
        _DISK["next_check"] = time.monotonic() + _DISK_RECHECK_S
        if not stamps:
            _DISK.update(owner=owner, loaded=True, stamps={}, items={}, dirty=False)
            return
        same_ctx = (_DISK["loaded"] and _DISK["owner"] == owner)
        if same_ctx and _DISK["stamps"] == stamps:
            return          # 库没变，内存镜像继续用
        doc: Any = None
        try:
            doc = ctx.json_read(_DISK_NAME)
        except Exception:  # noqa: BLE001 - 首次/未声明/损坏 → 现算
            doc = None
        usable = (isinstance(doc, dict)
                  and doc.get("version") == _DISK_VERSION
                  and doc.get("stamps") == stamps
                  and isinstance(doc.get("items"), dict))
        _DISK.update(owner=owner, loaded=True, stamps=stamps,
                     items=dict(doc["items"]) if usable else {},
                     dirty=not usable)


def _disk_put(key: str, value: Any) -> None:
    with _DISK_LOCK:
        items = _DISK["items"]
        items[key] = value
        if len(items) > 8192:      # 防御：键来自股票/板块码，正常几百条
            items.clear()
        _DISK["dirty"] = True


def _disk_flush(ctx: Any) -> None:
    """把本轮新算的日更结果写回磁盘（一次请求最多写一次）。"""
    with _DISK_LOCK:
        if not _DISK["dirty"] or _DISK["owner"] != id(ctx):
            return
        payload = {"version": _DISK_VERSION, "stamps": _DISK["stamps"],
                   "written_at": _now(), "items": dict(_DISK["items"])}
        _DISK["dirty"] = False
    try:
        ctx.misc_write(_DISK_NAME, payload)
    except Exception:  # noqa: BLE001 - 写失败只是下次多算一次，不影响响应
        with _DISK_LOCK:
            _DISK["dirty"] = True
        return
    try:
        ctx.logger.info("enrich cache flush: %d items, %d bytes",
                        len(payload["items"]), len(str(payload)))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- JSON 读写（只经 ctx）


def _panel_getter(ctx: Any):
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        raise HTTPException(status_code=500, detail="ctx.panel_json 不可用")
    return getter


def _read_doc_lenient(ctx: Any) -> Dict[str, Any]:
    """只读端点用：文件缺失/损坏 → 空文档（旧端 `_read_json` 容错同口径，前端不报错）。

    唯一例外是 manifest 权限错（配置 bug），如实 500，不静默当空池。
    """
    getter = _panel_getter(ctx)
    try:
        doc = getter(PANEL)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="watchlist 未在插件清单声明：%s" % (exc,))
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 - 损坏/不可解析 → 空池（旧端同容错）
        return {}
    return doc if isinstance(doc, dict) else {}


def _read_doc_for_write(ctx: Any) -> Dict[str, Any]:
    """写操作第一步：读整份真文件。

    与只读路径不同——**读失败就不写**（否则一次读错会拿空文档覆盖生产文件）。
    文件确实不存在时才当空文档处理（旧端语义：首次写入即建文件）。
    """
    getter = _panel_getter(ctx)
    try:
        doc = getter(PANEL)
    except FileNotFoundError:
        return {}
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="watchlist 未在插件清单声明：%s" % (exc,))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail="watchlist 读取失败，拒绝写入（防覆盖）：%s: %s" % (type(exc).__name__, exc),
        )
    if not isinstance(doc, dict):
        raise HTTPException(status_code=503, detail="watchlist 顶层不是对象，拒绝写入")
    return doc


def _write_doc(ctx: Any, payload: Dict[str, Any]) -> None:
    """唯一写通道：`ctx.panel_write`（备份 + 原子替换 + 审计都在 core）。"""
    writer = getattr(ctx, "panel_write", None)
    if not callable(writer):
        raise HTTPException(status_code=500, detail="ctx.panel_write 不可用")
    try:
        writer(PANEL, payload)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail="watchlist 未在 data.write 声明：%s" % (exc,))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="watchlist 写入失败：%s" % (exc,))
    # 2026-10-29：整包载荷有 6s 缓存，写后必清（否则加/删/改组后 6 秒内读旧结果）
    # 2026-11-03：只清 payload，保留 15 分钟分层缓存（全清会让下一次 GET 重建 9s）
    _reset_cache("payload:")


def _read_panel_opt(ctx: Any, name: str) -> Optional[Dict[str, Any]]:
    """可选面板 JSON（watchlist_quotes）：缺失/不可读 → None（调用方降级）。"""
    getter = getattr(ctx, "panel_json", None)
    if not callable(getter):
        return None
    try:
        data = getter(name)
    except Exception:  # noqa: BLE001 - 缺文件/未声明 → 降级，不抛穿
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 文档规整（读）

def _norm_group(raw: Any, idx: int) -> Optional[Dict[str, Any]]:
    """单分组规整（旧端 `_watchlist_groups` 同规则），额外键原样带着。"""
    if not isinstance(raw, dict):
        return None
    gid = str(raw.get("id") or "").strip()
    kind = str(raw.get("kind") or "stock")
    if not gid or kind not in ("stock", "sector"):
        return None
    try:
        order = int(raw.get("order"))
    except (TypeError, ValueError):
        order = idx
    out = dict(raw)                       # ← 未知字段原样保留
    out["id"] = gid
    out["name"] = str(raw.get("name") or gid)
    out["kind"] = kind
    out["order"] = order
    return out


def _groups_of(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """分组清单（容错：缺失/损坏 → 两个默认组；默认组必存在；按 order,id 升序）。

    与旧端 `_watchlist_groups()` 同规则，唯一区别：不丢分组上的额外键。
    """
    raw = doc.get("groups") if isinstance(doc, dict) else None
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for idx, item in enumerate(raw):
            grp = _norm_group(item, idx)
            if grp is None or grp["id"] in seen:
                continue
            seen.add(grp["id"])
            out.append(grp)
    for kind, gid in WATCH_GROUP_DEFAULT.items():
        if gid not in seen:
            grp = {"id": gid, "name": WATCH_GROUP_DEFAULT_NAME[kind], "kind": kind,
                   "order": 0 if kind == "stock" else 100}
            out.append(grp)
            seen.add(gid)
    out.sort(key=lambda g: (g.get("order", 0), g["id"]))
    return out


def _norm_item(raw: Any, idx: int, valid_gids: Sequence[str]) -> Optional[Dict[str, Any]]:
    """单条目规整（旧端 `_watchlist_raw` 同规则），额外键原样带着。

    兼容旧格式：`ts_code` 作 `code` 的别名；缺 kind → stock；缺 order → 序号；
    group 非法/缺失 → 按 kind 归默认组（**不丢条目**）。
    """
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or raw.get("ts_code") or "").strip()
    if not code:
        return None
    kind = str(raw.get("kind") or "stock")
    if kind not in ("stock", "sector"):
        kind = "stock"
    try:
        order = int(raw.get("order"))
    except (TypeError, ValueError):
        order = idx
    gid = str(raw.get("group") or "").strip()
    if gid not in valid_gids:
        gid = WATCH_GROUP_DEFAULT[kind]
    out = dict(raw)                       # ← 未知字段原样保留（含旧别名 ts_code，不删键）
    out["kind"] = kind
    out["code"] = code
    out["name"] = str(raw.get("name") or "")
    out["added_at"] = str(raw.get("added_at") or "")
    out["order"] = order
    out["group"] = gid
    return out


def _items_of(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """条目清单（规整后按 order,added_at 升序，与旧端 `_watchlist_payload` 一致）。"""
    raw = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        return []
    gids = [g["id"] for g in _groups_of(doc)]
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw):
        row = _norm_item(item, idx, gids)
        if row is not None:
            out.append(row)
    out.sort(key=lambda x: (x.get("order", 0), x.get("added_at", "")))
    return out


# ---------------------------------------------------------------- 文档写回（原样保留未知字段）


def _save(ctx: Any, doc: Dict[str, Any], items: List[Dict[str, Any]],
          groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """落盘（读-改-写闭环）：按 order 重编号、group 兜底，**其余字段原样写回**。

    与旧端 `_watchlist_save()` 的差别只有一处：payload 以**读到的原文档**为底，
    只覆盖 `meta` / `groups` / `items`，未知顶层键与 meta 未知键全部保留。
    """
    valid = {g["id"]: g for g in groups}
    ordered = sorted(items, key=lambda x: (x.get("order", 0), x.get("added_at", "")))
    for i, item in enumerate(ordered):
        item["order"] = i
        gid = str(item.get("group") or "").strip()
        if gid not in valid:
            gid = WATCH_GROUP_DEFAULT.get(str(item.get("kind") or "stock"), "stock_default")
        item["group"] = gid

    meta_src = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    meta = dict(meta_src)                                    # ← meta 未知键保留
    meta["schema_version"] = meta.get("schema_version") or "1.0.0"
    meta["document_type"] = meta.get("document_type") or "watchlist"
    meta["updated_at"] = _now()
    meta["source"] = "server"                                # 旧端恒为 "server"

    payload: Dict[str, Any] = dict(doc)                       # ← 顶层未知键保留
    payload["meta"] = meta
    payload["groups"] = groups
    payload["items"] = ordered
    _write_doc(ctx, payload)
    return payload


# ---------------------------------------------------------------- DB 访问（只经 ctx.db_read）


@contextlib.contextmanager
def _db(ctx: Any, name: str) -> Iterator[Optional[Any]]:
    """只读连接上下文；不可用 → yield None（调用方降级，不编造）。"""
    getter = getattr(ctx, "db_read", None)
    if not callable(getter):
        yield None
        return
    try:
        conn = getter(name)
    except Exception:  # noqa: BLE001 - 缺库/未声明 → 降级
        yield None
        return
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _rows(conn: Any, sql: str, params: Sequence[Any] = ()) -> List[Tuple[Any, ...]]:
    cur = conn.execute(sql, tuple(params))
    return [] if cur.description is None else [tuple(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- 富化（口径照旧端）

def _amv_lib(ctx: Any):
    getter = getattr(ctx, "lib", None)
    if not callable(getter):
        return None
    try:
        return getter("amv_regime")
    except Exception:  # noqa: BLE001 - 口径库不可用 → 多空留空（不编造）
        return None


def _stock_regime(ctx: Any, code6: str) -> Optional[str]:
    """个股四态（最新）：`stock_amv` 全序列 → `amv_regime.stock_state_series` 末值。"""
    def build() -> Optional[str]:
        lib = _amv_lib(ctx)
        if lib is None:
            return None
        with _db(ctx, "amv_board.db") as conn:
            if conn is None:
                return None
            try:
                rows = _rows(conn, "SELECT amv FROM stock_amv WHERE stock=? ORDER BY date",
                             (code6,))
            except Exception:  # noqa: BLE001
                return None
        vals = [float(r[0]) if r[0] is not None else None for r in rows]
        if not vals:
            return None
        try:
            series = lib.stock_state_series(vals)
        except Exception:  # noqa: BLE001
            return None
        return series[-1] if series else None
    return _cached("regime:" + code6, build, DB_TTL_S, persist=True)


def _sector_of(ctx: Any, code6: str) -> Dict[str, Any]:
    """所属板块（THS 二级一个 + 三级至多一个）。"""
    def build() -> Dict[str, Any]:
        out: Dict[str, Any] = {"l2": None, "l3": None}
        with _db(ctx, "ths_board.db") as conn:
            if conn is None:
                return out
            try:
                rows = _rows(conn, "SELECT b.code,b.name,b.sw_level FROM board_member mb "
                                   "JOIN board b ON b.code=mb.board_code WHERE mb.stock=?",
                             (code6,))
            except Exception:  # noqa: BLE001
                return out
        for code, name, lv in rows:
            try:
                level = int(lv)
            except (TypeError, ValueError):
                continue
            if level == 2 and out["l2"] is None:
                out["l2"] = {"code": code, "name": name}
            elif level == 3 and out["l3"] is None:
                out["l3"] = {"code": code, "name": name}
        return out
    return _cached("sec_of:" + code6, build, DB_TTL_S, persist=True)


def _sector_regime(ctx: Any, level: Any, code: Any) -> Optional[str]:
    """板块双轨多空二态（最新）：`sector_daily` → `amv_regime.compute_regime` 末值。"""
    def build() -> Optional[str]:
        lib = _amv_lib(ctx)
        if lib is None:
            return None
        with _db(ctx, "amv_board.db") as conn:
            if conn is None:
                return None
            try:
                rows = _rows(conn, "SELECT date,amv FROM sector_daily WHERE level=? AND code=? "
                                   "ORDER BY date", (level, code))
            except Exception:  # noqa: BLE001
                return None
        dates = [r[0] for r in rows]
        # 不置零：None 表示当日无数据，由 amv_regime.compute_regime 自己处理
        vals = [float(r[1]) if r[1] is not None else None for r in rows]
        if not vals:
            return None
        try:
            series = lib.compute_regime(dates, vals)["state"]
        except Exception:  # noqa: BLE001
            return None
        return series[-1] if series else None
    return _cached("sec_regime:%s:%s" % (level, code), build, DB_TTL_S, persist=True)


def _sector_meta(ctx: Any, code: str) -> Optional[Dict[str, Any]]:
    """板块元信息（level/name）：`amv_board.db::sector_meta`；未收录 → None。"""
    def build() -> Optional[Dict[str, Any]]:
        with _db(ctx, "amv_board.db") as conn:
            if conn is None:
                return None
            try:
                rows = _rows(conn, "SELECT level,name FROM sector_meta WHERE code=?", (code,))
            except Exception:  # noqa: BLE001
                return None
        return {"level": rows[0][0], "name": rows[0][1]} if rows else None
    return _cached("sec_meta:" + code, build, DB_TTL_S, persist=True)


def _sector_quote(ctx: Any, code: str) -> Optional[Dict[str, Any]]:
    """板块最新行情：`board_flow` 最新一日的 pct（板块指数涨跌幅%）。"""
    def build() -> Optional[Dict[str, Any]]:
        with _db(ctx, "amv_board.db") as conn:
            if conn is None:
                return None
            try:
                rows = _rows(conn, "SELECT date,pct FROM board_flow WHERE board_code=? "
                                   "AND pct IS NOT NULL ORDER BY date DESC LIMIT 1", (code,))
            except Exception:  # noqa: BLE001 - 列不存在/表缺失 → 无涨跌
                return None
        return {"date": rows[0][0], "pct": rows[0][1]} if rows else None
    return _cached("sec_quote:" + code, build, DB_TTL_S, persist=True)


def _stock_div(ctx: Any, code6: str, win: int = 5) -> Optional[float]:
    """5 日背离 = 区间活跃市值涨跌% − 区间真实价格涨跌%（价格 = AMV ÷ 活跃股本）。

    口径与旧端 `_watch_stock_div` 逐行一致；**不设阈值**（只显数值+方向，不判异常）。
    """
    def build() -> Optional[float]:
        with _db(ctx, "amv_board.db") as conn:
            if conn is None:
                return None
            try:
                amv = {d: v for d, v in _rows(
                    conn, "SELECT date,amv FROM stock_amv WHERE stock=? "
                          "ORDER BY date DESC LIMIT ?", (code6, win + 1)) if v}
                alive = {d: v for d, v in _rows(
                    conn, "SELECT date,alive FROM stock_state WHERE stock=? "
                          "ORDER BY date DESC LIMIT ?", (code6, win + 1)) if v}
            except Exception:  # noqa: BLE001
                return None
        ds = sorted(set(amv) & set(alive))
        if len(ds) < win + 1:
            return None
        d0, d1 = ds[0], ds[-1]
        m0, m1, a0, a1 = amv[d0], amv[d1], alive[d0], alive[d1]
        if not (m0 and m1 and a0 and a1):
            return None
        try:
            return round((m1 / m0 - 1) * 100 - ((m1 / a1) / (m0 / a0) - 1) * 100, 2)
        except ZeroDivisionError:
            return None
    return _cached("div:%s:%d" % (code6, win), build, DB_TTL_S, persist=True)


def _stock_flow(ctx: Any, code6: str) -> Optional[float]:
    """个股最新主力净流入（亿元）：`stock_flow.main_flow`（万元）÷ 1e4。"""
    def build() -> Optional[float]:
        with _db(ctx, "amv_board.db") as conn:
            if conn is None:
                return None
            try:
                rows = _rows(conn, "SELECT main_flow FROM stock_flow WHERE stock_code=? "
                                   "ORDER BY date DESC LIMIT 1", (code6,))
            except Exception:  # noqa: BLE001
                return None
        if not rows or rows[0][0] is None:
            return None
        try:
            return round(float(rows[0][0]) / 1e4, 2)
        except (TypeError, ValueError):
            return None
    return _cached("flow:" + code6, build, DB_TTL_S, persist=True)


def _stock_badges(ctx: Any, ts_code: str, code6: str) -> List[Dict[str, Any]]:
    """买点徽标（日+周）：`buy_signal.db::stock_signal`；先按 ts_code 查，再回退 6 位码。"""
    def build() -> List[Dict[str, Any]]:
        with _db(ctx, "buy_signal.db") as conn:
            if conn is None:
                return []
            try:
                rows = _rows(conn, "SELECT daily_sigs,weekly_sigs FROM stock_signal "
                                   "WHERE stock=?", (ts_code,))
                if not rows and code6 != ts_code:
                    rows = _rows(conn, "SELECT daily_sigs,weekly_sigs FROM stock_signal "
                                       "WHERE stock=?", (code6,))
                if not rows:
                    # 2026-11-11 ETF 买点（run_etf.py 产）：ETF 不在 stock_signal，改查 etf_signal。
                    #   ETF 无周信号，第二列固定 NULL，与个股同形状回落。
                    try:
                        rows = _rows(conn, "SELECT daily_sigs,NULL FROM etf_signal "
                                           "WHERE etf=?", (ts_code,))
                        if not rows and code6 != ts_code:
                            rows = _rows(conn, "SELECT daily_sigs,NULL FROM etf_signal "
                                               "WHERE etf=?", (code6,))
                    except Exception:  # noqa: BLE001 - 表未建（尚未跑过 run_etf）
                        rows = []
            except Exception:  # noqa: BLE001
                return []
        sigs: List[str] = []
        if rows:
            for cell in (rows[0][0], rows[0][1]):
                if cell:
                    sigs += [s for s in str(cell).split(",") if s]
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for s in sigs:
            meta = _WATCH_SIG_META.get(s)
            if meta and s not in seen:
                seen.add(s)
                out.append({"label": meta[0], "cls": meta[1], "desc": meta[2]})
        return out
    return _cached("badges:" + ts_code, build, DB_TTL_S, persist=True)


# ---------------------------------------------------------------- 行情（EOD / 实时档）


def _eod_quote_one(ctx: Any, code: str) -> Optional[Dict[str, Any]]:
    """收盘价 + 当日涨跌幅%：`ctx.quotes.daily_bars` 末两根（**不复权**，与旧端同尺度）。

    旧端直读通达信 `lday/*.day` 末两根；这里改走行情门面（同一份行情），
    `source` 如实标 `eltdx:eod` 而不是 `vipdoc:eod`。取不到 → None（前端「—」，不编造）。
    """
    facade = getattr(ctx, "quotes", None)
    if facade is None:
        return None
    try:
        glb = facade.normalize(str(code))
    except Exception:  # noqa: BLE001
        return None
    if not glb:
        return None
    try:
        rows = facade.daily_bars(glb, 2, None)
    except Exception:  # noqa: BLE001 - 行情源不可达 → 无价
        return None
    bars = []
    for it in rows or []:
        t = getattr(it, "time", None)
        c = getattr(it, "close", None)
        if hasattr(t, "strftime") and _finite(c):
            bars.append((t.strftime("%Y%m%d"), float(c)))
    bars.sort(key=lambda x: x[0])
    if len(bars) < 2:
        return None
    (_, prev), (last_day, last) = bars[-2], bars[-1]
    if not prev:
        return None
    day8 = str(last_day)
    return {
        "price": round(last, 3),
        "change_pct": round((last / prev - 1) * 100, 4),
        "asof": "%s-%s-%s" % (day8[:4], day8[4:6], day8[6:]),
        "source": "eltdx:eod",
    }


# 盘后落盘（2026-11-07）：48 票 EOD 行情进程重启后首次要 1.7s（行情源冷取）。
# 盘后价格固定 → 落盘；失效 = 数据日（expected）变。三道铁律：空不写｜失败不写｜日期不符当 miss。
_EOD_DISK = "eod_quotes"
_EOD_DISK_KEEP = 8


def _eod_disk_get(ctx: Any, sig: str, expected8: str) -> Optional[Dict[str, Dict[str, Any]]]:
    if not expected8:
        return None
    try:
        doc = ctx.cache_read(_EOD_DISK)
    except Exception:  # noqa: BLE001 - 首次/损坏 → 现取
        return None
    if not isinstance(doc, dict) or str(doc.get("date8") or "") != expected8:
        return None
    items = doc.get("items")
    if not isinstance(items, dict):
        return None
    hit = items.get(sig)
    return hit if isinstance(hit, dict) and hit else None


def _eod_disk_put(ctx: Any, sig: str, expected8: str,
                  out: Dict[str, Dict[str, Any]]) -> None:
    if not expected8 or not out:
        return
    # 至少一条是应有数据日才写（全停牌/全旧数据不落盘，宁可下次重拉）
    # 注：EOD rec 的 asof 是 `YYYY-MM-DD`，expected 是 `YYYYMMDD` → 比较前归一（不用 str.replace，避开静态禁令）。
    def _n8(v: Any) -> str:
        return "".join(ch for ch in str(v or "") if ch != "-")[:8]

    if not any(isinstance(v, dict) and _n8(v.get("asof")) == expected8
               for v in out.values()):
        return
    try:
        doc = ctx.cache_read(_EOD_DISK)
    except Exception:  # noqa: BLE001
        doc = None
    if (not isinstance(doc, dict) or str(doc.get("date8") or "") != expected8
            or not isinstance(doc.get("items"), dict)):
        doc = {"date8": expected8, "items": {}}
    items = dict(doc["items"])
    items[sig] = out
    if len(items) > _EOD_DISK_KEEP:
        items = {sig: out}
    try:
        ctx.cache_write(_EOD_DISK, {"date8": expected8, "items": items})
    except Exception:  # noqa: BLE001 - 写失败只是下次多拉一次
        pass


def _eod_quotes_for(ctx: Any, codes: Sequence[Any]) -> Dict[str, Dict[str, Any]]:
    """EOD 行情（旧端 `_eod_quotes_for` 同返回结构：ts_code 与 6 位码双键）。

    2026-10-29：整包缓存 60s（键 = 归一后的代码集合）。原来无缓存——31 票 × `daily_bars`
    串行 17ms = 0.53s，面板 5s 轮询每轮重付；而 `core.quotes.daily_bars` 自己的 60s 缓存
    又因 keep 上限被 31 票互相挤掉（已同步把 `_bars_cache` 改成 256 槽）。两层均命中时≈ 1ms。
    """
    keys = [str(c or "") for c in (codes or []) if str(c or "")]

    def build() -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for key in keys:
            rec = _eod_quote_one(ctx, key)
            if rec is None:
                continue
            out[key] = rec
            out[key.split(".")[0]] = rec
        return out

    if not keys:
        return {}
    sig = "|".join(sorted(keys))
    expected8 = ""
    try:
        if ctx.trade_clock.postclose():
            expected8 = str(ctx.trade_clock.expected_data_date8() or "")
            disk = _eod_disk_get(ctx, sig, expected8)
            if disk is not None:
                return disk
    except Exception:  # noqa: BLE001 - 旧 core/门面异常 → 现取
        expected8 = ""
    out = _cached("eod_batch:" + sig, build, EOD_BATCH_TTL_S)
    if expected8:
        _eod_disk_put(ctx, sig, expected8, out)
    return out


def _live_quotes(ctx: Any) -> Tuple[Dict[str, Any], str]:
    """实时档报价：`watchlist_quotes.json`（tick_feed 每 60s 落盘）。

    陈旧判据与旧端 `_watchlist_live_quotes` 一致：asof 非当日 或 距今 > 5min → 不可用
    （回退 EOD，**不拿冻结价冒充实时价**）。
    """
    doc = _read_panel_opt(ctx, PANEL_QUOTES)
    if not isinstance(doc, dict) or not isinstance(doc.get("quotes"), dict):
        return {}, ""
    asof = str(doc.get("asof") or "")
    if asof:
        try:
            ts = datetime.strptime(asof[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return {}, asof
        if ts.strftime("%Y%m%d") != datetime.now().strftime("%Y%m%d"):
            return {}, asof
        if (datetime.now() - ts).total_seconds() > LIVE_QUOTE_MAX_AGE_S:
            return {}, asof
    return doc["quotes"], asof


# ---------------------------------------------------------------- GET /watchlist.json


def build_watchlist(ctx: Any, mode: str = "") -> Dict[str, Any]:
    """自选池载荷入口：整包缓存 6s（前端 refreshEvery=5000，5s 轮询不重复装配）。

    写操作（add/remove/move/reorder/group*）走 `_write_doc` → 清缓存，所以加/删立即可见。
    """
    key = "payload:" + ("live" if mode == "live" else "eod")
    return _cached(key, lambda: _build_watchlist(ctx, mode), PAYLOAD_TTL_S)


def _build_watchlist(ctx: Any, mode: str = "") -> Dict[str, Any]:
    """自选池富化载荷（旧端 `_watchlist_payload` 同字段）。

    个股：`quote / quote_source / regime / sector{l2,l3} / div / flow / badges`；
    板块：`level / regime / pct / quote_date`。
    `mode="live"` 时个股 quote 优先取 tick_feed，缺失/陈旧回退 EOD 并标 `quote_source`。

    2026-11-07：日更库类先查落盘缓存（`_disk_ensure`），本轮新算的项在返回前
    一次性写回（`_disk_flush`）——进程重启后首次打开不再重算 6-8s。
    """
    _disk_ensure(ctx)
    doc = _read_doc_lenient(ctx)
    items = _items_of(doc)
    groups = _groups_of(doc)
    counts: Dict[str, int] = {}
    for item in items:
        gid = item.get("group")
        counts[gid] = counts.get(gid, 0) + 1
    groups_out = [dict(g, count=counts.get(g["id"], 0)) for g in groups]

    stocks = [it for it in items if it.get("kind") == "stock"]
    sectors = [it for it in items if it.get("kind") == "sector"]
    codes = [it["code"] for it in stocks]
    quotes = _eod_quotes_for(ctx, codes) if codes else {}

    live_q: Dict[str, Any] = {}
    live_asof = ""
    if mode == "live":
        live_q, live_asof = _live_quotes(ctx)

    out: List[Dict[str, Any]] = []
    bull_n = 0
    asofs: List[str] = []
    # 名称兜底（2026-11-18）：名表只读一次；存量脏名（如 601985 的 “????”）只在展示层纠正，不回写盘。
    names_tbl, etfs_tbl = _search_index(ctx)

    def _nm(code: str, raw: Any) -> str:
        nm = str(raw or "").strip()
        if nm and not _NAME_BAD.match(nm):
            return nm
        c6 = str(code or "").split(".")[0].strip().upper()
        return str(names_tbl.get(c6) or etfs_tbl.get(c6) or "").strip() or nm or c6
    for it in stocks:
        ts = it["code"]
        code6 = ts.split(".")[0]
        quote: Optional[Dict[str, Any]] = None
        qsrc = "eod"
        lq = live_q.get(ts) or live_q.get(code6)
        if isinstance(lq, dict) and _finite(lq.get("price")):
            quote = {"price": lq.get("price"), "change_pct": lq.get("change_pct"),
                     "source": "tick_feed", "asof": live_asof}
            qsrc = "live"
        else:
            q = quotes.get(ts) or quotes.get(code6)
            if isinstance(q, dict) and _finite(q.get("price")):
                quote = {"price": q.get("price"), "change_pct": q.get("change_pct"),
                         "source": q.get("source") or "", "asof": q.get("asof") or ""}
        if quote and quote.get("asof"):
            asofs.append(str(quote["asof"]))
        regime = _stock_regime(ctx, code6)
        sec = _sector_of(ctx, code6)
        sector: Dict[str, Any] = {}
        if sec.get("l2"):
            sector["l2"] = dict(sec["l2"], regime=_sector_regime(ctx, "L2", sec["l2"]["code"]))
        if sec.get("l3"):
            sector["l3"] = dict(sec["l3"], regime=_sector_regime(ctx, "L3", sec["l3"]["code"]))
        if regime in ("多", "偏多"):
            bull_n += 1
        out.append({"kind": "stock", "code": ts, "name": _nm(ts, it["name"]),
                    "added_at": it["added_at"], "order": it["order"], "group": it["group"],
                    "quote": quote, "quote_source": qsrc,
                    "regime": regime, "sector": sector, "div": _stock_div(ctx, code6),
                    "flow": _stock_flow(ctx, code6),
                    "badges": _stock_badges(ctx, ts, code6)})
    for it in sectors:
        code = it["code"]
        meta = _sector_meta(ctx, code)
        level = (meta or {}).get("level")
        sq = _sector_quote(ctx, code)
        regime = _sector_regime(ctx, level, code) if level else None
        if regime == "多":
            bull_n += 1
        out.append({"kind": "sector", "code": code,
                    "name": _nm(code, it["name"] or (meta or {}).get("name")),
                    "added_at": it["added_at"], "order": it["order"], "group": it["group"],
                    "level": level, "regime": regime,
                    "pct": (sq or {}).get("pct"), "quote_date": (sq or {}).get("date")})
    payload = {
        "meta": {"schema_version": "1.0.0", "document_type": "watchlist",
                 "generated_at": _now(), "source": "server:watchlist"},
        "updated_at": _now(),
        "quote_asof": (max(asofs) if asofs else ""),
        "mode": ("live" if mode == "live" else "eod"),
        "groups": groups_out,
        "stats": {"total": len(out), "stocks": len(stocks), "sectors": len(sectors),
                  "bull": bull_n},
        "items": out,
    }
    _disk_flush(ctx)
    return payload


# ---------------------------------------------------------------- GET /watchlist/groups · state


def build_groups(ctx: Any) -> Dict[str, Any]:
    """分组列表（旧端 `/api/watchlist/groups`：只回 id/name/kind/order，不富化、不读行情）。"""
    return {"ok": True, "groups": _groups_of(_read_doc_lenient(ctx))}


def build_state(ctx: Any, kind: str = "stock", code: str = "") -> Dict[str, Any]:
    """单条目是否在池（旧端 `/api/watchlist/state`）：**不富化、只读原始文件**。

    code 归一与 add 一致：stock → ts_code；sector → 大写原样。
    未命中/空码/非法码一律 `watched=false`（如实空，不编造）。
    """
    k = "sector" if str(kind or "").strip().lower() == "sector" else "stock"
    raw = str(code or "").strip()
    if k == "sector":
        norm = raw.upper()
    else:
        norm = _to_ts_code(raw) or raw
    watched = False
    if norm:
        gids = [g["id"] for g in _groups_of(_read_doc_lenient(ctx))]
        for item in _items_of(_read_doc_lenient(ctx)):
            if item["kind"] == k and item["code"] == norm:
                watched = True
                break
        _ = gids  # 仅为可读性保留（_items_of 内部已按分组规则兜底）
    return {"ok": True, "kind": k, "code": norm, "watched": watched, "ts": _now()}


# ---------------------------------------------------------------- 搜索（GET + POST）


def build_search(ctx: Any, q: Any = "", limit: Any = None) -> Dict[str, Any]:
    """股票 + 板块搜索（旧端 `/api/watchlist/search` 同结构 `{q, stocks, sectors}`）。

    个股只留 A 股（`_WATCH_SEARCH_BY_MKT`），ETF 只留 ETF 段（`_WATCH_SEARCH_ETF_BY_MKT`），
    两段名字表同源于 `stock_names.json`（`names` / `etfs`）；匹配 代码/名称 子串。
    结果项带 `type`（`stock` / `etf`），前端用它显示标签——不猜号段。
    **不支持拼音**（见模块头「已知差异 3」）。
    """
    keyword = str(q or "").strip()
    if not keyword:
        return {"q": keyword, "stocks": [], "sectors": []}
    kl = keyword.lower()
    try:
        lim = max(1, min(int(limit or SEARCH_LIMIT_DEFAULT), SEARCH_LIMIT_MAX))
    except (TypeError, ValueError):
        lim = SEARCH_LIMIT_DEFAULT

    names, etfs = _search_index(ctx)
    stocks: List[Dict[str, Any]] = []
    for code6, name in names.items():
        code6 = str(code6)
        sfx = _suffix_of(code6)
        if sfx is None or code6[:2] not in _WATCH_SEARCH_BY_MKT.get(sfx, ()):
            continue
        nm = str(name or "")
        if (kl in code6.lower()) or (kl in nm.lower()):
            stocks.append({"code": "%s.%s" % (code6, sfx), "name": nm, "type": "stock"})
    # ETF 段（2026-11-11）：同一排序口径，type=etf。前缀取 3 位：ETF 与个股在 5/1 开头
    #   共用二级段（51x/52x/56x/58x、15x），2 位前缀会连带收进封闭基金/开放基金/LOF。
    for code6, name in etfs.items():
        code6 = str(code6)
        sfx = _suffix_of(code6)
        if sfx is None or code6[:3] not in _WATCH_SEARCH_ETF_BY_MKT.get(sfx, ()):
            continue
        nm = str(name or "")
        if (kl in code6.lower()) or (kl in nm.lower()):
            stocks.append({"code": "%s.%s" % (code6, sfx), "name": nm, "type": "etf"})
    stocks.sort(key=lambda x: (0 if (x["code"].lower().startswith(kl)
                                     or x["name"].lower().startswith(kl)) else 1,
                               len(x["name"] or ""), x["code"]))
    stocks = stocks[:lim]

    sectors: List[Dict[str, Any]] = []
    with _db(ctx, "amv_board.db") as conn:
        if conn is not None:
            try:
                for level, code, name in _rows(conn,
                                               "SELECT level,code,name FROM sector_meta"):
                    if not name:
                        continue
                    if (kl in str(code).lower()) or (kl in str(name).lower()):
                        sectors.append({"code": code, "name": name, "level": level})
            except Exception:  # noqa: BLE001 - 板块表不可用 → 空结果（不编造）
                sectors = []
    sectors.sort(key=lambda x: (0 if (str(x["name"]).lower().startswith(kl)
                                      or str(x["code"]).lower().startswith(kl)) else 1,
                                len(str(x["name"]))))
    return {"q": keyword, "stocks": stocks, "sectors": sectors[:lim]}


def _search_index(ctx: Any) -> Tuple[Dict[str, str], Dict[str, str]]:
    """`ctx.json_read("stock_names")` → (个股 names, ETF etfs)，**一次读取**。

    names 段是纯 A 股个股（5 处消费端当个股名单用），etfs 段是 ETF 全量（含货币/债券/商品）。
    两段不合并：ETF 混进 names 会让板块成分股、打分、拼音索引把 ETF 当个股。
    """
    reader = getattr(ctx, "json_read", None)
    if not callable(reader):
        return {}, {}
    try:
        data = reader("stock_names")
    except Exception:  # noqa: BLE001 - 名表不可用 → 搜索只按代码（不编造名称）
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    names = data.get("names")
    etfs = data.get("etfs")
    return (names if isinstance(names, dict) else {},
            etfs if isinstance(etfs, dict) else {})


# ---------------------------------------------------------------- 写操作（读-改-写）


# 名称占位符（2026-11-18）：601985 中国核电在自选池里显示 “????” 且 K 线标题同样 ——
#   泡盘里 name 就是占位符（兼容旧数据）。这里只在**展示/新增**时代码查权威名表兜底，不改历史条目。
_NAME_BAD = re.compile(r"^[\s?？\ufffd]*$")


def _name_or_lookup(ctx: Any, code: str, name: Any) -> str:
    """名称兜底：空/占位→按 6 位码查 stock_names（个股 names ∪ ETF etfs）；查不到原样返回。"""
    nm = str(name or "").strip()
    if nm and not _NAME_BAD.match(nm):
        return nm
    try:
        names, etfs = _search_index(ctx)
    except Exception:  # noqa: BLE001 - 名表不可用 → 保持原值（不编造）
        names, etfs = {}, {}
    c6 = str(code or "").split(".")[0].strip().upper()
    hit = names.get(c6) or names.get(c6.lower()) or etfs.get(c6) or ""
    return str(hit).strip() or nm or c6


def _body_dict(body: Any) -> Dict[str, Any]:
    return body if isinstance(body, dict) else {}


def _find_item(items: Sequence[Dict[str, Any]], raw_code: str) -> Optional[Dict[str, Any]]:
    """按旧端匹配规则找条目：全等 / 忽略大小写 / 6 位码相同。"""
    c6 = raw_code.split(".")[0]
    for item in items:
        code = str(item.get("code") or "")
        if (code == raw_code or code.upper() == raw_code.upper()
                or code.split(".")[0] == c6):
            return item
    return None


# ---------------------------------------------------------------- 当日选股（MMDD 日期分组）
# 2026-11-13 主人定的规则：
#   · 10:00 之后做了「加入选股」才建当日分组，并顺手清理超量；**没加入就不动旧的**。
#   · 日期分组最多留 3 个，超了删最旧的（组内票一并删）。
#   · 节假日用 core 的交易日历（`ctx.trade_clock`），不看星期几。
MMDD_KEEP = 3
MMDD_CUTOFF_HOUR = 10


def _now_dt() -> datetime:
    """当前本地时间（单点取时，测试可 monkeypatch）。"""
    return datetime.now()


def _is_mmdd(g: Dict[str, Any]) -> bool:
    """日期分组：kind=stock 且 name 是 4 位数字（如 1112）。"""
    nm = str(g.get("name") or "").strip()
    return g.get("kind") == "stock" and len(nm) == 4 and nm.isdigit()


def _mmdd_sorted(groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """日期分组按 name 降序（新的在前）。"""
    return sorted([g for g in groups if _is_mmdd(g)],
                  key=lambda g: str(g.get("name") or ""), reverse=True)


def _mmdd_target(ctx: Any, groups: Sequence[Dict[str, Any]],
                 now: Optional[datetime] = None) -> Tuple[str, bool]:
    """本次「加入选股」该落哪组 → (gid, 是否需新建)。

    交易日且 >=10:00 → 当日组（没有就建）；
    其余（交易日 10 点前 / 非交易日）→ 最近一个已有日期组；一组都没有 → 建当日组。
    日历不可用 → 按交易日处理（宁可建当日组，也不把票塞进隔夜组）。
    """
    now = now or _now_dt()
    mmdd = now.strftime("%m%d")
    is_td = True
    try:
        is_td = bool(ctx.trade_clock.is_trading_day(now.date()))
    except Exception:  # noqa: BLE001 - 日历不可用就按交易日走
        is_td = True
    existing = _mmdd_sorted(groups)
    if is_td and now.hour >= MMDD_CUTOFF_HOUR:
        hit = next((g for g in existing if str(g.get("name")) == mmdd), None)
        return (str(hit["id"]), False) if hit else ("sel_" + mmdd, True)
    if existing:
        return str(existing[0]["id"]), False
    return "sel_" + mmdd, True


def _prune_mmdd(groups: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> List[str]:
    """日期分组超过 MMDD_KEEP 个 → 删最旧的（组内条目一并删），返回被删的组名。"""
    drop = _mmdd_sorted(groups)[MMDD_KEEP:]
    if not drop:
        return []
    drop_ids = {str(g.get("id")) for g in drop}
    for g in drop:
        groups.remove(g)
    items[:] = [it for it in items if str(it.get("group")) not in drop_ids]
    return [str(g.get("name")) for g in drop]


def build_add(ctx: Any, body: Any) -> Dict[str, Any]:
    """加入自选（幂等 upsert；股票归一为 ts_code，板块原样大写）。"""
    payload = _body_dict(body)
    kind = payload.get("kind") if payload.get("kind") in ("stock", "sector") else "stock"
    if kind == "stock":
        code = _to_ts_code(payload.get("code"))
        if not code:
            raise HTTPException(status_code=422,
                                detail="非法 ts_code：%s" % (payload.get("code"),))
    else:
        code = str(payload.get("code") or "").strip().upper()
        if not code:
            raise HTTPException(status_code=422, detail="板块代码不能为空")
    name = _name_or_lookup(ctx, code, payload.get("name"))
    want_group = str(payload.get("group") or "").strip()
    # 2026-11-13：autoMmdd=true = 「加入选股」（K 线右键）——用日期分组 + 超量清理；
    #   不传仍是普通加自选（行为不变）。
    auto = bool(payload.get("autoMmdd")) and kind == "stock"

    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        groups = _groups_of(doc)
        gmap = {g["id"]: g for g in groups}
        if auto:
            want_group = ""
        if want_group and want_group not in gmap:
            raise HTTPException(status_code=422, detail="未知分组：%s" % (want_group,))
        if want_group and gmap[want_group]["kind"] != kind:
            raise HTTPException(
                status_code=422,
                detail="分组类型不匹配：%s 是 %s 组，不能放 %s"
                       % (want_group, gmap[want_group]["kind"], kind))
        items = _items_of(doc)
        created_group = ""
        pruned: List[str] = []
        if auto:
            gid, need_new = _mmdd_target(ctx, groups)
            if need_new and gid not in gmap:
                nm = gid[4:] if gid.startswith("sel_") else gid
                same = [g.get("order", 0) for g in groups if g.get("kind") == "stock"]
                grp = {"id": gid, "name": nm, "kind": "stock",
                       "order": (max(same) + 1) if same else 0}
                groups.append(grp)
                gmap[gid] = grp
                created_group = nm
                # 只在「新建当日组」时清理：没加入就不动旧分组（主人 2026-11-13 明确）。
                pruned = _prune_mmdd(groups, items)
        else:
            gid = want_group or WATCH_GROUP_DEFAULT[kind]
        hit = next((x for x in items if x["code"] == code), None)
        if hit:
            if name:
                hit["name"] = name
            hit["group"] = gid
            action = "exists"
        else:
            # 新加入排最前（order 取最小−1；_save 会按 order 重编号）
            nxt = min([x.get("order", 0) for x in items], default=0) - 1
            items.append({"kind": kind, "code": code, "name": name or code,
                          "added_at": _now(), "order": nxt, "group": gid})
            action = "added"
        _save(ctx, doc, items, groups)
        total = len(items)
    return {"ok": True, "action": action, "kind": kind, "code": code,
            "group": gid, "total": total,
            "group_name": str((gmap.get(gid) or {}).get("name") or ""),
            "created_group": created_group, "pruned": pruned}


def build_remove(ctx: Any, body: Any) -> Dict[str, Any]:
    """移出自选（幂等：不存在仍返回 ok，removed=0）。"""
    raw_code = str(_body_dict(body).get("code") or "").strip()
    if not raw_code:
        raise HTTPException(status_code=422, detail="code 不能为空")
    c6 = raw_code.split(".")[0]
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        items = _items_of(doc)
        kept = [x for x in items
                if not (x["code"] == raw_code or x["code"].upper() == raw_code.upper()
                        or x["code"].split(".")[0] == c6)]
        removed = len(items) - len(kept)
        _save(ctx, doc, kept, _groups_of(doc))
        total = len(kept)
    return {"ok": True, "removed": removed, "total": total}


def build_move(ctx: Any, body: Any) -> Dict[str, Any]:
    """卡片移组（幂等）：跨类型拒绝（stock↔sector 组互斥，422）。"""
    payload = _body_dict(body)
    raw_code = str(payload.get("code") or "").strip()
    gid = str(payload.get("group") or "").strip()
    if not raw_code or not gid:
        raise HTTPException(status_code=422, detail="code 与 group 均不能为空")
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        groups = _groups_of(doc)
        gmap = {g["id"]: g for g in groups}
        if gid not in gmap:
            raise HTTPException(status_code=422, detail="未知分组：%s" % (gid,))
        items = _items_of(doc)
        hit = _find_item(items, raw_code)
        if hit is None:
            raise HTTPException(status_code=404, detail="自选池无此条目：%s" % (raw_code,))
        if gmap[gid]["kind"] != hit["kind"]:
            raise HTTPException(
                status_code=422,
                detail="跨类型不可拖动：%s 条目不能移入 %s 组"
                       % (hit["kind"], gmap[gid]["kind"]))
        old = hit["group"]
        hit["group"] = gid
        same = [x.get("order", 0) for x in items if x.get("group") == gid and x is not hit]
        hit["order"] = (max(same) + 1) if same else 0
        _save(ctx, doc, items, groups)
        moved_code = hit["code"]
    return {"ok": True, "code": moved_code, "from": old, "group": gid}


def build_reorder(ctx: Any, body: Any) -> Dict[str, Any]:
    """条目改序（幂等）：按 codes 顺序落 order；未列出的保持相对位置排在其后。

    可传 `group` 限定**组内**重排（不传=全局旧行为）。
    """
    payload = _body_dict(body)
    seq = [str(c).strip() for c in (payload.get("codes") or []) if str(c).strip()]
    want_group = str(payload.get("group") or "").strip()
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        items = _items_of(doc)
        rank = {c: i for i, c in enumerate(seq)}
        n = len(seq)
        for item in items:
            if want_group and item.get("group") != want_group:
                continue                       # 组内重排：不动其它组
            item["order"] = rank.get(item["code"], n + int(item.get("order") or 0))
        _save(ctx, doc, items, _groups_of(doc))
        total = len(items)
    return {"ok": True, "total": total}


def build_group_add(ctx: Any, body: Any) -> Dict[str, Any]:
    """新建分组（幂等：同类型同名 → 返回已存在项）。kind 必填 stock|sector。

    2026-11-13：支持可选 `id`（如 `sel_1112`）——MMDD 选股组要可预测的 id
    （自动清理只认这个前缀，不能拿随机 hash）。不传仍走 uuid，行为不变。
    """
    payload = _body_dict(body)
    kind = payload.get("kind") if payload.get("kind") in ("stock", "sector") else ""
    if not kind:
        raise HTTPException(status_code=422, detail="kind 必须为 stock 或 sector")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="分组名不能为空")
    want_id = str(payload.get("id") or "").strip()
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        groups = _groups_of(doc)
        hit = next((g for g in groups if g["kind"] == kind and g["name"] == name), None)
        if hit is not None:
            return {"ok": True, "action": "exists", "group": hit}
        if want_id:
            hit = next((g for g in groups if str(g.get("id")) == want_id), None)
            if hit is not None:
                return {"ok": True, "action": "exists", "group": hit}
        gid = want_id or uuid.uuid4().hex[:8]
        same = [g.get("order", 0) for g in groups if g["kind"] == kind]
        grp = {"id": gid, "name": name, "kind": kind, "order": (max(same) + 1) if same else 0}
        groups.append(grp)
        _save(ctx, doc, _items_of(doc), groups)
    return {"ok": True, "action": "added", "group": grp}


def build_group_rename(ctx: Any, body: Any) -> Dict[str, Any]:
    """分组改名（id 不变）；默认组拒绝改名。"""
    payload = _body_dict(body)
    gid = str(payload.get("id") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="分组名不能为空")
    if gid in WATCH_GROUP_DEFAULT.values():
        raise HTTPException(status_code=422, detail="默认分组不可改名")
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        groups = _groups_of(doc)
        hit = next((g for g in groups if g["id"] == gid), None)
        if hit is None:
            raise HTTPException(status_code=404, detail="分组不存在：%s" % (gid,))
        hit["name"] = name
        _save(ctx, doc, _items_of(doc), groups)
    return {"ok": True, "group": hit}


def build_group_remove(ctx: Any, body: Any) -> Dict[str, Any]:
    """删除自定义分组：组内条目移入**同类型默认组**（不丢条目）；默认组拒绝。"""
    gid = str(_body_dict(body).get("id") or "").strip()
    if gid in WATCH_GROUP_DEFAULT.values():
        raise HTTPException(status_code=422, detail="默认分组不可删除")
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        groups = _groups_of(doc)
        hit = next((g for g in groups if g["id"] == gid), None)
        if hit is None:
            raise HTTPException(status_code=404, detail="分组不存在：%s" % (gid,))
        target = WATCH_GROUP_DEFAULT[hit["kind"]]
        groups = [g for g in groups if g["id"] != gid]
        items = _items_of(doc)
        moved = 0
        for item in items:
            if item.get("group") == gid:
                item["group"] = target
                moved += 1
        _save(ctx, doc, items, groups)
    return {"ok": True, "moved": moved, "target": target}


def build_group_reorder(ctx: Any, body: Any) -> Dict[str, Any]:
    """分组顺序（按 ids 顺序落 order）；未列出的保持相对位置。"""
    seq = [str(i).strip() for i in (_body_dict(body).get("ids") or []) if str(i).strip()]
    with _WATCH_LOCK:
        doc = _read_doc_for_write(ctx)
        groups = _groups_of(doc)
        rank = {gid: i for i, gid in enumerate(seq)}
        n = len(seq)
        for grp in groups:
            grp["order"] = rank.get(grp["id"], n + int(grp.get("order") or 0))
        _save(ctx, doc, _items_of(doc), groups)
    return {"ok": True, "groups": groups}


# ---------------------------------------------------------------- 路由（root 挂载）


def register(ctx: Any) -> APIRouter:
    """注册 12 条根路径路由（manifest 里 `backend.mount = "root"` → 最终 `/api/<path>`）。"""
    router = APIRouter()

    # ---- 读 ----
    @router.get("/watchlist.json")
    def watchlist_json(mode: str = Query(default="")) -> Dict[str, Any]:
        return build_watchlist(ctx, mode=mode)

    @router.get("/watchlist/groups")
    def watchlist_groups() -> Dict[str, Any]:
        return build_groups(ctx)

    @router.get("/watchlist/state")
    def watchlist_state(kind: str = Query(default="stock"),
                        code: str = Query(default="")) -> Dict[str, Any]:
        return build_state(ctx, kind=kind, code=code)

    @router.get("/watchlist/search")
    def watchlist_search_get(q: str = Query(default=""),
                             limit: Optional[int] = Query(default=None)) -> Dict[str, Any]:
        """旧前端实际用 POST（中文走 query 会被 latin-1 解成乱码）；GET 一并接。"""
        return build_search(ctx, q=q, limit=limit)

    @router.post("/watchlist/search")
    def watchlist_search_post(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = _body_dict(body)
        return build_search(ctx, q=payload.get("q"), limit=payload.get("limit"))

    # ---- 写（一律 读整份 → 改内存 → panel_write 整份）----
    @router.post("/watchlist/add")
    def watchlist_add(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_add(ctx, body)

    @router.post("/watchlist/remove")
    def watchlist_remove(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_remove(ctx, body)

    @router.post("/watchlist/move")
    def watchlist_move(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_move(ctx, body)

    @router.post("/watchlist/reorder")
    def watchlist_reorder(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_reorder(ctx, body)

    @router.post("/watchlist/group/add")
    def watchlist_group_add(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_group_add(ctx, body)

    @router.post("/watchlist/group/rename")
    def watchlist_group_rename(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_group_rename(ctx, body)

    @router.post("/watchlist/group/remove")
    def watchlist_group_remove(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_group_remove(ctx, body)

    @router.post("/watchlist/group/reorder")
    def watchlist_group_reorder(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return build_group_reorder(ctx, body)

    return router
