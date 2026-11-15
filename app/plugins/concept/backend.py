# -*- coding: utf-8 -*-
"""concept 插件：概念追踪 Tab（旧 8800 `/api/concept.json` 的迁移实现）。

**为什么 root 挂载**：旧前端（`engine/web/index.html` 概念 Tab）直接请求根路径
`/api/concept.json`（含 `?slug=`），迁移策略是「复用旧前端、换数据源」，
所以 manifest 里 `backend.mount = "root"`，路径与旧端**完全同名**。

**数据源**（唯一事实源 = Markdown 知识库，模块内零第二份结构化副本）
- 总表 `夏天-activity/概念追踪/概念与催化总表.md` → active / watch / chain / archived /
  counts / sector_map（解析在 `source.py`，从旧仓同口径实现移植；旧副本 2026-11-07 已退役删除）。
- 单概念 `{slug}/概况.md` → 原文，经 `render.py`（从旧仓同口径实现移植）
  转 HTML，含个股名 linkify（点开 K 线）。

**取数一律走 ctx（契约 §4）**
- 文本目录：`ctx.text_dir("concept_tracking")` —— 4.2 新增的**只读文本目录**通道；
  插件只拿 Path 自己读，核心不提供任何写方法，本插件也不写任何文件。
- THS 板块名表：`ctx.db_read("ths_board.db")` 查 `concept` / `board` 两表
  （concept 优先、board 补，重名 concept 优先；库缺失 → 空表，板块反查全部留空，不编造）。
- 名称表：`ctx.json_read("stock_names")["names"]` 反查（6 位码 → 名称），
  构建「名称 → ts_code」callable 注入 `source.load_index` / `render.md_to_html`。
  同名多码 / 解析不到 → 空串（**不猜**，前端据此不伪装可点）。

**本插件严禁**（契约 §4 / §8，`cutover_gate.py` 静态扫描）：
`sqlite3.connect` / `from common` / `from core` / `xtquant` / `from eltdx` / 任何写文件代码。

**格式唯一依据**：逐行对齐旧仓 `engine/server.py::api_concept`（1766 / 2022 行）
与 `_backfill_chain_codes`（1959 行），字段名、嵌套、数组元素键名逐字照搬。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

# 插件模块加载方式说明（与 paper 插件同款处理）：
# 生产时 core 用 `spec_from_file_location("_panel_v2_plugin_concept_backend", ...)` 加载，
# 那种情况下本模块**没有父包**，相对导入不存在；测试里走 `app.plugins.concept.backend` 包导入。
# 因此三级兜底：相对导入 → 包绝对导入 → 按文件路径加载兄弟模块。
try:                                        # 1) 正常包导入（pytest / 其他模块 import）
    from . import render, source  # type: ignore
except ImportError:                         # pragma: no cover - 由 core 按文件加载时走这里
    try:                                    # 2) 项目根在 sys.path 时的绝对导入
        from app.plugins.concept import render, source  # type: ignore
    except ImportError:                     # 3) 兜底：按路径加载同目录兄弟模块
        import importlib.util as _ilu

        _here = Path(__file__).resolve().parent

        def _sibling(mod_name: str):
            """按文件路径加载同目录模块（core 以 module 形式加载 backend 时用）。"""
            _spec = _ilu.spec_from_file_location(
                "_panel_v2_concept_%s" % mod_name, _here / ("%s.py" % mod_name))
            if _spec is None or _spec.loader is None:
                raise ImportError("无法加载 concept 插件的兄弟模块: %s" % mod_name)
            _mod = _ilu.module_from_spec(_spec)
            sys.modules[_spec.name] = _mod
            _spec.loader.exec_module(_mod)
            return _mod

        source = _sibling("source")   # type: ignore
        render = _sibling("render")   # type: ignore

PLUGIN_ID = "concept"

TEXT_DIR_NAME = "concept_tracking"   # manifest.data.text 里声明的只读文本目录名
BOARD_DB = "ths_board.db"            # THS 板块名表（concept + board）
NAMES_JSON = "stock_names"           # 东财全量表（6 位码 → 名称）
# 东财名表没有、但旧 `common/names.code_of`（TDX 名表）能解析的个别名称。
# 2026-10-23 实测：概念总表当前只缺「宇树科技」一例；其它未命中保持空码（不猜）。
_NAME_CODE_ALIASES: Dict[str, str] = {
    "宇树科技": "688836.SH",
    "新能源车": "399417.SZ",
    # 概念概况里会出现的指数/板块简称（东财个股名表没有，旧 common.names 有）
    "创业板指": "399006.SZ",
    "创业板": "395004.SZ",
    "数据要素": "399418.SZ",
    # 2026-11-07：东财名表 601696 当日叫「XD中银证」（除权临时名，见 data/stock_names.json），
    # 概念概况写永久名「中银证券」→ 不补就漏链真实个股（旧 TDX 名表能解析）。
    "中银证券": "601696.SH",
}

# 链位「上游/本环节/下游」名称串的分隔符（与前端回退切分同口径，旧端 `_CHAIN_SPLIT` 一致）
_CHAIN_SPLIT = re.compile(r"[/、]")

# 除权/新股临时前缀（交易所命名规则）：XD/XR/DR = 除权除息，N = 首日，C = 上市次日起 5 日内。
# 沪市带前缀时简称被截到 3 字（600392 → 「XD盛和资」），概念库写永久名就解析不到。
_TEMP_NAME_RE = re.compile(r"^(?:XD|XR|DR|N|C)(?=[\u4e00-\u9fff0-9A-Za-z])")
_SURFACE_MIN_LEN = 3        # 表面名前缀兜底的最短长度（再短就撞常用词）

# A 股 ts_code（旧端 `_code_of_stock` 同款）：只认 0/2/3/6 开头的沪深主板+创业+科创。
# 目的：链位里混着「光模块/CPO」这类行业词或指数名时，**不补码**（前端显式不可点）。
_A_SHARE_TS = re.compile(r"^([0236]\d{5})\.(SH|SZ|BJ)$")

# 名称归一（旧 `common/names.py` 同口径）：全角字母/数字 → 半角 + 去全部空白。
# 为什么需要：TDX 名表把「万科Ａ」存成全角，概念总表手写半角 A；不去归一就漏命。
_NORM_FULLWIDTH = {ord(c): ord(c) - 0xFEE0 for c in
                   "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
                   "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
                   "０１２３４５６７８９"}
_NORM_FULLWIDTH[0x3000] = 0x20


def _norm_name(name: Any) -> str:
    """名称归一：全角 → 半角 + 去掉全部空白（中文证券名不含意义空格，全去安全）。"""
    s = str(name or "").translate(_NORM_FULLWIDTH)
    return re.sub(r"\s+", "", s)


def _ts_code(code6: str) -> str:
    """6 位代码 → 带交易所后缀的 ts_code（旧端 `_guess_ts_code` / `to_ts_code` 同口径）。

    920xxx 与 4/8 开头归北交所；6/5/9 开头归沪市；其余归深市。
    `stock_names` 只给 6 位码，而前端 `data-open-kline` 要带后缀的 ts_code，故在此补齐。
    """
    c = str(code6 or "").strip()
    if not c:
        return ""
    if c[:3] == "920" or c[:1] in ("4", "8"):
        return c + ".BJ"
    if c[:1] in ("6", "5", "9"):
        return c + ".SH"
    return c + ".SZ"


def _name_table(ctx: Any) -> Dict[str, Any]:
    """一次读名表 → ``{"names": {名: ts_code}, "surface": {表面名: ts_code}}``（2026-11-14）。

    - ``names``：精确名表（含歧义剔除、88/99 板块码剔除、显式别名补缺）——与旧版内容一致；
    - ``surface``：**除权/新股临时名**剥掉交易所前缀后的表面名（长 ≥3）。
      背景：名表今日把 600392 写成「XD盛和资」（XD 前缀 + 沪市 3 字截断），
      概念库写永久名「盛和资源」就解析不到 → 灰 chip。同一台机器上「XD吉比特」
      「XD常熟银」同理。
      口径：查询侧按「以表面名开头（含相等）+ 名字全长 ≥3」兜底，**且必须唯一命中**——
      两个码共用一个表面名 → 剔除，宁可不 link 也不误配（`数字人民币→数字人` 的教训）。
    """
    names: Dict[str, str] = {}
    surface: Dict[str, str] = {}
    reader = getattr(ctx, "json_read", None)
    if callable(reader):
        try:
            doc = reader(NAMES_JSON)
        except Exception:  # noqa: BLE001 - 名称表缺失 = 少 linkify，不拖垮概念 Tab
            doc = None
        raw = doc.get("names") if isinstance(doc, dict) else None
        if isinstance(raw, dict):
            ambiguous: set[str] = set()
            for code6, nm in raw.items():
                code6 = str(code6 or "").strip()
                key = _norm_name(nm)
                if not key or len(code6) != 6 or not code6.isdigit():
                    continue
                if code6[:2] in ("88", "99"):     # 板块/指数码，非个股
                    continue
                if key.endswith("-W") or key.endswith("W"):
                    # 「宇树科技-W」这类带权证/特殊后缀的条目：旧 common.names 不把它
                    # 当整名，linkify 会退回「宇树科技」；这里跳过以避免 HTML 多包 -W。
                    continue
                ts = _ts_code(code6)
                prev = names.get(key)
                if prev is not None and prev != ts:
                    ambiguous.add(key)            # 同名多码 → 标记歧义
                else:
                    names.setdefault(key, ts)
                # 临时名（XD/XR/DR/N/C）→ 登记表面名，供查询侧前缀兜底
                m = _TEMP_NAME_RE.match(str(nm or "").strip())
                if m:
                    sk = _norm_name(str(nm).strip()[m.end():])
                    if len(sk) >= _SURFACE_MIN_LEN:
                        prev_s = surface.get(sk)
                        if prev_s is not None and prev_s != ts:
                            surface.pop(sk, None)  # 表面名歧义 → 剔除
                        else:
                            surface.setdefault(sk, ts)
            for key in ambiguous:
                names.pop(key, None)
    # 显式别名表（东财名表缺、TDX 名表有的名称）；只补缺，不覆盖已有映射
    for alias, ts in _NAME_CODE_ALIASES.items():
        names.setdefault(_norm_name(alias), ts)
    return {"names": names, "surface": surface}


def _name_to_code_map(ctx: Any) -> Dict[str, str]:
    """名称 → ts_code 精确反查表（≈`_name_table()["names"]`；保留此名供旧调用/测试）。"""
    return _name_table(ctx)["names"]


def _make_name_to_code(names: Dict[str, str], surface: Optional[Dict[str, str]] = None):
    """名称 → ts_code 的 callable（注入 source / render）；查不到 → 空串（不猜）。

    两层：先用精确名表；未命中且传了 `surface` 时，再用「查询名以表面名开头 + 唯一命中」
    兜底除权/新股临时名（`盛和资源` → 名表里的 `XD盛和资`）。非临时名不参与前缀匹配，
    不然「数字人民币」会被链到「数字人」。
    """
    surf = surface or {}

    def resolve(name: Any) -> str:
        key = _norm_name(name)
        if not key:
            return ""
        code = names.get(key)
        if code:
            return code
        if surf:
            hits = {c for s, c in surf.items() if key.startswith(s)}
            if len(hits) == 1:
                return hits.pop()
        return ""

    return resolve


def _code_of_stock(name: Any, name_to_code) -> str:
    """名称 → 可跳转的 A 股 ts_code（旧端 `server.py::_code_of_stock` 同口径）。

    与 `name_to_code` 的区别：**只放行 A 股个股**（`_A_SHARE_TS`），
    链位里混进的行业词/指数名一律补空串，前端显式显示「不可点」。
    """
    nm = str(name or "").strip()
    if not nm:
        return ""
    code = name_to_code(nm) or ""
    return code if (code and _A_SHARE_TS.match(code)) else ""


def _backfill_chain_codes(payload: Dict[str, Any], name_to_code=None) -> Dict[str, Any]:
    """概念总表载荷：链位「上游/本环节/下游」名称串 → 补 up/mid/down_detail[{"name","code"}]。

    移植自旧 `engine/server.py::_backfill_chain_codes`（1959 行），口径逐字照搬：
    与前端回退切分同口径（`[/、]`）；补上后前端优先用 detail 数组渲染 chips。
    幂等：字段已存在（含空数组）就不覆盖——缓存对象会被反复下发。
    补码失败一律不抛（概念 Tab 不因补码挂掉）。
    """
    if not callable(name_to_code):
        return payload
    try:
        for x in payload.get("chain") or []:
            for key in ("up", "mid", "down"):
                dk = key + "_detail"
                if dk in x:
                    continue
                parts = [p.strip() for p in _CHAIN_SPLIT.split(str(x.get(key) or "")) if p.strip()]
                x[dk] = [{"name": p, "code": _code_of_stock(p, name_to_code)} for p in parts]
    except Exception:  # noqa: BLE001 - 补码失败不拖垮概念 Tab
        pass
    return payload


def _sector_names(ctx: Any) -> Dict[str, str]:
    """`{THS板块名: 代码}`（`ths_board.db` 的 concept + board 两表合并；重名 concept 优先）。

    旧端自己连库；插件禁止 `sqlite3.connect`，
    故改由本函数经 `ctx.db_read()` 取只读连接后传入 `source.load_index(sector_names=...)`。
    库缺失/表缺失/读错 → `{}`（板块反查全部留空 + 全部进 sector_unresolved，不编造）。
    """
    names: Dict[str, str] = {}
    try:
        conn = ctx.db_read(BOARD_DB)
    except Exception:  # noqa: BLE001 - 库不可用 = 无板块反查
        return names
    try:
        # concept 表先入（优先），board 表只补缺（旧端同名顺序一致）
        for table in ("concept", "board"):
            try:
                rows = conn.execute(f"SELECT code, name FROM {table}").fetchall()
            except Exception:  # noqa: BLE001 - 单表缺失不影响另一表
                continue
            for row in rows:
                try:
                    code, nm = row[0], row[1]
                except (TypeError, IndexError):
                    continue
                nm = str(nm or "").strip()
                if nm and nm not in names:
                    names[nm] = str(code or "").strip()
    finally:
        try:
            conn.close()          # 用完即关（同 sector 插件口径）
        except Exception:  # noqa: BLE001
            pass
    return names


def build_concept_index(ctx: Any) -> Dict[str, Any]:
    """无 slug → 总表索引（旧端 `JSONResponse(_backfill_chain_codes(load_index()))` 同口径）。

    注意：总表缺失时 `load_index` 返回 `{"ok": False, "error": ...}`，
    **旧端仍返回 200**（不检查 ok，由前端渲染缺失态）；这里保持同一行为，不擅自改 404。
    """
    concept_dir = ctx.text_dir(TEXT_DIR_NAME)
    table = _name_table(ctx)
    # 链位/核心标的是**离散 token**（已按分隔符切开）→ 可安全享用临时名表面兜底
    resolve = _make_name_to_code(table["names"], table["surface"])
    data = source.load_index(concept_dir, _sector_names(ctx), resolve)
    return _backfill_chain_codes(data, resolve)


def build_concept_detail(ctx: Any, slug: str) -> Dict[str, Any]:
    """有 slug → 单概念概况渲染 HTML（`{ok, slug, html}`，字段与旧端一致）。"""
    concept_dir = ctx.text_dir(TEXT_DIR_NAME)
    r = source.load_detail(slug, concept_dir)
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r.get("error") or "概念概况缺失")
    table = _name_table(ctx)
    # 概况正文 linkify 是**滑窗最长匹配**（render._linkify_seg 在任意位置试 3~8 字窗口），
    # 前缀兜底会把「盛和资源与数字人」整段包成一个 chip（2026-11-14 实测）——所以正文只用精确名表。
    resolve = _make_name_to_code(table["names"])
    return {"ok": True, "slug": r["slug"], "html": render.md_to_html(r["md"], resolve)}


# ---------------------------------------------------------------- 路由
def register(ctx: Any) -> APIRouter:
    """root 挂载：注册 `/concept.json`，完整路径 = `/api/concept.json`（与旧端同名）。"""
    router = APIRouter()

    @router.get("/concept.json")
    def concept(slug: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        """概念 Tab 数据源（旧 8800 `/api/concept.json` 同构）。

        · 无 slug → 总表索引 {ok, updated, active[], watch[], chain[], archived[],
          counts{}, sector_map{}, unresolved_targets[], sector_unresolved[]}
          （后两个是 2026-10-02 新增的**人工核对**字段，旧端本来就有，逐字照搬）
        · slug=概念目录名 → 该概念 概况.md 渲染为 HTML {ok, slug, html}
        · 概况缺失/非法 slug → 404；读取异常 → 500（不拖垮面板其他模块）
        """
        try:
            if slug:
                return build_concept_detail(ctx, slug)
            return build_concept_index(ctx)
        except HTTPException:
            raise
        except FileNotFoundError as e:
            # 2026-11-15：概念知识库目录/文件缺失属「数据不可用」（未部署知识库是常态），
            # 回 503 与其它插件同语义；其余异常仍 500。
            raise HTTPException(status_code=503, detail=f"概念知识库不可用：{e}")
        except Exception as e:  # noqa: BLE001 - 兜底：不 500 拖垮面板
            raise HTTPException(status_code=500, detail=f"概念数据读取失败：{e}")

    return router
