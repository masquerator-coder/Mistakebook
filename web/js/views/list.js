/* 左栏：错题列表 */

import { api } from '../api.js';
import { el, clear, loading, empty, pill, MASTERY_LABEL, truncate, relDue, describeError, toast } from '../ui.js';

export function createList({ onSelect }) {
  const box = document.getElementById('mistake-list');
  const title = document.getElementById('list-title');
  const subjectFilter = document.getElementById('subject-filter');
  const masteryFilter = document.getElementById('mastery-filter');

  let items = [];
  let selectedId = null;
  let mode = 'list'; // 'list' | 'due'

  function render() {
    clear(box);
    if (!items.length) {
      box.append(empty(mode === 'due' ? '今天没有需要复习的错题 🎉' : '还没有错题，点右上角「＋ 录错题」开始。'));
      return;
    }
    for (const m of items) {
      box.append(card(m));
    }
  }

  function card(m) {
    const isDue = m.next_review_at && new Date(m.next_review_at) <= new Date();
    const node = el(
      'div',
      {
        class: 'card' + (m.id === selectedId ? ' selected' : ''),
        onclick: () => select(m.id),
      },
      el('div', { class: 'card-stem' },
        m.stem_text ? truncate(m.stem_text, 70) : '（图片题，无文字）'),
      el('div', { class: 'card-meta' },
        pill(m.subject_name || '未分科', 'plain'),
        pill(MASTERY_LABEL[m.mastery] || m.mastery, m.mastery),
        m.error_count > 1 ? pill(`错 ${m.error_count} 次`, 'due') : null,
        pill(relDue(m.next_review_at), isDue ? 'due' : 'plain'),
      ),
    );
    return node;
  }

  function select(id) {
    selectedId = id;
    for (const c of box.children) c.classList.remove('selected');
    const idx = items.findIndex(x => x.id === id);
    if (idx >= 0 && box.children[idx]) box.children[idx].classList.add('selected');
    document.body.classList.add('show-detail');
    onSelect?.(items[idx]);
  }

  async function load(opts = {}) {
    clear(box).append(loading());
    try {
      if (opts.mode) mode = opts.mode;
      if (mode === 'due') {
        title.textContent = '今天待复习';
        items = await api.dueMistakes();
      } else {
        title.textContent = '全部错题';
        items = await api.mistakes({
          subject_id: subjectFilter.value || undefined,
          mastery: masteryFilter.value || undefined,
        });
      }
      render();
      return items;
    } catch (e) {
      clear(box).append(empty(describeError(e)));
      return [];
    }
  }

  subjectFilter.addEventListener('change', () => { mode = 'list'; load({ mode: 'list' }); });
  masteryFilter.addEventListener('change', () => { mode = 'list'; load({ mode: 'list' }); });

  return {
    load,
    select,
    selectById: (id) => select(id),
    getItems: () => items,
    getSelectedId: () => selectedId,
    setMode: (m) => { mode = m; },
    fillSubjects(subjects) {
      const cur = subjectFilter.value;
      clear(subjectFilter).append(el('option', { value: '' }, '全部科目'));
      for (const s of subjects) {
        subjectFilter.append(el('option', { value: s.id }, s.name));
      }
      subjectFilter.value = cur;
    },
    /* 删除后需要把已选项清掉，否则右栏还显示已删除的题 */
    removeLocal(id) {
      items = items.filter(x => x.id !== id);
      if (selectedId === id) selectedId = null;
      render();
    },
  };
}