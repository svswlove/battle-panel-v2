#!/usr/bin/env python3
"""
md2html.py — Markdown → 排版 HTML（v2 · 版面纸感/数据日报）
用法: python md2html.py input.md [output_path]
默认输出: 夏天-activity/每日复盘/日复盘 {today}.html
可选 output_path:
  以 .html/.htm 结尾 → 视为完整输出文件路径（需含文件名）
  否则 → 视为输出目录，文件名从 Markdown 的 # 标题自动提取并做安全处理

冻结接口（engine/server.py 动态 import，禁止改名/改签名/改返回结构）：
  extract_title_and_meta(md_text) -> (title, date_str, body_md, custom_meta)
  colorize_td_symbols(html)       -> html
  apply_color_marks(html)         -> html
其余函数可自由增删改。
"""
import sys, os, re
from datetime import datetime
from html import escape as esc, unescape as unesc
from markdown import markdown
from markdown.extensions import tables, fenced_code, codehilite, sane_lists

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(SCRIPT_DIR, "report.css")

# ===== 色标记（v1.2.3 语法糖保留）：{r}红{y}黄{g}绿{x}灰 → 带色 span，MD 保持纯文本 =====
# v2 微调：绿由橄榄 #4A6B4A → 墨绿 #1e7a52（与跌幅色统一，红绿在暖白纸上对比更干净）
COLOR_MARKS = {"r": "#c0392b", "y": "#8a6a00", "g": "#1e7a52", "x": "#888"}
# 状态符号（表格内自动着色）：● 强/红 ○ 中/金 · 弱/灰 × 无数据/浅灰（对齐 A 股红涨绿跌）
SWIM_SYMBOLS = {"\u25cf": "#c0392b", "\u25cb": "#9a7114", "\u00b7": "#888", "\u00d7": "#bbb"}
# 涨跌幅（表格内自动着色）：+ 红 / - 绿（A股红涨绿跌，别反）
UP_COLOR = "#c0392b"
DOWN_COLOR = "#1e7a52"


# =====================================================================
# 一、Markdown 预处理
# =====================================================================
def fix_lists(md_text):
    """段落紧邻列表修复（2026-09-03 知夏评审 TOP2）：
    markdown 库对「段落行直接跟列表项」（无空行）不解析 <ul>，会整块变裸段落。
    在「非块元素行」与「下一行列表项」之间补空行，强制列表解析。
    不处理 blockquote（> 开头）、表格（|）、标题（#）、已有列表行。"""
    lines = md_text.split("\n")
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        cur = line.strip()
        nx = nxt.strip()
        is_list_item = nx.startswith(("- ", "* ", "+ ")) or bool(re.match(r"^\d+\.\s", nx))
        if (cur and not cur.startswith((">", "#", "|", "- ", "* ", "+ "))
                and not re.match(r"^\d+\.\.?\s", cur)
                and nx and is_list_item):
            out.append("")
    return "\n".join(out)


# =====================================================================
# 二、冻结接口（engine/server.py 依赖，签名与返回结构不可变）
# =====================================================================
def colorize_td_symbols(html):
    """表格单元格内状态符号自动着色（仅表格内，正文不动）
    v2：保留内联 style（server.py 渲染路径无本 CSS，靠内联色），另加 class 供 CSS 增强。"""
    def repl(m):
        cell = m.group(0)
        for sym, c in SWIM_SYMBOLS.items():
            if sym in cell:
                cls = "sw"
                cell = cell.replace(sym, '<span class="%s" style="color:%s">%s</span>' % (cls, c, sym))
        return cell
    return re.sub(r'<t[dh][^>]*>.*?</t[dh]>', repl, html, flags=re.S)


