// 复现「收起左栏后整屏空白」：追踪 .layout 的列宽与 .list-pane 的显示状态。
//
// 背景：app.js 切到「学情/覆盖/计划」时会给 .list-pane / .layout 设**内联样式**
// （display:none 与 gridTemplateColumns='1fr'）。内联样式优先级高于样式表，
// 而折叠用的是 body.list-collapsed 类。两者相遇时算出来的列宽可能与预期不符。
//
// 用法：node scripts/test-collapse-layout.mjs

import assert from 'node:assert/strict';

let pass = 0, fail = 0;
async function check(name, fn) {
  try { await fn(); pass++; console.log(`  ✓ ${name}`); }
  catch (e) { fail++; console.log(`  ✗ ${name}\n      ${e.message}`); }
}

/* ── 垫片：只实现这一条链路需要的部分 ─────────────────────────── */

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.style = { _v: {}, setProperty(k, v) { this._v[k] = v; } };
    this.dataset = {};
    this._text = '';
    this.value = '';
    this.classList = {
      _s: new Set(),
      add: (c) => this.classList._s.add(c),
      remove: (c) => this.classList._s.delete(c),
      contains: (c) => this.classList._s.has(c),
      toggle: (c, on) => (on ? this.classList._s.add(c) : this.classList._s.delete(c)),
    };
  }
  get textContent() { return this._text || this.children.map((c) => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  append(...n) { this.children.push(...n); }
  appendChild(n) { this.children.push(n); return n; }
  replaceChildren(...n) { this.children = n; this._text = ''; }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; }
  addEventListener(e, f) { (this.listeners[e] ||= []).push(f); }
  click() { (this.listeners.click || []).forEach((f) => f({ target: this, currentTarget: this })); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getContext() { return { setTransform() {}, translate() {}, rotate() {}, drawImage() {} }; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 320, height: 240 }; }
}

const byId = new Map();
function el(id, tag = 'div') { const e = new El(tag); e.attributes.id = id; byId.set(id, e); return e; }

const listPane = el('list-pane');
const detailPane = el('detail-pane');
const layout = el('layout');
const toggleBtn = el('btn-toggle-list', 'button');
const backBtn = el('btn-back', 'button');

layout.append(listPane, detailPane);

globalThis.document = {
  body: new El('body'),
  documentElement: new El('html'),
  getElementById: (id) => byId.get(id) ?? null,
  querySelector: (sel) => (sel === '.list-pane' ? listPane
    : sel === '.detail-pane' ? detailPane
    : sel === '.layout' ? layout : null),
  querySelectorAll: (sel) => (sel === '.tab' ? [] : []),
  createElement: (t) => new El(t),
  createTextNode: (t) => { const e = new El('#text'); e.textContent = t; return e; },
};
globalThis.Node = El;
globalThis.window = { addEventListener() {}, devicePixelRatio: 1, innerWidth: 1280 };
globalThis.localStorage = {
  _m: new Map(),
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
  setItem(k, v) { this._m.set(k, String(v)); },
  removeItem(k) { this._m.delete(k); },
};
globalThis.confirm = () => true;
globalThis.Image = class { set src(_v) {} };
globalThis.URL.createObjectURL = () => 'blob:x';
globalThis.URL.revokeObjectURL = () => {};

/* ── 装一个最小的 app.js 等价物：只保留布局相关的逻辑 ──────────
   直接把 app.js 跑起来成本太高（它要装配 6 个视图），
   因此这里复刻它的两步关键操作，断言的是**这两步合起来的结果**。 */

const body = document.body;
function setListCollapsed(c) {
  body.classList.toggle('list-collapsed', c);
}
function switchView(name) {
  const fullWidth = ['analysis', 'coverage', 'plan'].includes(name);
  listPane.style.display = fullWidth ? 'none' : '';
  layout.style.gridTemplateColumns = fullWidth ? '1fr' : '';
  body.classList.remove('show-detail');
}

/* 浏览器里生效的列宽 = 内联样式（若有）优先，其次是 body.list-collapsed 规则，
   再次是 .layout 的默认值。这里把这个级联顺序写出来 —— 这正是 bug 的所在。 */
function effectiveColumns() {
  if (layout.style.gridTemplateColumns) return layout.style.gridTemplateColumns;
  if (body.classList.contains('list-collapsed')) return '1fr';
  return 'var(--list-width) 1fr';
}
function listVisible() {
  if (listPane.style.display === 'none') return false;
  return !body.classList.contains('list-collapsed');
}

