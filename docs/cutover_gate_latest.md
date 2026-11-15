# M4 切流门禁最新结果

- 时间：2026-11-14 12:55:15
- 结论：**PASS**
- 耗时：1.1s
- 端点：http://127.0.0.1:8810

| 项 | 结果 | 证据 |
|---|---|---|
| 8810 /api/health | PASS | HTTP 200 ok=True |
| 全部插件健康 | PASS | HTTP 200 n=23 bad=[] |
| AMV 快照 文件↔端点 | PASS | exit=0 amv_live: equal=True
amv_regime_latest: equal=True
position_cap_latest: equal=True |
| battle_plan 写域接管 | PASS | POST /api/sign -> HTTP 405（非 404=写域在）; POST /api/p/.../plan -> 405 (期望 405) |
| 插件无写代码 | PASS | clean (77 files) |
| kline 边界声明 | PASS | HTTP 200 intraday=False last=20261113 |

## 说明

任一项 FAIL 即退出码 1；本脚本不写生产数据、不改旧端口。
