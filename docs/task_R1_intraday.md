# 任务书 · 周末批次 R1 intraday 盘中卡（新插件 intraday）

> 派单：01 → 子代理 ｜ 2026-10-24 晚 ｜ 并发组 R1/5
> 方向：把旧「盘中」Tab 升级为 v2 插件卡，达到可替代水平。行为基准 = 旧 index.html renderIntraday（4687 行起）+ 盘中页全部数据块。

## 1. 目标

新建 `app/plugins/intraday/`（v0.1.0，mount=plugin，带卡 intraday.main），复刻旧盘中 Tab 的核心块：

1. **AMV 实时块**：/api/amv_live.json（读 panel_json: amv_live）——总值/indices/sectors/holdings 摘要（照旧 Tab 的信息层级，不做全量，摘要+TOP）
2. **恐慌广度（冰点卡）**：GET /api/panic（ops 插件已有）——当前值+状态+三项拆解+120 日序列迷你图
3. **广度 880**：GET /api/breadth_880.json（compat_sector 已有）
4. **板块轨迹**：GET /api/sector_track.json（amv_live_sector_track，10.5MB——注意只取摘要层，别全量渲染）
5. **盘中脉冲/指数快照**：/api/sector_intraday.json?codes=（compat_sector 已有）

刷新节奏：refreshEvery 20s（页面不可见跳过，core 机制已有）。

## 2. 环境事实

- 参考卡骨架：app/plugins/battle_plan/frontend.js（git 5762cc0）——防闪 setHtml、主题 token、事件委托同款
- 全部端点已就绪，零后端改动；plugin.json 的 data.panel 声明要补：amv_live（已有别的插件用过，照抄声明方式）
- 旧 Tab 行为基准文件只读：engine/web/index.html
- 卡片会经 v2bridge iframe 嵌入旧首页，卡内主题 token 跟 themes.css 走（旧页与 /v2/ 同源加载 themes.css 已可用）

## 3. 改动白名单

```
app/plugins/intraday/（backend.py 只需一个轻 summary 端点或直接纯前端消费现有根端点；plugin.json；frontend.js；tests/）
docs/intraday_contract.md
docs/HANDOFF_20261024_R1_intraday.md
```

优先纯前端卡（全部消费已有端点，backend 可为空模块或仅注册卡）——**能不加后端就不加**。

## 4. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest app/plugins/intraday/tests -q   # 若有
python -m pytest -q
python tools\cutover_gate.py
```

浏览器/CDP 实测：/v2/ 打开卡渲染无错、冰点卡有数（panic 端点活体 200）、AMV 摘要与旧盘中 Tab 同源一致。

## 5. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit 不重启 8810。
