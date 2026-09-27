/* 右栏：错题详情、复习操作、AI 讲解 */

import { api } from '../api.js';
import {
  el, clear, loading, empty, pill, toast, describeError,
  MASTERY_LABEL, REASON_LABEL, fmtDateTime, relDue,
} from '../ui.js';

export function createDetail({ onChanged, onDeleted }) {
  const box = document.getElementById('detail-body');
  let current = null;

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

    if (m.stem_text) {
      box.append(el('div', { class: 'stem-box' }, m.stem_text));
    } else {
      box.append(el('p', { class: 'hint' }, '这道题只存了图片，没有文字题干。'));
    }

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

    if (m.kps?.length) {
      box.append(el('div', { class: 'kp-chips' },
        ...m.kps.map(k => el('span', { class: 'chip' }, k.name))));
    } else {
      box.append(el('p', { class: 'hint' },
        '这道题还没有关联知识点。关联之后，「学情」和「覆盖」页面才会有依据。'));
    }

    // ── 复习 ──
    box.append(el('div', { class: 'section' },
      el('h3', {}, isDue ? '复习（已到复习时间）' : '复习'),
      el('div', { class: 'review-row' },
        el('button', { class: 'btn-correct', onclick: () => submit('correct') }, '✓ 做对了'),
        el('button', { class: 'btn-partial', onclick: () => submit('partial') }, '△ 半对'),
        el('button', { class: 'btn-wrong', onclick: () => submit('wrong') }, '✗ 又错了'),
      ),
      el('p', { class: 'hint' }, '做对了间隔会翻倍（最长 30 天）；又错了就回到 1 天后重来。'),
    ));

    // ── AI 讲解 ──
    const aiBox = el('div', { id: 'ai-content' });
    box.append(el('div', { class: 'section' },
      el('h3', {}, '讲解'),
      aiBox,
      el('button', { class: 'btn-ai', id: 'btn-explain', onclick: () => explain(false) }, '🤖 讲解这道题'),
      el('p', { class: 'hint' }, '讲解由本地模型生成，会缓存，重复点不会重复调用。'),
    ));

    // ── 复习历史 ──
    const hist = el('div', { id: 'review-history' });
    box.append(el('div', { class: 'section' }, el('h3', {}, '复习历史'), hist));

    loadHistory(m.id);
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
        const label = r.result === 'correct' ? '做对' : r.result === 'partial' ? '半对' : '做错';
        t.append(el('tr', {},
          el('td', {}, fmtDateTime(r.reviewed_at)),
          el('td', {}, label),
          el('td', {}, r.note || '—'),
        ));
      }
      hist.append(t);
    } catch (e) {
      clear(hist).append(el('p', { class: 'hint' }, describeError(e)));
    }
  }

  async function submit(result) {
    if (!current) return;
    try {
      const updated = await api.submitReview(current.id, { result });
      current = updated;
      render();
      toast(result === 'correct' ? '已记录：做对了' : result === 'wrong' ? '已记录：又错了，明天再练' : '已记录：半对');
      onChanged?.();
    } catch (e) {
      toast(describeError(e));
    }
  }

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

  function renderExplain(box2, p) {
    clear(box2);
    if (!p) {
      box2.append(el('p', { class: 'hint' }, '没有拿到讲解内容。'));
      return;
    }
    const wrap = el('div', { class: 'ai-box' });
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

  async function load(id) {
    clear(box).append(loading());
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
    clear: () => { current = null; render(); },
    getCurrent: () => current,
  };
}