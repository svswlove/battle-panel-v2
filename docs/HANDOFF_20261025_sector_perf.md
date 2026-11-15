# 板块页性能整改 · 交接

2026-10-25 · 01

## 一、问题

主人报：8810 流畅度明显不如 8800/8002，在二级行业与三级行业之间切换尤其明显。

## 二、实测（同一台机器，交替点击，取数据就绪时刻）

改前：

| 操作 | 8002 | 8810 |
|---|---|---|
| 首次切三级 | 481ms，1 请求 | 3000ms，3 请求 |
| 切回二级 | 202ms，**0 请求** | 1000ms，4 请求 |
| 再切三级 | 166ms，**0 请求** | 1200ms，3 请求 |
| 切周期 20 天 | 无请求 | 1400ms，1 请求 |

## 三、三个根因（逐个量过）

1. **8002 首屏把数据一次拿齐，切层级只补 1 个请求**；8810 每次切层级都重新拉 `board_flow`。
   证据：8002 首屏 5 个请求（manifest / rank_recent / hcl_idx / sector_level / board_flow），
   切三级只新增 `rank_recent.json?level=L3` 一个。
2. **后端 `board_flow` 无缓存，每次全量重算**。
   拆解（L3 层 1.45s）：SQL 取数 ~0.7s（`sector_daily` 500 日窗口 15.6 万行）；
   `_dual_bull` ~0.7s（231 个板块 × 500 日双轨状态机）。同一请求连打两次都是 1.45s ⇒ 确认无缓存。
3. **取数天数取错**：v2 取 250 天，8002 只取 80 天（`manifest.n_days=80 / show_start=20`）。
   L3 层返回 287KB，8002 同层 92KB，差 3 倍。

**排除项**：前端渲染不是瓶颈。DOM 仅 537 个节点，切换耗时稳定 125ms；无长任务。

## 四、改了什么

| 位置 | 改动 |
|---|---|
| `backend.py` | 新增 `_FLOW_CACHE` / `_RECENT_CACHE` 两个进程内缓存，键 = (标签, 最新交易日, period, level)；`ds` 前进即自动失效，无需定时器；上限 64 条防膨胀 |
| `frontend.js` | 取数天数 250 → 80（与 8002 同口径）；新增 `_rrCache`（层级数据）与 `_flowCache`（资金快照），切回过的组合不再发请求；空 flow 不入缓存 |
| `frontend.js` | 换层级后 `dayA/dayB` 越界保护 |
| `tests/test_sector_perf.py` | 新增 6 例守门：缓存存在且键含日期、命中早于重算、有上限、不泄漏到写类端点、天数 80、卡内双缓存 |

**为什么缓存键带日期而不是定时失效**：收盘后新数据落库 ⇒ `ds` 变 ⇒ 键变 ⇒ 自动换新。
不需要 TTL、不需要定时器，也不会出现「缓存过期但数据已变」的窗口。

## 五、改后实测

后端（同一端点连打两次）：

| 端点 | 首次 | 第二次 |
|---|---|---|
| `rank_recent?level=L2` | 197ms | **6.6ms** |
| `rank_recent?level=L3` | 392ms | **7.4ms** |
| `board_flow?level=L2&period=3` | 621ms | **4.4ms** |
| `board_flow?level=L3&period=3` | 1453ms | **5.3ms** |
| `board_flow?level=L3`（前一日） | 1519ms | **4.1ms** |

端到端（点击 → 数据就绪，`setTimeout` 4ms 轮询，避开 rAF 节流）：

| 操作 | 8810 改后 | 8002 |
|---|---|---|
| 切三级（暖缓存） | 18 / 6 / 22ms | 16 / 5 / 16ms |
| 切二级（暖缓存） | 6 / 3ms | 4 / 4ms |
| **均值** | **11ms** | **9ms** |
| 冷启首次切三级 | 24ms | 333ms |

浏览器侧请求耗时：改前 1700ms 级，改后 **6–19ms**。

## 六、验证

- `pytest app/plugins/sector/tests app/tests` → **220 passed**
- 与 8002 逐格比对：**diffCount=0**（12 + 13 行 × 7 列全等）
- 四层级（L2/L3/概念/指数）与三视图（表格/网格/四象）逐项点过，零错误卡
- 8810 重启后健康检查 200，18 个插件全部挂载

## 七、测量方法上的坑（记录，避免重犯）

本轮前三次计时全部无效，原因是：

1. **rAF 被节流**：页面 `visibilityState` 是 `visible`，但 `requestAnimationFrame` 实测
   10 帧耗时 9.95s（约 1s/帧）。用 rAF 轮询会把任何操作都测成 800ms。
   改用 `setTimeout(check, 4)`（实测 10 次 17ms，不受节流影响）。
2. **中文字符串经 CDP 传参会失配**：`page.evaluate(fn, "三级")` 在页面里匹配不到按钮，
   全部返回 `no btn`。改为传索引、或在页面内用 `\uXXXX` 硬编码。
3. **`el.click()` 绕过命中测试**，不可作为交互验证依据（上一轮已踩过）。

## 八、未验证

- 盘中实时数值（验收时非交易时段，板块数据为 T-1 收盘口径）。
- 多用户并发下的缓存内存占用（单用户场景 64 条上限，实际不到 10 条）。
- 后端重启后**首次**请求仍是冷启动（L3 约 1.5s），这是可接受的；如需消除，
  可在启动钩子里预热常用层级。

## 九、回退

```
git checkout -- projects/panel-v2/app/plugins/sector/backend.py \
               projects/panel-v2/app/plugins/sector/frontend.js \
               projects/panel-v2/app/plugins/sector/tests/test_sector_contract.py
rm projects/panel-v2/app/plugins/sector/tests/test_sector_perf.py
```

回退后需重启 8810。缓存是纯进程内结构，回退无数据影响。
