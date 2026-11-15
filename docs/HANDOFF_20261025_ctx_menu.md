# B 单验收：全局右键菜单（2026-10-25）

## 结论

**通过。** 六项实测全部有效，代码只改壳层，未污染卡片。

## 改动

| 文件 | 内容 |
|---|---|
| `app/web/shell.js` | 新增右键菜单段（17 个函数 + 启动处 `bindCtxMenu()`） |
| `app/web/shell.css` | 新增 `.ctx-menu` / `.ctx-item` / `.ctx-submenu` / `.ctx-sep` / `.ctx-head` / `.ctx-arrow` 一组规则 |

菜单节点 id = **`ctx-menu`**（连字符），与旧页的 `ctxMenu`（驼峰）不同。这是个坑：
旧页源码读惯了会去 `getElementById('ctxMenu')`，拿不到。补验时我自己踩了一次。

未改任何 `app/plugins/*`、`app/core/`、`engine/`、`data/panel/*`，未重启 8810。

## 实测（真实鼠标事件，CDP 派发，非 `.click()`）

| # | 项 | 结果 |
|---|---|---|
| 1 | 自选池个股右键 | 菜单弹出，位置 (200,185)，头部「恒生电子 · 600570.SH」，已自选则有「移出自选」 |
| 2 | 子菜单 | 悬停展开，列出 4 个分组（自选/追踪/走坏/权重）+ 新建分组… |
| 3 | 复制代码 | 剪贴板读出 `600570.SH`，菜单自动关闭 |
| 4 | 打开 K 线 | 切到 `kline::kline.main` 卡，canvas 2 个，显示「600570.SH · 241 点 · 分时·实时」 |
| 5 | 点菜单外 / Esc | 均关闭 |
| 6 | **贴边翻转** | 鼠标在 (1441,638) 右键 → 菜单自动移到 (1296,514)，右边界 1472 < 视口 1480、下边界 686 < 694，无溢出 |

第 6 项第一次测出「溢出 168px」，是我探针写错了——我手动改了 `style.left/top` 去模拟贴边，
绕过了代码里的翻转逻辑。改用真实鼠标在右下角右键后，翻转正常。

## 与旧页的三处差异（有意为之，已记）

1. `.wl-wc` 直接读 `data-kind`。旧页靠缓存 items 反查，v2 卡片结构不同，读属性更稳。
2. 加 v2 专用兜底 `.card-host[data-card-key^="sector::"] [data-code]`。
   板块卡的行只带 `data-code`（无 `data-ctx`/`data-open-*`），不加这条整张卡没菜单。
3. 打开板块详情 / K 线：嵌入旧页时走既有 `v2:openAmv` / `v2:openKline`；
   独立打开则切卡，不伪造入口。

## 属性覆盖现状（决定哪些卡右键生效）

| 卡 | `data-ctx` | `data-open-kline` | `data-open-amv` | `data-kname` | 右键是否生效 |
|---|---|---|---|---|---|
| concept | - | 7 | 6 | - | 生效 |
| review | - | 4 | - | 2 | 生效 |
| watchlist | - | 3 | 2 | - | 生效 |
| sector | - | 2 | - | - | 生效（走兜底） |
| selection | - | 2 | - | - | 生效 |
| dashboard | 1 | 3 | - | - | 生效 |
| **intraday** | - | - | - | - | **不生效**（C 单补） |
| amv | - | - | - | - | **不生效**（本单不管） |

`intraday` 和 `amv` 一个属性都没有。按任务书边界，本单只做壳层菜单，
卡片的属性补全归各卡自己的单子——C 单会重做盘中，顺带补；`amv` 暂不动。

## 测试

`node --check app/web/shell.js` 通过。
`pytest app/tests/test_fe_shell_bridge.py` → **20 passed**（守护壳层契约那条通过）。

`pytest app/tests` 有 1 条红：`test_undefined_tokens_not_used`，
报 `sector/frontend.js: ['--hm-b1']`。这是 **A 单（板块 DOM 照抄）正在改「在榜」网格时引入的**，
与本单无关（本单没碰 sector 插件）。A 单还在运行，待其交付后由 01 验收时一并核。

## 遗留

- 三套主题下的菜单配色对照未做（需要连续切换主题 + 多次右键，本轮只验了浅色）
- `intraday` / `amv` 右键不生效，等 C 单补属性
- 旧页还有一个独立的右键菜单：**选股战法标签右键 = 选显示哪几种形态**（`selTabMenu`，带全选/清空）。
  这个在 v2 里也没有，但它是 selection 卡内部逻辑，不属于壳层菜单职责，未做。

## 另一个踩过的坑：菜单节点 id

旧页是 `id="ctxMenu"`（驼峰），v2 实现是 `id="ctx-menu"`（连字符）。
两者不一致本身不违契约，但**写探测脚本/后续维护时极易拿错**，拿错的表现是
「`getElementById('ctxMenu')` 返回 null → 误判成菜单未实现」。

本轮我自己就这样误判了一次。已记：v2 侧用 `ctx-menu`。
