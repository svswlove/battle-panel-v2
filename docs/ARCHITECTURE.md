# 架构说明

## 1. 目标形态

```
浏览器
  └─ panel_v2/web/shell.html + shell.js
       └─ GET /_plugins → 动态 import 插件 frontend.js → Panel 注册卡片
FastAPI 127.0.0.1:8810
  ├─ app/core/main.py        启动/插件扫描/路由挂载/健康
  ├─ app/core/contract.py    manifest 校验、PluginContext、加载器
  └─ app/plugins/<id>/       domain 插件（四件套）
       ├─ backend.py         def register(ctx) -> APIRouter
       ├─ frontend.js        export default { cards: [...] }
       ├─ plugin.json        data-only manifest
       └─ tests/
```

## 2. 边界

| 层 | 负责 | 禁止 |
|---|---|---|
| core | 启动、扫描、路由前缀、生命周期、健康、静态壳、白名单（库/panel JSON/其他生产 JSON） | 写业务 SQL、认识 AMV/K线/交易 |
| plugin | 自己的 domain API、自己的卡片、自己的测试 | 跨插件 import、直连 DB、写全局状态 |
| data | 生产库只读、panel JSON 与 `READABLE_JSON` 只读；插件输出写 `data/panel_v2/<id>/` | 写 `data/panel`、写生产库 |
| shell | 布局、导航、主题、加载插件卡片 | 业务计算、硬编码卡片 |

## 3. 迁移顺序

1. `sector`（只读，首块样板）— 已完成 v0.3（含买点徽标、分钟级）
2. `kline`（只读，v0.1）— 已完成
3. `amv`（只读，shadow）— 已完成 v0.2（含 HCL）
4. `battle_plan`（只读/影子，最后；不接交易）— 已完成 v0.1
5. `watchlist`（只读）— 已完成 v0.1

每一步：老路由保留 → 新插件 shadow 双跑 → 字段级 diff=0 → 前端切卡片 → 删老路径。

### 3.1 数据白名单的三层

| 层 | 入口 | 白名单 | 适用 |
|---|---|---|---|
| 数据库 | `ctx.db_read(name)` | `paths.READABLE_DB` | SQLite 库（AMV/K线/池/信号/分钟） |
| panel JSON | `ctx.panel_json(name)` | `paths.READABLE_PANEL` | `data/panel/*.json`（生产链写入的卡片数据） |
| 其他生产 JSON | `ctx.json_read(name)` | `paths.READABLE_JSON` | 不在 `data/panel` 的落盘产物（`hcl_latest` / `stock_names`） |
| 口径库 | `ctx.lib(name)` | `paths.READABLE_LIB` | 纯函数口径模块（`amv_contrib` / `amv_regime`），加载前 AST 扫描副作用 |

三层都是只读 + manifest 双声明（`data.read` / `data.panel` / `data.json`），缺一即拒。新增一项 = 改 `paths.py` + 契约文档 + 对应插件 manifest，三处同步。

## 4. 双跑与回滚

- 旧 8800/8002 不动；新 8810 独立进程。
- `/_plugins/health` 显示每个插件状态。
- 新版异常：停 8810 即可回滚；旧面板继续。
- 新版稳定一周后，才考虑把旧面板标为 deprecated。

## 5. 为什么不是微服务/微前端

本地单用户、SQLite、无构建链；引入进程/构建/模块联邦只增加复杂度。用同一个 FastAPI 进程内的 APIRouter 插件，已经能达到“改一个域只动一个文件夹”。

## 6. 打包与搬家

- 项目根 = `panel-v2/`，代码、文档、插件、输出目录都在项目内。
- 所有路径从 `app/core/paths.py` 的项目位置推导；不写死盘符。
- 生产库是外部只读依赖，可用 `PANEL_V2_DATA_DIR` 指向；插件输出默认 `panel-v2/data/panel_v2/`，随项目走。
- 启动用 `PANEL_V2_PYTHON` 或项目相对 `.venv` / 上层 `envs`，不写死解释器路径。

## 7. 多 Agent 协同

- 一个插件目录一个 owner；每个 Agent 只读 `docs/CONTRACT.md` + 自己的插件目录。
- 接口变更：先改 `docs/CONTRACT.md` 版本，再改 core，再通知插件 owner；禁止各自改契约。
- 集成验收：01 负责；嗅嗅负责独立审核。
