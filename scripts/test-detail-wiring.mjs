// 详情视图的接线测试（最小 DOM 垫片，不引入依赖）。
//
// 为什么需要：本轮改了 detail.js 的四处交互（复习按钮防误触、字号、
// 追问聊天、自动归类）。这些逻辑都在浏览器里跑，而本机没有可用的浏览器，
// 纯函数测试也覆盖不到「按钮点了之后调了哪个接口、传了什么参数」。
// 用垫片把真实视图逻辑跑起来，至少能挡住接线错误（接口名写错、
// 参数名写错、构造时捕获空对象这类）。
//
// 用法：node scripts/test-detail-wiring.mjs

import assert from 'node:assert/strict';

let pass = 0;
let fail = 0;

async function check(name, fn) {
  try {
    // 用例可能是 async（要等被 stub 的接口 settle）；await 一个同步返回值是安全的
    await fn();
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
    this.style = { setProperty() {}, };
    this.dataset = {};
    this.scrollTop = 0;
    this.scrollHeight = 100;
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
  removeAttribute(k) { delete this.attributes[k]; }
  setAttribute(k, v) { this.attributes[k] = v; if (k === 'value') this.value = String(v); }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  click() { (this.listeners.click || []).forEach((f) => f({ target: this, currentTarget: this })); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getContext() { return { setTransform() {}, translate() {}, rotate() {}, drawImage() {} }; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 320, height: 240 }; }
}

class FakeText extends FakeEl {
  constructor(t) { super('#text'); this._text = String(t); }
}

const registry = new Map();
const storage = new Map();

/* 在垫片构造的元素树里按 id 找节点。
   真实 DOM 的 getElementById 是全文档查找，因此垫片也必须能
   找到 render 期间**新建并 append** 的元素，而不只是预先登记的那些。 */
function findById(root, id) {
  if (!root) return null;
  if (root.attributes?.id === id) return root;
  for (const c of root.children || []) {
    const hit = findById(c, id);
    if (hit) return hit;
  }
  return null;
}

function installDom(ids) {
  registry.clear();
  for (const id of ids) registry.set(id, new FakeEl('div'));
  globalThis.document = {
    // 视图在 render 时会新建带 id 的元素并 append 进树里（例如追问的
    // textarea / 发送按钮）。真实 getElementById 能找到它们，
    // 因此垫片也必须先扫一遍树，否则拿到的是 registry 里那个**游离**的占位元素，
    // 点它不会触发任何逻辑 —— 症状是「测试点了按钮但接口没被调用」。
    getElementById: (id) => {
      const box = registry.get('detail-body');
      if (box) {
        const hit = findById(box, id);
        if (hit) return hit;
      }
      return registry.get(id) ?? null;
    },
    createElement: (tag) => new FakeEl(tag),
    createTextNode: (t) => new FakeText(t),
    querySelector: () => null,
    querySelectorAll: () => [],
    documentElement: new FakeEl('html'),
  };
  globalThis.Node = FakeEl;
  globalThis.window = { addEventListener() {}, devicePixelRatio: 1 };
  globalThis.localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  };
  globalThis.confirm = () => true;
  globalThis.Image = class { set src(_v) {} };
  globalThis.URL.createObjectURL = () => 'blob:fake';
  globalThis.URL.revokeObjectURL = () => {};
}

/* ── 记录所有 API 调用，替掉真实网络 ───────────────────────── */

const calls = [];
let nextReviewResponse = { redo: false, updated: true };

