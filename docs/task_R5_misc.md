# 任务书 · 周末批次 R5 遗留小项清尾（trade_mode 修复 + xdxr 口径 + STATUS 收口）

> 派单：01 → 子代理 ｜ 2026-10-24 晚 ｜ 并发组 R5/5
> 方向：清掉这两天累积的遗留小项，全部是小 diff。

## 1. 任务清单

**A. /api/status 的 trade_mode=null 修复（compat 插件 0.2.0→0.3.0）**
- 现象：engine_state.json 里 trade_mode=paper 完好，但 compat 的 api_status 返回 null——engine_state 不在其读取白名单
- 修：core paths.py 新增只读状态通道 READABLE_STATE（engine_state/kill_switch 对应文件，注意与 WRITABLE_STATE 同源但只读）+ contract.py ctx.state_json(name)（若 N2 已加过就用现成的，检查后接线）；compat 的 api_status 读 engine_state 的 trade_mode/kill_switch_active 字段
- 验收：/api/status 与 8800 同参逐字段一致（trade_mode=paper）

**B. xdxr 的 _tdx_market 口径缺陷登记或修复（ops 插件或 core quotes）**
- M1 遗留：`000001.SH`（上证指数）被当个股取到平安银行的除权记录——数字前缀判市场不识别指数
- 修：quotes.xdxr 里指数码（000001.SH 上证/399001.SZ 深指等）直接返回空列表（指数无除权）；测试补两例
- 两边一致性说明：旧端同样有此缺陷（两边一致），修了新版会更好——HANDOFF 登记「新版修正旧端缺陷」类差异

**C. STATUS.md 收口**
- 更新为冲刺后最新状态：R1~R4 完成情况、待验收项、周一盘前清单

## 2. 改动白名单

```
app/plugins/compat/backend.py + plugin.json + tests/（A）
app/core/quotes.py + tests/（B，最小 diff）
app/core/paths.py / contract.py（A 需要时的白名单通道，注意与并发单冲突时后到 rebase）
docs/STATUS.md（C）
docs/HANDOFF_20261024_R5_misc.md
```

## 3. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q
python tools\cutover_gate.py
# A 活体：/api/status vs 8800 逐字段
# B 活体：/api/kline/000001.SH/xdxr 返回空数组（旧端返回平安银行记录，登记差异）
```

## 4. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit 不重启。