def colorize_td_pct(html):
    """表格单元格内涨跌幅数字自动着色：+红 -绿（A股红涨绿跌，东财口径）；仅 td 内，正文不动。
    v2 三处修正：
      1) 支持全角/排版减号 −(U+2212) －(U+FF0D) –(U+2013)，旧版只认 ASCII '-'，导致
         「−6.54%」整列漏着色（MD 里用的就是 U+2212）；
      2) 数字前一位是数字时不着色（"30-50" 这种区间不再被染成 -50）；
      3) 只替换标签外的文本，绝不污染 style/class 属性。
    v2.1（2026-09-16 修金额误染）：**只染以 % / ％ 结尾的数字**。
      复盘 MD 的表格内不带 % 的正负数就是金额/股数/板数（如持仓表「浮盈亏 -612 / +413」），
      不是涨跌幅，染成红绿会严重误导。判据：sign + 数字 + 可选空格 + % 或 ％。
    """
    pct_re = re.compile(r'(?<![\d.])([+＋]|[-−－–])(\d+(?:\.\d+)?)\s*[%％]')

    def sub(mm):
        sign, num = mm.group(1), mm.group(2)
        up = sign in ("+", "\uff0b")
        color = UP_COLOR if up else DOWN_COLOR
        cls = "pct pct--up" if up else "pct pct--down"
        tail = mm.group(0)[len(sign) + len(num):]   # 数字与 % 之间可能有的空格，原样保留
        return '<span class="%s" style="color:%s">%s%s%s</span>' % (cls, color, sign, num, tail)

    def repl(m):
        cell = m.group(0)
        # 只在标签外的文本片段上着色，避免误伤属性值
        parts = re.split(r'(<[^>]+>)', cell)
        return "".join(p if p.startswith("<") else pct_re.sub(sub, p) for p in parts)

    return re.sub(r'<t[dh][^>]*>.*?</t[dh]>', repl, html, flags=re.S)


def apply_color_marks(html):
    """{r}文字{r} 等色标记 → 带色 span（未闭合标记保持原样，安全）。
    v1.2.3：单开标记（{k}原文 无闭标记）到行尾自动闭合，防未闭合 span 红染下一段（知夏评审 TOP2）。
    v2：同上保留内联 style，另加 class。"""
    if html:
        for k, c in COLOR_MARKS.items():
            html = re.sub(r'\{' + k + r'\}(.+?)\{' + k + r'\}',
                          lambda m: '<span class="cm cm-%s" style="color:%s">%s</span>' % (k, c, m.group(1)),
                          html, flags=re.S)
            # 单开标记：{k}后不再出现闭标记 → 到行尾闭合
            html = re.sub(r'\{' + k + r'\}([^{}\n]*)$',
                          lambda m: '<span class="cm cm-%s" style="color:%s">%s</span>' % (k, c, m.group(1)),
                          html, flags=re.M)
    return html


# =====================================================================
# 三、小工具
# =====================================================================
def load_css():
    if os.path.exists(CSS_PATH):
        with open(CSS_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def sanitize_filename(name):
    """把标题变成安全的文件名（保留中文）"""
    # 移除或替换 Windows 文件名非法字符
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # 限制长度
    if len(name) > 80:
        name = name[:80]
    return name


def get_output_dir():
    """返回输出目录: 夏天-activity/每日复盘/（2026-08-29 主人定，复盘文件直放）
    2026-09-02 修跨日坑：文件名日期优先从标题提取（如「日复盘 2026-09-02」），
    不取系统日期——凌晨跨 0 点跑不会把前天复盘命名成新日期。"""
    today = datetime.now().strftime("%Y-%m-%d")
    workspace = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
    out_dir = os.path.join(workspace, "Desktop", "OH-WorkSpace", "夏天-activity", "每日复盘")
    return out_dir


_TITLE_DATE = re.compile(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})')


def title_date(title):
    """从标题提取输出日期（YYYY-MM-DD）；无匹配时回退系统日期。"""
    m = _TITLE_DATE.search(title or "")
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return datetime.now().strftime("%Y-%m-%d")


def strip_tags(s):
    return unesc(re.sub(r'<[^>]+>', '', s or ""))


def md_inline(text):
    """行内 markdown → HTML（去掉外层 <p>），用于速览带这种手工拼装的片段。"""
    try:
        h = markdown(text or "", extensions=[], output_format="html5").strip()
    except Exception:
        return esc(text or "")
    return re.sub(r'(^<p>|</p>$)', '', h)


# =====================================================================
# 四、标题 / 速览带（报头区）
# =====================================================================
def extract_title_and_meta(md_text):
    """从 markdown 提取 h1 作为标题，📅 行作为自定义 meta。"""
    lines = md_text.split("\n")
    title = ""
    date_str = ""
    custom_meta = ""
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            content_start = i + 1
        elif stripped.startswith("📅") and not custom_meta:
            custom_meta = stripped
            content_start = i + 1
        elif stripped.lower().startswith("date:") or stripped.lower().startswith("日期:"):
            date_str = stripped.split(":", 1)[1].strip()
            if content_start <= i:
                content_start = i + 1
        elif stripped == "" and content_start <= i + 1:
            continue
        elif not title:
            continue
        else:
            break

    if not title:
        for line in lines:
            s = line.strip()
            if s and not s.startswith("date:") and not s.startswith("日期:") and not s.startswith("📅"):
                title = s.lstrip("#").strip()
                break

    if not date_str and not custom_meta:
        date_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    body = "\n".join(lines[content_start:]).strip()
    return title, date_str, body, custom_meta