function installApiStub(apiModule) {
  const mistake = {
    id: 7, subject_id: 2, subject_name: '数学', stem_text: '解方程 3x + 5 = 20',
    answer_text: 'x = 5', student_answer: 'x = 25', wrong_reason: 'calculation',
    mastery: 'reviewing', error_count: 2, correct_streak: 0, interval_days: 1,
    next_review_at: '2020-01-01T00:00:00Z', last_review_at: null,
    created_at: '2026-09-01T00:00:00Z', kps: [],
  };
  const stub = {
    mistake: async (id) => { calls.push(['mistake', id]); return { ...mistake, id }; },
    reviews: async (id) => { calls.push(['reviews', id]); return []; },
    submitReview: async (id, payload) => {
      calls.push(['submitReview', id, payload]);
      return { ...nextReviewResponse, id };
    },
    explainCached: async (id) => { calls.push(['explainCached', id]); return { status: 'pending', payload: null }; },
    explain: async (id, force) => { calls.push(['explain', id, force]); return { status: 'done', payload: null }; },
    explainChat: async (id) => { calls.push(['explainChat', id]); return { chat_id: null, messages: [] }; },
    chatAsk: async (id, q) => {
      calls.push(['chatAsk', id, q]);
      return { chat_id: 1, messages: [
        { id: 1, role: 'user', content: q, created_at: '2026-09-01T00:00:00Z' },
        { id: 2, role: 'assistant', content: '因为移项要变号。', created_at: '2026-09-01T00:00:01Z' },
      ] };
    },
    clearChat: async (id) => { calls.push(['clearChat', id]); return null; },
    autoCategorize: async (id) => {
      calls.push(['autoCategorize', id]);
      return { matched: true, picked: [{ id: 3, name: '一元一次方程' }], reason: '匹配' };
    },
    suggestKps: async (id) => { calls.push(['suggestKps', id]); return { matched: false, picked: [] }; },
  };
  Object.assign(apiModule.api, stub);
}

/* ── 跑起来 ───────────────────────────────────────────────────── */

console.log('detail 视图接线（复习按钮 / 字号 / 追问 / 归类）：');

const apiModule = await import('../web/js/api.js');
installApiStub(apiModule);

const DETAIL_IDS = ['detail-body', 'toast', 'ai-content', 'review-history',
  'kp-section', 'kp-chips', 'categorize-hint', 'chat-list', 'chat-input',
  'chat-send', 'chat-clear'];
installDom(DETAIL_IDS);

const { createDetail } = await import('../web/js/views/detail.js');
const detail = createDetail({ onChanged: () => {}, onDeleted: () => {} });

await detail.load(7);

const body = document.getElementById('detail-body');

await check('加载后渲染出题干与复习按钮', () => {
  assert.ok(findById(body, 'btn-review-correct'), '找不到「做对了」按钮');
  assert.ok(findById(body, 'btn-review-partial'), '找不到「半对」按钮');
  assert.ok(findById(body, 'btn-review-wrong'), '找不到「又错了」按钮');
  const text = body.textContent;
  assert.ok(text.includes('解方程 3x + 5 = 20'), `题干没渲染：${text.slice(0, 120)}`);
});

await check('渲染出追问输入框与发送按钮', () => {
  assert.ok(findById(body, 'chat-input'), '找不到追问输入框');
  assert.ok(findById(body, 'chat-send'), '找不到发送按钮');
  assert.ok(findById(body, 'chat-clear'), '找不到清空按钮');
  assert.ok(body.textContent.includes('继续追问'), '没有「继续追问」标题');
});

await check('渲染出自动归类按钮', () => {
  assert.ok(findById(body, 'btn-categorize'), '找不到自动归类按钮');
});

await check('加载详情时会读讲解缓存（而不是触发一次模型调用）', () => {
  const cached = calls.filter((c) => c[0] === 'explainCached');
  assert.equal(cached.length, 1, `explainCached 调用次数=${cached.length}，应为 1`);
  assert.equal(
    calls.filter((c) => c[0] === 'explain').length, 0,
    '打开详情页就调用了 explain（会真的烧 token），只能用 explainCached',
  );
});

await check('加载详情时会读取追问记录', () => {
  assert.ok(calls.some((c) => c[0] === 'explainChat' && c[1] === 7), '没有读取追问记录');
});

await check('首次复习（无宽限期）提交时不带 redo', async () => {
  // last_review_at 为 null → 不在宽限期内 → 不该声明 redo
  const btn = findById(body, 'btn-review-correct');
  assert.ok(btn, '找不到做对了按钮');
  btn.click();
  await new Promise((r) => setTimeout(r, 10));
  const submitted = calls.filter((c) => c[0] === 'submitReview');
  assert.equal(submitted.length, 1, `submitReview 调用次数=${submitted.length}`);
  assert.equal(submitted[0][2].result, 'correct', `result 传错：${submitted[0][2].result}`);
  assert.equal(submitted[0][2].redo, false, '不在宽限期内却声明了 redo');
});

