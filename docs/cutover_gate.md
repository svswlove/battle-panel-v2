# M4 切流门禁（`tools/cutover_gate.py`）

**用途**：把 M4 切流前的检查串成一条命令；每日观察期直接跑，不用手抄 checklist。
**边界**：只读检查 + 子进程调用现有 diff 工具；不写生产数据、不改 8800/8002、不改插件。

## 用法

```powershell
cd <panel-v2>

python tools\cutover_gate.py                 # 全套，约 6 秒
python tools\cutover_gate.py --skip-pytest    # 只跑运行时检查
python tools\cutover_gate.py --skip-shadow    # 跳过 shadow 双跑
```

退出码：`0` 全 PASS；`1` 任一 FAIL。结果同时写 `docs/cutover_gate_latest.md`。

## 检查项

| 项 | 判定 |
|---|---|
| `8810 /api/health` | HTTP 200 且 `ok=true` |
| 全部插件健康 | `/_plugins/health` 的 `ok=true`，且全部插件 `ok=true` |
| shadow 双跑 | `tools/shadow_check.py --once` 退出码 0（**30/30**） |
| AMV 原始 JSON | `tools/diff_amv.py` 退出码 0（三对 equal=True） |
| battle_plan 无写接口 | `POST /api/p/battle_plan/plan` → 405 |
| 插件无写代码 | 静态扫描 `app/plugins/*/backend.py` + `*/frontend.js`：`open(...,"w/a")`、`Path.write_text/write_bytes/mkdir/touch/unlink/rmdir`、`os.remove/rename/makedirs`、`shutil.rmtree/move/copy`、`json.dump`、`to_sql/to_csv/to_parquet`、`INSERT/UPDATE/DELETE/DROP/CREATE TABLE` 均禁 |
| kline 边界 | `/bars/000001.SH` 返回 `intraday=false`、`last_bar_date`、`notice` |
| pytest 全套 | core + 5 插件 + 比较器自检（当前 **151 passed**） |

## 与 M4 文档的关系

`docs/M4_CUTOVER.md` 是评审标准；本脚本只把其中可自动化的部分做成门禁。它**不替代**：
- 两个交易日的 shadow 观察；
- 16:10 主链 / 18:00 灌库后的产物核对；
- 回退演练与主人/01 的确认。

## 当前基线

2026-10-22 首次运行：PASS 5.7s，7 项全过。
2026-10-23 02:2x 最新：**PASS 14.7s，8 项全过**（shadow 30/30、pytest 134；写代码扫描覆盖 backend+frontend，已用临时探针验证能抓到写）。结果见 `docs/cutover_gate_latest.md`。
