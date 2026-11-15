# -*- coding: utf-8 -*-
"""concept/source.py · 概念催化追踪按需数据源（纯函数，零 sqlite / 零 common 依赖）

职责
    把 `夏天-activity/概念追踪/` 的 Markdown 知识库解析成作战面板「概念 Tab」
    可直接渲染的结构化数据：
      · 总表 `概念与催化总表.md` → 活跃概念 / 观察池(AMV口径) / 产业链链位映射 / 已归档
      · 单概念 `{slug}/概况.md`  → 原文（由调用方转 HTML，见同目录 render.py）

迁移说明（2026-10-23，任务书 docs/task_concept_4_2.md §3.2）
    本文件从旧仓同口径实现（2026-11-07 已退役删除）**逐段移植**，
    解析逻辑一字不改，只把三处**取数依赖改成注入**——因为插件契约禁止
    `sqlite3.connect` 与 `from common import ...`：

      1. 概念目录：不再写死 `common.paths.SUMMER_DIR/概念追踪`，
         改为 `concept_dir` 参数；生产必须传 `ctx.text_dir("concept_tracking")`。
         `CONCEPT_DIR` 只是**缺省常量**（测试可 monkeypatch），不是生产取数通道。
      2. THS 板块名表：不再自己连 `ths_board.db`（旧 `_ths_sector_names()` 已删），
         改为 `sector_names` 参数（`{板块名: 代码}`），由插件用 `ctx.db_read("ths_board.db")`
         查 `concept`/`board` 两表构建后传入。
      3. 名→码：不再 `from common import names`，改为 `name_to_code` callable，
         由插件从 `ctx.json_read("stock_names")` 反查表构建；缺省按「解析不到」处理
         （code 置空，**不猜**）。

设计初心
    · 「MD 是唯一事实源」——概念追踪只维护 Markdown（总表 + 概况/阶段），
      面板按需解析渲染，不新增第二份结构化副本（违 AGENTS.md §2 口径唯一源）。
    · **按表头取值**：总表列结构会演进（2026-10-01 观察池换 AMV 口径 + 新增链位映射），
      解析以表头关键字定位字段，不写死列序号，换列不炸。
    · 解析失败/文件缺失 → 返回 ok:False + error，端点层据此 404/降级，**不抛穿**。
    · 只读：本模块绝不写任何文件；文本目录只读。

数据源与口径
    · 概念目录：`<WS>/夏天-activity/概念追踪/`（由调用方传入，见上）
    · 总表：概念与催化总表.md（二级标题：活跃概念 / 观察池 / 产业链链位映射 / 已归档）
    · 概况：{概念目录}/{{slug}}/概况.md（slug 即目录名，禁路径穿越）

上下游
    上游：`app/plugins/concept/backend.py` 的 `/api/concept.json` 路由（薄壳）。
    下游：`app/plugins/concept/render.py`（md → HTML）；旧前端概念 Tab
          （消费 index / html / sector_map 字段，字段名与旧端**逐字一致**）。
    单测：`app/plugins/concept/tests/test_concept_source.py`。

观察池结构化字段（2026-10-02，主人「观察池做得潦草不直观」）
    背景：观察池原先只有 ``sectors``（「第三代半导体 885908.TI / 半导体材料 884091.TI」）与
    ``amv``（「空（−0.00103 / +0.00058）」）两串**混排文本**，前端只能整行照抄 → 密密麻麻一堆字。
    口径：解析责任留在服务端（守 §3「HTML 纯展示零逻辑」），新增两个**结构化**字段供卡片渲染——
      · ``sectors_list``: [{"name", "code"}]（板块名与 THS 代码拆开）
      · ``deltas``: [float]（权重变化，可多个；含 U+2212 负号归一）
    旧字段 ``sectors``/``codes``/``amv``/``kind`` **一律保留**（契约只加不改名）。

核心标的 名→码（2026-10-02）
    背景：活跃概念卡的「核心标的」原只有名（``targets``），前端无法点开 K 线；主人定「从源头解决」
    ——但源头=**解析层**（本模块），不是让夏天在 MD 里手写代码。
    口径：新增 ``targets_detail: [{"name", "code"}]``（旧 ``targets`` 名字数组保留），
    code 由注入的 ``name_to_code`` 反查；
    **解析不到/同名多码 → code 置空（不猜）**，并由 ``unresolved_targets`` 汇总上报。

链位单元 名→码与括号分组（2026-11-14）
    背景：主人报「带括号的链位单元被 `/` 直接切碎 → 灰 chip 点不动」。
    口径：链位三格原文（形如 `类别A/类别B（标的1/标的2）`）新增 ``up_detail``/``mid_detail``/
    ``down_detail``：有序 items ``[{name, code, kind, in_group}]``，``kind ∈ {label, stock}``
    且**只由 ``name_to_code`` 解析结果判定**（不认关键词表、不猜「未上市」「待补」）；
    括号内的 token 带 ``in_group=True``。旧 ``up``/``mid``/``down`` 原文一律保留不改义。
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple, Union

# ---------------------------------------------------------------- 常量

# 缺省概念目录：模块常量，**只作缺省**（生产必须传 ctx.text_dir("concept_tracking")）。
#
# 为什么不 import `app.core.paths`：插件契约禁止 `from core import ...`（静态扫描会拦），
# 所以这里按「本文件位置 → 项目根 → 工作区根」自行推导，规则与 core/paths.py 一致
# （panel-v2/app/plugins/concept/source.py → parents[3]=panel-v2，再上两级=OH-WorkSpace）。
_THIS = Path(__file__).resolve()
_PANEL_V2_DIR = _THIS.parents[3] if len(_THIS.parents) >= 4 else _THIS.parent
_WS = _PANEL_V2_DIR.parents[1] if len(_PANEL_V2_DIR.parents) >= 2 else _PANEL_V2_DIR.parent

CONCEPT_DIR: Path = _WS / "夏天-activity" / "概念追踪"

MASTER_NAME = "概念与催化总表.md"
DETAIL_NAME = "概况.md"

SCAN_TTL_S = 30.0        # 总表解析节流（前端 60s 轮询 → 每 30s 至多解析一次）

# ---- 解析缓存（锁保护多线程 ASGI worker）----
# sig = (概念目录, 总表 mtime_ns, 总表字节数)：目录或文件一变就必然重算（任务书 §3.2 要求）。
_lock = threading.Lock()
_cache: Dict[str, object] = {"at": 0.0, "sig": None, "data": None}

# 阶段 → 温度带（hot/warm/cool）关键词，按优先级自上而下判定（口径唯一源）
_COOL_KEYS = ("降温", "回撤", "蓄势", "退潮", "防守", "冷却", "待归档", "归档")
_HOT_HEAD_KEYS = ("主升", "量产元年")
_HOT_KEYS = ("催化", "放量", "启动", "量产")
_WARM_KEYS = ("兑现", "验证", "回落", "高位", "分歧")
_TAIL_DOWN_KEYS = ("分歧", "回调", "回落", "分化")

# THS 板块代码：6 位数字 + .TI（同花顺指数）。观察池「AMV 参照板块」列即携带。
_THS_CODE_RE = re.compile(r"\b(\d{6}\.(?:TI|SH|SZ|BJ))\b")

# 类型别名：名→码解析器（注入；缺省 None = 全部解析不到，code 留空不猜）
NameResolver = Callable[[str], Optional[str]]


def reset_cache() -> None:
    """清空总表解析缓存（测试与热更新用；生产靠 30s TTL + 指纹自动失效）。"""
    with _lock:
        _cache.update({"at": 0.0, "sig": None, "data": None})


def _resolve_dir(concept_dir: Union[str, Path, None]) -> Path:
    """概念目录：显式传入优先，缺省回落模块常量（每次调用时读，便于测试 monkeypatch）。"""
    if concept_dir is None:
        return Path(CONCEPT_DIR)
    return Path(concept_dir)


def _read(path) -> Optional[str]:
    """读文本（utf-8）；不存在/读取失败 → None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