# 速览带可识别的行首标签（只有这些才从正文提升到报头，其余 > 引用照旧当语义卡）
BRIEF_LABELS = ("数据时点", "口径", "温度", "指令", "执行说明", "备注")


def _split_brief_label(text):
    m = re.match(r'^\*{0,2}\s*([^：:]{2,8}?)\s*[：:]\s*(.+)$', text, re.S)
    if m and m.group(1).strip() in BRIEF_LABELS:
        return m.group(1).strip(), m.group(2).strip()
    return None, text.strip()


def parse_temp(text):
    """「11°C 极寒档（cap_effective=48%，战略震荡 x0.8）」→ 温度表盘数据"""
    m = re.match(r'^\s*(-?\d+(?:\.\d+)?)\s*[°度]?\s*[CcC℃]?\s*(.*)$', text or "")
    if not m:
        return None
    val = float(m.group(1))
    rest = (m.group(2) or "").strip()
    label, meta = rest, ""
    m2 = re.search(r'[（(]([^）)]*)[）)]', rest)
    if m2:
        label = rest[:m2.start()].strip(" ··-—")
        meta = m2.group(1).strip()
    label = label.strip(" ·")
    band = "cold" if val < 20 else "cool" if val < 40 else "mid" if val < 60 else "warm" if val < 80 else "hot"
    shown = ("%g" % val)
    return {"val": shown, "label": label, "meta": meta, "band": band}


def extract_briefing(md_text):
    """把正文开头（第一个 ## 之前）的「> 数据时点 / 口径 / 温度 / 备注」提升为报头速览带。
    非播报型引用（🎯🚨📌⚠💡📊 开头的语义卡）原样留在正文。
    返回 (items, temp, cleaned_md)，items = [(label, text), ...]

    v2.1（2026-09-16 修报头温度渲染两次）：label == "温度" 只喂给温度表盘，**不再进 items**，
    否则速览带会出现 `<dl class="bi"><dt>温度</dt>` 与左侧 .temp 表盘重复。
    兜底：parse_temp 解析不出数值时（如「温度：极寒档」无数字）仍退回 items，避免信息丢失。
    """
    lines = md_text.split("\n")
    items, temp = [], None
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    start, j = None, i
    while j < len(lines):
        s = lines[j].strip()
        if s.startswith("#"):
            break
        if s.startswith(">"):
            body = s.lstrip(">").strip()
            label, text = _split_brief_label(body)
            if label is None and "**本次" in body[:12]:
                label = "备注"
            if label is None:
                break  # 不是播报行（大概率是 🎯/💡 语义卡）→ 停止，交还正文
            if start is None:
                start = j
            if label == "温度":
                temp = parse_temp(text)
                if temp is not None:
                    j += 1
                    continue   # 温度只走表盘，不进速览带
            items.append((label, text))
            j += 1
        elif s == "" and start is not None:
            j += 1
        elif s == "":
            j += 1
        else:
            break
    if not items and temp is None:
        return [], None, md_text   # 没有播报行 → 原样返回（旧 MD 兜底，结构与 v2 一致）
    if start is None:
        start = j
    cleaned = "\n".join(lines[:start] + lines[j:])
    return items, temp, cleaned


_TITLE_FULL = re.compile(r'^\s*(.*?)[\s　]*(\d{4}[-年]\d{1,2}[-月]\d{1,2})\s*[（(]([^）)]*)[）)]\s*$')
_WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _weekday_of(date_ymd):
    try:
        return _WEEKDAY[datetime.strptime(date_ymd, "%Y-%m-%d").weekday()]
    except Exception:
        return ""


def parse_title(title):
    """「日复盘 2026-09-15（周二 · 普跌延续第八日 · 跌停潮 · 科创50独红）」
       → (prefix, date, weekday, [chips...])；非日复盘标题自动退化。"""
    t = (title or "").strip()
    m = _TITLE_FULL.match(t)
    if not m:
        return t, "", "", []
    prefix, date_raw, inner = m.group(1).strip(), m.group(2), m.group(3).strip()
    d = _TITLE_DATE.search(date_raw)
    date = "%s-%02d-%02d" % (d.group(1), int(d.group(2)), int(d.group(3))) if d else date_raw
    wd, rest = "", inner
    m2 = re.match(r'^\s*(周[一二三四五六日天]|星期[一二三四五六日天])\s*[·•、,，\-—]?\s*(.*)$', inner)
    if m2:
        wd, rest = m2.group(1).replace("星期", "周").replace("周天", "周日"), m2.group(2)
    chips = [c.strip() for c in re.split(r'[·•|｜]', rest) if c.strip()]
    if not wd:
        wd = _weekday_of(date)
    return (prefix or "每日复盘"), date, wd, chips


