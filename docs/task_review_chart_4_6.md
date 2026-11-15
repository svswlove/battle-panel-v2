# 任务书 4.6 · `/api/review_chart.json`（复盘图表档）

> 执行者：DSHana｜主会话验收｜2026-10-23
> **无 shell，不运行 pytest/python；只写代码，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`

---

## 0. 背景

`compat_misc` 的 `/api/review_chart.json` 仍是空骨架，复盘 Tab 的图表档不可用；默认 `/api/review.md` 已迁完并与旧端 HTML 全等。
旧端装配器：`engine/common/review_chart_data.py`（67KB，`build_cached(pick)`），从 MD + 多份 panel JSON + `ths_index.db` / `track_pool.db` 装配 `RV_DATA`。

本任务把装配器搬进 **core**（不是插件），因为：
- 它直接读 sqlite（插件契约禁止 `sqlite3.connect`）；
- 数据源是 core 路径白名单，适合 core 内部服务；
- 插件 `review` 只做 HTTP 薄壳。

---

## 1. 交付文件

```
app/core/review_chart.py          # 从 _vendor_src/review_chart_data.py 移植
app/core/paths.py                 # 增加 SUMMER_DIR / SUMMER_TOOLS_DIR（如缺）
app/plugins/review/backend.py     # 新增 GET /review_chart.json
app/plugins/compat_misc/backend.py # 删除 /review_chart.json 骨架路由
app/plugins/compat_misc/tests/test_compat_misc.py # 同步改骨架测试
app/core/tests/test_review_chart.py  # 从 _vendor_src/test_review_chart_data.py 移植（或放 app/core/tests）
```

> `app/core/tests` 目录当前没有就新建；pytest 会收集。

---

## 2. `app/core/review_chart.py` 移植

- 从 `_vendor_src/review_chart_data.py` **逐字复制逻辑**，只改 import：
  ```python
  from app.core.paths import DATA_DIR, PANEL_DIR, SUMMER_DIR, SUMMER_TOOLS_DIR, WS
  ```
  若 `app/core/paths.py` 无 `SUMMER_DIR` / `SUMMER_TOOLS_DIR`，新增：
  ```python
  SUMMER_DIR = WS / "夏天-activity"
  SUMMER_TOOLS_DIR = SUMMER_DIR / "工具"
  ```
- `TEMPERATURE_FILE` / `TRACK_DB` / `INDEX_DB` / `ALL_A_FILE` 从这些常量推导（保留原值）。
- 不修改 `parse_md` / `assemble` / `build_cached` 的算法、字段名、口径、缓存键。
- 模块头注明来源与迁移日期。
- `build_cached(pick)` 接受 `review.source.ReviewPick`（有 `.path/.date/.mtime_ns/.size`）；它内部 `pick.path` / `pick.date` 可直接用。

## 3. 插件路由

`app/plugins/review/backend.py` 新增：
```python
@router.get("/review_chart.json")
def review_chart(date: Optional[str] = None):
    pick = source.pick_latest(date, ctx.text_dir("daily_review"))
    if pick is None: raise HTTPException(404, ...)
    try:
        return review_chart_mod.build_cached(pick)
    except review_chart_mod.ReviewChartError as e:
        raise HTTPException(502, detail=f"复盘图表装配失败：{e}")
    except Exception as e:
        raise HTTPException(502, detail=f"复盘图表装配异常：{e}")
```
- 插件通过 `from app.core import review_chart as review_chart_mod`（插件允许 import core 的公开服务？当前 `paper`/`concept` 插件没有直接 import core 模块；但 `review` 插件可以像 `ctx.paper` 一样使用 core 服务。为避免契约争议，可在 `backend.py` 顶部 `from app.core import review_chart as ...`；core 不是插件源码扫描对象，且不是 `from core` 旧包。保持简单。）
- 删除 `compat_misc` 的 `/review_chart.json` 路由与 `build_review_chart` 骨架（只删这一条）；更新其测试。
- `review` 插件 manifest 不需要新增 data 声明（core 直接读路径）。

## 4. 测试

### `app/core/tests/test_review_chart.py`

从 `_vendor_src/test_review_chart_data.py` 移植：
- 原测试 monkeypatch `review_chart_data` 的 `TEMPERATURE_FILE` / `TRACK_DB` / `INDEX_DB` / `ALL_A_FILE` / `PANEL_DIR` 到 tmp；改成 `app.core.review_chart` 同名常量。
- 保留 parse/assemble/cache/date-mismatch/segment 断言（原 30+ 用例）。
- 不依赖真实生产文件；tmp fixture 沿用原测试的 `_Base`。
- 如果原测试 import `common.review_chart_data`，改成 `from app.core import review_chart`。

### `app/plugins/review/tests/test_review_api.py`

新增：
1. `GET /api/review_chart.json` 200，key 集合与旧端一致（`ok/meta/head/levels/index/amv/breadth/temp/turnover/sector/ladder/anomaly/sentiment/news/positions/gate/tomorrow/scenarios/gaps/md_quotes/file/mtime_ns/cache`）；
2. `?date=1900-01-01` → 404；
3. `GET /api/review_chart.json` 与 `/api/review.md` 同一最新日期；
4. 旧端对比由主会话人工跑（值允许因源文件日期差异；结构必须一致）。

## 5. 硬约束

- 不修改 `review.md` 已验收逻辑。
- `app/core/review_chart.py` 可以 import `sqlite3`；插件仍然禁止。
- 不新增第三方依赖。
- 中文注释保留。

## 6. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/core/tests/test_review_chart.py app/plugins/review/tests -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
```
