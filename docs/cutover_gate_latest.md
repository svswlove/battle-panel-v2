# M4 切流门禁最新结果

- 时间：2026-11-18 19:22:45
- 结论：**PASS**
- 耗时：163.7s
- 端点：http://127.0.0.1:8810

| 项 | 结果 | 证据 |
|---|---|---|
| 8810 /api/health | PASS | HTTP 200 ok=True |
| 全部插件健康 | PASS | HTTP 200 n=23 bad=[] |
| 8810 接口结构回归 | WARN | exit=1 oneType']
  DRIFT amv.hcl: 8 处结构变化，例 ['新增 caliber.formula（str）', '新增 caliber.name（str）', '新增 caliber.note（str）']
  DRIFT battle_plan.plan: 6 处结构变化，例 ['缺失 market_context.overnight_news（原 empty_list）', '新增 market_context.overnight_news[].impact（str）', '新增 market_context.overnight_news[].mapping |
| AMV 快照 文件↔端点 | PASS | exit=0 amv_live: equal=True
amv_regime_latest: equal=True
position_cap_latest: equal=True |
| battle_plan 写域接管 | PASS | POST /api/sign -> HTTP 405（非 404=写域在）; POST /api/p/.../plan -> 405 (期望 405) |
| 插件无写代码 | PASS | clean (78 files) |
| kline 边界声明 | PASS | HTTP 200 intraday=False last=20261118 |
| pytest 全套 | PASS | exit=0           Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    @app.on_event("shutdown")

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2064 passed, 6 skipped, 225 warnings in 150.13s (0 |

## 说明

任一项 FAIL 即退出码 1；WARN 只记录不参与判定。本脚本不写生产数据、不改旧端口。