def build_masthead(title, date_str, custom_meta, items, temp, notes=""):
    """报头（墨底通栏 + 速览带）：三秒拿到今天最重要的一句话。

    notes：正文最前面的元信息（数据时点/降级项这类），收编进速览带末尾的小灰字，
    不再占正文首屏（_take_leading_meta 摘出来传进来）。
    """
    prefix, date, weekday, chips = parse_title(title)
    if not date:
        date = title_date(title)
        weekday = _weekday_of(date)
    if not chips:
        chips = [prefix] if prefix else [title]
        head_line = esc(unesc(title))
        prefix_show = "每日复盘"
    else:
        head_line = '<i>·</i>'.join(esc(c) for c in chips)
        prefix_show = prefix or "每日复盘"

    date_line = esc(date) + (" · " + esc(weekday) if weekday else "")

    temp_html = ""
    if temp:
        meta = esc(temp["meta"]) if temp["meta"] else ""
        temp_html = (
            '<div class="temp" data-band="%s">'
            '<div class="temp-val">%s<i>°C</i></div>'
            '<div class="temp-lab">%s</div>'
            '%s'
            '</div>' % (temp["band"], esc(temp["val"]), esc(temp["label"] or "—"),
                        '<div class="temp-meta">%s</div>' % meta if meta else '')
        )

    bis = []
    for label, text in items:
        bis.append('<dl class="bi"><dt>%s</dt><dd>%s</dd></dl>' % (esc(label), md_inline(text)))
    items_html = '<div class="brief-items">%s</div>' % "".join(bis) if bis else ""
    notes_html = '<div class="brief-notes">%s</div>' % notes if notes else ""
    brief_html = ""
    if items_html or notes_html:
        brief_html = ('<div class="brief%s">%s%s%s</div>'
                      % (" is-solo" if not temp_html else "", temp_html, items_html, notes_html))
    elif temp_html:
        brief_html = '<div class="brief is-solo">%s</div>' % temp_html

    foot = ""
    if custom_meta:
        foot = esc(unesc(custom_meta))
    elif date:
        # 报头日期优先用标题里的日期（跨日也不掉链），不写系统渲染时间
        foot = "数据日期 %s%s" % (esc(date), (" · " + esc(weekday)) if weekday else "")

    # v2.1：速览带（.brief）并入 <header> —— 报头区作为一个整体元素，
    # 便于「报头内某文本出现几次」这类校验（如温度「极寒档」应恰好 1 次）。
    return (
        '<header class="masthead">'
        '<div class="mh-band">'
        '<div class="mh-bar"><span class="mh-brand">%s<i>DAILY REVIEW</i></span>'
        '<span class="mh-date">%s</span></div>'
        '<h1 class="mh-title">%s</h1>'
        '%s'
        '</div>'
        '%s'
        '</header>' % (
            esc(prefix_show), date_line, head_line,
            '<div class="mh-foot">%s</div>' % foot if foot else "",
            brief_html
        )
    )


# =====================================================================
# 五、八节节奏（〇~七：重节/轻节 + 色调）
# =====================================================================
_TONES = (
    ("情景", "scenario"), ("应对", "scenario"),
    ("温度", "temp"),
    ("消息", "news"),
    ("板块", "sector"), ("复板块", "sector"),
    ("情绪", "mood"),
    ("选股", "pick"),
    ("池内", "pool"), ("买点", "pool"),
    ("持仓", "hold"), ("纪律", "hold"),
    ("五日线", "signal"), ("信号", "signal"),
    ("盯盘", "watch"), ("明日", "watch"),
    ("自查", "check"), ("收尾", "check"),
)
# 重节（数据/决策）：白底 + 双线 + 实心序号
_MAJOR_TONES = ("temp", "sector", "mood", "hold", "scenario")
_SEC_NUM = re.compile(r'^\s*([〇零一二三四五六七八九十\d]{1,3})\s*[、\.．]\s*(.+)$')
_SEC_NOTE = re.compile(r'[（(]([^）)]*)[）)]\s*$')


def _tone_of(text):
    for key, tone in _TONES:
        if key in text:
            return tone
    return "note"   # 未命中关键词（非日复盘文档）→ 中性 tone，不冒充「消息面」


