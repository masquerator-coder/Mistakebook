/* 右栏：错题详情、复习操作、AI 讲解、追问、归类

两处交互设计需要解释，否则很容易被后来的改动破坏：

1. **复习按钮（P2）**：每点一次就新增一条历史是不行的 —— 误触、看错按钮、
   手抖都会凭空多出记录，`error_count` 与正确率统计随之失真，而家长完全看不出
   数字为什么不对。现在由**服务端**判断：最新一条记录在宽限期内且本次结果不同，
   就算「改判」（删掉那条、撤回 SRS 推进、按新结果重算）；点同一个按钮则完全
   不动。前端不自己算这个判断：客户端时钟与服务端不一致时会误判，
   而且刷新页面后仍要能正确显示「还能改判几秒」。

2. **字号（P5）**：题目原文与讲解分开控制。孩子看题干需要大字，
   而讲解是成段的文字，字太大反而要来回滚动，两者需求不同。
*/

import { api } from '../api.js';
import {
  el, clear, loading, empty, toast, describeError,
  MASTERY_LABEL, REASON_LABEL, REVIEW_LABEL, REVIEW_LABEL_SHORT,
  fmtDateTime, relDue, FONT_SIZES,
} from '../ui.js';

/* 正文卡片（题干、讲解正文）的局部字号。存 localStorage 而非全局变量：
   切题、切标签页都会重建 DOM，全局变量存不住。 */
const ZOOM_KEY = 'mistakebook.zoom';
const ZOOM_LEVELS = [0, 1, 2, 3]; // 对应 --zoom-0 … --zoom-3

function loadZoom() {
  try {
    const v = localStorage.getItem(ZOOM_KEY) || '{}';
    const parsed = JSON.parse(v);
    return {
      stem: ZOOM_LEVELS.includes(parsed.stem) ? parsed.stem : 0,
      explain: ZOOM_LEVELS.includes(parsed.explain) ? parsed.explain : 0,
    };
  } catch {
    return { stem: 0, explain: 0 };
  }
}

function saveZoom(z) {
  try { localStorage.setItem(ZOOM_KEY, JSON.stringify(z)); } catch { /* 忽略 */ }
}

