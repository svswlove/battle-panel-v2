# 任务书 4.2 · `/api/concept.json`（概念 Tab 唯一未对齐端点）

> 执行者：DSHana｜主会话验收｜2026-10-23
> **无 shell，不运行 pytest/python/curl；只写代码，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`

---

## 0. 背景

旧 8800 `/api/concept.json` 是唯一还没对齐的旧端点（`compare_legacy.py` 31/32）。
数据源不是 JSON 文件，而是 **`夏天-activity/概念追踪/` 的 Markdown 知识库**：
- `概念与催化总表.md` → active / watch / chain / archived / counts / sector_map
- `{slug}/概况.md` → 单个概念详情 HTML
- 解析器：`engine/common/concept_source.py`（21KB，纯函数但读文件）
- 渲染器：`engine/common/concept_render.py`（markdown → HTML，含个股名 linkify）

本任务把它迁成 **`concept` 插件**（root 挂 `/api/concept.json`），并新增 `data.text` 目录白名单。

---

## 1. 先读（只读参考）

`_vendor_src/` 已放好：
- `concept_source.py`
- `concept_render.py`
- `test_concept_source.py`
- `test_concept_watch_view.py`

项目现状：
- `app/core/paths.py`（`READABLE_DB` / `READABLE_JSON` / `WRITABLE_PANEL`）
- `app/core/contract.py`（`PluginContext` / manifest 解析）
- `app/plugins/compat_misc/backend.py`（当前 `/concept.json` 由 `compat` 插件返回 503 骨架，见 `compat` 插件）
- `app/core/main.py`（root mount）

---

## 2. 契约扩展：`data.text` + `ctx.text_dir(name)`

新增：
```python
# app/core/paths.py
READABLE_TEXT_DIR: dict[str, Path] = {
    "concept_tracking": WS / "夏天-activity" / "概念追踪",
}

def text_dir(name): return READABLE_TEXT_DIR.get(name)
```

`app/core/contract.py`：
- `Manifest.text_read: tuple[str, ...]`；`public()["data"]["text"]`。
- `validate_manifest` 解析 `data_block.get("text", [])`；每项必须字符串；未登记 → `ContractError`。
- `PluginContext.text_dir(name)`：
  - 未在 `manifest.data.text` 声明 → `PermissionError`
  - 未登记/不存在 → `FileNotFoundError` / `PermissionError`
  - 返回 `Path`
- **只读**：不提供写方法；插件仍然禁止 `open(...,"w")`。

`plugin.json` 的 `data` 增加 `"text": ["concept_tracking"]`。

---

## 3. 插件 `app/plugins/concept/`

### 3.1 `plugin.json`

```json
{
  "manifest_version": 1,
  "id": "concept",
  "name": "概念追踪",
  "version": "0.1.0",
  "min_core_version": "0.3.1",
  "enabled": true,
  "kind": "domain",
  "backend": {"module": "backend", "entry": "register", "mount": "root", "timeout_ms": 10000,
              "permissions": ["read:text", "read:db", "read:json"]},
  "data": {
    "read": ["ths_board.db"],
    "panel": [],
    "json": ["stock_names"],
    "lib": [],
    "text": ["concept_tracking"],
    "quotes": false,
    "write": []
  },
  "config": {},
  "depends": []
}
```

### 3.2 `source.py`（从 `_vendor_src/concept_source.py` 移植）

保留全部解析逻辑（`_sections` / `_table` / `_parse_active` / `_parse_watch` / `_parse_chain` / `_parse_archived` / `_sector_resolve` / `_build_sector_map` / `load_index` / `load_detail`），只改依赖注入：

```python
def load_index(concept_dir: Path | str | None = None,
               sector_names: dict[str, str] | None = None,
               name_to_code: Callable[[str], str] | None = None) -> dict
def load_detail(slug: str, concept_dir: Path | str | None = None) -> dict
```

- `concept_dir` 缺省用模块常量（兼容旧测试可 monkeypatch，但生产必须传 `ctx.text_dir("concept_tracking")`）。
- `sector_names`：`{板块名: 代码}`，由插件用 `ctx.db_read("ths_board.db")` 查 `concept`/`board` 两表构建后传入；**source.py 内不得出现 `sqlite3.connect`**（插件静态扫描会拦）。
- `name_to_code`：名称→代码 callable，由插件从 `stock_names` 反查表构建；`_targets_detail` 与 `_backfill_chain_codes` 都用它。缺省返回 `""`。
- `_ths_sector_names()` 删除或改为「返回传入的 sector_names」；不要再连库。
- `load_index` 缓存要包含 `concept_dir` 指纹；保持 30s TTL。
- `_targets_detail` 改为 `_targets_detail(cell, name_to_code=None)`；`load_index` 内部调用传参。
- `load_detail` 仍做路径穿越防护。

### 3.3 `render.py`（从 `_vendor_src/concept_render.py` 移植）

保留 `md_to_html(md_text)` 与 `linkify_stock_names(html)` 的逻辑，把 `common.names.code_of` 改成可注入：
```python
def linkify_stock_names(html: str, name_to_code: Callable[[str], str] | None = None) -> str
def md_to_html(md_text: str, name_to_code: Callable[[str], str] | None = None) -> str
```
- `markdown` 包已在 envs/qmt312 安装（3.10.3）。
- 不出现 `common.*` import。

### 3.4 `backend.py`

```python
@router.get("/concept.json")
def concept(slug: str | None = Query(default=None)) -> Dict[str, Any]:
    """无 slug → 总表索引；有 slug → 单概念 HTML。"""