def decorate_sections(html):
    """h2 → 报头式节标题，并把每节内容包进 <section>（供 CSS 做节与节的节奏差）"""
    parts = re.split(r'(?=<h2>)', html)
    out = []
    for p in parts:
        m = re.match(r'<h2>(.*?)</h2>', p, re.S)
        if not m:
            out.append(p)
            continue
        raw = strip_tags(m.group(1))
        num, title, note = "", raw, ""
        mn = _SEC_NUM.match(raw)
        if mn:
            num, title = mn.group(1), mn.group(2).strip()
        mnote = _SEC_NOTE.search(title)
        if mnote:
            note = mnote.group(1).strip()
            title = title[:mnote.start()].strip()
        tone = _tone_of(raw)
        cls = "sec sec--%s tone-%s" % ("major" if tone in _MAJOR_TONES else "minor", tone)
        head = (
            '<h2 class="sec-h">'
            + ('<span class="s-num">%s</span>' % esc(num) if num else '')
            + '<span class="s-t">%s</span>' % esc(title)
            + ('<span class="s-note">%s</span>' % esc(note) if note else '')
            + '<span class="s-rule"></span></h2>'
        )
        out.append('<section class="%s">%s%s</section>' % (cls, head, p[m.end():]))
    return "".join(out)


# =====================================================================
# 六、表格增强（列语义 → 涨跌幅列当主角）
# =====================================================================
_PCT_KEYS = ("涨跌", "当日", "3日", "5日", "距锚", "涨幅", "跌幅")
_NUM_KEYS = ("家数", "收盘", "成交额", "得分", "量", "成本", "价格", "股数", "板")


def _col_kind(text, idx):
    t = (text or "").strip()
    if idx == 0:
        return "lbl"
    if any(k in t for k in _PCT_KEYS):
        return "num pctcol"
    if any(k in t for k in _NUM_KEYS):
        return "num"
    if re.fullmatch(r'[\d\.,]+', t):
        return "num"
    return ""


def _add_classes(block, tag, kinds):
    idx = [0]

    def _r(m):
        i = idx[0]
        idx[0] += 1
        k = kinds[i] if i < len(kinds) else ""
        if not k:
            return m.group(0)
        t = m.group(0)
        if "class=" in t:
            return re.sub(r'class="', 'class="%s ' % k, t, count=1)
        return t[:-1] + ' class="%s">' % k

    return re.sub(r'<%s\b[^>]*>' % tag, _r, block)


def _mark_chips(block):
    """整格只是一个涨跌数字 → 加 chip 类（带底色芯片，让涨跌幅列成为视觉主角）"""
    def td_sub(m):
        cell = m.group(0)
        if "pct " not in cell and 'pct"' not in cell:
            return cell
        txt = strip_tags(cell).strip()
        if re.fullmatch(r'[+＋\-−－–]\d+(?:\.\d+)?[%％]', txt):
            return cell.replace('class="pct ', 'class="pct chip ')
        return cell
    return re.sub(r'<td\b[^>]*>.*?</td>', td_sub, block, flags=re.S)


def enhance_tables(html):
    """按表头语义给列上类：首列 lbl / 涨跌幅列 num pctcol / 其它数字列 num（右对齐等宽）"""
    def repl(m):
        tbl = m.group(0)
        hm = re.search(r'<thead[^>]*>.*?</thead>', tbl, re.S)
        if not hm:
            return _mark_chips(tbl)
        ths = re.findall(r'<th[^>]*>(.*?)</th>', hm.group(0), re.S)
        kinds = [_col_kind(strip_tags(t), i) for i, t in enumerate(ths)]
        head_html = _add_classes(hm.group(0), "th", kinds)
        tbl = tbl[:hm.start()] + head_html + tbl[hm.end():]

        def row_sub(rm):
            return _add_classes(rm.group(0), "td", kinds)

        tbl = re.sub(r'<tr\b[^>]*>.*?</tr>', row_sub, tbl, flags=re.S)
        return _mark_chips(tbl)

    return re.sub(r'<table\b[^>]*>.*?</table>', repl, html, flags=re.S)


# =====================================================================
# 七、图表 / 语义卡
# =====================================================================
def enhance_figures(html):
    """独立成段的图片 → figure + figcaption（图 N）"""
    def repl(m):
        attrs, = m.groups()
        src = re.search(r'src="([^"]+)"', attrs)
        alt = re.search(r'alt="([^"]*)"', attrs)
        if not src:
            return m.group(0)
        cap = esc(unesc(alt.group(1))) if alt else ""
        return ('<figure class="chart"><img src="%s" alt="%s">%s</figure>'
                % (src.group(1), cap,
                   '<figcaption>%s</figcaption>' % cap if cap else ''))
    return re.sub(r'<p>\s*<img((?:(?!>).)*)>\s*</p>', repl, html, flags=re.S)