def _clean(s) -> str:
    """去 markdown 粗体标记 + 首尾空白。"""
    return re.sub(r"\*\*", "", s or "").strip()


def _stage_short(stage) -> str:
    """阶段串截到首个中文括号（去掉「（9/19 阶段下修：…）」这类注记），保留 · 分段。"""
    s = _clean(stage)
    s = re.sub(r"[（(].*$", "", s).strip()
    return s or _clean(stage)


def _heat(stage) -> str:
    """阶段 → 温度带：hot（主升/催化中）/ warm（分歧/兑现）/ cool（降温/蓄势）。

    判定：先看首段主阶段，再看副段状态是否把 hot 降一档（如「催化进行中·高位放量分歧」→ warm）；
    「主升浪 / 量产元年」为最高档，副段分歧不降级。
    """
    s = _stage_short(stage)
    parts = re.split(r"[·・]", s)
    head = parts[0].strip() if parts else ""
    tail = "·".join(parts[1:])
    if any(k in head for k in _COOL_KEYS):
        return "cool"
    if any(k in head for k in _HOT_HEAD_KEYS):
        return "hot"
    if any(k in head for k in _HOT_KEYS):
        return "warm" if any(k in tail for k in _TAIL_DOWN_KEYS) else "hot"
    if any(k in head for k in _WARM_KEYS):
        return "warm"
    return "warm"


