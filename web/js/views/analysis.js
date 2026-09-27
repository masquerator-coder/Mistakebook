/* 学情分析：覆盖统计、薄弱点、趋势。
   所有数字都来自服务端 SQL，前端不做任何统计计算。 */

import { api } from '../api.js';
import {
  el, clear, loading, empty, pill, describeError,
  COVERAGE_LABEL, COVERAGE_ORDER, REASON_LABEL,
} from '../ui.js';

export function createAnalysis({ getSubjectId, getGrade }) {
  const box = document.getElementById('detail-body');

  async function load() {
    const subjectId = getSubjectId();
    const grade = getGrade();
    clear(box).append(loading('正在统计…'));

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
    const { coverage, weak_points: weak, trend } = data;

    box.append(el('h2', { class: 'detail-title' }, '学情分析'));

    if (!coverage.total_kp) {
      box.append(empty(
        '这个科目在所选年级下还没有知识点。\n' +
        '先去「覆盖」页或后台录入知识点，分析才有依据。'
      ));
      return;
    }

    // ── 总览数字 ──
    const rate = coverage.mastery_rate === null
      ? '—'
      : `${Math.round(coverage.mastery_rate * 100)}%`;

    box.append(el('div', { class: 'stat-grid' },
      stat(coverage.total_kp, '知识点总数'),
      stat(coverage.touched_kp, '考过的知识点'),
      stat(coverage.untouched_kp, '还没考过的'),
      stat(rate, '考过的里面已掌握'),
    ));

    box.append(el('p', { class: 'hint' },
      '「考过的里面已掌握」的分母只算有错题的知识点。' +
      '没考过的知识点不计入，否则会把「没做过」算成「没学会」，对你的评价是失真的。'));

    // ── 四类覆盖分布 ──
    box.append(el('div', { class: 'section' },
      el('h3', {}, '知识点覆盖情况'),
      distribution(coverage),
    ));

    // ── 薄弱知识点 ──
    box.append(el('div', { class: 'section' },
      el('h3', {}, '需要优先补的知识点'),
      weak.length ? weakTable(weak) : el('p', { class: 'hint' }, '暂时没有薄弱项。'),
    ));

    // ── 复习趋势 ──
    if (trend.length) {
      box.append(el('div', { class: 'section' },
        el('h3', {}, '近两周复习正确率'),
        trendChart(trend),
      ));
    }
  }

  function stat(num, label) {
    return el('div', { class: 'stat' },
      el('div', { class: 'stat-num' }, String(num)),
      el('div', { class: 'stat-label' }, label));
  }

  function distribution(cov) {
    const wrap = el('div');
    for (const key of COVERAGE_ORDER) {
      const n = cov.counts[key] ?? 0;
      const total = cov.total_kp || 1;
      const pct = Math.round((n / total) * 100);
      // 颜色区分：红色=要下功夫，绿色=已修复，灰色/紫色=还没做过
      const color = {
        weak: 'var(--danger)',
        covered: 'var(--mastered)',
        untested: 'var(--untested)',
        untested_with_content: 'var(--untested-content)',
      }[key];

      wrap.append(el('div', { class: 'bar-row' },
        el('span', { class: 'bar-label' }, COVERAGE_LABEL[key]),
        el('span', { class: 'bar-track' },
          el('span', { class: 'bar-fill', style: `width:${pct}%;background:${color}` })),
        el('span', { class: 'bar-num' }, `${n}`)));
    }
    wrap.append(el('p', { class: 'hint' },
      '注意：「还没做过题」不等于「已掌握」，它只说明还没遇到这类题。'));
    return wrap;
  }

  function weakTable(rows) {
    const t = el('table');
    t.append(el('tr', {},
      el('th', {}, '知识点'), el('th', {}, '错题数'), el('th', {}, '未掌握'), el('th', {}, '教材页')));
    for (const w of rows) {
      t.append(el('tr', {},
        el('td', {}, w.name),
        el('td', {}, String(w.mistake_cnt)),
        el('td', {}, String(w.mistake_cnt - w.mastered_cnt)),
        el('td', {}, w.page_from ? `${w.page_from}-${w.page_to ?? w.page_from}` : '—'),
      ));
    }
    return t;
  }

  function trendChart(trend) {
    const max = Math.max(...trend.map(t => t.total), 1);
    const wrap = el('div');
    for (const t of trend) {
      const w = Math.round((t.total / max) * 100);
      wrap.append(el('div', { class: 'bar-row' },
        el('span', { class: 'bar-label' }, t.day.slice(5)),
        el('span', { class: 'bar-track' },
          el('span', { class: 'bar-fill', style: `width:${w}%` })),
        el('span', { class: 'bar-num' }, `${Math.round(t.accuracy * 100)}%`)));
    }
    return wrap;
  }

  return { load };
}