_SEMANTIC_BLOCKS = (
    ("🎯", "takeaway"),   # 结论
    ("🚨", "alert"),      # 警示
    ("📌", "keycard"),    # 重点
    ("⚠", "warning"),    # 注记（技术备注/口径，不占用风险红）
    ("💡", "highlight"),  # 高亮
    ("📊", "highlight"),  # 高亮
)
# 未命中语义标记的引用（口径脚注 / 梯队结构 / 替代观察 / 指令说明…）统一进默认引用样式，
# 不再以裸 blockquote 形式出现在纸面上（v2.2：配合 apply_semantic_blocks 的配平重写）
_DEFAULT_QUOTE_CLASS = "quote"
# 结论带三兄弟（🎯 一句话 / 📌 三件事 / 🚨 次日方向）：口径要求它们各自独立成块、排在最前
_CONCLUSION_CLASSES = ("takeaway", "keycard", "alert")
_MARK_CLASS = dict(_SEMANTIC_BLOCKS)
# 「行首语义标记」切分点：换行 + 可选缩进 + 紧跟标记字符（只在标签外的换行处生效）
_MARK_LINE = re.compile(r"\n[ \t]*(?=(?:%s))" % "|".join(re.escape(m) for m, _ in _SEMANTIC_BLOCKS))
# 顶层元素扫描（拆 blockquote 内容用）：区分开/闭标签、属性里带引号不误判、自闭标签不入栈
_TAG_TOKEN = re.compile(r"<(/?)([a-zA-Z][\w-]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>")
_VOID_TAGS = frozenset(("br", "img", "hr", "input", "meta", "link", "col",
                        "area", "base", "source", "wbr"))


def _quote_units(inner):
    """blockquote 内容 → [(kind, html)]，kind 为 "p"（顶层段落）或 "raw"（其余裸内容）。

    只认 depth==0 的 <p>：嵌套结构（已转成 div 的内层引用、列表、表格）整块当 raw，
    绝不从中间切开——切在 <div> 与其子元素之间会让容器开闭错位。
    """
    units, pos, depth, p_start = [], 0, 0, None
    for m in _TAG_TOKEN.finditer(inner):
        closing, name, selfclose = bool(m.group(1)), m.group(2).lower(), m.group(4) == "/"
        if not closing:
            if name == "p" and depth == 0 and p_start is None:
                if m.start() > pos:
                    units.append(("raw", inner[pos:m.start()]))
                p_start = m.start()
            if name in _VOID_TAGS or selfclose:
                continue
            depth += 1
        else:
            depth = max(0, depth - 1)
            if name == "p" and depth == 0 and p_start is not None:
                units.append(("p", inner[p_start:m.end()]))
                p_start = None
                pos = m.end()
    if pos < len(inner):
        units.append(("raw", inner[pos:]))
    return units


def _split_at_mark_lines(html):
    """<p> 内容按「行首语义标记」切段（MD 里 🎯📌🚨 常挤在同一段，靠换行分隔）。

    只在**标签外**的换行处切（文本 token 内匹配，标签整体不参与），且要求切点处
    内联标签已闭合（depth==0）——否则会把 <strong> 拦腰截断，浏览器自动纠错后
    容易出现半边加粗、半边灰字的脏排版。切不动就并回去，宁可不切也不出坏结构。
    """
    out, buf, depth = [], [], 0
    for tok in re.findall(r"<[^>]+>|[^<]+", html):
        if tok.startswith("<"):
            m = _TAG_TOKEN.match(tok)
            if m:
                if m.group(1):
                    depth = max(0, depth - 1)
                elif not m.group(4) and m.group(2).lower() not in _VOID_TAGS:
                    depth += 1
            buf.append(tok)
            continue
        segs = _MARK_LINE.split(tok)
        buf.append(segs[0])
        for s in segs[1:]:
            if depth == 0:
                out.append("".join(buf))
                buf = [s]
            else:
                buf.append("\n")
                buf.append(s)
    out.append("".join(buf))
    return [s for s in out if s.strip() and strip_tags(s).strip()]


def _mark_class(html_seg):
    """段首语义标记 → 块 class；无标记返回 None。"""
    text = strip_tags(html_seg).strip()
    for mark, cls in _SEMANTIC_BLOCKS:
        if text.startswith(mark):
            return cls
    return None


