# intraday 插件数据契约（v0.1.0 · 2026-10-24）

卡：`intraday.main`（标题「盘中」，slot=main，order=15，`refreshEvery: 20000`）。
任务书：`docs/task_R1_intraday.md`。行为基准：旧端 `engine/web/index.html` 的「盘中」Tab。

## 1. 数据源（全部只读，零后端新增业务口径）

| 块 | 端点 | 提供方 | 卡片拿到什么 |
|---|---|---|---|
| 冰点监控 · 恐慌广度 | `GET /api/panic` | `ops`（mount=root） | 当前值/状态/三项拆解（值+250 日分位）/120 日序列/六阶段 |
| 实时活跃市值 | `GET /api/amv_live.json` | `compat`（mount=root） | all + indices + sectors.leaders/laggards + holdings + coverage |
| 广度 880 | `GET /api/breadth_880.json` | `compat_sector`（mount=root） | today / prev（涨跌平/涨跌停/成交额/统计） |
| 板块轨迹摘要 | `GET /api/p/intraday/summary` | **本插件** | 当日全A 序列 + 最新快照领先/落后各 top 板块 |
| 盘中脉冲 | `GET /api/sector_intraday.json?codes=` | `compat_sector`（mount=root） | 每个码的 `points[{t, amv_pct, change_pct}]` |

## 2. `/api/p/intraday/summary`

查询参数：`date`（`20261023` 或 `2026-10-23`，缺省取最新一日）、`top`（1–20，缺省 6，0 回落 6）。

响应：

```json
{
  "ok": true, "date": "20261023", "asof": "2026-10-23 15:00:56",
  "source": "qmt:full-tick+v9(tick_feed)", "last_ts": "2026-10-23 15:00:56",
  "snaps": 235, "boards_total": 90,
  "all_series": [{"t": "09:30", "all": -1.4171}, "…"],
  "first_all": -1.4171, "last_all": 5.0754, "all_delta": 6.4925,
  "strong": 27, "weak": 63, "top": 6,
  "leaders":  [{"code":"881277.TI","name":"电机","amv_pct":26.7692,"rel":21.6938,"chg_e":5.3026,"chg_w":5.8242,"amt_share":0.6567}],
  "laggards": [{"code":"881101.TI","name":"种植业与林业","amv_pct":-0.5943,"rel":-5.6697,"chg_e":-0.0768,"chg_w":0.4453,"amt_share":0.3366}]
}
```

错误分档：文件缺失 → `404`；文件坏/读失败 → `503`；manifest 未声明 → `500`；
文件里没有 `dates` 或当日快照全在盘外 → `200 {"ok": false, "reason": "…"}`（不编造零值）。

口径定义：

- `rel`（超额强度）= 该板块 `amv_pct` − **同一快照**的 `all`，即旧端 `_stVal` 的 `cal='rel'` 分支（index.html:4906）。不做别的换算。
- 快照过滤：`ts` 的 `HH:MM` 落在 `09:30–15:00`（旧端 `_stSnaps`，index.html:4905），剔盘前 08:xx 与收盘后点。
- `first_all` 取序列中**首个非空**值；`all_delta` = `last_all − first_all`，任一端为空则 `null`。
- `nan/inf` 当缺值处理，不参与排序、不写入序列。

## 3. 为什么不复用 `/api/sector_track.json`

旧端 `/api/sector_track.json` 下发 `amv_live_sector_track.json` 的**当日全量**：
235 个快照 × 90 个板块 ≈ 2.7MB 响应（磁盘文件 10.2MB，含两日）。卡片 20s 刷一次，
全量 = 每分钟约 8MB 无效负载 + 每次 2 万多个 board 字典的重复解析。

摘要端点读**同一个生产文件**（`ctx.panel_json("amv_live_sector_track")`，白名单内、只读），
在服务端裁到约 8KB。这不是第二份口径：`rel` 与时段过滤都是旧端既有显示口径，
排序/取头部是纯展示切片，不读库、不重算 AMV、不产生新指标。

## 4. 与旧端的差异（如实登记）

1. **不下发权重**：旧 `/api/sector_track.json` 带 `weights`（前一交易日 L2 成交额占比基线，取 `amv_board.db`）。
   本插件不连库，摘要不含权重；权重四象图仍在旧端，未迁移到本卡。
2. **不重算陈旧闸门**：旧端 `_track_stale`（盘中 >3min 无新点判陈旧）是口径判定，唯一 owner 是
   `compat_sector`。本卡只如实展示 `last_ts` 与采样点数，不产生第二套阈值。
3. **快照数差异**：摘要按 09:30–15:00 过滤，`snaps` 会小于文件里的原始条数（含盘前/盘后点时）。
4. **盘中脉冲码表**：默认取轨迹摘要的领先/落后各 3（最多 6 张卡），用户点轨迹表一行可再钉，
   钉住上限 8，总码数上限 20。旧端自选池的板块实时卡是另一条路径（`watchlist` 卡负责），本卡不复刻。
5. **不继承 3s 重绘**：旧端 `renderIntraday` 每 3s 重建整个 `#panel-intraday`，本卡是 20s 增量刷新 +
   `setHtml` 内容比对防闪（任务书 §1 指定节奏）。

## 5. 边界

- 只读：`data.write` / `data.state` / `data.misc` 全空；`quotes=false`、`feed=false`、`paper=false`、`ops_run=false`。
- `backend.mount=plugin`（**不是 root**）：根路径 `/api/panic` 等已由 ops / compat / compat_sector 持有，
  再挂 root 会在启动时触发 core 的根路径冲突保护。
- 前端零 import、零裸 `fetch`（只走 `ctx.api.get`）、零裸 hex（全部 themes.css token）、零写盘。
- 不拉 `/api/sector_track.json`：契约测试 `test_frontend_does_not_pull_full_track` 反回归锁住。
