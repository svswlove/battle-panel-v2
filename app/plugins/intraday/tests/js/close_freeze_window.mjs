// 盘中卡 · 自动取数窗口口径单测载体（给 tests/test_intraday.py::test_close_freeze_window 调用）
//
// 为什么要单独一个文件：口径函数在 frontend.js 里（模块顶层零 DOM），但把多行代码塞进
// `node -e` 在 Windows 上容易被 shell 吃掉引号；落成文件后 node 直接跑，退出码干净。
//
// 输出最后一行 JSON，供 pytest 解析。
import * as M from '../../frontend.js';

const D = (y, mo, d, h, mi) => new Date(y, mo - 1, d, h, mi);

console.log(JSON.stringify({
  // 2026-10-28 周三
  pre: M.inAutoRefreshWindow(D(2026, 10, 28, 9, 0)),
  open: M.inAutoRefreshWindow(D(2026, 10, 28, 10, 0)),
  close1510: M.inAutoRefreshWindow(D(2026, 10, 28, 15, 10)),
  tail: M.inAutoRefreshWindow(D(2026, 10, 28, 16, 0)),
  after: M.inAutoRefreshWindow(D(2026, 10, 28, 16, 30)),
  late: M.inAutoRefreshWindow(D(2026, 10, 28, 21, 30)),
  // 2026-10-31 周六
  weekend: M.inAutoRefreshWindow(D(2026, 10, 31, 10, 0)),
  frozenAfter: M.isCloseFrozen(D(2026, 10, 28, 17, 0)),
  frozenOpen: M.isCloseFrozen(D(2026, 10, 28, 10, 0)),
}));