def _quote_to_blocks(inner):
    """一个 blockquote 的内容 → 若干完整 <div class="...">（语义块 / 默认引用块）。

    v3（2026-11-08 修「结论带被埋」）：统计口径——Python-Markdown 会把**相邻**的
    `> a` / 空行 / `> b` 合并进同一个 <blockquote>，段内软换行也不拆 <p>。所以
    「看 blockquote 首字符」只能识别第一段，🎯📌🚨 全被埋进 12.8px 的 .quote。
    改为先把引用拆到「段内行首标记」这一粒度，再逐段定级：
      · 有标记 → 各自独立成块（takeaway / keycard / alert / warning / highlight）
      · 无标记 → 并入上一块（续行语义），没有上一块才新开 .quote
      · 本引用内含 🎯/📌/🚨 时，它们**之前**的段落一律降为 .meta（元信息，
        见 _take_leading_meta：会被收编进报头速览带，不跟结论带挤同一视觉单元）
    """
    parts = []                       # [class 或 None, 完整 html 片段]
    for kind, chunk in _quote_units(inner):
        if kind == "p":
            body = re.sub(r"^<p(?:\s[^>]*)?>", "", chunk)
            body = body[:-4] if body.endswith("</p>") else body
            for seg in _split_at_mark_lines(body):
                parts.append([_mark_class(seg), "<p>%s</p>" % seg])
        else:
            if chunk.strip():            # 纯空白的裸内容丢掉，别让它单独撑出一个空块
                parts.append([None, chunk])
    if not parts:
        return ""
    first_conc = next((i for i, p in enumerate(parts) if p[0] in _CONCLUSION_CLASSES), None)
    groups = []                      # [class, [html 片段]]
    for i, (cls, html) in enumerate(parts):
        if first_conc is not None and i < first_conc:
            cls = "meta"             # 结论带之前 = 元信息，降到第三层
        if cls is None:
            if groups:
                groups[-1][1].append(html)
            else:
                groups.append([_DEFAULT_QUOTE_CLASS, [html]])
            continue
        if groups and groups[-1][0] == cls == "meta":
            groups[-1][1].append(html)
            continue
        groups.append([cls, [html]])
    return "".join('<div class="%s">%s</div>' % (c, "".join(h)) for c, h in groups)


_META_HEAD = re.compile(r'\A\s*(?:<div class="meta">[\s\S]*?</div>\s*)+')


def _take_leading_meta(html):
    """摘出正文最前面的元信息块（数据时点/口径/降级项这类前导注记）。

    返回 (去掉元信息的正文, 元信息内层 html)。由 convert_md_to_html 交给报头速览带
    收编（口径：元信息 ≤12px 灰字、归第三层，不得与结论带同视觉单元）。
    """
    m = _META_HEAD.match(html or "")
    if not m:
        return html, ""
    notes = "".join(re.findall(r'<div class="meta">([\s\S]*?)</div>', m.group(0)))
    return html[m.end():], notes


def apply_semantic_blocks(html):
    """blockquote 语义分块（MD 保持纯文本可读，视觉层次交给 CSS）

    v2.2（2026-09-16 修「标签失衡导致版面崩坏」P0）：
      旧实现 = 两步字符串替换：
        ① <blockquote>\\n<p>MARK  →  <div class="cls">\\n<p>MARK   （只换「命中语义」的开标签）
        ② 所有 </blockquote>      →  </div>                        （无差别全换）
      ⇒ N 个引用里只有 M 个命中语义时，凭空多出 (N-M) 个 </div>。
      09-15 实例：6 个引用 / 1 个命中 ⚠ ⇒ 多 5 个 </div>，第一个就在字符位 3885 处
      提前关闭 <div class="sheet">，导致「〇」之后的一~九节全部脱出纸面（宽 1379、left 16）。

      新实现 = **单遍扫描 + 显式栈**，开闭严格一一对应：
        · 命中语义标记 → <div class="takeaway|alert|keycard|warning|highlight"> ... </div>
        · 未命中       → <div class="quote"> ... </div>（默认引用样式，不再裸奔）
        · 栈内残留     → 结尾补闭合（兜底，保证 div / blockquote / section 三类容器全部配平）
      副作用可控：语义卡视觉不变；非语义引用（口径脚注/梯队结构/替代观察/指令说明）
      统一拿到 .quote 样式，不再是没有归属的裸 blockquote。
      统一用 div（而非给 blockquote 挂 class）是刻意的：容器内只有一种元素类型时，
      「open 数 == close 数」这条机械校验才不会被带属性的开标签漏计（<blockquote class=…>）。
      面板路径（server.py._md_to_body_html）不经过本函数，裸 blockquote 的兜底样式保留在 CSS。
    """
    if "<blockquote>" not in html and "</blockquote>" not in html:
        return html

    # 单遍扫描 + 层栈：每个 </blockquote> 就地把它那一层内容转成若干完整 <div> 块，
    # 开闭严格一一对应（内层先转完，外层拿到的内容里已无裸引用）。
    bufs = [[]]
    pos = 0
    for m in re.finditer(r'<blockquote>|</blockquote>', html):
        bufs[-1].append(html[pos:m.start()])
        pos = m.end()
        if m.group(0) == "<blockquote>":
            bufs.append([])
        elif len(bufs) > 1:                             # 配对的闭合：就地出块
            inner = "".join(bufs.pop())
            bufs[-1].append(_quote_to_blocks(inner))
        # 无主闭合直接丢弃：它就是旧实现凭空造出的多余 </div> 的来源，
        # 留着只会提前关掉 .sheet，丢掉不丢信息。
    bufs[-1].append(html[pos:])
    while len(bufs) > 1:                                # 兜底：未闭合的引用按嵌套收口（不丢文字）
        inner = "".join(bufs.pop())
        bufs[-1].append(_quote_to_blocks(inner))
    return "".join(bufs[0])


