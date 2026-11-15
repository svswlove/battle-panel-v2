# 任务书 4.1.3b · paper HTTP 插件（root 挂载，接旧前端）

> 执行者：DSHana｜主会话验收｜2026-10-23
> **你没有 shell，不要运行 pytest / python / curl。只写代码 + 自检语法，交付写「未运行验证」。**
> cwd：`OH-WorkSpace\projects\panel-v2`
> **前置：4.1.2 的 `app/core/paper/` 与 4.1.3a 的 `app/core/feed.py` 已完成。**

---

## 0. 先读

- `docs/paper_trading_architecture.md` §2.1、§3、§6
- `app/core/paper/service.py`、`broker.py`、`orders.py`、`state.py`
- `app/core/feed.py`、`app/core/contract.py`、`app/core/main.py`
- `app/plugins/compat_misc/backend.py`（现有 5 个骨架路由，要删）
- `_vendor_src/paper_views_reference.py`（旧 8800 端点参考）
- `_vendor_src/quote5_reference.py`（`_build_holdings_view` / `_book_extras` 参考）

---

## 1. 目标

新增 `paper` 插件，root 挂载到 `/api`，接管旧前端的下单/条件单/账户/账本/买卖点路径。
**业务逻辑全部在 `app/core/paper/`，插件只做 HTTP 参数校验与 JSON 装配。**

### 交付文件

```
app/plugins/paper/plugin.json        # 新增
app/plugins/paper/backend.py         # 新增
app/plugins/paper/views.py           # 新增（账户/账本/买卖点纯装配）
app/plugins/paper/tests/test_paper_api.py   # 新增
app/core/contract.py                 # 追加 manifest.data.paper + PluginContext.paper
app/core/feed.py                     # 追加 set_interest_provider(fn)
app/core/paper/service.py            # 追加 quote_for()/quote_map()/on_quotes()/wanted_codes()（若 4.1.2 未提供）
app/plugins/compat_misc/backend.py   # 删除 5 个冲突路由
app/plugins/compat_misc/tests/test_compat_misc.py  # 同步改测试
```

---

## 2. 契约扩展

### 2.1 manifest `data.paper`

- 新增可选布尔字段 `data.paper`（默认 false）。
- `Manifest` dataclass 加 `paper_read: bool`；`public()` 的 data 里加 `"paper"`。
- 类型错 → `ContractError`。

### 2.2 `PluginContext.paper`

```python
@property
def paper(self) -> Any:
    if not self.manifest.paper_read:
        raise PermissionError(f"插件 {self.id} 未在 manifest.data.paper 声明 true")
    from app.core.paper.service import get_service
    return get_service()
```

### 2.3 `QuoteFeed.set_interest_provider(fn)`

- `fn() -> Iterable[str]`，supervisor 每 15 秒调用一次，用返回值 `ensure()`。
- 异常吞掉（行情订阅不能因 provider 挂掉）。
- 该接口在 4.1.3a 的 feed.py 上追加即可，不改线程模型。

### 2.4 插件启动装配（`register(ctx)` 里）

```python
svc = ctx.paper
ctx.feed.set_interest_provider(lambda: svc.wanted_codes())
ctx.feed.ensure(svc.wanted_codes())
ctx.feed.add_listener(lambda quotes: svc.on_quotes(quotes))
svc._session_ok = _make_session_ok(ctx)     # 真实交易日历；失败 fail-open=True（旧口径）
```

`_make_session_ok(ctx)`：
- 工作日判断优先 `ctx.quotes.call("workdays.is_workday", today)`（eltdx WorkdayService）；不可用 → 退化为 `weekday()<5`；
- 时段固定 09:30–11:30 / 13:00–15:00（连续竞价）；
- 日历调用异常 → fail-open 返回 True（与旧 `ManualOrderService._default_session_ok` 同口径，纸面演练不因日历故障停摆）。

`signal_buy` 条件单：本任务**不接**信号库（`signals/library.py` 60KB，属下一批），`svc.signal_provider` 保持 None；前端可建单但永不触发，需在交付摘要里明确列出这个已知缺口。

---

## 3. `app/plugins/paper/plugin.json`

```json
{
  "manifest_version": 1,
  "id": "paper",
  "name": "纸面交易",
  "version": "0.1.0",
  "min_core_version": "0.3.1",
  "enabled": true,
  "kind": "domain",
  "backend": {
    "module": "backend",
    "entry": "register",
    "mount": "root",
    "timeout_ms": 10000,
    "permissions": ["paper:trade", "feed:read", "quotes:read"]
  },
  "data": {
    "read": [],
    "panel": ["real_positions"],
    "json": ["stock_names"],
    "lib": [],
    "quotes": true,
    "feed": true,
    "paper": true,
    "write": []
  },
  "config": {},
  "depends": []
}
```

---

## 4. `app/core/paper/service.py` 追加方法

