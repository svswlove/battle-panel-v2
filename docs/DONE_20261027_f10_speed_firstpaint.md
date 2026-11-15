# F10 提速与首屏改造（2026-10-27）

> 接 `DONE_20261026_kline_modal_f10_cache.md`。主人在此基础上追加两条要求：
> ① 能否探测哪台服务器响应更快 ② 首屏要 **500ms 以内**。
> 方案选定「首屏只等 1 个请求，其余后台补」。

## 1. 关键事故：自锁死（先写，因为它带偏过排查方向）

改造过程中所有 F10 调用变成 100% 超时，表面像「网络间歇超时」，实际是**我自己引入的死锁**。

两处叠加：

1. `_F10_LOCK` 是 `threading.Lock`（不可重入），而调用链是嵌套拿锁：
   `_f10_no_proxy()`（拿锁，需在整个 F10 调用期间持有）→ `_f10_call()` → `_f10_client()`（再拿锁）
2. `_f10_client()` 在 `with _F10_LOCK` 块里又调了 `_f10_endpoint()`，后者内部也拿同一把锁

修法：改用 `threading.RLock()`（可重入），并在 `_f10_client()` 加锁块内**不再调用** `_f10_endpoint()`。
立回归测试 `app/core/tests/test_f10_lock.py`（含 3 秒看门狗线程，真死锁时不会让 pytest 永久挂住）。

## 2. 实测结论（推翻了三条先前假设）

### 2.1 `hot.icfqs.com` 不能当主用

| 服务器 | 握手 | 8 段合计 | 功能完整性 |
|---|---|---|---|
| `static.tdx.com.cn`（现用） | 47 ms | 1704 ms | **9 段全通** |
| `hot.icfqs.com`（候选） | 31 ms | **718 ms** | **9 段里 7 段 503** |

`hot.icfqs` 快一倍，但 `gdyj`/`zlcc`/`ybpj`/`ggzp`/`cwfx` 全部 503——只服务涨停梯队那一个 Entry
（对应 eltdx 的 `DEFAULT_LIMIT_BOARD_LADDER_BASE_URL`）。

**判定口径**：探测必须按「能取到值」而非「能连上」，否则会误选。

### 2.2 16 秒的根因：IPv6 不可达 × 每个地址各吃满 timeout

`static.tdx.com.cn` 解析出 **1 个 IPv6 + 1 个 IPv4**，本机 IPv6 实测 connect 稳定 8000ms 超时。

`eltdx/f10/_http.py::_connect_ipv4_first` 是「排序后**遍历全部地址**」——AF_INET 优先只是排序
不是过滤，**每个地址各消耗一次 timeout**。`timeout=8` 时最坏 ≈ 8s + 8s = 16s，与实测 16047ms 吻合。

补充：`prefer_ipv4` 参数在 `_http.py` 里**根本没被使用**（仅存在于 client 签名与文档）。
上游 `github.com/electkismet/eltdx` 18 条 issue 无同款现象，`docs/F10_7615.md:46` 只说明
「多地址时总耗时可能超过一次 timeout」——即官方已知但不处理。

### 2.3 并发不可行

| 轮次 | 墙钟 | 结果 |
|---|---|---|
| 1 | 2578 ms | 7/8（valuation 超时） |
| 2 | 391 ms | 8/8 |
| 3 | 2656 ms | 7/8（rzrq 超时） |

快时能进 400ms，但**每轮必有 1 项失败**，服务端不接受并发。不采用。

### 2.4 单请求本身 190–300ms 波动

串行 9 段 1704–1844ms（好时）；差时单项各卡 2.5s、合计 8.3s。
**「不请求」是唯一能稳定进 500ms 的路径**，即缓存命中 + 首屏只取一项。

## 3. 改动清单