# =====================================================================
# 八、组装
# =====================================================================
DISCLAIMER = ("本报告由 TdxClaw 报告管线离线生成，数据源见正文各处标注；"
              "内容仅用于个人研究与复盘记录，不构成任何投资建议，据此操作风险自负。")


def wrap_html(title, date_str, body_html, css, custom_meta="", masthead_html=""):
    """组装整页。masthead_html 缺省时退化为朴素报头（兼容旧调用）。
    v2.1：速览带 .brief 已并入 masthead_html 内部，不再单独传 brief_html。"""
    if custom_meta:
        meta_html = custom_meta
    else:
        meta_html = "📅 %s" % date_str if date_str else ""
    if not masthead_html:
        masthead_html = ('<header class="masthead mh-simple">'
                         '<div class="mh-band">'
                         '<div class="mh-bar"><span class="mh-brand">%s<i>REPORT</i></span></div>'
                         '<h1 class="mh-title">%s</h1>%s</div></header>'
                         % (esc("每日复盘"), esc(unesc(title)),
                            '<div class="mh-foot">%s</div>' % esc(unesc(meta_html)) if meta_html else ""))
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%(title)s</title>
<style>
%(css)s
</style>
</head>
<body>
<div class="sheet">
%(masthead)s
<main class="doc">

%(body)s

</main>
<footer class="colophon">
  <div class="disc">%(disc)s</div>
  <div class="mark">OFFLINE · NO-JS · report-builder v2</div>
</footer>
</div>
</body>
</html>""" % {
        "title": esc(unesc(title)),
        "css": css,
        "masthead": masthead_html,
        "body": body_html,
        "disc": esc(DISCLAIMER),
    }


def convert_md_to_html(md_text, css):
    title, date_str, body_md, custom_meta = extract_title_and_meta(md_text)
    # 报头速览带（数据时点/口径/温度/备注）——从正文首部提升，正文不再重复
    items, temp, body_md = extract_briefing(body_md)
    body_md = fix_lists(body_md)  # 段落紧邻列表修复（v1.2.3，知夏评审 TOP2）
    body_html = markdown(
        body_md,
        extensions=["tables", "fenced_code", "codehilite", "sane_lists"],
        output_format="html5"
    )
    body_html = colorize_td_symbols(body_html)  # 表格内状态符号着色
    body_html = colorize_td_pct(body_html)      # 表格内涨跌幅红涨绿跌（含全角减号）
    body_html = enhance_tables(body_html)       # 列语义 → 涨跌幅列主角化
    body_html = apply_color_marks(body_html)    # 色标记语法（包裹的符号以标记色为准）
    body_html = re.sub(r'(<table>)', r'<div class="table-wrapper">\1', body_html)
    body_html = re.sub(r'(</table>)', r'\1</div>', body_html)
    body_html = apply_semantic_blocks(body_html)  # 🎯🚨📌⚠💡📊 语义卡（v3：段内逐段分块）
    body_html, meta_notes = _take_leading_meta(body_html)  # 文首元信息 → 报头速览带
    body_html = enhance_figures(body_html)        # 图表 figure/figcaption
    body_html = decorate_sections(body_html)      # 八节节奏
    masthead_html = build_masthead(title, date_str, custom_meta, items, temp, meta_notes)
    return title, date_str, wrap_html(title, date_str, body_html, css,
                                      custom_meta, masthead_html)