def _split_targets(cell) -> List[str]:
    """核心标的串 → 列表（支持 / 和 、 分隔）。"""
    s = _clean(cell)
    if not s or s == "—":
        return []
    return [t.strip() for t in re.split(r"[/、]", s) if t.strip()]


def _resolve_code(name: str, name_to_code: Optional[NameResolver]) -> str:
    """名称 → 代码（经注入的解析器）；解析器缺失/抛错/返回 None → 空串（**不猜**）。"""
    if not callable(name_to_code):
        return ""
    try:
        return str(name_to_code(name) or "")
    except Exception:  # noqa: BLE001 - 解析失败不影响概念解析主流程
        return ""


def _targets_detail(cell, name_to_code: Optional[NameResolver] = None) -> List[dict]:
    """核心标的串 → [{"name", "code"}]（2026-10-02：名→码解析，供前端点开 K 线）。

    解析源 = 注入的 `name_to_code`（生产由插件从 `ctx.json_read("stock_names")` 反查表构建）。
    解析不到 / 同名多码 → code 置空（**不猜**；前端据此不伪装可点，
    并由 load_index 汇总到 unresolved_targets 供人工核）。
    职责切分：夏天决定「哪些是核心标的」（研究判断），系统把名字解析成代码（机器活）。
    """
    out = []
    for nm in _split_targets(cell):
        out.append({"name": nm, "code": _resolve_code(nm, name_to_code)})
    return out


def _name_slug(cell):
    """概念列 → (显示名, slug)。

    支持 `[名](./目录/概况.md)` 链接形态；无链接（观察池/链位/已归档）→ slug=None。
    slug 只取链接路径首个目录段，禁止含路径分隔与上跳。
    """
    raw = (cell or "").strip()
    m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", raw)
    if not m:
        return _clean(raw), None
    name = m.group(1).strip()
    path = m.group(2).strip()
    seg = path.replace("\\", "/").split("/")
    slug = next((p for p in seg if p and p not in (".", "..")), "")
    slug = slug if _safe_slug(slug) else None
    return _clean(name), slug


