# 任务书 4.3 · 补齐 compat_misc 选股三端点（selection / selection_pool / selection_prefs）

> 执行者：DSHana｜主会话验收｜2026-10-23
> **无 shell，不运行 pytest/python/curl；只写代码，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`

---

## 0. 背景

`compat_misc` 之前把选股三端点做成骨架，原因是数据文件未登记白名单。实际文件都存在：

| 文件 | 大小 | 用途 |
|---|---|---|
| `data/panel/selection_latest.json` | 8.2MB | `/api/selection` 主数据（盘后快照） |
| `data/panel/selection_pool.json` | 1.8KB | `/api/selection_pool` 选股池 |
| `data/panel/selection_filter.json` | 561B | `/api/selection_prefs` 筛选设置 |

本任务只做**只读透传 + 轻量拼装**，不重算选股。

---

## 1. 白名单

`app/core/paths.py::READABLE_PANEL` 增加：

```python
"selection_latest": PANEL_DIR / "selection_latest.json",
"selection_pool": PANEL_DIR / "selection_pool.json",
"selection_filter": PANEL_DIR / "selection_filter.json",
```

`app/plugins/compat_misc/plugin.json` 的 `data.panel` 增加这三个名字。

---

## 2. `compat_misc/backend.py`

### 2.1 `/api/selection`

旧端 `_selection_snapshot_payload` 口径（可参考 `_vendor_src/selection_reference.py`）：

```python
def build_selection(ctx):
    snap = ctx.panel_json("selection_latest")   # 8.2MB，一次解析
    out = dict(snap)                            # 原样带出 meta/date/asof/filters/...
    out["pool"] = build_selection_pool(ctx, "") # 实时拼池
    out["freshness"] = _freshness(snap)         # 见 §2.4
    return out
```

要求：
- 文件缺失/JSON 坏 → 保留现有 `SELECTION_SKELETON` 分支（ok 键? 旧端骨架里没有 `ok`，保持当前实现即可），`error` 写原因。
- 不重算 `groups/rows`，不查 `buy_signal.db`。
- `pool` 每次实时读 `selection_pool.json`（文件很小）。
- 加进程内缓存：按 `path.stat().st_mtime_ns + size` 缓存 8.2MB 解析结果，避免 60s 轮询反复解析。缓存 key 用 `ctx.panel_json` 不行（没有 mtime），可让 core 加 `panel_path`？**不要改 core**；在插件里用 `ctx.panel_json` 简单缓存按 `snap["meta"]["generated_at"]` + 文件大小不可得。**简化：不缓存**，8.2MB JSON 解析一次约 0.1–0.3s，页面 60s 轮询可接受；若实测慢再单独开任务。

### 2.2 `/api/selection_pool`

```python
def build_selection_pool(ctx, date=""):
    doc = ctx.panel_json("selection_pool") or {}
    items = doc.get("items") or []
    d = str(doc.get("date") or "")
    return {"date": d, "count": len(items), "is_current": bool(d),
            "names": [x["name"] for x in items if x.get("name")][:3],
            "items": items,
            "slots": [{"date": d, "count": len(items), "saved_at": (doc.get("meta") or {}).get("updated_at") or ""}] if d else [],
            "keep": 5}
```
- 旧端 `slots` 是历史快照目录；当前只有一份 `selection_pool.json`，给单 slot（不编造历史）。
- `date` 参数：当前文件只有一份；传了不同日期 → 仍返回当前 `date`，`is_current=False`（不假装有历史）。
- 文件缺失/坏 → 现有骨架（slots 空/items 空/keep=5）。

### 2.3 `/api/selection_prefs`

```python
def build_selection_prefs(ctx):
    try:
        doc = ctx.panel_json("selection_filter")
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}      # 旧端口径：缺失/坏 → {}
```

### 2.4 `freshness`

旧端结构：
```json
{"state":"fresh|stale|unknown","snapshot_date":"YYYY-MM-DD",
 "expected_date":"YYYY-MM-DD","produce_by":"18:00","today_pending":false}
```
本版简化（不读交易日历）：
- `snapshot_date = snap.get("date") or snap.meta.signal_date`
- 今天工作日、当前时间 < 18:00 且 snapshot_date 是今天 → `fresh`
- 今天工作日、当前时间 >= 18:00 且 snapshot_date < 今天 → `stale`
- 否则 `unknown`（周末/无法判断不喊 stale）
- `today_pending` = 今天工作日且当前 < 18:00 且 snapshot_date != today。
不猜交易日，不新增依赖。

---

## 3. 测试

`app/plugins/compat_misc/tests/test_compat_misc.py` 里现有 selection 测试改：
- 用 tmp 造 `selection_latest.json`（最小 `{meta,date,groups:[],...}`）、`selection_pool.json`、`selection_filter.json`；
- `monkeypatch.setitem(paths.READABLE_PANEL, "selection_latest", tmp/...)` 等；
- 断言三个端点返回真实数据、键集合与旧端一致、缺失文件降级不抛。
- 保留其余端点测试。

新增 `app/plugins/compat_misc/tests/test_selection_real.py`（可选）用真实 `data/panel/selection_latest.json` 只读跑一次，断言 `groups` 非空、`pool` 形状；如果文件缺失自动 skip（避免 CI 依赖）。

---

## 4. 硬约束

- 不重算选股、不查 `buy_signal.db`、不写任何文件。
- 不改 `paper` / `concept`。
- 插件禁止 `sqlite3.connect` / `from common` / `from core`。
- 中文注释保留。

---

## 5. 交付格式

1. 文件清单。
2. 三个端点的返回键。
3. 「未运行验证」声明。
4. 需确认问题。

## 6. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/plugins/compat_misc/tests -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
```

预期 `compare_legacy.py` 的 32 项里 `/api/concept.json` 之外全部保持 ok；`/api/selection`、`/api/selection_prefs` 从骨架变为真实数据（如果 compare 覆盖它们；不覆盖则人工对比旧 8800）。