export function createDetail({ onChanged, onDeleted }) {
  const box = document.getElementById('detail-body');
  let current = null;
  let zoom = loadZoom();
  // 追问会话状态：chatId 为空表示还没聊过（不建会话行）
  let chat = { loaded: false, sending: false, messages: [] };

  function render() {
    clear(box);
    if (!current) {
      box.append(empty('从左边选一道错题，或者点右上角「＋ 录错题」开始。'));
      return;
    }
    const m = current;
    const isDue = m.next_review_at && new Date(m.next_review_at) <= new Date();

    box.append(
      el('h2', { class: 'detail-title' },
        `${m.subject_name || '未分科'} · 第 ${m.id} 题`),
    );

    if (m.image_path) {
      box.append(el('img', {
        class: 'detail-img',
        src: `/uploads/${m.image_path}`,
        alt: '题目原图',
        loading: 'lazy',
      }));
    }

    // ── 题干（带自己的字号控制）──
    const stemSection = el('div', { class: 'section', style: 'margin-top:0' });
    stemSection.append(zoomBar('题目字号', 'stem'));
    if (m.stem_text) {
      stemSection.append(
        el('div', { class: `stem-box zoom-${zoom.stem}` }, m.stem_text));
    } else {
      stemSection.append(el('p', { class: 'hint' }, '这道题只存了图片，没有文字题干。'));
    }
    box.append(stemSection);

    const kv = el('dl', { class: 'kv' });
    if (m.answer_text) {
      kv.append(el('dt', {}, '正确答案'), el('dd', { class: 'right' }, m.answer_text));
    }
    if (m.student_answer) {
      kv.append(el('dt', {}, '我的答案'), el('dd', { class: 'wrong' }, m.student_answer));
    }
    if (m.wrong_reason) {
      kv.append(el('dt', {}, '错因'), el('dd', {}, REASON_LABEL[m.wrong_reason] || m.wrong_reason));
    }
    kv.append(el('dt', {}, '状态'), el('dd', {}, MASTERY_LABEL[m.mastery] || m.mastery));
    kv.append(el('dt', {}, '累计错次'), el('dd', {}, String(m.error_count)));
    kv.append(el('dt', {}, '下次复习'), el('dd', {}, relDue(m.next_review_at)));
    kv.append(el('dt', {}, '录入时间'), el('dd', {}, fmtDateTime(m.created_at)));
    box.append(kv);

    // ── 知识点（P7：可让 AI 自动归类）──
    box.append(kpSection(m));

    // ── 复习 ──
    box.append(reviewSection(m, isDue));

    // ── AI 讲解 ──
    const aiBox = el('div', { id: 'ai-content' });
    box.append(el('div', { class: 'section' },
      el('h3', {}, '讲解'),
      zoomBar('讲解字号', 'explain', true),
      aiBox,
      el('button', { class: 'btn-ai', id: 'btn-explain', onclick: () => explain(false) },
        '🤖 讲解这道题'),
      el('p', { class: 'hint' }, '讲解由本地模型生成，会缓存，重复点不会重复调用。'),
    ));

    // ── 追问（P6）──
    box.append(chatSection());

    // ── 复习历史 ──
    const hist = el('div', { id: 'review-history' });
    box.append(el('div', { class: 'section' }, el('h3', {}, '复习历史'), hist));

    loadHistory(m.id);
    loadExplainIfCached();
    loadChat();
  }

  /* ── 字号控制条 ────────────────────────────────────── */

  function zoomBar(label, key, forExplain = false) {
    const value = zoom[key];
    const apply = (next) => {
      zoom = { ...zoom, [key]: Math.max(0, Math.min(ZOOM_LEVELS.length - 1, next)) };
      saveZoom(zoom);
      // 只改这一块的字号，不整页重渲染 —— 否则正在读的位置会跳掉
      const scope = forExplain ? document.querySelector('.ai-box') : document.querySelector('.stem-box');
      if (forExplain && scope) {
        scope.className = `ai-box zoom-${zoom.explain}`;
      } else if (scope) {
        scope.className = `stem-box zoom-${zoom.stem}`;
      }
      barValue.textContent = ['标准', '大', '更大', '最大'][zoom[key]];
    };
    const barValue = el('span', { class: 'zoom-value' },
      ['标准', '大', '更大', '最大'][value]);
    return el('div', { class: 'zoom-bar' },
      el('span', { class: 'zoom-label' }, label),
      el('button', {
        class: 'btn-zoom', title: '缩小',
        onclick: () => apply(zoom[key] - 1),
      }, 'A−'),
      barValue,
      el('button', {
        class: 'btn-zoom', title: '放大',
        onclick: () => apply(zoom[key] + 1),
      }, 'A+'),
    );
  }

  /* ── 知识点与自动归类（P7）──────────────────────────── */

  function kpSection(m) {
    const wrap = el('div', { class: 'section', id: 'kp-section' });
    wrap.append(el('h3', {}, '知识点'));

    const chips = el('div', { class: 'kp-chips', id: 'kp-chips' });
    if (m.kps?.length) {
      for (const k of m.kps) chips.append(el('span', { class: 'chip' }, k.name));
    } else {
      chips.append(el('span', { class: 'hint' }, '还没有关联知识点。'));
    }
    wrap.append(chips);

    const btn = el('button', {
      class: 'btn-secondary btn-sm', id: 'btn-categorize',
      onclick: () => categorize(btn),
    }, '🤖 自动归类');
    wrap.append(el('div', { class: 'btn-row' }, btn));
    wrap.append(el('p', { class: 'hint', id: 'categorize-hint' },
      'AI 会从本科目已有知识点里挑最匹配的（不会自创新名称）。归类后「学情」「覆盖」才统计得到。'));
    return wrap;
  }

  async function categorize(btn) {
    if (!current) return;
    btn.disabled = true;
    const hint = document.getElementById('categorize-hint');
    if (hint) hint.textContent = 'AI 正在归类，大约需要十几秒…';
    try {
      const res = await api.autoCategorize(current.id);
      if (!res.matched) {
        if (hint) hint.textContent = res.reason || '没有找到匹配的知识点，可手工在后台补充。';
      } else {
        toast(`已归类：${res.picked.map(p => p.name).join('、')}`);
        // 重新拉一次详情：知识点列表是老服务端给的数据，本地拼接容易和服务端不一致
        current = await api.mistake(current.id);
        render();
        onChanged?.();
      }
    } catch (e) {
      if (hint) hint.textContent = describeError(e);
    } finally {
      btn.disabled = false;
    }
  }

  /* ── 复习按钮（P2）────────────────────────────────── */

  /* 宽限期与服务端 REDO_GRACE_SECONDS 必须一致。
     前端只用它来决定「这次点击要不要带 redo 标记」以及提示文案；
     最终以服务端的时间判断为准（客户端时钟可能不准）。 */
  const REDO_GRACE_MS = 10000;

  function reviewSection(m, isDue) {
    // 最近一次复习在宽限期内时，按钮上要能看出「现在点＝修正上一次」，
    // 否则用户不敢点，误触就真的改不掉了。
    const last = m.last_review_at ? new Date(m.last_review_at) : null;
    const canRedo = last ? (Date.now() - last.getTime()) <= REDO_GRACE_MS : false;

    const row = el('div', { class: 'review-row' });
    for (const [result, cls] of [
      ['correct', 'btn-correct'], ['partial', 'btn-partial'], ['wrong', 'btn-wrong'],
    ]) {
      const prefix = result === 'correct' ? '✓ ' : result === 'partial' ? '△ ' : '✗ ';
      row.append(el('button', {
        class: cls,
        id: `btn-review-${result}`,
        onclick: (ev) => submit(result, ev.currentTarget, canRedo),
      }, prefix + REVIEW_LABEL[result]));
    }

    return el('div', { class: 'section' },
      el('h3', {}, isDue ? '复习（已到复习时间）' : '复习'),
      row,
      el('p', { class: 'hint' },
        canRedo
          ? '刚记过一次。现在改点别的按钮＝**修正这一次**（不会多记一条）。'
          : '做对了间隔会翻倍（最长 30 天）；又错了就回到 1 天后重来。'),
    );
  }

  async function submit(result, btn, canRedo) {
    if (!current) return;
    // 防连点：同一个按钮在请求飞行期间再触发会发出两次请求。
    if (btn?.disabled) return;
    if (btn) btn.disabled = true;
    try {
      // 只有在「上一条刚记下」时才声明 redo。服务端仍会核对时间，
      // 超出宽限期它会当作一次普通复习，不会报错。
      const res = await api.submitReview(current.id, {
        result,
        redo: Boolean(canRedo),
      });
      current = await api.mistake(current.id);

      chat = { loaded: false, sending: false, messages: [] };
      render();

      if (res.redo) {
        toast(`已改成「${REVIEW_LABEL[result]}」，上一条记录被替换`);
      } else {
        toast(`已记录：${REVIEW_LABEL[result]}`);
      }
      onChanged?.();
    } catch (e) {
      toast(describeError(e));
      if (btn) btn.disabled = false;
    }
  }

  async function loadHistory(id) {
    const hist = document.getElementById('review-history');
    if (!hist) return;
    try {
      const rows = await api.reviews(id);
      clear(hist);
      if (!rows.length) {
        hist.append(el('p', { class: 'hint' }, '还没复习过。'));
        return;
      }
      const t = el('table');
      t.append(el('tr', {}, el('th', {}, '时间'), el('th', {}, '结果'), el('th', {}, '备注')));
      for (const r of rows) {
        t.append(el('tr', {},
          el('td', {}, fmtDateTime(r.reviewed_at)),
          el('td', {}, REVIEW_LABEL_SHORT[r.result] || r.result),
          el('td', {}, r.note || '—'),
        ));
      }
      hist.append(t);
    } catch (e) {
      clear(hist).append(el('p', { class: 'hint' }, describeError(e)));
    }
  }

  /* ── 讲解 ──────────────────────────────────────────── */

  async function explain(force) {
    if (!current) return;
    const box2 = document.getElementById('ai-content');
    const btn = document.getElementById('btn-explain');
    if (btn) btn.disabled = true;
    clear(box2).append(el('p', { class: 'hint' }, '讲解生成中，大约需要十几秒…'));
    try {
      const res = await api.explain(current.id, force);
      renderExplain(box2, res.payload);
    } catch (e) {
      clear(box2).append(el('p', { class: 'hint' }, describeError(e)));
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* 进详情页时把已缓存的讲解直接显示出来。
     用 explainCached 而不是 explain：后者在缓存未命中时**会真的调用模型**，
     那样每打开一道没讲解过的题都会白白烧一次 token 并等待十几秒。
     这个端点只读缓存，没生成过就什么也不做。 */
  async function loadExplainIfCached() {
    const box2 = document.getElementById('ai-content');
    if (!box2 || !current) return;
    try {
      const res = await api.explainCached(current.id);
      if (res.payload) renderExplain(box2, res.payload);
    } catch {
      // 「顺手预取」失败不该在详情页报错：真要讲解时用户点按钮，那时才提示。
    }
  }

  function renderExplain(box2, p) {
    clear(box2);
    if (!p) {
      box2.append(el('p', { class: 'hint' }, '没有拿到讲解内容。'));
      return;
    }
    const wrap = el('div', { class: `ai-box zoom-${zoom.explain}` });
    const add = (label, value) => {
      if (!value) return;
      wrap.append(el('span', { class: 'label' }, label));
      wrap.append(el('div', {}, value));
    };
    add('这道题考什么', p.summary);
    add('错在哪里', p.error_analysis);
    if (Array.isArray(p.steps) && p.steps.length) {
      wrap.append(el('span', { class: 'label' }, '讲解步骤'));
      wrap.append(el('ol', {}, ...p.steps.map(s => el('li', {}, s))));
    }
    add('关键知识点', p.key_point);
    add('想一想', p.hint);
    add('下次这样做', p.similar_hint);
    box2.append(wrap);
  }

  /* ── 追问聊天（P6）────────────────────────────────── */

  function chatSection() {
    const list = el('div', { class: 'chat-list', id: 'chat-list' },
      el('p', { class: 'hint' }, '还没问过。哪里没听懂就直接问，例如「第 2 步为什么是负的」。'));

    const input = el('textarea', {
      id: 'chat-input', rows: '2', class: 'chat-input',
      placeholder: '问一个问题，比如「为什么要先移项？」',
    });
    // 回车发送、Shift+回车换行 —— 平板上外接键盘和手机上都是这个习惯
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
      }
    });

    const sendBtn = el('button', {
      class: 'btn-primary', id: 'chat-send', onclick: () => sendChat(),
    }, '发送');

    const clearBtn = el('button', {
      class: 'btn-secondary btn-sm', id: 'chat-clear',
      onclick: () => clearChatHistory(),
    }, '清空对话');

    return el('div', { class: 'section' },
      el('h3', {}, '继续追问'),
      list,
      el('div', { class: 'chat-compose' }, input, sendBtn),
      el('div', { class: 'btn-row' }, clearBtn),
      el('p', { class: 'hint' }, '对话会保存下来，下次打开这道题还能看到。'),
    );
  }

  async function loadChat() {
    if (!current) return;
    try {
      const res = await api.explainChat(current.id);
      chat = { loaded: true, sending: false, messages: res.messages || [] };
      renderChat();
    } catch (e) {
      chat = { loaded: true, sending: false, messages: [] };
      renderChat(describeError(e));
    }
  }

  function renderChat(errorMsg) {
    const list = document.getElementById('chat-list');
    if (!list) return;
    clear(list);

    if (errorMsg) {
      list.append(el('p', { class: 'hint' }, errorMsg));
      return;
    }
    if (!chat.messages.length && !chat.sending) {
      list.append(el('p', { class: 'hint' },
        '还没问过。哪里没听懂就直接问，例如「第 2 步为什么是负的」。'));
      return;
    }

    for (const msg of chat.messages) {
      list.append(el('div', { class: `chat-msg ${msg.role}` },
        el('div', { class: 'chat-bubble' }, msg.content),
        el('div', { class: 'chat-time' }, fmtDateTime(msg.created_at)),
      ));
    }
    if (chat.sending) {
      list.append(el('div', { class: 'chat-msg assistant' },
        el('div', { class: 'chat-bubble thinking' }, '老师正在想…')));
    }
    // 新消息在底部，自动滚过去，否则孩子看不到自己刚发出的问题
    list.scrollTop = list.scrollHeight;
  }

  async function sendChat() {
    if (chat.sending || !current) return;
    const input = document.getElementById('chat-input');
    const question = (input?.value || '').trim();
    if (!question) {
      toast('先写点什么再发送吧');
      return;
    }

    // 乐观渲染：先把自己那句显示出来，再等回答。
    // 否则十几秒里界面上什么都没有，孩子会以为没发出去而反复点。
    const optimistic = {
      id: -1, role: 'user', content: question, created_at: new Date().toISOString(),
    };
    chat.messages = [...chat.messages, optimistic];
    chat.sending = true;
    if (input) input.value = '';
    renderChat();
    setChatDisabled(true);

    try {
      const res = await api.chatAsk(current.id, question);
      chat = { loaded: true, sending: false, messages: res.messages || [] };
      renderChat();
    } catch (e) {
      // 失败时把乐观消息撤掉：留在界面上的话，用户会以为问题已经问过了，
      // 但服务端并没有这条记录，刷新之后它会突然消失。
      chat = { ...chat, sending: false, messages: chat.messages.filter(x => x.id !== -1) };
      renderChat();
      toast(describeError(e));
    } finally {
      setChatDisabled(false);
    }
  }

  function setChatDisabled(disabled) {
    for (const id of ['chat-send', 'chat-clear', 'chat-input']) {
      const node = document.getElementById(id);
      if (node) node.disabled = disabled;
    }
  }

  async function clearChatHistory() {
    if (!current) return;
    if (!chat.messages.length) return;
    if (!confirm('清空这道题的追问记录？此操作不可恢复。')) return;
    try {
      await api.clearChat(current.id);
      chat = { loaded: true, sending: false, messages: [] };
      renderChat();
      toast('追问记录已清空');
    } catch (e) {
      toast(describeError(e));
    }
  }

  async function load(id) {
    clear(box).append(loading());
    chat = { loaded: false, sending: false, messages: [] };
    try {
      current = await api.mistake(id);
      render();
    } catch (e) {
      current = null;
      clear(box).append(empty(describeError(e)));
    }
  }

  return {
    load,
    clear: () => { current = null; chat = { loaded: false, sending: false, messages: [] }; render(); },
    getCurrent: () => current,
  };
}