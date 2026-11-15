// 8810 v2 逐 Tab 替换器（R4，docs/task_R4_bridge.md）
//
// 旧页零改动：core 在 index() 注入本脚本；本脚本读 /static/v2_map.json 决定每个 Tab 走旧 section
// 还是 v2 卡视图：
//   map[tabKey] = "old"         走旧界面（默认，出厂全 old）
//              | "v2"          v2 卡，卡 id 按约定 <tabKey>.main
//              | "v2:<cardId>" v2 卡，指定卡 id（如 v2:sector.board）
//
// 机制：包装 window.activate —— Tab 按钮 onclick、壳消息、hashchange 最终都落到它，
// 一个包装点覆盖全部入口。v2 的 Tab 不跑旧渲染（不拉旧 .json），改显示 /v2/?card=<id> iframe；
// 3 秒内 iframe 里没出现卡片宿主、或出现错误卡 → 回退旧界面并在该 Tab 上方留一行提示。
// 「v2 插件」总入口 Tab 保留，走同一包装（tabKey=__v2，无 card 参数 → 整个 v2 壳）。
//
// 视图用 div 不用 section：旧页全局 CSS 有 `section{display:none}`，且 activate() 只切 section 的
// .active 类，div 天然不受影响。v2 期间旧 section 用行内 display:none 压住，退出时还原。
(function () {
  "use strict";
  if (window.__v2Bridge) return;
  window.__v2Bridge = true;

  var MAP_URL = "/static/v2_map.json";
  var PLUGINS_URL = "/_plugins";
  var CARD_TIMEOUT_MS = 3000;
  var V2_TAB_KEY = "__v2";
  var V2_TAB_LABEL = "v2 插件";

  var map = Object.create(null);    // tabKey -> 映射原值
  var failed = Object.create(null); // tabKey -> {cardId, reason}：本页内已回退，锁定旧界面
  var cardIds = null;               // Set<string> | null（null = 清单未知，跳过存在性校验）
  var cardIdsReq = null;
  var frame = null;                 // {tabKey, cardId, box, iframe, timer, token}
  var hintNode = null;              // 当前那一行回退提示（同一时刻只留一条）
  var seq = 0;                      // 切换序号：异步校验回来时判断是否已被后来的切换取代
  var installed = false;
  var origActivate = null;

  function log() {
    try { console.log.apply(console, ["[v2bridge]"].concat([].slice.call(arguments))); } catch (e) { /* 忽略 */ }
  }

  // ---------------------------------------------------------------- v2 卡 → 父页：开 K 线/板块卡桥（R5）
  //
  // v2 卡在 iframe 里，卡内的可点元素（data-open-kline / data-open-amv）拿不到本页的
  // KLineApp / AmvCard，只能发消息。父页这一侧**只做转发**：消息 → 旧页既有入口，
  // 与面板自己的 data-open-* 委托（index.html:10924 openTarget）调用同一对函数，
  // 不复刻打开逻辑、不新建卡片组件：
  //   v2:openKline（来自 data-open-kline）→ KLineApp.open(code, name, list)
  //   v2:openAmv  （来自 data-open-amv）  → AmvCard.open(code, name)
  // 分成两个类型而不是按码猜：旧页的 data-open-kline 允许 .TI 指数码走 K 线，
  // 「.TI 就是板块」只在右键菜单用（_ctxIsIndexCode），拿来分派会判错。
  // 来源校验只认本脚本自己挂过的 iframe（ev.source === iframe.contentWindow），
  // 与 origin 无关——同一进程内 8810/8801 都可能嵌本页，不硬编码端口。
  var frameEls = [];      // 本脚本创建过的 iframe 元素（消息来源白名单；只增不删，判定时跳过已脱离 DOM 的）

  function isOwnFrame(win) {
    if (!win) return false;
    for (var i = 0; i < frameEls.length; i++) {
      var f = frameEls[i];
      if (!f || !f.isConnected) continue;
      var w = null;
      try { w = f.contentWindow; } catch (e) { w = null; }
      if (w && w === win) return true;
    }
    return false;
  }

  function openFromCard(d) {
    var code = (d && d.code != null) ? String(d.code).trim() : "";
    if (!code) return;
    var name = (d.name == null) ? "" : String(d.name);
    if (d.type === "v2:openAmv") {
      if (!window.AmvCard || typeof window.AmvCard.open !== "function") {
        log("父页无 AmvCard.open，忽略板块打开请求", code);
        return;
      }
      try { window.AmvCard.open(code, name); } catch (e) { log("AmvCard.open 失败", code, e); }
      return;
    }
    if (!window.KLineApp || typeof window.KLineApp.open !== "function") {
      log("父页无 KLineApp.open，忽略开 K 线请求", code);
      return;
    }
    var list = Array.isArray(d.list) ? d.list : null;     // 有 list 才能上下键切换，缺了照常开
    try { window.KLineApp.open(code, name, list); } catch (e) { log("KLineApp.open 失败", code, e); }
  }

  // ---------------------------------------------------------------- 主题/字号跟随（2026-10-25 修）
  //
  // 真缺陷：旧页切主题只改**本页** `<html data-theme>` 并写 localStorage `panel_theme`；
  // v2 壳原先只读 `shell_theme`，两个键名不同 → 卡里的主题永远停在默认值。
  // 两处都补：
  //   ① shell.js 改为同时读写两套键（panel_theme/shell_theme），重载后能继承；
  //   ② 本文件观察父页 `data-theme`/`data-fs` 变化，把值推给本脚本挂过的 iframe，
  //      **运行中切换即时生效**，不用刷新。
  //
  // 用 MutationObserver 而不是改旧页：旧仓 index.html 一字节不动（同 v2bridge 的既有纪律）。
  var THEME_MSG = "hana:theme";

  function pushTheme(f) {
    if (!f || !f.contentWindow) return;
    var theme = document.documentElement.getAttribute("data-theme");
    var fs = document.documentElement.getAttribute("data-fs");
    try {
      f.contentWindow.postMessage({ type: THEME_MSG, theme: theme, fs: fs }, "*");
    } catch (e) { /* 跨域帧：它自己读 localStorage */ }
  }

  function pushThemeToAll() {
    for (var i = 0; i < frameEls.length; i++) {
      var f = frameEls[i];
      if (f && f.isConnected) pushTheme(f);
    }
  }

  // 2026-10-26 旧端口下线：此处原有「#amvFrame 主题推送」整段（AMV_FRAME_ID / AMV_ORIGIN /
  //   AMV_THEME_VARS / pushThemeToAmvCard / watchAmvFrameLoad）已删。原因：#amvFrame 在 v2 全仓
  //   从未创建，而它服务的 8002 板块工作台已退役 —— 该段永远在 `if (!f) return` 处退出，纯死代码。

  function watchTheme() {
    if (typeof MutationObserver !== "function") return;
    var last = null;
    var obs = new MutationObserver(function () {
      var theme = document.documentElement.getAttribute("data-theme");
      var fs = document.documentElement.getAttribute("data-fs");
      var cur = theme + "|" + fs;
      if (cur === last) return;                   // 同一秒内多次属性写：只推一次
      last = cur;
      pushThemeToAll();
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-fs"] });
  }

  window.addEventListener("message", function (ev) {
    var d = ev.data || {};
    if (d.type !== "v2:openKline" && d.type !== "v2:openAmv") return;
    if (!isOwnFrame(ev.source)) return;                  // 非 v2 卡来源（含旧 8002 工作台消息）一律不接
    openFromCard(d);
  });

  // ---------------------------------------------------------------- 映射解析
  function resolveTarget(key) {
    if (!key) return null;
    if (key === V2_TAB_KEY) return { cardId: null };     // 总入口：整个 v2 壳
    if (failed[key]) return null;                        // 已回退：本页锁定旧界面
    var raw = map[key];
    if (typeof raw !== "string") return null;
    var v = raw.trim();
    if (!v || v === "old") return null;
    if (v === "v2") return { cardId: key + ".main" };
    if (v.indexOf("v2:") === 0) {
      var id = v.slice(3).trim();
      return { cardId: id || (key + ".main") };
    }
    log("未知映射值，按 old 处理", key, raw);             // 认不出的值一律不动旧界面
    return null;
  }

  function currentKey() {
    var btn = document.querySelector("#tabs button.tab.active");
    if (btn && btn.dataset && btn.dataset.key) return btn.dataset.key;
    return (location.hash || "").replace("#", "") || null;
  }

  // ---------------------------------------------------------------- 卡 id 存在性
  // 用 /_plugins 的卡片清单校验一次：映射写错卡 id 时立刻回退（不等 3 秒），提示里给出排查线索。
  // 清单拉不到就不校验，交给 iframe 自己试 —— 校验失败不能变成「功能不可用」。
  function ensureCardIds() {
    if (cardIds) return Promise.resolve(cardIds);
    if (cardIdsReq) return cardIdsReq;
    cardIdsReq = fetch(PLUGINS_URL, { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (payload) {
        if (!payload || !Array.isArray(payload.plugins)) return null;
        var ids = new Set();
        payload.plugins.forEach(function (p) {
          var cards = (p && p.frontend && p.frontend.cards) || [];
          cards.forEach(function (c) { if (c && c.id) ids.add(c.id); });
        });
        cardIds = ids;
        return ids;
      })
      .catch(function () { return null; });
    return cardIdsReq;
  }

  // ---------------------------------------------------------------- 回退提示（旧 section 的兄弟节点）
  // 不放进 panel 内部：旧渲染是 `panel.innerHTML = ...`，放里面第一次渲染就被冲掉。
  function clearHint() {
    if (hintNode && hintNode.parentNode) hintNode.parentNode.removeChild(hintNode);
    hintNode = null;
  }

  function showHint(tabKey, cardId, reason) {
    clearHint();
    var panel = document.getElementById("panel-" + tabKey);
    if (!panel || !panel.parentNode) return;
    var hint = document.createElement("div");
    hint.className = "v2-bridge-hint";
    hint.setAttribute("data-tab-key", tabKey);
    hint.style.cssText = "margin:0 0 var(--d-gap,10px);padding:6px 12px;"
      + "border-radius:var(--radius-md,8px);font-size:0.8125rem;"
      + "border:1px solid color-mix(in srgb,var(--warn,#d80) 35%,transparent);"
      + "background:var(--warn-bg,var(--card2));color:var(--warn-ink,var(--fg))";
    hint.textContent = "v2 卡" + (cardId ? "（" + cardId + "）" : "") + "未就绪，已回退旧界面："
      + reason + "（改完 v2_map.json 刷新页面重试）";
    panel.parentNode.insertBefore(hint, panel);
    hintNode = hint;
  }

  // ---------------------------------------------------------------- v2 视图
  function destroyFrame() {
    if (!frame) return;
    var tabKey = frame.tabKey;
    if (frame.timer) clearInterval(frame.timer);
    if (frame.box && frame.box.parentNode) frame.box.parentNode.removeChild(frame.box);
    frame = null;
    var host = document.getElementById("panel-" + tabKey);
    if (host) host.style.display = "";                   // 还原被压住的旧 section
  }

  function mountFrame(tabKey, cardId) {
    destroyFrame();
    clearHint();
    var box = document.createElement("div");
    box.className = "v2-frame";
    box.setAttribute("data-tab-key", tabKey);
    box.style.cssText = "display:block;margin:0;padding:0";

    var iframe = document.createElement("iframe");
    /* 2026-10-26 主人："为什么8810页面底下还会有一大片位置的留白呢"。
       根因：高度写死 calc(100vh - 90px)。那 90px 是给顶部导航让的位，但 8810 已改成
       无顶栏（body 只有 12px 上下 padding），于是 90px 里多出的 66px 全变成底部空白
       （1600×1004 视口实测：body 高 938，iframe 底边 926，下方空 78px）。
       现改为 "填满 body 可用高度"：100dvh 减 body 上下 padding（各 12px），
       不再猜顶栏高度；dvh 取浏览器实际可视高度，地址栏伸缩时也不会溢出。
       带兜底：老浏览器不认 dvh 时用 100vh。 */
    iframe.style.cssText = "display:block;width:100%;border:0;"
      + "height:calc(100vh - 24px);"                        // 兜底：不支持 dvh 时
      + "height:calc(100dvh - 24px);"                       // 12px 上 + 12px 下 body padding
      + "background:var(--bg,#0b0e16)";
    iframe.setAttribute("title", cardId ? ("v2 卡 " + cardId) : "v2 壳");
    iframe.src = cardId ? ("/v2/?card=" + encodeURIComponent(cardId)) : "/v2/";
    box.appendChild(iframe);

    var host = document.getElementById("panel-" + tabKey);
    if (host && host.parentNode) {
      host.parentNode.insertBefore(box, host.nextSibling);
      host.style.display = "none";                       // activate() 仍会给它挂 .active，必须压住
    } else {
      document.body.appendChild(box);
    }

    frameEls.push(iframe);                               // 登记来源白名单（开卡桥校验用）
    frame = { tabKey: tabKey, cardId: cardId, box: box, iframe: iframe, timer: null, token: ++seq };
    var mine = frame;

    // 主题：iframe 首帧读完 DOM 后补推一次。不靠 load 事件——卡片是自己挂载的，
    // 壳的 load 早于卡渲染，推早了会被后续 applyTheme 覆盖；这里等核壳层就绪后推。
    iframe.addEventListener("load", function () {
      if (frame !== mine) return;
      pushTheme(iframe);
      setTimeout(function () { if (frame === mine) pushTheme(iframe); }, 600);
    });

    iframe.addEventListener("error", function () {
      if (frame !== mine) return;                        // 已切走的过期回调
      fallback(tabKey, cardId, "卡视图加载出错");
    });

    // 总入口（整壳）不设看门狗：它没有可回退的旧界面，失败时由 v2 壳自己提示。
    if (!cardId) return;
    // 看门狗：每 500ms 查一次，看到卡片宿主就收工；看到错误卡立即回退；满 3 秒仍无宿主 → 回退。
    // 早退而不是硬等 3 秒，是为让「卡不存在/渲染报错」快速暴露（上限仍是 3 秒）。
    var deadline = Date.now() + CARD_TIMEOUT_MS;
    mine.timer = setInterval(function () {
      if (frame !== mine) { clearInterval(mine.timer); return; }
      var doc = null;
      try { doc = mine.iframe.contentDocument; } catch (err) { doc = null; }
      if (doc && doc.querySelector(".card-host")) { clearInterval(mine.timer); mine.timer = null; return; }
      if (doc && doc.querySelector(".card.is-error")) {
        clearInterval(mine.timer); mine.timer = null;
        fallback(tabKey, cardId, "卡渲染报错");
        return;
      }
      if (Date.now() >= deadline) {
        clearInterval(mine.timer); mine.timer = null;
        fallback(tabKey, cardId, doc ? "3 秒内未渲染出卡片" : "卡视图不可访问");
      }
    }, 500);
  }

  function showCard(tabKey, cardId) {
    // 轮询 tick 会反复走到这里：同一 Tab + 同一张卡直接返回，不重载 iframe
    if (frame && frame.tabKey === tabKey && frame.cardId === cardId) return;
    var token = ++seq;
    destroyFrame();
    if (!cardId) { mountFrame(tabKey, null); return; }
    ensureCardIds().then(function (ids) {
      if (token !== seq) return;                          // 期间已切到别的 Tab
      if (ids && !ids.has(cardId)) {
        fallback(tabKey, cardId, "v2 卡不存在：" + cardId);
        return;
      }
      mountFrame(tabKey, cardId);
    });
  }

  // 回退：撤 iframe → 旧 Tab 上方留一行提示 → 跑原 loadTab 把旧界面渲染出来
  function fallback(tabKey, cardId, reason) {
    log("回退旧界面", tabKey, reason);
    destroyFrame();
    if (tabKey === V2_TAB_KEY) return;                    // 总入口没有旧界面可退
    failed[tabKey] = { cardId: cardId, reason: reason };  // 锁定：3s 轮询不再挂回来
    var panel = document.getElementById("panel-" + tabKey);
    if (panel) panel.style.display = "";
    showHint(tabKey, cardId, reason);
    if (origActivate) {
      // 回退：跑旧 activate 把旧界面渲染回来（它内部会裸调 loadTab）
      try { origActivate.call(window, tabKey); } catch (err) { log("旧界面回退渲染失败", tabKey, err); }
    }
  }

  // ---------------------------------------------------------------- 安装
  //
  // 2026-10-25 修（真缺陷）：原来包装的是 `window.loadTab`，实际**从未生效**。
  //   旧页 `activate()` 里是**裸调用** `loadTab(key)`（`legacy/index.html:8401`），
  //   裸调用走词法作用域直接命中 `<script>` 顶层那个函数声明，根本不经过 `window.loadTab`。
  //   全页 20+ 处都是裸调用，逐个改成 `window.loadTab` 不现实，所以改包 `window.activate`——
  //   它是 Tab 按钮 `onclick="activate('x')"` 的全局入口，**所有** Tab 切换都必经它。
  //   症状（修前）：v2_map 全改 v2 后，所有 Tab 仍渲染旧界面且无 iframe（实测 9/9）。
  // 2026-10-26：v2 已接管的 Tab，把旧面板留下的重量级 iframe 拆掉。
  // 为何不能只靠旧页 renderSector 里的开关：首屏直接开 #sector 时，旧页 boot 早于本脚本
  // 安装（waiter 300ms + fetch），那一刻 __v2OwnsTab 还未定义 → 旧面板已经把 8002
  // 的 iframe 建出来了。之后 dataset.ready='1' 让它不再重建，但首次那次已经发出去了。
  // 这里在切卡后主动拆：移除节点会中止未完成的请求，确定性不依赖时序。
  function dropLegacyPanel(key) {
    var map = { sector: ["panel-sector", "sectorFrame"] };
    var ids = map[key];
    if (!ids) return;
    var frame = document.getElementById(ids[1]);
    if (frame) {
      try { frame.removeAttribute("src"); } catch (err) { /* 忽略 */ }
      try { frame.remove(); } catch (err) { /* 忽略 */ }
    }
    var panel = document.getElementById(ids[0]);
    if (panel) {
      panel.innerHTML = "";
      panel.dataset.ready = "1";     // 旧页 renderSector 见 ready=1 不再重建
    }
  }

  function install() {
    if (installed) return true;
    if (typeof window.activate !== "function") return false;
    installed = true;
    origActivate = window.activate;
    window.activate = function (key) {
      var target = resolveTarget(key);
      // 两种情况都先跑旧 activate：它负责 `active` 变量、`.tab.active` 高亮、
      // 清缓存与重设定时器。跳过它会让旧页状态错乱。
      var ret = origActivate.apply(this, arguments);
      if (target) {
        showCard(key, target.cardId);       // 命中 v2：再盖成卡视图
        dropLegacyPanel(key);               // 旧面板的 8002 iframe 拆掉（否则 8002 关不掉）
        return ret;
      }
      destroyFrame();                       // 未命中（old / 已回退）：确保不留 v2 框
      clearHint();
      if (failed[key]) showHint(key, failed[key].cardId, failed[key].reason);
      return ret;
    };

    var tabs = document.querySelector("#tabs");
    if (tabs && !tabs.querySelector('[data-key="' + V2_TAB_KEY + '"]')) {
      var btn = document.createElement("button");
      btn.className = "tab rail-btn";
      btn.setAttribute("data-key", V2_TAB_KEY);
      btn.setAttribute("aria-label", V2_TAB_LABEL);
      btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
        + 'stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/>'
        + '<path d="M9 3v18"/></svg><span class="rail-tip">' + V2_TAB_LABEL + "</span>";
      btn.onclick = function () { window.activate(V2_TAB_KEY); };
      tabs.appendChild(btn);
    }
    return true;
  }

  function loadMap() {
    return fetch(MAP_URL, { cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (!data || typeof data !== "object") return;
        Object.keys(data).forEach(function (k) {
          if (k.charAt(0) === "_") return;                // "_doc" 之类说明字段
          map[k] = data[k];
        });
      })
      .catch(function (err) {
        // 映射缺失/损坏 = 全 old（出厂默认），只留总入口 Tab，不阻断旧页
        log("读取 v2 映射失败，全部按 old 处理", err && err.message);
      });
  }

  // 等旧页 Tab 栏与 loadTab 就绪（注入点在旧页 init 之后，通常第一轮就绪）
  var tries = 0;
  var waiter = setInterval(function () {
    tries += 1;
    var ready = document.querySelector("#tabs") && typeof window.activate === "function";
    if (!ready) {
      if (tries > 100) clearInterval(waiter);             // ~30s 仍未就绪：放弃，旧页照常用
      return;
    }
    clearInterval(waiter);
    loadMap().then(function () {
      if (!install()) return;
      // 2026-10-26：告知旧页「哪些 Tab 已由 v2 卡接管」。
      // 为何需要：activate 包装是「先跑旧 activate（含旧面板渲染）→ 再盖 v2 卡」，
      // 所以板块 Tab 即使早已命中 v2:sector.board，旧面板仍会去探 / 拉 8002 iframe，
      // 成了 8002 关不掉的唯一原因（全 Tab 审计：8/9 只走 8810，只有板块带 8002 请求）。
      // 旧页侧据此跳过重量级渲染；v2_map 改回 old 时自动恢复，不写死。
      window.__v2OwnsTab = function (k) {
        try { return !!resolveTarget(k); } catch (err) { return false; }
      };
      watchTheme();                                     // 父页切主题 → 推给已挂的 v2 帧
      // 脚本注入晚于旧页 bootTab：首屏可能已渲染成旧界面，当前 Tab 命中 v2 就补切一次
      var key = currentKey();
      if (key && resolveTarget(key) && typeof window.activate === "function") {
        try { window.activate(key); } catch (err) { log("补切 v2 视图失败", key, err); }
      }
    });
  }, 300);
})();