/* 关键：折叠后右侧题目区实际占多宽。
   规则是网格自动放置 —— display:none 的项不占轨道，
   剩余项按顺序填进第 1、2… 条轨道。
   因此若列定义是 `0 1fr` 而只剩详情一个项，它会落进**宽度 0** 的那条轨道，
   右侧区域被压成零宽（整屏空白）。
   这个函数就是用来锁住这个语义的。 */
function detailTrackWidth() {
  const cols = effectiveColumns().trim().split(/\s+(?![^(]*\))/);
  const items = [];
  if (listVisible()) items.push('list');
  items.push('detail');                       // detail 永远在 DOM 里
  const idx = items.indexOf('detail');
  return cols[idx] ?? cols[cols.length - 1];
}

const CSS_RAW = await (await import('node:fs/promises')).readFile(
  new URL('../web/css/app.css', import.meta.url), 'utf8',
);
// 断言前先剥掉注释：修复说明里会引用被删掉的那条选择器原文，
// 不剥离的话检查会命中自己的注释，变成永远失败。
const CSS = CSS_RAW.replace(/\/\*[\s\S]*?\*\//g, '');

console.log('左栏折叠后的布局计算：');

await check('折叠规则不能写成 `0 1fr`（详情会被放进零宽轨道）', () => {
  const m = CSS.match(/body\.list-collapsed \.layout \{([^}]*)\}/);
  assert.ok(m, '找不到折叠的 layout 规则');
  assert.ok(
    !/grid-template-columns:\s*0\s/.test(m[1]),
    '折叠列宽是 `0 …`：display:none 的列表不再占轨道，详情会被自动放进宽度 0 的'
    + '第 1 条轨道而压成零宽 —— 这正是「右侧试题区域没了」的原因',
  );
  assert.ok(
    /grid-template-columns:\s*1fr/.test(m[1]),
    `折叠时应只留一列，实际规则：${m[1].trim()}`,
  );
});

await check('单栏档不能再用 list-collapsed 去覆盖 show-detail', () => {
  const mobile = CSS.slice(CSS.indexOf('@media (max-width: 820px)'));
  assert.ok(
    !/body\.list-collapsed \.list-pane\s*\{[^}]*display:\s*flex/.test(mobile),
    '≤820px 里仍有 `body.list-collapsed .list-pane { display: flex }`：'
    + '它与 `body.show-detail .list-pane { display:none }` 优先级相同却更靠后，'
    + '折叠过左栏的用户在手机上点进题目会被列表盖住',
  );
});

await check('默认（未折叠）时是双栏，列表可见', () => {
  body.classList.remove('list-collapsed');
  switchView('list');
  assert.equal(effectiveColumns(), 'var(--list-width) 1fr');
  assert.equal(listVisible(), true);
  assert.equal(detailTrackWidth(), '1fr', '右栏应占满剩余宽度');
});

await check('折叠后左栏消失、右栏（题目区）占满整个宽度', () => {
  setListCollapsed(true);
  assert.equal(listVisible(), false, '折叠后列表仍然可见');
  assert.equal(
    detailTrackWidth(), '1fr',
    `折叠后详情所在轨道宽度是 ${detailTrackWidth()}，应为 1fr（整屏空白就是这里错了）`,
  );
});

await check('【回归】去过「学情」再回来折叠，右栏不能又被吃掉', () => {
  setListCollapsed(false);
  switchView('analysis');
  assert.equal(layout.style.gridTemplateColumns, '1fr', '学情页应当占满宽度');

  switchView('list');
  assert.equal(layout.style.gridTemplateColumns, '', '回到错题页应当清掉内联列宽');

  setListCollapsed(true);
  assert.equal(detailTrackWidth(), '1fr', `折叠后右栏被压缩成 ${detailTrackWidth()}`);
  assert.equal(listVisible(), false);
});

await check('【回归】折叠状态下切到「计划」再切回来，列表不会永久焊死', () => {
  setListCollapsed(true);
  switchView('plan');
  assert.equal(listVisible(), false, '计划页不该显示列表');

  switchView('list');
  assert.equal(listPane.style.display, '', '回到错题页时内联 display 没清掉');
  assert.equal(listVisible(), false, '折叠状态应当继续保持收起');

  setListCollapsed(false);
  assert.equal(listVisible(), true, '展开后列表没回来');
  assert.equal(detailTrackWidth(), '1fr');
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);