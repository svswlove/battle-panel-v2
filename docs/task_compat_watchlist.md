# 任务书：compat_watchlist 插件（旧前端自选池，含写操作）

- 派单：01 → DSHana
- 日期：2026-10-23
- **并行执行**：另有任务在写其他插件目录。**只写 `app/plugins/compat_watchlist/`**。

## 1. 目标

旧面板「自选池」Tab 调用 9 个端点（4 读 + 5 写）。响应/请求格式必须与旧 8800 逐字段一致。

| 路径 | 方法 | 说明 |
|---|---|---|
| `/api/watchlist.json` | GET | 条目 + 分组 + meta |
| `/api/watchlist/groups` | GET | 分组列表 |
| `/api/watchlist/state` | GET | 前端状态（选中分组等）|
| `/api/watchlist/search` | GET | 股票搜索（`?q=`）|
| `/api/watchlist/add` | POST | 加条目 |
| `/api/watchlist/remove` | POST | 删条目 |
| `/api/watchlist/move` | POST | 移到分组 |
| `/api/watchlist/reorder` | POST | 排序 |
| `/api/watchlist/group/add` · `remove` · `rename` · `reorder` | POST | 分组增删改序 |

（表里 12 行、去重后 9 个逻辑端点；分组类 4 个算一组。）

## 2. 数据源与写通道（已就绪，直接用）

- **读**：`ctx.panel_json("watchlist")` → `data/panel/watchlist.json`
- **写**：`ctx.panel_write("watchlist", payload)`
  - 已实现：白名单校验 + 写前备份 + 原子替换（tmp→os.replace）+ 审计日志
  - 备份落 `data/panel_v2/backups/watchlist/<时间戳>.json`，审计 `audit.log`
- manifest 必须写：
  ```json
  "data": {
    "panel": ["watchlist"],
    "write": ["watchlist"],
    "read": ["track_pool.db"]
  }
  ```

## 3. 关键做法

1. **先探旧端**：`curl http://127.0.0.1:8800/api/watchlist.json` 存样本，逐个 POST 端点看它接受什么 body、返回什么结构。
2. **写操作必须读-改-写整份文件**：`panel_write` 接收完整 dict（不是增量）。流程是：
   `读当前 → 改内存 → panel_write 回去`。不要只写局部，否则会丢字段。
3. **保留未知字段**：旧文件里可能有本插件不认识的键（别的模块写的）。读-改-写时必须**原样保留**，只动自己负责的部分。
4. **并发**：同一进程内用模块级 `threading.Lock()` 包住「读-改-写」，避免两个请求交叉覆盖。
5. **失败不写**：校验不过（非法代码/分组不存在）直接 400，不要写一半。

## 4. 边界

**允许**
- 只写 `app/plugins/compat_watchlist/`
- `ctx.panel_json` / `ctx.panel_write` / `ctx.db_read("track_pool.db")` / `ctx.json_read("stock_names")`

**禁止**
- 改 core、改其他插件、改 `engine/`
- 写 `watchlist` 以外的任何文件
- 插件互 import、发 HTTP、新依赖

## 5. 环境事实

- 8810 在跑；旧 8800 在跑
- **重启 8810**：
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*port 8810*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  schtasks /Run /TN "PanelV2_KeepAlive"
  ```
- `backend.mount` 必须 `"root"`（端点落 `/api/...`）
- **你没有 shell**：不要试图 curl/pytest/重启。只写代码，在报告里标注「未运行验证」，01 会验。

## 6. 自测（写进报告，实际由 01 执行）

1. 4 个 GET 与 8800 递归比对结构一致
2. 5 个写操作：调用后 `watchlist.json` 内容正确、备份存在、审计有记录
3. 写操作不破坏未知字段（对比写前写后的键集合）

## 7. 交付

- `app/plugins/compat_watchlist/{plugin.json, backend.py}`
- `app/plugins/compat_watchlist/tests/test_compat_watchlist.py`（用 tmp 目录，不碰真文件）
- `docs/compat_watchlist.md`
- 回执 **≤200 字 + 路径**