```
- 每次请求拿 `ctx.text_dir("concept_tracking")`。
- `sector_names` 从 `ctx.db_read("ths_board.db")` 构建（`concept` 表优先、`board` 表补；重名 concept 优先）。
- `name_to_code`：`ctx.json_read("stock_names")["names"]` 反查；同名多码 → `""`；全角/空格归一（至少 `strip`）。
- 无 slug：
  ```python
  data = source.load_index(concept_dir, sector_names, name_to_code)
  data = _backfill_chain_codes(data, name_to_code)
  return data
  ```
  `_backfill_chain_codes` 从旧 `server.py:1959` 移植，用 `[/、]` 切分，补 `up_detail/mid_detail/down_detail`。
- 有 slug：
  ```python
  r = source.load_detail(slug, concept_dir)
  if not r.get("ok"): raise HTTPException(404, detail=r.get("error") or "概念概况缺失")
  return {"ok": True, "slug": r["slug"], "html": render.md_to_html(r["md"], name_to_code)}
  ```
- 异常：`HTTPException` 原样；其他 → `HTTPException(500, detail=f"概念数据读取失败：{e}")`。
- 插件不含任何写文件/sqlite3 代码。

### 3.5 旧 `compat` 插件

当前 `app/plugins/compat/backend.py` 里 `/concept.json` 返回 503 骨架（如果存在）。把它删掉/禁用该路由，避免 root 冲突（`concept` 插件会注册同一路径）。检查 `compat` 和 `compat_misc` 两个插件，确保没有其他 `/concept.json` 路由。

---

## 4. 测试

### 4.1 `app/plugins/concept/tests/test_concept_source.py`

从 `_vendor_src/test_concept_source.py` 移植，适配：
- `CONCEPT_DIR` 用 `tmp_path` 指向的临时目录；
- `sector_names` 直接传 dict（不 monkeypatch sqlite）；
- `name_to_code` 传固定映射；
- 所有旧断言保留（active/watch/chain/archived/counts/sector_map、slug 防穿越、detail 读取）。

### 4.2 `app/plugins/concept/tests/test_concept_api.py`

用 `TestClient` + 临时 `PANEL_V2_DATA_DIR`/text dir？`ctx.text_dir` 使用 paths 常量；测试可 monkeypatch `paths.READABLE_TEXT_DIR` 指向 tmp 概念目录，`paths.READABLE_JSON["stock_names"]` 指向 tmp json，`paths.READABLE_DB["ths_board.db"]` 指向临时 sqlite（建立 `concept`/`board` 表，插 2-3 行）。
覆盖：
1. `GET /api/concept.json` 200，key 集合与旧端一致；
2. 插入一个最小 `概念与催化总表.md` + 一个 `测试概念/概况.md`，`?slug=测试概念` 200 且 `html` 含 `<h`；
3. `?slug=../evil` 404；
4. 缺失总表 → 404/ok:false（与旧端一致：`load_index` 返回 ok:false 但旧端点仍 200？旧端 `JSONResponse(_backfill_chain_codes(...))` 不检查 ok；实际 compare 用旧 200。按旧端行为返回 200 + ok:false）；
5. 静态扫描：插件目录无 `sqlite3.connect` / `open(...,"w")` / `xtquant` / `from eltdx`。

### 4.3 旧端对比

主会话会跑：
```powershell
python tools/compare_legacy.py
```
目标 `32/32`，`/api/concept.json` 从 `ERROR 旧200 新503` 变为 `ok`（结构一致；值允许差异由 compare 规则处理）。

---

## 5. 硬约束

1. 插件目录禁止 `sqlite3.connect` / `from common` / `from core` / `xtquant` / `xtdata` / `from eltdx`。
2. 插件不得写文件；文本目录只读。
3. 不改 `app/core/paper/`、`app/plugins/paper/`、`app/plugins/compat_misc/`（除删除 `/concept.json` 冲突路由）。
4. 中文注释保留。
5. 不新增第三方依赖（`markdown` 已装）。

---

## 6. 交付格式

1. 文件清单。
2. `data.text` / `ctx.text_dir` 的契约实现说明（3 行）。
3. 旧 `compat` 的 `/concept.json` 骨架是否删除。
4. 「未运行验证」声明。
5. 需确认问题。

---

## 7. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/plugins/concept/tests -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe tools\compare_legacy.py
```
