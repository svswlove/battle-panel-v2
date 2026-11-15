# -*- coding: utf-8 -*-
"""`app/core/quotes.py::ths_index_weekly()` 单测（S2 接缝，2026-10-24）。

口径唯一源：旧 8800 `engine/server.py::_ths_index_bars_mcp`（同 URL / 同参数 / 同字段名）。
这里锁的是四件最容易搬错的事：

1. **零网络**：`urllib.request.urlopen` 全程 monkeypatch，任何用例都不出站；
2. **失败即 None**：凭据缺失 / HTTP 异常 / 上游 `code != 0`（含今日实测的 5003）/
   空 items —— 一律 None，不抛、不编造替代数据（不用日线聚合冒充周线）；
3. **响应体两种形态**：纯 JSON 与 SSE（`event:`/`data:` 行）都能解析成同一份 bars；
4. **字段映射与顺序**：`date_ms → t`（YYYYMMDD）、`*_price → o/h/l/c`、
   `volume → v`、`turnover → amount`，且结果按 `t` 升序（上游下发顺序不可信）。

另锁一条纪律：**凭据不进异常文案/日志**——上游异常时函数静默返回 None，
不会把 `X-api-key` 带出去。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import quotes  # noqa: E402

# 测试夹具用的假 key：只用于构造配置、断言不泄漏，**不是任何真实凭证**。
# 2026-11-15 公开前改造：原值是真实形状的串，换掉。
API_KEY = "test-api-key-not-a-real-credential"
CODE = "881121.TI"


# ---------------------------------------------------------------------------
# 假上游
# ---------------------------------------------------------------------------

def _ms(y: int, m: int, d: int) -> int:
    """本地时区当日 0 点毫秒（与实现同口径：`datetime.fromtimestamp`）。"""
    return int(datetime(y, m, d).timestamp() * 1000)


def _item(y: int, m: int, d: int, o: float, h: float, l: float, c: float,
          v: float, amt: float) -> Dict[str, Any]:
    return {"date_ms": _ms(y, m, d), "open_price": o, "high_price": h,
            "low_price": l, "close_price": c, "volume": v, "turnover": amt}


def _tool_result(data: Dict[str, Any], code: int = 0, msg: str = "ok") -> Dict[str, Any]:
    """MCP `tools/call` 外层：result.content[0].text = 业务 JSON 字符串。"""
    return {"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text",
                                    "text": json.dumps({"code": code,
                                                        "message": msg,
                                                        "data": data})}]}}


class _Resp:
    """最小 urlopen 响应替身（context manager + read().decode()）。"""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """凭据文件指到 tmp（默认含有效 key）。"""
    p = tmp_path / "hithink_config.json"
    p.write_text(json.dumps({"headers": {"X-api-key": API_KEY}}), encoding="utf-8")
    monkeypatch.setattr(quotes, "THS_CFG_FILE", p)
    return p


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, body: str | None,
                   seen: List[Any] | None = None,
                   exc: BaseException | None = None) -> None:
    """把 `urllib.request.urlopen` 换成假实现；`seen` 收集每次的 Request。"""

    def _fake(req: Any, timeout: Any = None, *a: Any, **kw: Any) -> Any:
        if seen is not None:
            seen.append(req)
        if exc is not None:
            raise exc
        return _Resp(body or "")

    import urllib.request as _u

    monkeypatch.setattr(_u, "urlopen", _fake)


def _header(req: Any, name: str) -> str | None:
    """取 Request 头（urllib 会把键 `.capitalize()`，故按小写比对）。"""
    for k, v in (req.header_items() or []):
        if k.lower() == name.lower():
            return v
    return None


# ---------------------------------------------------------------------------
# 1. 凭据
# ---------------------------------------------------------------------------

def test_missing_cfg_file_returns_none(tmp_path, monkeypatch):
    """凭据文件不存在 → None，且**不发请求**（零出站）。"""
    monkeypatch.setattr(quotes, "THS_CFG_FILE", tmp_path / "nope.json")
    seen: List[Any] = []
    _patch_urlopen(monkeypatch, None, seen=seen)
    assert quotes._ths_api_key() is None
    assert quotes.ths_index_weekly(CODE, "20260101") is None
    assert seen == [], "凭据缺失时不应发出任何请求"


def test_broken_cfg_file_returns_none(tmp_path, monkeypatch):
    """凭据文件损坏（非 JSON）→ None（不抛）。"""
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(quotes, "THS_CFG_FILE", p)
    assert quotes._ths_api_key() is None


def test_empty_key_returns_none(cfg, monkeypatch):
    """文件在但 key 为空串 → None，不发请求。"""
    cfg.write_text(json.dumps({"headers": {"X-api-key": ""}}), encoding="utf-8")
    seen: List[Any] = []
    _patch_urlopen(monkeypatch, None, seen=seen)
    assert quotes.ths_index_weekly(CODE, "20260101") is None
    assert seen == []


def test_api_key_fallback_field(cfg, monkeypatch):
    """`headers.X-api-key` 缺失时退到顶层 `api_key`（旧配置写法）。"""
    cfg.write_text(json.dumps({"api_key": API_KEY}), encoding="utf-8")
    assert quotes._ths_api_key() == API_KEY


# ---------------------------------------------------------------------------
# 2. 上游异常 / 业务错误 → None
# ---------------------------------------------------------------------------

def test_urlopen_exception_returns_none(cfg, monkeypatch):
    """HTTP 层异常（拒绝连接/超时等）→ None，不抛。"""
    import urllib.error

    _patch_urlopen(monkeypatch, None,
                   exc=urllib.error.URLError("connection refused"))
    assert quotes.ths_index_weekly(CODE, "20260101", "20261024") is None


def test_upstream_code_5003_returns_none(cfg, monkeypatch):
    """上游 `code=5003 Data source unavailable`（今日实测）→ None，不编造 bars。"""
    body = json.dumps(_tool_result({}, code=5003, msg="Data source unavailable"))
    seen: List[Any] = []
    _patch_urlopen(monkeypatch, body, seen=seen)
    assert quotes.ths_index_weekly(CODE, "20260101", "20261024") is None
    assert len(seen) == 1


def test_empty_items_returns_none(cfg, monkeypatch):
    """`code=0` 但 `item` 为空 → None（空列表不算可用）。"""
    body = json.dumps(_tool_result({"item": []}))
    _patch_urlopen(monkeypatch, body)
    assert quotes.ths_index_weekly(CODE, "20260101", "20261024") is None


def test_no_content_text_returns_none(cfg, monkeypatch):
    """外层缺 `result.content[0].text` → None。"""
    _patch_urlopen(monkeypatch, json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}))
    assert quotes.ths_index_weekly(CODE, "20260101") is None


def test_garbage_body_returns_none(cfg, monkeypatch):
    """响应体既非 JSON 也无 `data:` 行 → None。"""
    _patch_urlopen(monkeypatch, "<html>502 Bad Gateway</html>")
    assert quotes.ths_index_weekly(CODE, "20260101") is None


def test_bad_start8_returns_none(cfg, monkeypatch):
    """start8 非法（非 YYYYMMDD）→ None，不抛（上游窗口构造失败即不可用）。"""
    seen: List[Any] = []
    _patch_urlopen(monkeypatch, None, seen=seen)
    assert quotes.ths_index_weekly(CODE, "2026-01-01") is None
    assert seen == []


# ---------------------------------------------------------------------------
# 3. 正常解析：JSON 体 / SSE 体
# ---------------------------------------------------------------------------

def _items_unordered() -> List[Dict[str, Any]]:
    """上游下发顺序**故意乱序**（先近后远），验证本层按 t 升序排。"""
    return [_item(2026, 10, 16, 1200.0, 1260.0, 1180.0, 1250.0, 3.1e9, 3.2e10),
            _item(2026, 9, 30, 1100.0, 1150.0, 1090.0, 1140.0, 2.2e9, 2.3e10),
            _item(2026, 10, 9, 1140.0, 1210.0, 1130.0, 1200.0, 2.8e9, 2.9e10)]


EXPECTED = [
    {"t": "20260930", "o": 1100.0, "h": 1150.0, "l": 1090.0, "c": 1140.0,
     "v": 2.2e9, "amount": 2.3e10},
    {"t": "20261009", "o": 1140.0, "h": 1210.0, "l": 1130.0, "c": 1200.0,
     "v": 2.8e9, "amount": 2.9e10},
    {"t": "20261016", "o": 1200.0, "h": 1260.0, "l": 1180.0, "c": 1250.0,
     "v": 3.1e9, "amount": 3.2e10},
]


def test_parse_plain_json_body(cfg, monkeypatch):
    """纯 JSON 响应体 → 升序 bars（字段映射与顺序全对）。"""
    body = json.dumps(_tool_result({"item": _items_unordered()}))
    seen: List[Any] = []
    _patch_urlopen(monkeypatch, body, seen=seen)

    bars = quotes.ths_index_weekly(CODE, "20260101", "20261024")

    assert bars == EXPECTED
    assert [b["t"] for b in bars] == sorted(b["t"] for b in bars)
    # 请求侧：URL / 头 / 参数一次钉死，防“能解析但对错了源”
    req = seen[0]
    assert req.full_url == quotes.THS_INDEX_MCP_URL
    assert _header(req, "X-api-key") == API_KEY
    sent = json.loads(req.data.decode())
    assert sent["method"] == "tools/call"
    assert sent["params"]["name"] == "get_a_share_index_prices_historical"
    args = sent["params"]["arguments"]
    assert args["interval"] == "1w"
    assert args["thscode"] == CODE          # 大写归一
    assert args["start"] == _ms(2026, 1, 1)
    assert args["end"] == _ms(2026, 10, 24) + 86_400_000   # end 含当日 → +1 天


def test_parse_sse_body(cfg, monkeypatch):
    """SSE 响应体（`event:` + `data:` 行）→ 与 JSON 体同一份 bars。"""
    payload = _tool_result({"item": _items_unordered()})
    body = ("event: message\n"
            "data: " + json.dumps(payload) + "\n\n")
    _patch_urlopen(monkeypatch, body)
    assert quotes.ths_index_weekly(CODE, "20260101", "20261024") == EXPECTED


def test_end8_defaults_to_today(cfg, monkeypatch):
    """`end8` 省略 → 用今天（含当日）。"""
    seen: List[Any] = []
    _patch_urlopen(monkeypatch, json.dumps(_tool_result({"item": _items_unordered()})),
                   seen=seen)
    quotes.ths_index_weekly(CODE, "20260101")
    args = json.loads(seen[0].data.decode())["params"]["arguments"]
    today = datetime.now().strftime("%Y%m%d")
    assert args["end"] == int(datetime.strptime(today, "%Y%m%d").timestamp() * 1000) + 86_400_000


def test_items_without_date_ms_skipped(cfg, monkeypatch):
    """缺 `date_ms` 的脏行跳过，其余照常返回（不因一行坏数据整批放弃）。"""
    items = [{"open_price": 1, "close_price": 2}] + _items_unordered()
    _patch_urlopen(monkeypatch, json.dumps(_tool_result({"item": items})))
    assert quotes.ths_index_weekly(CODE, "20260101", "20261024") == EXPECTED


# ---------------------------------------------------------------------------
# 4. 代理旁路：泛化后的 `_no_proxy_hosts` 与 `_f10_no_proxy` 行为一致
# ---------------------------------------------------------------------------

def test_no_proxy_hosts_adds_and_restores(monkeypatch):
    """`_no_proxy_hosts` 期间写入主机、退出还原；`_f10_no_proxy` 写的仍是 F10 那两台。"""
    import os

    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    with quotes._no_proxy_hosts((quotes.THS_INDEX_MCP_HOST,)):
        live = os.environ.get("no_proxy") or ""
        assert quotes.THS_INDEX_MCP_HOST in live
    assert not os.environ.get("no_proxy")

    with quotes._f10_no_proxy():
        live = os.environ.get("no_proxy") or ""
        for h in quotes._F10_HOSTS:
            assert h in live
    assert not os.environ.get("no_proxy")


def test_weekly_call_sets_no_proxy_for_ths_host(cfg, monkeypatch):
    """真实调用路径会走代理旁路：urlopen 执行时 no_proxy 里含 THS 主机。"""
    import os

    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    seen_env: List[str] = []

    def _fake(req: Any, timeout: Any = None, *a: Any, **kw: Any) -> Any:
        seen_env.append(os.environ.get("no_proxy") or "")
        return _Resp(json.dumps(_tool_result({"item": _items_unordered()})))

    import urllib.request as _u

    monkeypatch.setattr(_u, "urlopen", _fake)
    quotes.ths_index_weekly(CODE, "20260101", "20261024")
    assert seen_env and quotes.THS_INDEX_MCP_HOST in seen_env[0]


# ---------------------------------------------------------------------------
# 5. 凭据不泄漏
# ---------------------------------------------------------------------------

def test_credential_not_leaked_on_failure(cfg, monkeypatch, caplog, capsys):
    """上游异常时：返回 None、不抛、凭据不进日志/标准输出/异常文案。"""
    import urllib.error

    _patch_urlopen(monkeypatch, None, exc=urllib.error.HTTPError(
        quotes.THS_INDEX_MCP_URL, 401, "Unauthorized", {}, None))  # type: ignore[arg-type]
    assert quotes.ths_index_weekly(CODE, "20260101") is None
    out = capsys.readouterr()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for blob in (out.out, out.err, logged):
        assert API_KEY not in blob
