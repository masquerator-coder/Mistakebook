// 校验前端 ES module 的 import/export 一致性。
// 无构建步骤的原生 ES module 没有编译器兜底，写错一个导出名只会在浏览器里
// 静默变成运行时错误，所以用一个脚本静态检查一遍。
// 用法：node scripts/check-imports.mjs

import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(process.argv[2] ?? 'web/js');

const files = [];
(function walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p);
    else if (p.endsWith('.js') || p.endsWith('.mjs')) files.push(p);
  }
})(ROOT);

function exportedNames(src) {
  const names = new Set();
  // export const/function/let/class/async function
  for (const m of src.matchAll(/export\s+(?:async\s+)?(?:const|function|let|class)\s+([A-Za-z_$][\w$]*)/g)) {
    names.add(m[1]);
  }
  // export { a, b as c }
  for (const m of src.matchAll(/export\s*\{([^}]*)\}/g)) {
    for (const part of m[1].split(',')) {
      const t = part.trim();
      if (!t) continue;
      const as = t.split(/\s+as\s+/);
      names.add((as[1] ?? as[0]).trim());
    }
  }
  return names;
}

let problems = 0;

for (const f of files) {
  const src = fs.readFileSync(f, 'utf8');
  for (const m of src.matchAll(/import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g)) {
    const target = path.resolve(path.dirname(f), m[2]);
    if (!fs.existsSync(target)) {
      console.log(`MISSING FILE   ${path.relative(ROOT, f)} -> ${m[2]}`);
      problems++;
      continue;
    }
    const avail = exportedNames(fs.readFileSync(target, 'utf8'));
    for (const part of m[1].split(',')) {
      const t = part.trim();
      if (!t) continue;
      const name = t.split(/\s+as\s+/)[0].trim();
      if (!avail.has(name)) {
        console.log(`MISSING EXPORT ${name}  in ${path.relative(ROOT, target)}  (imported by ${path.relative(ROOT, f)})`);
        problems++;
      }
    }
  }
}

console.log(problems === 0
  ? `OK: ${files.length} 个模块的 import/export 全部一致`
  : `PROBLEMS: ${problems}`);
process.exit(problems === 0 ? 0 : 1);