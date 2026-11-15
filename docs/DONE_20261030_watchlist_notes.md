# 2026-10-30 自选池标注批（4 条）：41 / 43 / 47 / 52

> 说明：这批原派给子代理，它在跑到第 6 分钟时失败（未留回执）。已改的文件我逐处核对后接手收尾：
> 撤掉它超范围加的「个股打分投影」，补完没做的 52，其余保留并复验。

## 1. 逐条

| 标注 | 原话 | 处理 |
|---|---|---|
| `N20261028-141425-41` | 把副图改为个股AMV+10日线/白线 | **已改**。副图从「收盘归一 + AMV 归一」两条各自归一到 100，改成 `amv / amvMa / amvWhite` 三条同量纲（亿）共用一条纵轴；CSS 换成 `wl-amv / wl-amv-ma / wl-amv-wh`。实测 DOM 里三条 path 都在 |
| `N20261028-212502-43` | 点击右键打开板块详情时…被 K 线挡住 | **已解决（10-28 的浮层栈）**。复核：`shell.js::overlayStack` 按栈序重排 z（Z_BASE=100、Z_STEP=5），板块详情后开 → z=105 > K 线 100。截图 `note43_sector_over_kline.png` 里详情完整盖在上层 |
| `N20261028-215730-47` | 自选池打开速度太慢 | **已改**。① 取数结果搬到模块作用域 `MOD`（切 Tab 回来不重拉，15s 内先画缓存再后台刷新）；② 条目与关键位 `Promise.all` 并行；③ 盘后档数据无变化不重建 DOM（`lastSig`），日线缓存过期仍重建。实测冷开 **536ms**、切走再切回 **63ms** |
| `N20261029-191333-52` | 卡片的黄线计算不全，只能计算到最后面一小段 | **已改**（我补）。根因：黄线 = (MA14+MA28+MA57+MA114)/4，前 113 根必然为空；前端只拉 `limit=60`，画 45 个点里只有末尾 7 个有黄线。改 `limit=160`（114 预热 + 45 显示 + 1 余量），实测黄线点数 7 → **45**，与收盘线等长 |

## 2. 撤掉的超范围改动

子代理额外加了「个股打分投影」（`fetchScore` + `scoreRow` + 6 条 CSS + 页眉文案 + `MOD.score` 缓存），
不在本批 4 条标注里，会增加 score 插件依赖与每卡一行 UI。已全部删除，`frontend.js` 里
`MOD.score / scoreRow / fetchScore / scoreCaliber / wl-score` 零残留。

保留的相邻改动：卡片 meta 的相对时间（`relTime`，对应标注 44 的同类问题；F10 面板那一处上一轮已改）。

## 3. 验证

- `python -m pytest app/plugins/watchlist/tests -q`：**27 passed**（新增 3 条断言：副图三条线同轴、模块缓存与并行取数、黄线根数）。
- 实机（headless，8810）：副图 path class = `wl-amv / wl-amv-ma / wl-amv-wh`；黄线 45 点；冷开 536ms / 热切 63ms。
- 截图：`_vis/verify_20261030/watchlist_after.png`、`note43_sector_over_kline.png`。

## 4. 边界

- 只改 `app/plugins/watchlist/{frontend.js,tests/test_watchlist_frontend.py}`；未动 K 线模块、未动其他插件、未重启 8810。
- `limit=160` 会让单票日线请求比 60 根多一点（约 +20ms），但只在冷开时发生，热切走模块缓存。
