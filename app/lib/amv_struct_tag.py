# -*- coding: utf-8 -*-
"""板块结构标签（口径唯一源，2026-10-27 重定）。

## 主人口径（2026-10-27 最终版）

「用更简单的对比方法，就是 AMV 的涨跌到底是由什么驱动的。
 为的就是可以筛选活跃市值虽然在涨，但价格一直在跌的，比如宁德时代、电池板块这种。
 这种即使站上多头区间也不可信。」

⇒ 比较两个**同窗口、同单位的百分比**，不拆分项、不做加权：

    amv_pct  = AMV 涨幅（%）
    px_pct   = 价格涨幅（%），板块用板块指数收盘，个股用不复权收盘
    gap      = amv_pct − px_pct   ← 换手贡献（单位 pp，可比可加）

四种组合（互斥且穷尽）：

    amv↑ 价↑   量价齐升      两者同向放大
    amv↑ 价↓   **换手虚涨**   ← 主人要筛的：「AMV 在涨但价格在跌」，不可信
    amv↓ 价↑   价格独涨      换手在退、价格在涨
    amv↓ 价↓   量价齐跌      两者同向走弱

为什么放弃分项判定（px/act/sh 五档分解）：
  · amv_contrib.parts 的 px 用**期末**活跃股本加权、act 用**期初**价格加权（混合基准），
    两者不同期，直接比大小有偏（实测 300432 因 a1/a0=1.109，两种基准差 10.9%）；
  · 该分解的价值在于「Σ = ΔAMV」逐分钱对账，不宜为比大小而改动；
  · 而主人的问题本质是「涨跌由什么驱动」，用两个百分比直接对比，零歧义。
  分项数值仍在 /contrib 端点保留，供详情页核对，但不参与徽标判定。

**阈值**：gap 只用于区分强弱，不改变四分类。默认不设门槛——
四分类本身就是答案；需要时可用 gap_min 过滤「微小虚涨」。

## ⚠ 已知偏差（2026-10-27 实测，不修但必须知道）

AMV 涨幅天然被放大，幅度随窗口递增（230 个 L3 板块 × 598 日实测）：

    窗口   |AMV|中位  |价格|中位   倍数   拟合 AMV% = a + b×价格%    b 的 R²
     1日     2.21%     1.08%     1.86×   +0.096 + 0.936×     0.395
     3日     5.02%     1.90%     2.45×   +0.437 + 1.195×     0.366
     5日     7.27%     2.53%     2.73×   +0.873 + 1.440×     0.373
    10日    11.60%     3.67%     3.09×   +2.084 + 1.878×     0.406
    20日    17.68%     5.40%     3.33×   +4.136 + 2.500×     0.496

两个后果（**用徽标时必须知道**）：

1. **系数 b > 1**：同样涨 1%，AMV 会涨 1.0~2.5%，所以「AMV↑ 价↓」不一定是异常，
   可能只是换手把价格的小跌放大成了 AMV 的正数。
2. **截距 a > 0**（20 日高达 +4.14%）：价格完全不动时 AMV 也会涨。
   ⇒ 本模块用 `amv_pct > 0` 判「涨」时，窗口越长越容易归到「涨」侧
   （实测 20 日有 87.0% 的样本 AMV>价格）。**长窗口下的「虚涨」名单偏多，含结构噪声。**

更公平的做法（残差法，已验未采用）：`resid = AMV% − (a + b×价格%)`，
按当日截面 p60 分「换手超常 / 换手萎缩」，再叠价格方向。5 日窗口 + 未来 20 日超额：

    价格↑ 换手超常   −0.259%  胜率 47.9%
    价格↑ 换手萎缩   +0.051%  50.6%
    价格↓ 换手超常   −0.130%  48.8%   ← 就是「虚涨」，但没直接比那么差
    价格↓ 换手萎缩   +0.165%  51.6%

（对比直接比：虚涨 −0.347%。差的那 0.22pp 就是放大造成的假象。）
**不采用的理**：要拟合 a、b，而拟合用的是全历史数据（前视）；改滚动窗口拟合会更准，
但主人 2026-10-27 判「越弄越复杂」，暂停。**在滚动拟合落地前，把本标签当「观察」而非「判据」。**

另外两条实测结论（支持本标签「不可信」方向成立，不必再查）：
  · 时间先后：AMV **不领先**价格。不重叠 5 日窗口 lead-lag：k=−5 时 r=+0.112（价格先动），
    k=+5 时 r=−0.078（AMV 在前则价格反向）。方向是 价格 → AMV。
  · 虚涨持续越久越差：1天 T+20 −0.30%（胜率 47.2%）→ 2天 −0.50% → 3~4天 −0.90%。
    若 AMV 是提前量，应反过来；实测单调恶化 ⇒ 是派发，不是吸筹。
"""
from __future__ import annotations

