# 任务书 · 周末批次 R3 concept+review 概念与复盘卡

> 派单：01 → 子代理 ｜ 2026-10-24 晚 ｜ 并发组 R3/5
> 方向：旧「概念」「复盘」两 Tab 升级为 v2 插件卡。两个 Tab 的后端插件（concept/review）已存在且有根端点，本单是补**前端卡**。

## 1. 目标

给两个现有插件补 frontend 卡：

**concept（0.1.0→0.2.0，卡 concept.main）**
- 行为基准 = 旧概念 Tab（index.html 7690 行 renderConcept 起）：概念索引列表（活跃/观察/链位/归档分组）+ 点开概念详情（概况 MD 渲染）
- 端点：GET /api/concept.json 与 /api/concept.json?slug=（concept 插件已有，MD 知识库解析）
- 分组折叠交互照旧

**review（0.1.0→0.2.0，卡 review.main）**
- 行为基准 = 旧复盘 Tab（8014 行 renderReviewMd 起）：复盘文档渲染（v6）+ review_chart 图表 + paper=1 纸面版切换
- 端点：GET /api/review.md（?paper=1）、/api/review_chart.json（review 插件已有）
- 主题切换（md 渲染的 dark/light）跟 themes.css

## 2. 环境事实

- 两插件后端已全等验收过（HTML 逐字节相等/RV_DATA 全等）
- concept 的详情是 MD→HTML，参考旧 index.html 的 linkify 处理（个股名链接点开 K 线——v2 卡内先用 data-open-kline 属性占位，桥接后旧页可捕获）
- 卡骨架参考 battle_plan/paper

## 3. 改动白名单

```
app/plugins/concept/plugin.json + frontend.js（新建）+ tests/
app/plugins/review/plugin.json + frontend.js（新建）+ tests/
docs/concept_contract.md、docs/review_contract.md（各追加卡片节）
docs/HANDOFF_20261024_R3_concept_review.md
```

零后端改动。

## 4. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q
python tools\cutover_gate.py
```

实测：/v2/ 两卡渲染；概念分组/详情切换；复盘 v6/paper 双版；控制台零错误。

## 5. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit 不重启。
