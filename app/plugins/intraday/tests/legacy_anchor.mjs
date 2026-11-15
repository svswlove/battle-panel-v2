/**
 * 旧页源码切片器（四个对齐脚本共用）：**按可辨锚点取段，不按行号**。
 *
 * 2026-10-30：旧页 engine/web/index.html 相对对齐脚本编写时整体下移了 2 行，四个运行器原本
 * 按硬编码行号切片，切在表达式中间 → node 抛 SyntaxError → pytest 在比对之前就判红
 * （这 9 条断言从未真正比过内容）。docs/DONE_20261026_old_ports_retire.md:73 已记过
 * 「偏移量在文件内不一致，不能靠单一 delta 修」，故改成锚点定位：旧页再加行也不会切错。
 *
 * 锚点规则（都从文件顶部找第一条命中）：
 *   line(re)          单行锚点（表达式/声明）
 *   each([re…])       按名字取若干条固定行，保持给定顺序（工具函数四行用这个）
 *   block(re)         锚点行 → 该块的顶格收尾行（`}` / `};`），整块原样返回
 *   span(a, b)        起始锚点行 → 结束锚点行（含两端）
 *   upto(a, b)        起始锚点行 → b 所在块的顶格收尾行（含两端）
 *   stmt(re)          锚点行 → 语句收尾行（行尾是 "`;" 的跨行模板串）
 *
 * 命中不到锚点直接抛错：宁可红在「锚点没命中」，也不要静默切一段半截代码。
 */
import fs from 'node:fs';

const TOP_CLOSE = /^\}[;,)]?\s*(?:\/\/.*)?$/;   // 顶格收尾行：} / }; / },
const STMT_END = /`;\s*$/;                       // 跨行模板串的语句收尾

export function makeSeg(file) {
  const lines = fs.readFileSync(file, 'utf8').split(/\r?\n/);

  const index = (re, from = 0) => {
    for (let i = from; i < lines.length; i += 1) {
      if (re.test(lines[i])) return i;
    }
    throw new Error('旧页锚点没命中：' + re + '（' + file + '）');
  };
  const slice = (a, b) => lines.slice(a, b + 1).join('\n');

  const blockEnd = (start) => {
    const end = index(TOP_CLOSE, start + 1);
    if (end <= start) throw new Error('旧页锚点块异常：' + lines[start]);
    return end;
  };

  return {
    file,
    lines,
    index,
    slice,
    line: (re) => lines[index(re)],
    each: (res) => res.map((re) => lines[index(re)]).join('\n'),
    block: (re) => {
      const start = index(re);
      return slice(start, blockEnd(start));
    },
    span: (a, b) => {
      const start = index(a);
      return slice(start, index(b, start));
    },
    upto: (a, b) => {
      const start = index(a);
      return slice(start, blockEnd(index(b, start)));
    },
    stmt: (re) => {
      const start = index(re);
      let end = start;
      while (end < lines.length && !STMT_END.test(lines[end])) {
        if (TOP_CLOSE.test(lines[end]) && end > start) {
          throw new Error('模板语句没在锚点块内收尾：' + re);
        }
        end += 1;
      }
      if (end >= lines.length) throw new Error('模板语句没找到收尾 "`;"：' + re);
      return slice(start, end);
    },
  };
}

/** 旧页工具函数四行：esc / num / pct / cls（对齐脚本一律用这个，不写行号） */
export const UTIL_LINES = [/^const esc = /, /^const num = /, /^const pct = /, /^const cls = /];