```python
def quote_for(self, ts_code: str) -> dict:
    """下单/估值行情：feed.order_quote 优先；feed 不可用 → quotes.order_quote；都失败 → {}。"""

def quote_map(self, codes: Iterable[str]) -> dict[str, dict]:
    """批量版；单个失败不影响其他。"""

def on_quotes(self, quotes: dict[str, dict]) -> None:
    """feed listener：evaluate + match_pending；异常写 audit，绝不抛回 feed 线程。"""

def wanted_codes(self) -> list[str]:
    """持仓 + ACTIVE 条件单 + REPORTED/PARTIAL 挂单，去重排序。"""
```

要求：
- `on_quotes` 里把 `quotes` 传给 `self.evaluate(quotes)` 和 `self.match_pending(quotes)`；只处理有 `last` 的项。
- `quote_for` 通过 `from app.core.feed import get_feed` 懒 import；`feed.order_quote` 返回空时回落 `from app.core import quotes` 的 `order_quote`。
- `wanted_codes` 用 `self.list_conditions(status="ACTIVE")` 和 `self.list_orders()`；过滤 `status in ("REPORTED","PARTIAL")`；持仓取 `self.account().get("positions", {})` 的键。

---

## 5. 插件路由（全部 root）

### 5.1 `POST /api/order/place`

Pydantic body 与旧端一致：
```python
class OrderPlaceBody(BaseModel):
    code: str
    side: str = "buy"
    qty: int
    price: float | None = None
    order_type: str = "limit"
    name: str = ""
    note: str = ""
```
处理：
```python
svc = ctx.paper
code = to_ts_code(body.code)  # 用 app.core.quotes.to_ts_code
quote = svc.quote_for(code) if code else {}
try: res = svc.place(code or body.code, body.side, body.qty, price=body.price,
                     order_type=body.order_type, quote=quote,
                     source="manual", name=body.name, note=body.note)
except PaperOrderError as e: raise HTTPException(e.status, detail=f"{e}（{e.code}）")
return {"ok": True, "order": res.get("order") or {},
        "warnings": res.get("warnings") or [], "account": res.get("account") or {},
        "mode": svc.trade_mode()}
```

### 5.2 `POST /api/order/cancel`
`{order_id}` → `svc.cancel_order`；返回 `{ok:true, order_id, status}`。

### 5.3 `GET /api/order/list?date=YYYYMMDD`
```python
conds = svc.list_conditions()
acc = svc.account_view()
codes = {c["code"] for c in conds if c.get("status")=="ACTIVE"} | set((acc.get("positions") or {}).keys())
metrics = {c: order_metrics(glb) for c in codes}
return {"ok": True, "mode": svc.trade_mode(), "date": date or today8(),
        "orders": svc.list_orders(date), "conditions": conds,
        "account": acc, "metrics": metrics}
```
`order_metrics` 调 `app.core.quotes.order_metrics(normalize(code))`，失败给 `{"atr14": None, "ma5": None}`。

### 5.4 `POST /api/condition/add`
Pydantic body 与旧端一致（`code,type,qty,trigger_price,pct,amount,order_type,limit_price,side,at,valid_until,group,confirm_count,buffer_atr,signal,name,note`）。
调 `svc.add_condition(...)`，返回 `{ok:true, condition: rec}`。

### 5.5 `POST /api/condition/cancel` → `{ok:true, condition: rec}`

### 5.6 `GET /api/condition/list?code=&status=` → `{ok:true, conditions: [...]}`

### 5.7 `POST /api/condition/evaluate?force=1`
同旧端：取所有 ACTIVE 条件 + 活跃挂单代码 → `quote_map` → `svc.evaluate(quotes, force=bool(force))` → `svc.match_pending(quotes)`；返回 `{ok, triggered, cancelled, expired, filled, quotes:{code:{last,quote_time,fresh,src}}}`。

### 5.8 `GET /api/paper_account`
按旧端 `api_paper_account` 的形状（`_vendor_src/paper_views_reference.py`）：
- `out = dict(acct)`；`acct` 不存在时返回 404（同旧端）。
- `positions_list` 用 `_build_holdings_view` 移植版（`views.py`）。
- quote map：`svc.quote_map(positions.keys())`，元素需转换为 `{price, prev_close, source, asof}`（`price=last`，`prev_close=pre_close`）。
- `account` 字段：`total_asset/available_cash/market_value/frozen_cash/unrealized_pnl/single_order_cap/cash_reserve_pct/updated_at`，口径同参考。
- 名称表：`ctx.json_read("stock_names")` 的 `names` dict；取不到回退代码。

### 5.9 `GET /api/ledger?mode=paper`
按旧端 paper 分支逐字移植（参考文件 4179–4305）：
- 冻结：`svc.frozen_cash()` / `svc.frozen_qty(code)` 或读 `freeze` 文件的 `frozen` 段（与旧端一致，直接用 service 方法更稳）。
- `account` 10 个键：`total_asset,cash,available_cash,frozen_cash,market_value,position_pnl,position_pnl_pct,day_pnl,day_pnl_pct,cash_ratio`。
- `positions` 行：`code,name,qty,available_qty,frozen_qty,cost_price,price,price_source,pnl,pnl_pct,day_pnl,day_pnl_pct,market_value,weight_pct,bought_today,sold_today,mode="paper"`。
- `trades`：当日 FILLED/PARTIAL，按时间倒序。
- `conds_expiring`：可移植 `_conds_expiring` 简化版（days_left<=3）。
- `book`：移植 `_book_extras`（活跃单/历史，50 条上限）。
- `mode=live`：返回 `{"ok": False, "mode": "live", "error": "paper-only: 实盘账本请在旧 8800 查看"}`，HTTP 200（前端不会崩）。

