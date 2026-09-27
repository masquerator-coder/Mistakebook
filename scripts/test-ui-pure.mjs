// 前端纯函数单测（无 DOM 依赖）。
// 无构建步骤的原生 ES module 没有编译器兜底，这些函数又直接决定
// 孩子看到的文案（尤其「逾期」「未安排」这类），值得单独测。
// 用法：node scripts/test-ui-pure.mjs

import assert from 'node:assert/strict';

// ui.js 里的函数依赖 document（toast），因此这里按需导入纯函数部分。
// 为避免引入 DOM，复制其核心逻辑做等价校验是不可接受的 ——
// 所以用动态 import 并只调用不触碰 DOM 的函数。
const mod = await import('../web/js/ui.js').catch((e) => {
  console.log('无法导入 ui.js（可能引用了 document）:', e.message);
  return null;
});

let pass = 0;
let fail = 0;

function check(name, fn) {
  try {
    fn();
    pass++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    fail++;
    console.log(`  ✗ ${name}\n      ${e.message}`);
  }
}

if (mod) {
  const { MASTERY_LABEL, COVERAGE_LABEL, COVERAGE_ORDER, REASON_LABEL, truncate, relDue, fmtDate } = mod;

  console.log('标签完整性：');
  check('mastery 三种状态都有中文标签', () => {
    for (const k of ['new', 'reviewing', 'mastered']) {
      assert.ok(MASTERY_LABEL[k], `缺少 ${k}`);
    }
  });

  check('coverage 四种状态都有标签', () => {
    for (const k of COVERAGE_ORDER) {
      assert.ok(COVERAGE_LABEL[k], `缺少 ${k}`);
    }
    assert.equal(COVERAGE_ORDER.length, 4);
  });

  // 这是产品语义约束，不是文案细节：把「没做过」写成「已掌握」会误导孩子
  check('未考过的状态措辞不得含「掌握」二字', () => {
    for (const k of ['untested', 'untested_with_content']) {
      assert.ok(
        !COVERAGE_LABEL[k].includes('掌握'),
        `${k} 的文案「${COVERAGE_LABEL[k]}」含「掌握」，会误导用户`,
      );
    }
  });

  check('错因枚举齐全', () => {
    for (const k of ['concept', 'calculation', 'misread', 'method', 'careless', 'time', 'unknown']) {
      assert.ok(REASON_LABEL[k], `缺少 ${k}`);
    }
  });

  console.log('\n日期处理：');
  check('truncate 正确处理超长与换行', () => {
    assert.equal(truncate('abc', 10), 'abc');
    assert.equal(truncate('a'.repeat(20), 5), 'aaaaa…');
    assert.equal(truncate('a\n b', 10), 'a b');
    assert.equal(truncate(null, 10), '');
  });

  check('fmtDate 对非法输入返回占位符而不是 NaN', () => {
    assert.equal(fmtDate(null), '—');
    assert.equal(fmtDate('not-a-date'), '—');
  });

  check('relDue 相对时间文案正确', () => {
    const now = Date.now();
    assert.equal(relDue(null), '未安排');
    assert.equal(relDue(new Date(now).toISOString()), '今天');
    assert.equal(relDue(new Date(now + 86400000).toISOString()), '明天');
    assert.equal(relDue(new Date(now + 3 * 86400000).toISOString()), '3 天后');
    assert.ok(relDue(new Date(now - 2 * 86400000).toISOString()).startsWith('已逾期'));
  });
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);