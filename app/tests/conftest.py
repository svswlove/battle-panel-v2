# -*- coding: utf-8 -*-
"""测试全局隔离：禁止真实外部推送（2026-10-28）。

背景：`test_notify.py` / `test_cond_watch_v2.py` 会走 enqueue → sender 线程 → QQ 官方 API，
实测污染了生产 `notify_health.json` 并真发出一条测试消息（22:39:52）。
这里给整个测试进程设置硬开关，`core/notify.py::_env_disabled()` 会拒绝入队与发送；
需要验证发送路径的测试自己 `monkeypatch.delenv("PANEL_V2_NOTIFY_DISABLE")`。
"""
import os

os.environ.setdefault("PANEL_V2_NOTIFY_DISABLE", "1")
# 盘后预热线程（2026-11-07）：测试里不启，避免用真 OUT_DIR 的清单去请求真插件。
os.environ.setdefault("PANEL_V2_PREWARM", "0")
