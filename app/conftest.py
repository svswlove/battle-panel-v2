# -*- coding: utf-8 -*-
"""app 级测试隔离（2026-11-07；2026-11-15 增「无数据环境按用例跳过」）。

`app/tests/conftest.py` 只覆盖自身目录；插件测试（`app/plugins/*/tests`）不会读到它。
这里给整个 `app/` 测试树设硬开关：

- `PANEL_V2_NOTIFY_DISABLE=1`：禁真实外部推送（同 app/tests/conftest.py）。
- `PANEL_V2_PREWARM=0`：禁盘后预热线程，避免测试用真 OUT_DIR 的清单请求真插件。

无数据环境（GitHub Actions / 别人 clone 下来）：
有一批测试是**对着本机生产数据或旧引擎文件**做断言的（复盘 MD、恐慌广度、
旧前端 CSS/文件、概念知识库…）。没有 `data/panel/` 时它们必然失败，但那不是代码坏了。
这类用例逐个登记在 `_NEEDS_DATA_CASES`，**只在无数据环境**打 `needs_data` 并跳过；
本地有数据时一条都不跳，行为与改动前完全一致。

维护约定：CI 上新增的失败如果属于「缺本机数据」，把它的 nodeid 加进本清单；
如果是真的回归，就修代码。清单只减不滥加。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("PANEL_V2_NOTIFY_DISABLE", "1")
os.environ.setdefault("PANEL_V2_PREWARM", "0")

_ROOT = Path(__file__).resolve().parents[1]                 # panel-v2
_WS = Path(os.environ.get("PANEL_V2_WS") or _ROOT.parents[1])
_HAS_PROD_DATA = (_WS / "data" / "panel").is_dir()

# 需要本机生产数据 / 旧引擎文件 / Hana 宿主 skill 资源的用例（nodeid 相对 panel-v2）。
# 2026-11-15 实测：无数据环境下初测 41 条失败；首次跑 CI 又多出 1 条
# （test_paper_css_cache_key_covers_shim 依赖 ~/.hanako/skills/report-builder/report.css）。
_NEEDS_DATA_CASES = frozenset({
    "app/tests/test_contract.py::test_lib_accepts_real_amv_contrib",
    "app/tests/test_fe_shell_bridge.py::test_undefined_tokens_not_used",
    "app/tests/test_fe_shell_bridge.py::test_shell_css_tokens_exist",
    "app/tests/test_fe_shell_bridge.py::test_legacy_page_still_served_for_rollback",
    "app/tests/test_v2_notes_panel.py::test_panel_tokens_are_defined",
    "app/tests/test_web_single_source.py::test_legacy_mounts_point_to_engine_web",
    "app/tests/test_web_single_source.py::test_vendored_shell_equals_source_plus_patches",
    "app/tests/test_web_single_source.py::test_vendor_manifest_in_sync",
    "app/plugins/battle_plan_rw/tests/test_bp_batch_switches.py::test_notify_type_keys_match_legacy",
    "app/plugins/compat_misc/tests/test_compat_misc.py::test_selection_board_options",
    "app/plugins/compat_misc/tests/test_compat_misc.py::test_is_custom_board_999_segment",
    "app/plugins/concept/tests/test_concept_api.py::test_concept_index_real",
    "app/plugins/concept/tests/test_concept_api.py::test_chain_detail_outer_tokens_are_never_stock",
    "app/plugins/concept/tests/test_concept_api.py::test_concept_detail_missing_404",
    "app/plugins/intraday/tests/test_audit.py::test_audit_css_scoped_and_tokens_exist",
    "app/plugins/intraday/tests/test_audit.py::test_mapping_tables_match_legacy_verbatim",
    "app/plugins/intraday/tests/test_audit.py::test_cn_and_source_boundaries",
    "app/plugins/intraday/tests/test_audit.py::test_table_and_kcode_match_legacy",
    "app/plugins/intraday/tests/test_quotes.py::test_quotes_css_scoped_and_tokens_exist",
    "app/plugins/intraday/tests/test_research.py::test_research_css_scoped_and_tokens_exist",
    "app/plugins/intraday/tests/test_research.py::test_back_to_all_survives_click_during_inflight_request",
    "app/plugins/intraday/tests/test_research.py::test_watchdog_retries_stale_data_in_session",
    "app/plugins/intraday/tests/test_research.py::test_live_err_visible_when_fallback_to_close",
    "app/plugins/intraday/tests/test_research.py::test_back_button_shows_busy_state",
    "app/plugins/kline/tests/test_kline_legacy_mount.py::test_supertrend_single_line_and_hollow_candles",
    "app/plugins/kline/tests/test_kline_legacy_mount.py::test_range_volume_profile_uses_minute_bars",
    "app/plugins/ops/tests/test_ops_m1_endpoints.py::test_panic_store_mode_shape",
    "app/plugins/ops/tests/test_ops_m1_endpoints.py::test_panic_snapshot_only_when_needed",
    "app/plugins/ops/tests/test_ops_m1_endpoints.py::test_panic_stage_attached",
    "app/plugins/ops/tests/test_ops_m1_endpoints.py::test_panic_volume_item_forecast_on_down_day",
    "app/plugins/ops/tests/test_ops_m1_endpoints.py::test_panic_volume_skipped_on_up_day",
    "app/plugins/ops/tests/test_ops_m1_endpoints.py::test_panic_volume_close_uses_actual",
    "app/plugins/ops/tests/test_ops_plugin.py::test_ops_json_refresh_param_is_compatible",
    "app/plugins/review/tests/test_review_api.py::test_review_md_real",
    "app/plugins/review/tests/test_review_api.py::test_review_md_date_404",
    "app/plugins/review/tests/test_review_api.py::test_review_md_paper_real",
    "app/plugins/review/tests/test_review_api.py::test_review_md_paper_missing_css_is_failure",
    "app/plugins/review/tests/test_review_api.py::test_review_md_paper_render_error_not_500",
    "app/plugins/review/tests/test_review_api.py::test_build_review_md_paper_date_404",
    "app/plugins/review/tests/test_review_api.py::test_review_chart_real",
    "app/plugins/review/tests/test_review_frontend.py::test_scoped_css_rule_headers_match_legacy",
    "app/plugins/review/tests/test_paper_semantic_blocks.py::test_paper_css_cache_key_covers_shim",
})


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    if _HAS_PROD_DATA:
        return
    skip = pytest.mark.skip(
        reason="需要本机生产数据（data/panel 或旧引擎文件）；独立部署/CI 上自动跳过"
    )
    for item in items:
        if item.nodeid in _NEEDS_DATA_CASES:
            item.add_marker(pytest.mark.needs_data)
            item.add_marker(skip)
