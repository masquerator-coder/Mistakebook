// 前端视图接线测试（最小 DOM 垫片，不引入任何依赖）。
//
// 为什么需要它：`capture.js` 的科目下拉曾经永远是空的 ——
// 构造时用闭包捕获了空数组 `subjects: []`，之后 `capture.subjects = ...`
// 赋值赋在返回对象上（该对象根本没有这个属性），是死代码。
// 纯函数测试和 import 一致性检查都发现不了这类**接线**错误，
// 而本机没有浏览器可用，因此这里用一个最小 DOM 垫片把真实逻辑跑起来。
//
// 用法：node scripts/test-views.mjs

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

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

/* ── 最小 DOM 垫片 ───────────────────────────────────────────── */

class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._text = '';
    this.value = '';
    this.disabled = false;
    this.className = '';
    this.classList = {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      contains(c) { return this._set.has(c); },
      toggle(c, on) { on ? this._set.add(c) : this._set.delete(c); },
    };
  }
  get textContent() {
    return this._text || this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  append(...nodes) { for (const n of nodes) this.children.push(n); }
  appendChild(n) { this.children.push(n); return n; }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  removeAttribute(k) { delete this.attributes[k]; if (k === 'value' && !this._valueSet) this.value = ''; }
  setAttribute(k, v) {
    this.attributes[k] = v;
    // 真实 DOM 里 <option value="x"> 的 .value 就是 "x"（ui.js 的 el() 走 setAttribute），
    // 垫片必须同样反映这个语义，否则测出来的选中值全是空串。
    if (k === 'value') this.value = String(v);
  }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  click() { (this.listeners.click || []).forEach((f) => f({ target: this })); }
  querySelector() { return null; }
  get options() { return this.children; }
  // crop.js（裁剪层）在构造时就拿 canvas 的 2d 上下文。垫片必须提供它，
  // 否则整个 capture 视图会因为「读不到 getContext」而构造失败，
  // 连带下面所有与裁剪无关的断言一起变红 —— 那种失败方向会误导排查。
  getContext() {
    return {
      setTransform() {}, translate() {}, rotate() {}, drawImage() {},
      getImageData() { return { data: new Uint8ClampedArray(4) }; },
    };
  }
  toBlob(cb) { cb(null); }
  getBoundingClientRect() {
    return { left: 0, top: 0, width: 320, height: 240 };
  }
  setPointerCapture() {}
  releasePointerCapture() {}
}

const registry = new Map();

class FakeText extends FakeEl {
  constructor(text) { super('#text'); this._text = String(text); }
}