# 四类标签（短名，主人 2026-10-27 定「标签尽量简短」）
T_UP = '齐升'          # amv↑ 价↑
T_FAKE = '虚涨'        # amv↑ 价↓  ← 核心要筛的
T_PXONLY = '独涨'      # amv↓ 价↑
T_DN = '齐跌'          # amv↓ 价↓
T_NA = '—'

ALL_TAGS = [T_UP, T_FAKE, T_PXONLY, T_DN, T_NA]

FILTER_GROUPS = [
    {'k': 'all', 't': '全部'},
    {'k': T_FAKE, 't': '虚涨'},
    {'k': T_UP, 't': '齐升'},
    {'k': T_DN, 't': '齐跌'},
    {'k': T_PXONLY, 't': '独涨'},
]

TAG_HINT = {
    T_UP: 'AMV 与价格同向上涨',
    T_FAKE: 'AMV 在涨但价格在跌——换手抬起来的，多头区间也不可信',
    T_PXONLY: '价格在涨但 AMV 在缩——换手在退',
    T_DN: 'AMV 与价格同向下跌',
    T_NA: '该日缺 AMV 或价格数据',
}


def classify(amv_pct, px_pct, gap_min=0.0):
    """按「AMV 涨幅 vs 价格涨幅」判四类。

    amv_pct : AMV 区间涨幅（%）
    px_pct  : 价格区间涨幅（%）
    gap_min : 可选门槛；|gap| < gap_min 时归「齐跌/齐升」由符号决定，不额外标记

    → {'tag','gap','dir'}
      dir: 'up'/'dn'/'flat' —— 用**价格方向**，与表内「真实涨跌」一致
    """
    if amv_pct is None or px_pct is None:
        return {'tag': T_NA, 'gap': None, 'dir': 'flat'}

    gap = amv_pct - px_pct
    if gap_min and abs(gap) < gap_min:
        # 差额太小：只看价格方向，不算「虚涨」
        tag = T_UP if px_pct > 0 else (T_DN if px_pct < 0 else T_DN)
    elif amv_pct > 0 and px_pct > 0:
        tag = T_UP
    elif amv_pct > 0 and px_pct <= 0:
        tag = T_FAKE
    elif amv_pct <= 0 and px_pct > 0:
        tag = T_PXONLY
    else:
        tag = T_DN

    d = 'up' if px_pct > 0 else ('dn' if px_pct < 0 else 'flat')
    return {'tag': tag, 'gap': round(gap, 2), 'dir': d}


def display(res):
    """徽标文案 = 标签本身（四类全显示，这是分类不是异常提示）。"""
    if not res:
        return None
    return res.get('tag')


def compute_row(amv_pct, px_pct, gap_min=0.0):
    """算一行标签（含两个原始百分比与差额，供悬停核对）。"""
    r = classify(amv_pct, px_pct, gap_min)
    return {
        'tag': r['tag'],
        'label': display(r),
        'dir': r['dir'],
        'gap': r['gap'],
        'amv_pct': round(amv_pct, 2) if amv_pct is not None else None,
        'px_pct': round(px_pct, 2) if px_pct is not None else None,
    }