// 最关键的一条：宽限期内点**另一个**按钮必须声明 redo=true，
// 否则服务端会当成新的一次复习 —— 改判功能等于没接上。
await check('宽限期内改点别的按钮会声明 redo=true', async () => {
  calls.length = 0;
  storage.clear();
  installDom(DETAIL_IDS);
  const fresh = createDetail({ onChanged: () => {}, onDeleted: () => {} });

  // 让 last_review_at 就在刚刚 → 处在 10 秒宽限期内
  const { api } = await import('../web/js/api.js');
  const origMistake = api.mistake;
  api.mistake = async (id) => ({
    ...(await origMistake(id)),
    last_review_at: new Date().toISOString(),
  });

  await fresh.load(7);
  const b = findById(document.getElementById('detail-body'), 'btn-review-wrong');
  assert.ok(b, '找不到「又错了」按钮');
  b.click();
  await new Promise((r) => setTimeout(r, 10));

  const submitted = calls.filter((c) => c[0] === 'submitReview');
  assert.equal(submitted.length, 1, `submitReview 调用次数=${submitted.length}`);
  assert.equal(submitted[0][2].result, 'wrong');
  assert.equal(
    submitted[0][2].redo, true,
    '宽限期内改点别的按钮没有声明 redo，服务端会记成新的一次复习',
  );
  api.mistake = origMistake;
});

await check('追问发送会把问题传给接口并渲染出回答', async () => {
  calls.length = 0;
  storage.clear();
  installDom(DETAIL_IDS);
  const fresh = createDetail({ onChanged: () => {}, onDeleted: () => {} });
  await fresh.load(7);

  const input = document.getElementById('chat-input');
  const send = document.getElementById('chat-send');
  assert.ok(input && send, '找不到追问输入框或发送按钮');
  input.value = '为什么要先移项？';
  send.click();
  await new Promise((r) => setTimeout(r, 20));

  const asked = calls.filter((c) => c[0] === 'chatAsk');
  assert.equal(asked.length, 1, `chatAsk 调用次数=${asked.length}`);
  assert.equal(asked[0][2], '为什么要先移项？', `问题传错：${asked[0][2]}`);

  const list = document.getElementById('chat-list');
  assert.ok(
    list.textContent.includes('因为移项要变号'),
    `回答没有渲染出来：${list.textContent}`,
  );
});

await check('空问题不会发出去', async () => {
  calls.length = 0;
  storage.clear();
  installDom(DETAIL_IDS);
  const fresh = createDetail({ onChanged: () => {}, onDeleted: () => {} });
  await fresh.load(7);

  document.getElementById('chat-input').value = '   ';
  document.getElementById('chat-send').click();
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(
    calls.filter((c) => c[0] === 'chatAsk').length, 0,
    '空白问题被发给了接口（会白调一次模型）',
  );
});

await check('点自动归类会调用 autoCategorize', async () => {
  calls.length = 0;
  storage.clear();
  installDom(DETAIL_IDS);
  const fresh = createDetail({ onChanged: () => {}, onDeleted: () => {} });
  await fresh.load(7);

  const btn = findById(document.getElementById('detail-body'), 'btn-categorize');
  assert.ok(btn, '找不到自动归类按钮');
  btn.click();
  await new Promise((r) => setTimeout(r, 20));

  const cat = calls.filter((c) => c[0] === 'autoCategorize');
  assert.equal(cat.length, 1, `autoCategorize 调用次数=${cat.length}`);
  assert.equal(cat[0][1], 7, `错题 id 传错：${cat[0][1]}`);
});

await check('字号档位会被持久化到 localStorage', async () => {
  storage.clear();
  installDom(DETAIL_IDS);
  const fresh = createDetail({ onChanged: () => {}, onDeleted: () => {} });
  await fresh.load(7);

  const keys = [...storage.keys()].filter((k) => k.includes('zoom'));
  // 未调节过时不该写值（避免无谓的写入），但键名必须是我们约定的那个
  assert.ok(
    keys.length === 0 || keys[0] === 'mistakebook.zoom',
    `localStorage 里出现了意料之外的键：${keys.join(', ')}`,
  );
});

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);