function installDom(ids) {
  registry.clear();
  for (const id of ids) {
    registry.set(id, new FakeEl(id.startsWith('btn') || id.startsWith('cap-') ? 'button' : 'div'));
  }
  globalThis.document = {
    getElementById: (id) => registry.get(id) ?? null,
    createElement: (tag) => new FakeEl(tag),
    createTextNode: (t) => new FakeText(t),
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  // ui.js 的 el() 用 `c instanceof Node` 区分元素与纯文本
  globalThis.Node = FakeEl;
  globalThis.window = { addEventListener: () => {} };
}

/* ── 被测模块 ─────────────────────────────────────────────────── */

// 从源码里自动提取该视图需要的 DOM id。
// 手写清单会随源码改动而失配（我自己就漏了 capture-close 而白跑一轮），
// 这里直接扫源码，保证垫片与实际需求永远一致。
//
// 注意要扫**整条依赖链**：capture.js 现在会构造 crop.js，
// 而 crop.js 也 getElementById 了一批 id。只扫 capture.js 的话，
// 垫片里没有 crop-canvas，getContext 就会在 null 上被调用而整个视图构造失败。
function idsUsedBy(...relPaths) {
  const ids = new Set();
  for (const relPath of relPaths) {
    const src = readFileSync(new URL(relPath, import.meta.url), 'utf8');
    for (const m of src.matchAll(/getElementById\(\s*['"]([^'"]+)['"]\s*\)/g)) {
      ids.add(m[1]);
    }
  }
  return [...ids];
}

console.log('capture 视图的科目下拉：');

const { createCapture } = await import('../web/js/views/capture.js');

const CAPTURE_IDS = idsUsedBy('../web/js/views/capture.js', '../web/js/views/crop.js');

function makeCapture() {
  installDom(CAPTURE_IDS);
  return createCapture({
    getGrade: () => '七上',
    onSaved: async () => {},
  });
}

check('构造时不再要求传入 subjects（避免闭包捕获空数组）', () => {
  const cap = makeCapture();
  assert.equal(typeof cap.fillSubjects, 'function');
});

check('传入科目后下拉里真的有选项（回归：曾经永远是空的）', () => {
  const cap = makeCapture();
  const subjects = [
    { id: 1, name: '语文' },
    { id: 2, name: '数学' },
    { id: 3, name: '英语' },
  ];
  cap.fillSubjects(subjects);

  const sel = document.getElementById('cap-subject');
  assert.equal(sel.children.length, 3, `下拉里应有 3 个选项，实际 ${sel.children.length} 个`);
  const names = sel.children.map((c) => c.textContent);
  assert.deepEqual(names, ['语文', '数学', '英语']);
  const values = sel.children.map((c) => c.value);
  assert.deepEqual(values, ['1', '2', '3']);
  assert.equal(sel.disabled, false, '有科目时下拉不应被禁用');
});

check('科目为空时给出明确提示并禁用下拉，而不是静默留空', () => {
  const cap = makeCapture();
  cap.fillSubjects([]);

  const sel = document.getElementById('cap-subject');
  const text = sel.children.map((c) => c.textContent).join('');
  assert.ok(text.includes('没有科目'), `空状态提示不对：${text}`);
  assert.equal(sel.disabled, true, '无科目时应禁用下拉');
});

check('重复填充不会累积重复选项', () => {
  const cap = makeCapture();
  const subjects = [{ id: 1, name: '语文' }, { id: 2, name: '数学' }];
  cap.fillSubjects(subjects);
  cap.fillSubjects(subjects);
  const sel = document.getElementById('cap-subject');
  assert.equal(sel.children.length, 2, `重复填充后应仍是 2 个，实际 ${sel.children.length} 个`);
});

check('重新填充时保留用户已选科目', () => {
  const cap = makeCapture();
  cap.fillSubjects([{ id: 1, name: '语文' }, { id: 2, name: '数学' }]);
  const sel = document.getElementById('cap-subject');
  sel.value = '2';
  cap.fillSubjects([{ id: 1, name: '语文' }, { id: 2, name: '数学' }, { id: 3, name: '英语' }]);
  assert.equal(sel.value, '2', '用户选的科目被重置了，会让人以为选错了');
});

check('已选科目消失时不保留失效值', () => {
  const cap = makeCapture();
  cap.fillSubjects([{ id: 1, name: '语文' }, { id: 2, name: '数学' }]);
  const sel = document.getElementById('cap-subject');
  sel.value = '2';
  cap.fillSubjects([{ id: 1, name: '语文' }]);
  assert.notEqual(sel.value, '2', '保留了一个已不存在的科目 id，提交会 404');
});

/* ── 静态检查：防止闭包捕获空数组的写法回潮 ─────────────────────── */

console.log('\n静态接线检查：');

const appSrc = readFileSync(new URL('../web/js/app.js', import.meta.url), 'utf8');

check('app.js 不再向 createCapture 传空的 subjects 数组', () => {
  const m = appSrc.match(/createCapture\(\{[\s\S]*?\}\)/);
  assert.ok(m, '找不到 createCapture 调用');
  assert.ok(
    !/subjects\s*:\s*\[\s*\]/.test(m[0]),
    'createCapture 又传了 subjects: [] —— 这会让科目下拉永远是空的',
  );
});

check('app.js 不再对视图对象做“赋值补丁”式修复', () => {
  assert.ok(
    !/capture\.subjects\s*=/.test(appSrc),
    'app.js 里出现 capture.subjects = ... 这种死代码赋值',
  );
});

check('fillSubjects 调用点都传了参数', () => {
  const calls = [...appSrc.matchAll(/(\w+)\.fillSubjects\(([^)]*)\)/g)];
  assert.ok(calls.length > 0, '找不到 fillSubjects 调用');
  for (const c of calls) {
    assert.ok(c[2].trim().length > 0, `${c[1]}.fillSubjects() 没有传参数`);
  }
});

// 通用防线：视图构造时收到空数组/空对象，事后又想用赋值补上，
// 是纯函数测试和 import 检查都发现不了的接线错误。
check('没有视图在构造时收到空数组字面量', () => {
  const constructs = [...appSrc.matchAll(/create\w+\(\{([\s\S]*?)\n\}\)/g)];
  for (const c of constructs) {
    const body = c[1];
    assert.ok(
      !/:\s*\[\s*\]/.test(body),
      `某个视图构造参数里有空数组字面量，它会在闭包里永久为空：\n${body.trim()}`,
    );
    assert.ok(
      !/:\s*\{\s*\}/.test(body),
      `某个视图构造参数里有空对象字面量：\n${body.trim()}`,
    );
  }
});

// 通用防线：所有 .fillXxx(...) 形式的初始化调用都必须带参数
check('所有 fill* 调用都传了参数', () => {
  const calls = [...appSrc.matchAll(/\.(fill\w+)\(([^)]*)\)/g)];
  assert.ok(calls.length > 0, '找不到 fill* 调用');
  for (const c of calls) {
    assert.ok(c[2].trim().length > 0, `.${c[1]}() 没有传参数`);
  }
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);