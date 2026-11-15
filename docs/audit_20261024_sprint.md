# 旧界面 8810 托管 · 对抗性审查报告（2026-10-24）

> 审查人：嗅嗅（只读授权）｜对象：panel-v2 旧前端托管可用性
> 授权缺口说明：本席 exec_command/web_fetch(localhost)/write 均被只读授权拦截，A/B 项活体 HTTP 实测与 C 项 console 采集未执行；本报告为源码级静态取证（文件+行号背书），由 01 代为落盘。值语义一致性待活体复验。

## 判定

**8810 现状不能当日常入口。** 前端 fetch 点逐条与 8810 注册路由差集比对，除 01 自报 5 个 404 外，新发现 3 个未注册端点（均在被托管的旧仓前端 engine/web/ 中有调用）。

## 新发现缺口（01 未列）

| # | 端点 | 前端调用点 | 影响 |
|---|---|---|---|
| 1 | `GET /api/kline/{code}/xdxr` | kline_lc.js:3131（.catch→null 静默降级） | K线弹窗无复权事件，数据质量静默受损 |
| 2 | `POST /api/sector_track_backfill` | index.html:4050 | 追踪池回填触发死（status 端点有，触发端点无） |
| 3 | `GET /api/physical_ai.json` | index.html:7748（404→整条不渲染） | 概念 Tab 读数带消失，按设计优雅降级 |

## 分级清单

**P0（不动就不能当日常入口）×4**
1. `/api/panic`（index.html:4568，盘中 Tab 核心卡片：冰点监控）
2. `/api/module_switches_batch`（index.html:9071，模块开关批量写全废；battle_plan_rw/backend.py:70 自认写域不覆盖）
3. `/api/amv_refresh`（index.html:5277，盘中活跃市值手动刷新）
4. `/api/sector/restart`（index.html:7632，板块 Tab 一键拉起 8002）

**P1 ×5**
5. `kline-indicator-settings` GET+PUT（kline_lc.js:3453/3457，K线指标设置不持久化且静默）
6. xdxr（同新发现#1）
7. backfill 触发（同新发现#2）
8. `SECTOR_ORIGIN` 写死 http://127.0.0.1:8002（index.html:7183，板块 Tab iframe 仍依赖旧 8002 在线）
9. `shell/restart`/`shell/kill` 目标是 8800（main.py:158,383 注释明言「绝不碰 8810」——8800 退役前必须重定义）；急停存在双通道（抽屉走 8800 代理、面板内走原生 rw 写 engine/state），两路径写同一批文件，需确认语义合一

**P2 ×3**
10. physical_ai（同新发现#3）
11. `app/web/legacy/index.html` 是过期副本（与 engine/web/index.html 行号漂移、缺 physical_ai 段），虽不被路由使用但构成双源混淆
12. 零拷贝托管意味着旧仓前端一更新就可能引入新 404（漂移已实际发生），建议加路由差集巡检

## 其余审查项

- **B（源码级）**：写端点均注册且带 Pydantic 校验/显式 4xx（battle_plan_rw/backend.py:707-731、ops/backend.py:323-407、compat_watchlist/backend.py:931-1073）；注意 `POST /api/sign` 空 body 即全部签收并 ARMED（SignBody 缺省 + sign_plan backend.py:444-476），与旧端语义一致，保留风险。
- **C（源码级）**：shell.html → /legacy/themes.css → iframe /legacy/（engine/web，main.py:451-460）→ vendor/*.js 链路文件齐全，静态链无缺文件。
- **D**：shell/search、shell/status、shell/version 均注册（compat/backend.py:1517-1544）；主题/字号走同源 postMessage（shell.html:182,287），8810 下语义不变。

## 建议

P0 四端点补齐后重审；授权 HTTP 实测席位复跑 A/B 活体验证。
