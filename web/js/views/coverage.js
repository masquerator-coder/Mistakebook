/* 知识库覆盖盘点：回答「哪些知识点还没复习到」。
   这是纯 SQL 查询的结果，不调用 AI。 */

import { api } from '../api.js';
import { el, clear, loading, empty, pill, describeError, COVERAGE_LABEL } from '../ui.js';

export function createCoverage({ getSubjectId, getGrade }) {
  const box = document.getElementById('detail-body');
  let filter = ''; // '' | 'weak' | 'untouched'

  async function load() {
    const subjectId = getSubjectId();
    const grade = getGrade();
    clear(box).append(loading('正在盘点…'));

    if (!subjectId) {
      clear(box).append(empty('先在「错题」页选一个科目。'));
      return;
    }

    try {
      const data = await api.subjectAnalysis(subjectId, grade);
      render(data);
    } catch (e) {
      clear(box).append(empty(describeError(e)));
    }
  }

  function render(data) {
    clear(box);
    const cov = data.coverage;

    box.append(el('h2', { class: 'detail-title' }, '知识库覆盖盘点'));

    if (!cov.total_kp) {
      box.append(empty(
        '这个科目还没有知识点。\n' +
        '知识点可以手工添加，也可以由后台从教材提取（见 README）。'
      ));
      return;
    }

    box.append(el('div', { class: 'stat-grid' },
      stat(cov.counts.weak, '有错题未掌握', 'var(--danger)'),
      stat(cov.counts.covered, '错题已掌握', 'var(--mastered)'),
      stat(cov.counts.untested_with_content, '未考过（有教材）', 'var(--untested-content)'),
      stat(cov.counts.untested, '完全没碰过', 'var(--untested)'),
    ));

    // 关键语义提示：这是产品正确性问题，不是文案细节
    box.append(el('p', { class: 'hint' },
      '「未考过」「完全没碰过」都**不是**「已掌握」。它们只表示还没做过这类题，' +
      '需要你自己翻教材确认是否学过。'));

    // ── 筛选 ──
    const filters = el('div', { class: 'filters', style: 'margin:1rem 0' });
    for (const [key, label] of [
      ['', '全部'], ['weak', '只看未掌握'], ['untouched', '只看没碰过的'],
    ]) {
      filters.append(el('button', {
        class: 'tab' + (filter === key ? ' active' : ''),
        onclick: () => { filter = key; render(data); },
      }, label));
    }
    box.append(filters);

    let items = cov.items;
    if (filter === 'weak') items = items.filter(i => i.coverage === 'weak');
    if (filter === 'untouched') {
      items = items.filter(i => i.coverage === 'untested' || i.coverage === 'untested_with_content');
    }

    if (!items.length) {
      box.append(empty('这个筛选条件下没有知识点。'));
      return;
    }

    const t = el('table');
    t.append(el('tr', {},
      el('th', {}, '知识点'),
      el('th', {}, '状态'),
      el('th', {}, '错题'),
      el('th', {}, '教材页'),
      el('th', {}, '最近复习'),
    ));
    for (const i of items) {
      t.append(el('tr', {},
        el('td', {}, i.name, i.reviewed ? null : el('span', { class: 'opt' }, ' （待核对）')),
        el('td', {}, pill(COVERAGE_LABEL[i.coverage], i.coverage)),
        el('td', {}, String(i.mistake_cnt)),
        el('td', {}, i.page_from ? `${i.page_from}-${i.page_to ?? i.page_from}` : '—'),
        el('td', {}, i.last_reviewed_at ? i.last_reviewed_at.slice(5, 10) : '—'),
      ));
    }
    box.append(t);
  }

  function stat(num, label, color) {
    return el('div', { class: 'stat' },
      el('div', { class: 'stat-num', style: `color:${color}` }, String(num ?? 0)),
      el('div', { class: 'stat-label' }, label));
  }

  return { load };
}