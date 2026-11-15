# compat_misc 契约样本（键集对照）

## 状态：**未运行验证 / 未 curl 样本**

本子代理**没有 shell**，任务书 §2.1 要求的
`curl http://127.0.0.1:8800/api/... > docs/misc_samples/<名>.json` **没有执行**。
因此本目录不放「旧端真实响应」，只放**可复现的字段依据**，由 01 补齐真样本后逐键比对：

```powershell
cd OH-WorkSpace\projects\panel-v2
$paths = @(
  'ops.json','order/list','condition/list','paper_account','ledger?mode=paper',
  'ledger?mode=live','selection','selection_board_options','selection_pool',
  'selection_pool?date=1025','selection_prefs','stock_amv/603629.SH?days=120',
  'sector_amv/881166.TI?days=120','sector_amv/X4001?days=120',
  'sector_kline/881166.TI?limit=240','trades/002812.SZ?days=60',
  'ui_notes/list','ui_notes/list?status=open','review.md','review_chart.json'
)
New-Item -ItemType Directory -Force docs\misc_samples | Out-Null
foreach ($p in $paths) {
  $f = 'docs/misc_samples/' + ($p -replace '[\\/?=&.]', '_') + '.json'
  try { Invoke-RestMethod -Uri ("http://127.0.0.1:8800/api/" + $p) -TimeoutSec 60 |
        ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 $f }
  catch { "ERR $p : $($_.Exception.Message)" | Set-Content -Encoding utf8 $f }
}
python tools\compare_legacy.py --only /api/ops.json /api/order/list ...
```

## 本目录内容

| 文件 | 依据 | 用途 |
|---|---|---|
| `keys_expected.json` | 逐端点键集，来源见每个 `src` 字段：**旧仓源码逐行**（`engine/server.py` / `engine/ops/ops_status.py` / `engine/common/review_chart_data.py`）**或磁盘实读**（`data/panel/*.json`、`engine/state/*.json`，2026-10-23） | 结构比对基线（`tools/compare_legacy.py` 判 `structure_missing/extra`） |

## 哪些键是「源码级」、哪些是「磁盘实读」

- **源码级**（最可靠，旧端装配逻辑逐行）：`/ops.json`（`ops_status._aggregate` 的
  return 字面量）、`/order/list`（`api_order_list` 的 body + `_account_view`）、
  `/trades`（`build_trades_payload` return）、`/ledger`（`api_ledger` 两个 return）、
  `/paper_account`（`api_paper_account` 的 out + account）、`/selection`
  （`_selection_snapshot_payload` 缺快照分支）、`/selection_pool`
  （`_selection_pool_payload`）、`/selection_board_options`、`/sector_amv` /
  `/sector_kline` / `/stock_amv`（`tdx_sector_api.py` 各 return）、`/review_chart.json`
  （`review_chart_data.assemble` return）、`/ui_notes/list`（`api_ui_notes_list`）。
- **磁盘实读**（补源码里从别的模块带出来的键）：`paper_account` 原账本键
  （`engine/state/paper_account.json`）、`review.md` 的 `charts` 子键
  （`review_source.render_cached` 的渲染器返回值，见 `_review_renderer`）、
  `selection_filter.json` 的偏好键（`data/panel/selection_filter.json`）。

## 已知会「结构不一致」的地方（提前声明，别当 bug）

1. `/paper_account`：旧端 `out = dict(acct) + account + positions_list`，`acct` 顶层键
   由 `paper_broker` 决定（当前 8 个：initial_cash / cash / positions / realized_pnl /
   equity_curve / stats / updated_at / trade_date）。若 `paper_broker` 加键，骨架会缺。
2. `/review_chart.json` 的 `levels`：旧端在缺上证数据时为 `None`、有数据时为
   `{"sse": {...}}`。骨架固定 `null`——`compare_legacy` 对 `null` 不展开，所以旧端有值时
   会报 `levels.sse.*` 缺失。**这在预期内**（源不可读），比对时用 `allow_missing`。
3. `/order/list` 的 `mode=live` 分支旧端多一个 `qmt_reports` 键；骨架无（不猜 mode）。
4. `/sector_kline` 的 `source` 字符串必然与旧端不同（`eltdx:daily_bars` vs
   `vipdoc:sh881xxx.day`）——值差异，不是结构差异。
5. `/selection_prefs` 旧端不可读时就是 `{}`，键集为空 → 结构比对恒过（数据差另算）。