### 5.10 `GET /api/trades/{ts_code}?days=60`
移植 `build_trades_payload` 的**纸面部分**（参考文件 9753–10018）：
- 只取 `status=FILLED`；
- points 字段 `mode/side/date/t/price/qty/amount/name/id`；
- `cost.paper`：持仓 `cost_price`，没有则 None；
- `cost.live`：从 `ctx.panel_json("real_positions")` 找该 code 的 `cost_price`，没有 None；
- 非法代码/参数 → 400。

### 5.11 `GET /api/paper/feed/status`
返回 `ctx.feed.status()`（新增诊断端点）。

---

## 6. compat_misc 冲突处理

`app/plugins/compat_misc/backend.py` 目前注册了：
- `@router.get("/order/list")`
- `@router.get("/trades/{ts_code}")`
- `@router.get("/condition/list")`
- `@router.get("/paper_account")`
- `@router.get("/ledger")`

**删除这 5 个路由装饰器与对应的路由函数**（可以保留 `build_*` 纯函数供测试，不注册即可）。
同步修改 `app/plugins/compat_misc/tests/test_compat_misc.py`：删除/改写这 5 个端点的断言，保留其余 11 个只读端点测试。

启动时 `main.py` 会检查 root 路径冲突：paper 插件与 compat_misc 不得再抢同一路径。

---

## 7. 测试 `app/plugins/paper/tests/test_paper_api.py`

用 `TestClient` + 临时账本目录 + 假行情：

1. fixture：
   - `monkeypatch.setenv("PANEL_V2_PAPER_DIR", str(tmp_path/"paper"))`
   - `monkeypatch.setenv("PANEL_V2_FEED","0")`
   - `from app.core.paper import service; service.reset_service()`
   - `create_app()` → `TestClient`。
   - monkeypatch `PaperOrderService.quote_for` 返回固定 quote：
```python
{"last": 10.0, "pre_close": 9.8, "bids": [{"price": 9.99, "vol": 1000}],
 "asks": [{"price": 10.01, "vol": 1000}], "limit_up": 11.0, "limit_down": 9.0,
 "quote_time": "10:00:00", "fresh": True, "src": "fake"}
```
2. `test_place_and_list`：POST place buy 100 @10.0 → 200 `ok:true`；GET order/list 有 1 条 order；GET paper_account 有 `account`/`positions_list`；GET ledger paper 有 `positions`/`trades`/`book`。
3. `test_cancel`：挂单后 cancel → `status=CANCELLED`。
4. `test_condition_add_list_cancel`：加 stop_loss 条件单 → list 有 1 条 → cancel 后状态 CANCELLED。
5. `test_trades_points`：造一笔 FILLED 的 paper trade（可直接写 `paper_trades.json` 或走 place 后 reconcile），GET `/api/trades/{code}` 有 points、`count>=1`。
6. `test_no_route_conflict`：`GET /api/health` 200，`/_plugins/health` 全 ok；`compat_misc` 不再拥有 `/api/paper_account`。
7. `test_feed_status_endpoint`：GET `/api/paper/feed/status` 200，有 `state` 键。

**不要真连 QMT / eltdx**：所有 `quote_for` 都 monkeypatch。

---

## 8. 硬约束

1. 插件代码禁止出现 `xtquant`/`xtdata`/`TdxClient`/`from eltdx`；`FORBIDDEN_QUOTE_PATTERNS` 会扫描。
2. 插件不得写文件；所有写账本都在 `app/core/paper/`。
3. 不改 `app/core/paper/broker.py` / `orders.py`（除非接口不匹配，先问主会话）。
4. 不改 `app/core/feed.py` 的线程模型，只加 `set_interest_provider`。
5. 中文注释保留。
6. JSON 一律 `ensure_ascii=False`，UTF-8。

---

## 9. 交付格式

1. 文件清单（绝对路径）。
2. 路由清单与返回键。
3. 删掉 compat_misc 的哪些路由。
4. 明确写：**「未运行验证：本环境无 shell，pytest 由主会话执行」**。
5. 需主会话确认的问题单独列。

---

## 10. 验收（主会话）

```powershell
cd OH-WorkSpace\projects\panel-v2
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest app/plugins/paper/tests -q
OH-WorkSpace\envs\qmt312\Scripts\python.exe -m pytest -q
```

`compare_legacy.py` 的 32 项中，`/api/paper_account`、`/api/order/list`、`/api/ledger`、`/api/trades` 现在应返回真实数据（不再是骨架），主会话会重新比对。
