# watchlist 插件契约（v0.1）

**插件**：`app/plugins/watchlist/`（id=`watchlist`，v0.1.0）
**挂载前缀**：`/api/p/watchlist`
**数据源**：只读 `data/panel/watchlist.json`（`ctx.panel_json("watchlist")`）；不连库、不写文件。
**边界**：只读自选池；不下单、不改分组、不碰旧 8800。

| 端点 | 说明 |
|---|---|
| `GET /summary` | `meta`、分组数、条目数、按 kind/group 计数、`read_only=true` |
| `GET /items?kind=&group=&limit=200` | 条目按 `order` 排序；limit 1–1000 |
| `GET /groups` | 分组列表 |

样例数据结构：`{meta, groups[], items[]}`；`items[]` 至少含 `kind/code/name/group/order`。

**v0.1 不覆盖**：加自选/删自选/改分组（写接口留在旧 8800）、行情富化、个股多空/板块徽标。
