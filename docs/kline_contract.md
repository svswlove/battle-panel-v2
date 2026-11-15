# kline 插件契约（v0.1）

**插件**：`app/plugins/kline/`（id=`kline`，v0.1.0）
**挂载前缀**：`/api/p/kline`
**数据源**：`data/track_pool.db::kline` + `factor`，只读 `ctx.db_read("track_pool.db")`
**前复权**：`round(原始价 × qfq_scale + qfq_offset, 精度)`；股票/指数 2 位，ETF/场内基金 3 位。

| 新端点 | 旧 8800 | 说明 |
|---|---|---|
| `GET /bars/{ts_code}?limit=120&adjust=qfq` | `GET /api/kline/{ts_code}?period=d&limit=` | 日线 OHLCV + amount；`adjust=qfq/raw` |
| `GET /quote/{ts_code}?adjust=qfq` | `GET /api/quote5/{ts_code}`（部分） | 最新一根 bar；不含盘口五档 |

返回：

```json
{"code":"000001.SH","period":"d","adjust":"qfq","stale":false,
 "warnings":[],"bars":[{"date":"20261021","open":...,"high":...,"low":...,"close":...,"volume":...,"amount":...}],
 "intraday":false,"last_bar_date":"20261021",
 "notice":"仅历史已完成日线；不含当日未收盘 bar 与 yellow/white/AMV 指标"}
```

### 边界字段（v0.1 有意约定）

| 字段 | 类型 | 含义 |
|---|---|---|
| `intraday` | `bool` | 恒为 `false`：响应里不含当日未收盘 bar |
| `last_bar_date` | `str \| null` | `bars` 最后一根的 `date`；无 bar 时为 `null`（无 bar 走 404） |
| `notice` | `str` | 固定文案，声明历史日线子集边界，供前端与调用方展示 |

这三个字段是**追加**的：`code/period/adjust/stale/warnings/bars` 与 bar 内 7 个字段完全不变，旧调用方无需改动。

## v0.1 边界（有意为之，不是未完成的 bug）

kline 插件定位是**历史已完成日线子集**，不是旧 8800 的等价替代：

- 不含当日未收盘 bar。`track_pool.db::kline` 只有已完成日线；旧 8800 会从实时源补当日 bar。
- 不含 `yellow / white / amv / amvMa / amvWhite / chg / amp / turnover` 指标列。指标归 amv 插件所有，kline 不复制、不重算 AMV。
- 不含分时/多周期、盘口五档、板块归属、K 线图交互工具。

补齐当日 bar 需要独立实时行情源接入，属后续版本的事；在此之前，kline 用 `intraday=false` + `notice` 明确暴露边界，调用方据此决定是否切流。旧 8800 不改。