def _safe_slug(slug) -> Optional[str]:
    """slug 合法性：单层目录名，禁分隔符/上跳/空。返回规范化 slug 或 None。"""
    if not slug:
        return None
    s = str(slug).strip()
    if not s or s in (".", ".."):
        return None
    if any(ch in s for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|")):
        return None
    return s


def _sections(md: str) -> List[dict]:
    """按二级/三级标题切分 → [{"title": str, "lines": [str]}]（仅保留有标题的段）。"""
    secs = []
    cur = {"title": "", "lines": []}
    for ln in md.splitlines():
        m = re.match(r"^#{2,3}\s+(.*)$", ln)
        if m:
            secs.append(cur)
            cur = {"title": m.group(1).strip(), "lines": []}
        else:
            cur["lines"].append(ln)
    secs.append(cur)
    return [s for s in secs if s["title"]]


def _table(lines):
    """取段内首个 markdown 表 → (header, rows)；无表 → ([], [])。"""
    raw = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            raw.append(cells)
    raw = [r for r in raw if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
    if len(raw) < 2:
        return [], []
    return raw[0], raw[1:]


def _table_dicts(lines) -> List[dict]:
    """段内首个表 → [{表头: 单元格}]（按表头取值，抗换列）。"""
    header, rows = _table(lines)
    if not header:
        return []
    out = []
    for r in rows:
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        out.append({header[i]: r[i] for i in range(len(header))})
    return out


def _field(d, *keys) -> str:
    """按表头关键字（子串）取字段值；命中首个即返回，全不中 → ''。"""
    for k in keys:
        for hk, v in d.items():
            if k in hk:
                return v
    return ""


def _pick_section(secs, *keywords):
    for s in secs:
        if any(k in s["title"] for k in keywords):
            return s
    return None


def _extract_updated(md) -> str:
    m = re.search(r"最后更新[：:]\s*(\d{4}-\d{2}-\d{2})", md or "")
    return m.group(1) if m else ""


def _parse_active(sec, name_to_code: Optional[NameResolver] = None) -> List[dict]:
    out = []
    for d in _table_dicts(sec["lines"]) if sec else []:
        raw_name = _field(d, "概念")
        if not raw_name:
            continue
        name, slug = _name_slug(raw_name)
        stage = _field(d, "当前阶段")
        out.append({
            "name": name,
            "slug": slug,
            "stage": _clean(stage),
            "stage_short": _stage_short(stage),
            "heat": _heat(stage),
            "logic": _clean(_field(d, "逻辑")),
            "duration": _clean(_field(d, "持续性")),
            "updated": _clean(_field(d, "最近更新", "更新")),
            "next_node": _clean(_field(d, "验证节点", "下一个验证")),
            "targets": _split_targets(_field(d, "核心标的", "标的")),
            # 2026-10-02：结构化标的（名+码），供前端 chips 点开 K 线；旧 targets 保留（只加不改名）
            "targets_detail": _targets_detail(_field(d, "核心标的", "标的"), name_to_code),
        })
    return out


def _watch_kind(amv) -> str:
    """AMV 多空串 → 'bull'(多) / 'bear'(空) / 'na'。"""
    s = _clean(amv)
    if s.startswith("多"):
        return "bull"
    if s.startswith("空"):
        return "bear"
    return "na"


# 权重变化数值（含 U+2212 负号与前后空白；如「−0.00103」「+0.00058」）
_NUM_RE = re.compile(r"[+\-\u2212]\s*\d+(?:\.\d+)?")


def _watch_sectors(sectors) -> List[dict]:
    """参照板块串 → [{"name", "code"}]（拆板块名与 THS 代码，供前端分片渲染）。

    「第三代半导体 885908.TI / 半导体材料 884091.TI」→ 两条；无代码的段保留 name、code 置空。
    不编造：解析不出名字时回落整段原文。"""
    out = []
    for part in re.split(r"\s*/\s*", _clean(sectors)):
        part = part.strip()
        if not part:
            continue
        codes = _THS_CODE_RE.findall(part)
        name = _THS_CODE_RE.sub("", part).strip(" ·,，/\t")
        out.append({"name": name or part, "code": codes[0] if codes else ""})
    return out


def _watch_deltas(amv) -> List[float]:
    """AMV 多空串 → 权重变化浮点序列（「空（−0.00103 / +0.00058）」→ [-0.00103, 0.00058]）。

    取不到数字 → []（不编造，前端不显示该行）。"""
    out = []
    for m in _NUM_RE.findall(_clean(amv)):
        try:
            out.append(float(m.replace("\u2212", "-").replace(" ", "")))
        except ValueError:
            continue
    return out


def _parse_watch(sec) -> List[dict]:
    out = []
    for d in _table_dicts(sec["lines"]) if sec else []:
        name = _clean(_field(d, "方向", "概念"))
        if not name:
            continue
        sectors = _clean(_field(d, "参照板块", "板块"))
        # 注意：先按「多空」取值——表头「AMV 参照板块（THS）」也含 AMV，用 AMV 会撞车取成板块列
        amv = _clean(_field(d, "多空", "AMV"))
        out.append({
            "name": name,
            "sectors": sectors,
            "codes": _THS_CODE_RE.findall(sectors),
            "amv": amv,
            "kind": _watch_kind(amv),
            # 结构化字段（2026-10-02）：供前端卡片渲染，避免整行文本堆叠
            "sectors_list": _watch_sectors(sectors),
            "deltas": _watch_deltas(amv),
            "question": _clean(_field(d, "待验证", "问题")),
        })
    return out


# ------------------------------------------------ 链位单元 → 有序 items（2026-11-14）
# 背景：链位三格原文是 `类别A/类别B（标的1/标的2）`，旧实现只按 `[/、]` 盲切，
#   括号被 `/` 切碎后「茂莱光学」这类名字成不了独立 token → 解析不到代码 → 灰 chip 点不动。
# 口径（任务书 2026-11-14，主人定「解析器说了算」）：
#   · 括号（中/英括号）**外层**分隔符：`/` `+` `→` `、`，另加 `，` `；` `;`
#     （实侧：`…（A）；动捕/视觉/IMU（B）` 里的全角分号会把两段括号粘在一起）；
#     **内层**：`/` `、`，同样加 `，` `；` `;`——括号里的注记（`御量升，未上市`）
#     必须断开，否则「未上市」会粘成整词，既解析不到也说不清是哪一类。
#   · **只有括号内是标的**：括号外天生是环节/类别名，一律 label、不查名表（2026-11-14 实测：
#     括号外 5 个 stock 全错、0 个对；括号内 176 个全对）；括号内才进名→码解析。
#   · `：` 及其同义写法 `=`（「A股映射=张江高科参股」）左侧是**类别**（kind 一律 label），
#     右侧按标的正常解析（仅在括号内；括号外的右侧同样是环节名）。
#   · stock / label **只由注入的 name_to_code 判**：解析出码 = stock（前端可点），
#     解析不出 = label（灰 chip）。不认关键词表，不拿「未上市」「待补」这类词去猜。
#   · 整词解析不到时，**仅对括号内的标的位**（`：`/`=` 右侧）再按「最长可解析前缀」剥尾部说明
#     （`A股映射=张江高科参股` → `张江高科`，name 仍保留原文，不改写事实源）；
#     其它位置只认整词——实测放宽到全文时「数字人民币」会被剥成「数字人」
#     挂到另一只票上，这种假链接比灰 chip 更糟。
#   · `/` 两侧紧贴英文/数字 → 当英文复合词（`DRAM/NAND 制造`）不拆，其余 `/` 照常拆。
_CHAIN_OUTER_SEP = "/+→、，；;"
_CHAIN_INNER_SEP = "/、，；;"
_CHAIN_LABEL_MARK_RE = re.compile(r"[：=]")
_ASCII_WORD_RE = re.compile(r"[0-9A-Za-z]")


def _chain_is_latin_slash(text: str, i: int) -> bool:
    """下标 i 的 `/` 是否夹在英文字母/数字之间（英文复合词，不拆）。"""
    return (0 < i < len(text) - 1
            and bool(_ASCII_WORD_RE.match(text[i - 1]))
            and bool(_ASCII_WORD_RE.match(text[i + 1])))


def _chain_split(text: str, seps: str) -> List[str]:
    """按 seps 切段（`/` 命中英文复合词时跳过），去空段。"""
    out, buf = [], []
    for i, ch in enumerate(text):
        if ch in seps and not (ch == "/" and _chain_is_latin_slash(text, i)):
            tok = "".join(buf).strip()
            if tok:
                out.append(tok)
            buf = []
        else:
            buf.append(ch)
    tok = "".join(buf).strip()
    if tok:
        out.append(tok)
    return out


def _chain_segments(text: str) -> List[Tuple[str, bool]]:
    """原文 → [(片段, in_group)]；括号本身丢弃，括号内容单独成段。"""
    segs: List[Tuple[str, bool]] = []
    buf: List[str] = []
    depth = 0
    for ch in text:
        if ch in "（(":
            if depth == 0:
                tok = "".join(buf).strip()
                if tok:
                    segs.append((tok, False))
                buf = []
            else:
                buf.append(ch)          # 嵌套括号：内层括号字符保留在文本里
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
            if depth == 0:
                tok = "".join(buf).strip()
                if tok:
                    segs.append((tok, True))
                buf = []
            else:
                buf.append(ch)
        else:
            buf.append(ch)
    tok = "".join(buf).strip()
    if tok:
        segs.append((tok, False))
    return segs


def _chain_resolve(tok: str, name_to_code: Optional[NameResolver],
                   allow_trim: bool = False) -> str:
    """token → 代码：整词优先；allow_trim 时再试最长可解析前缀；都不中 → 空串。"""
    code = _resolve_code(tok, name_to_code)
    if code or not allow_trim or not callable(name_to_code):
        return code
    for end in range(len(tok) - 1, 1, -1):   # 至少留 2 字，短于 2 字的前缀不查（防单字误命中）
        code = _resolve_code(tok[:end], name_to_code)
        if code:
            return code
    return ""


def _chain_item(tok: str, in_group: bool, name_to_code: Optional[NameResolver],
                as_label: bool = False, allow_trim: bool = False) -> dict:
    """单个 token → {name, code, kind, in_group}（kind 只由代码有无决定）。"""
    code = "" if as_label else _chain_resolve(tok, name_to_code, allow_trim)
    return {
        "name": tok,
        "code": code,
        "kind": "stock" if code else "label",
        "in_group": bool(in_group),
    }


def _chain_detail(cell, name_to_code: Optional[NameResolver] = None) -> List[dict]:
    """链位单元格原文 → 有序 items（括号分组，名称原序）。

    `up`/`mid`/`down` 的原文一字不动，detail 只是**附加**的拆分视图（契约只加不改）。
    结构规则：**括号外一律 label**（环节名，不参与名→码解析）；**括号内才是标的**（类别位仍 label）。
    """
    text = _clean(cell)
    if not text or text == "—":
        return []
    out: List[dict] = []
    for seg, in_group in _chain_segments(text):
        seps = _CHAIN_INNER_SEP if in_group else _CHAIN_OUTER_SEP
        m = _CHAIN_LABEL_MARK_RE.search(seg)      # `：` / `=` 只认段内首个
        if m:
            for t in _chain_split(seg[:m.start()], seps):   # 类别位：一律 label
                out.append(_chain_item(t, in_group, name_to_code, as_label=True))
            out.extend(_chain_item(t, in_group, name_to_code,
                                   as_label=not in_group, allow_trim=True)
                       for t in _chain_split(seg[m.end():], seps))
        else:
            # 括号外 = 环节名（不进名表）；括号内 = 标的（按名表判 stock/label）
            out.extend(_chain_item(t, in_group, name_to_code, as_label=not in_group)
                       for t in _chain_split(seg, seps))
    return out


def _parse_chain(sec, name_to_code: Optional[NameResolver] = None) -> List[dict]:
    out = []
    for d in _table_dicts(sec["lines"]) if sec else []:
        name = _clean(_field(d, "概念"))
        if not name:
            continue
        up = _clean(_field(d, "上游"))
        mid = _clean(_field(d, "本环节", "中游"))
        down = _clean(_field(d, "下游"))
        out.append({
            "name": name,
            "up": up,                    # 原文，不改名不改义
            "mid": mid,
            "down": down,
            # 2026-11-14：拆好的有序 items [{name, code, kind, in_group}]
            "up_detail": _chain_detail(up, name_to_code),
            "mid_detail": _chain_detail(mid, name_to_code),
            "down_detail": _chain_detail(down, name_to_code),
        })
    return out


def _parse_archived(sec) -> List[dict]:
    out = []
    for d in _table_dicts(sec["lines"]) if sec else []:
        raw_name = _field(d, "概念", "名称")
        if not raw_name:
            continue
        name, slug = _name_slug(raw_name)
        out.append({"name": name, "slug": slug, "note": _clean(_field(d, "说明", "备注"))})
    return out


# ------------------------------------------------ 活跃概念 → THS 板块（2026-10-02）
# 主人报「活跃池跳不了板块详情」：活跃概念表**没有板块列**（观察池才有「AMV 参照板块」），
# 所以只能反查。规则三级、**纯名字、不含研判**，数据源 = 调用方传入的 sector_names
# （生产 = `ctx.db_read("ths_board.db")` 的 concept + board 两表）：
#   ① 概念名（或按 / 、 · 拆出的词）与 THS 板块名**精确同名**
#   ② 名字 +「概念」后缀同名（THS 概念表用「PCB概念」「光伏概念」这类写法）
#   ③ 名字被**唯一一个** THS 板块名包含（如 CPO ⊂ 共封装光学(CPO)）；多个候选一律放弃
# 命中不了 → 空列表（**不编造**），由 payload 的 sector_unresolved 汇总给人工补口径。
# 为什么不用成分股重合度：那是研判（实测会把「薄膜铌酸锂」配到「融资融券」这类大杂烩板块），
#   属于夏天口径范围，本模块不拍。
#
# 2026-10-23 迁移：旧 `_ths_sector_names()`（自连 sqlite3）已删除——插件禁止 sqlite3.connect，
#   板块名表改为 `sector_names` 参数注入，本模块**不再有任何数据库代码**。


def _sector_resolve(tok, names: Mapping[str, str]):
    """单 token → (代码, 依据, 板块真名)；未命中 → (None, "", "")。"""
    if not tok:
        return None, "", ""
    if tok in names:
        return names[tok], "精确", tok
    if (tok + "概念") in names:
        return names[tok + "概念"], "加概念", tok + "概念"
    hits = [n for n in names if tok in n]
    if len(hits) == 1:
        return names[hits[0]], "唯一包含", hits[0]
    return None, "", ""


def _active_sectors(name, names: Mapping[str, str]) -> List[dict]:
    """活跃概念名 → [{"name","code","src"}]（长词优先；命中不了返回 []）。"""
    toks = sorted({t.strip() for t in re.split(r"[/、·]", name or "") if t.strip()},
                  key=lambda t: (-len(t), t))   # 长词优先；同长按字典序 → 结果可复现
    for t in toks:
        code, src, real = _sector_resolve(t, names)
        if code:
            return [{"name": real, "code": code, "src": src}]
    return []


def _build_sector_map(active, watch) -> dict:
    """板块 → 概念 反查表（0AMV 异动深链用）。

    来源两路：① 观察池「AMV 参照板块」的 THS 代码（显式，权威）；② 活跃概念名按 / 拆词（弱匹配）。
    返回 {"codes": {代码: 概念名}, "names": [概念名...]}。
    """
    codes = {}
    for w in watch:
        for c in w.get("codes", []):
            codes.setdefault(c, w["name"])
    names = []
    for a in active:
        names.append(a["name"])
        for tok in re.split(r"[/、]", a["name"]):
            tok = tok.strip()
            if tok and tok not in names:
                names.append(tok)
    return {"codes": codes, "names": names}


def _master_sig(path) -> Optional[tuple]:
    try:
        st = os.stat(path)
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def load_index(concept_dir: Union[str, Path, None] = None,
               sector_names: Optional[Mapping[str, str]] = None,
               name_to_code: Optional[NameResolver] = None) -> dict:
    """解析总表 → {ok, updated, active[], watch[], chain[], archived[], counts{}, sector_map{}}。

    参数（全部注入，模块内零 IO 依赖）：
      · concept_dir  概念追踪目录（生产传 `ctx.text_dir("concept_tracking")`；缺省用 CONCEPT_DIR）
      · sector_names `{THS板块名: 代码}`（生产由插件查 `ctx.db_read("ths_board.db")` 构建）；
                     缺省/空 → 活跃概念的 sectors_list 全空 + 全部进 sector_unresolved（不编造）
      · name_to_code 名→码解析器（生产由插件从 `stock_names` 反查表构建）；缺省 → code 全空

    文件缺失 → ok:False + error（端点层转 404）；解析异常 → 保留上次成功结果并标 degraded。
    缓存：按 (概念目录, 总表 mtime_ns, 总表字节数) 指纹 + 30s TTL。
    """
    root = _resolve_dir(concept_dir)
    path = root / MASTER_NAME
    md = _read(path)
    if md is None:
        return {"ok": False, "error": f"{MASTER_NAME} 缺失（{root}）"}

    sig = (str(root), _master_sig(path))
    now = time.monotonic()
    with _lock:
        if (_cache["data"] is not None and _cache["sig"] == sig
                and now - float(_cache["at"] or 0.0) < SCAN_TTL_S):
            return _cache["data"]  # type: ignore[return-value]
    try:
        secs = _sections(md)
        active = _parse_active(_pick_section(secs, "活跃概念"), name_to_code)
        watch = _parse_watch(_pick_section(secs, "观察池"))
        # 活跃概念 → THS 板块码（纯名字规则，名字表由调用方注入；命中不了留空，不编造）
        _secnames = dict(sector_names or {})
        _sector_unresolved = []
        for _a in active:
            _a["sectors_list"] = _active_sectors(_a["name"], _secnames) if _secnames else []
            if not _a["sectors_list"]:
                _sector_unresolved.append(_a["name"])
        chain = _parse_chain(_pick_section(secs, "产业链链位映射", "链位映射"), name_to_code)
        archived = _parse_archived(_pick_section(secs, "已归档"))
        data = {
            "ok": True,
            "updated": _extract_updated(md),
            "active": active,
            "watch": watch,
            "chain": chain,
            "archived": archived,
            "counts": {
                "active": len(active),
                "watch": len(watch),
                "chain": len(chain),
                "archived": len(archived),
                "hot": sum(1 for c in active if c["heat"] == "hot"),
                "warm": sum(1 for c in active if c["heat"] == "warm"),
                "cool": sum(1 for c in active if c["heat"] == "cool"),
            },
            "sector_map": _build_sector_map(active, watch),
            # 2026-10-02：名→码解析不到的标的名（去重排序）——让「点了没反应」可见，供人工核
            "unresolved_targets": sorted({
                t["name"] for a in active for t in (a.get("targets_detail") or [])
                if not t.get("code")}),
            # 2026-10-02：反查不到对应板块的活跃概念名——供夏天/主人补口径（不编造）
            "sector_unresolved": _sector_unresolved,
        }
    except Exception as e:  # noqa: BLE001 - 解析异常不拖垮面板：保留上次成功
        with _lock:
            prev = _cache["data"]
        if prev is not None:
            return dict(prev, degraded=True, error=str(e))  # type: ignore[arg-type]
        return {"ok": False, "error": f"总表解析失败：{e}"}

    with _lock:
        _cache.update({"at": now, "sig": sig, "data": data})
    return data


def load_detail(slug, concept_dir: Union[str, Path, None] = None) -> dict:
    """读单概念概况 → {ok, slug, md}；slug 非法/越界/缺失 → ok:False + error。

    `concept_dir` 同 load_index（生产传 `ctx.text_dir("concept_tracking")`）。
    防路径穿越沿用旧口径：realpath 归一后必须仍在概念目录之下。
    """
    s = _safe_slug(slug)
    if not s:
        return {"ok": False, "error": "非法概念标识"}
    root = os.path.realpath(str(_resolve_dir(concept_dir)))
    path = os.path.realpath(os.path.join(root, s, DETAIL_NAME))
    if not path.startswith(root + os.sep):     # 防路径穿越
        return {"ok": False, "error": "路径越界"}
    md = _read(path)
    if md is None:
        return {"ok": False, "error": f"{s}/{DETAIL_NAME} 缺失"}
    return {"ok": True, "slug": s, "md": md}
