# 任务书 · 周末批次 R4 旧 Tab 隐藏与卡替换开关（v2bridge 升级）

> 派单：01 → 子代理 ｜ 2026-10-24 晚 ｜ 并发组 R4/5
> 方向：v2bridge 从「加一个 Tab」升级为「可配置的逐 Tab 替换器」——旧 Tab 与 v2 卡并存，谁显示可切换，为绞杀式替换提供开关。

## 1. 目标

升级 `app/web/v2bridge.js`（+后端小改）：

1. **替换映射表**：core 新增 `app/web/v2_map.json`——`{"watchlist": "v2", "dashboard": "v2", "intraday": "old", ...}`，声明每个 Tab 用旧界面还是 v2 卡
2. **桥逻辑**：读 /static/v2_map.json（或注入时内联）；映射为 v2 的 Tab，点击时显示对应 iframe 卡视图（/v2/ 带 ?card=xxx 参数定位单卡）而非旧 section；映射为 old 的走旧逻辑不动
3. **降级**：卡加载失败（iframe onerror/超时 3s）自动回退旧 section，行内提示一行
4. **默认全 old**：出厂映射全部 old + 侧栏「v2 插件」Tab 保留——主人手动逐个切，不搞惊喜
5. v2 壳（/v2/）支持 `?card=<id>` 参数：只渲染指定卡（shell.js 小改）

## 2. 环境事实

- v2bridge 现状：app/web/v2bridge.js（30d4f5f），注入方式在 core main.py index() 响应注入
- shell.js 是 manifest 驱动（3.4KB），?card= 过滤在挂载循环处加一个判断即可
- 旧 Tab 激活函数 activate(name) 已验证可包（R4 的替换就在包装层做）

## 3. 改动白名单

```
app/web/v2bridge.js
app/web/v2_map.json（新）
app/web/shell.js（仅 ?card= 过滤，最小 diff）
app/core/main.py（仅注入参数内联 v2_map，若需）
docs/HANDOFF_20261024_R4_bridge.md
```

## 4. 自测

```powershell
cd OH-WorkSpace\projects\panel-v2
python -m pytest -q
python tools\cutover_gate.py
```

实测（ego/CDP）：默认全 old（九 Tab 行为不变）；把 watchlist 切 v2 后点击自选池显示卡视图；断卡（改错 card id）3s 回退旧 Tab 有提示；「v2 插件」总入口仍在。

## 5. 交付

回执 ≤300 字；明细落 HANDOFF；不 commit 不重启。
