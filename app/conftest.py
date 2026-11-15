# -*- coding: utf-8 -*-
"""app 级测试隔离（2026-11-07）。

`app/tests/conftest.py` 只覆盖自身目录；插件测试（`app/plugins/*/tests`）不会读到它。
这里给整个 `app/` 测试树设硬开关：

- `PANEL_V2_NOTIFY_DISABLE=1`：禁真实外部推送（同 app/tests/conftest.py）。
- `PANEL_V2_PREWARM=0`：禁盘后预热线程，避免测试用真 OUT_DIR 的清单请求真插件。
"""
import os

os.environ.setdefault("PANEL_V2_NOTIFY_DISABLE", "1")
os.environ.setdefault("PANEL_V2_PREWARM", "0")