### 3.1 `app/core/quotes.py`
- `F10_HTTP_TIMEOUT` 8.0 → **2.5**（与旧端 `F10_HTTP_TIMEOUT` 同值）；注释写明「最坏 = timeout × 地址数」
- `F10_ENDPOINTS`：2 条候选（IP 直连 / 域名），各带 `Host` 头（直连 IP 会丢 vhost 路由）
- `F10_PROBE_TTL = 900s`、`F10_PROBE_PREFER_MS = 200ms`
- `F10_ENDPOINT_STATE`：探测结果落盘 `data/cache/f10_endpoint.json`，跨进程复用
  （否则每次重启重探 ~500ms，会把冷查首屏从 250ms 抬到 890ms）
- 新增 `_f10_build_client(base_url, host)`、`_f10_probe_endpoints()`、`_f10_endpoint()`、
  `_f10_endpoint_load()`、`_f10_endpoint_save()`
- `_f10_client()` 按选中 endpoint 建 client，endpoint 变了自动重建
- 新增 `f10_valuation_only(glb)` + `_step_valuation(key)`：只跑 valuation 一步，只读磁盘缓存、不写
- 新增 `logger`（本模块原先无）

**选优规则**（为什么不是纯「选最快」）：实测 IP 直连与域名差 40–60ms，方向不稳定；但 IP 直连
消除了「IPv6 不可达时逐个地址等满 timeout」这个 16 秒级风险。所以
**差距 ≤200ms 时优先清单靠前者（IP 直连），超过才真选最快**。

### 3.2 `app/plugins/compat_kline/backend.py`
- 新增 `GET /api/f10_brief/{ts_code}/quick` → `{ok, code, val, fetched_at}`
  失败不 500（`val` 为空对象，前端继续等完整段）；旧门面无 `f10_valuation_only` 时也安全返回

### 3.3 `app/plugins/kline/frontend/overlay.js`
- 新增 `f10QuickHtml(q)`：只渲染总市值 / PE / PB / 分位 + 「其余段载入中…」
- `loadF10()` 改两路并发：`/quick` 先出首屏，完整端点后台补
- `el.f10.dataset.full` 标记：完整段已出则轻量不覆盖；右键刷新时清空许可重占位
- 完整段失败时若 quick 已出数则**不顶掉**

### 3.4 新增 `app/core/tests/test_f10_lock.py`
3 条守卫：锁类型必须可重入、嵌套获取不挂、`_f10_client()` 全链路不挂（均带超时看门狗）。

## 4. 验证（全部实测）

### 4.1 端点耗时

| 场景 | 耗时 |
|---|---|
| `/quick` 冷查（磁盘无该代码，走真实 valuation） | **360 / 203 / 265 ms** |
| `/quick` 命中磁盘缓存 | **31 / 16 ms** |
| 完整端点命中磁盘缓存 | 46 ms |

### 4.2 弹窗端到端（CDP 实测，记录 F10 栏每次变化）

| 时刻 | 状态 |
|---|---|
| 78 ms | 弹窗打开 |
| 101 ms | 「F10 载入中…」 |
| **353 ms** | **首屏估值出现**：总市值 10718 亿 / PE(TTM) 7.07（68%分位）/ PB 0.94（38%分位） |
| 5797 ms | 完整段补齐：行业、上市日、股东户数、主力今日/5日、北向、2026 预测 EPS、季度净利、股东户数序列 |

首屏 353ms **达 500ms 目标**。未改卡（`main-title` 前后一致）。

### 4.3 死锁守卫

`test_f10_lock.py` 3 passed。

### 4.4 全量回归

见 §6。

## 5. 未做（及原因）

- **并发化**：服务端不接受，每轮必有 1 项失败（§2.3）
- **后台预热**：主人判断「5 分钟内 F10 不会变」，有磁盘缓存后预热无意义
- **`hot.icfqs` 作主用**：功能不全（§2.1）
- **前端标注 `fetched_at`**：上一批已做（F10 标题栏显示「刚刚/N 分钟前」+ 右键刷新）

## 6. 待补

- [ ] 全量 pytest 最终计数
- [ ] 文件修改日志登记
