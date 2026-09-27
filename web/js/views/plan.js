/* 复习计划：调模型生成，但所有喂进去的数字都来自服务端 SQL。 */

import { api } from '../api.js';
import { el, clear, loading, empty, describeError, toast, fmtDateTime } from '../ui.js';

export function createPlan({ getGrade }) {
  const box = document.getElementById('detail-body');

  async function load() {
    clear(box).append(loading('正在读取上次的计划…'));
    try {
      const plan = await api.latestPlan();
      render(plan.payload, { generatedAt: plan.generated_at, days: daysBetween(plan) });
    } catch (e) {
      if (e?.status === 404) {
        renderEmpty();
      } else {
        clear(box).append(empty(describeError(e)));
      }
    }
  }

  function daysBetween(plan) {
    const a = new Date(plan.period_start), b = new Date(plan.period_end);
    return Math.max(1, Math.round((b - a) / 86400000));
  }

  function renderEmpty(msg) {
    clear(box);
    box.append(el('h2', { class: 'detail-title' }, '复习计划'));
    if (msg) box.append(el('p', { class: 'hint' }, msg));
    box.append(empty('还没有生成过复习计划。点下面的按钮生成一份。'));
    box.append(generateRow());
  }

  function generateRow(busy = false) {
    return el('div', { class: 'section' },
      el('button', {
        class: 'btn-ai',
        disabled: busy,
        onclick: () => generate(),
      }, busy ? '生成中，请稍候…' : '📅 生成未来 7 天计划'),
      el('p', { class: 'hint' },
        '计划基于已经统计好的错题数据生成。数字由数据库算好，模型只负责安排优先级，不负责算数。'),
    );
  }

  async function generate() {
    clear(box).append(loading('正在生成计划，大约需要十几秒…'));
    box.append(generateRow(true));
    try {
      const res = await api.generatePlan(7);
      if (res.message) toast(res.message);
      render(res.payload, { generatedAt: new Date().toISOString(), days: 7 });
      toast('计划已生成');
    } catch (e) {
      clear(box).append(empty(describeError(e)));
      box.append(generateRow());
    }
  }

  function render(p, meta) {
    clear(box);
    box.append(el('h2', { class: 'detail-title' }, '复习计划'));
    if (meta?.generatedAt) {
      box.append(el('p', { class: 'hint' }, `生成于 ${fmtDateTime(meta.generatedAt)}`));
    }

    if (p.priority_gaps?.length) {
      const t = el('table');
      t.append(el('tr', {},
        el('th', {}, '优先补的知识点'), el('th', {}, '错题数'), el('th', {}, '为什么优先')));
      for (const g of p.priority_gaps) {
        t.append(el('tr', {},
          el('td', {}, g.name || '—'),
          el('td', {}, String(g.mistake_count ?? '—')),
          el('td', {}, g.reason || '—')));
      }
      box.append(el('div', { class: 'section' }, el('h3', {}, '优先补什么'), t));
    }

    if (p.daily_plan?.length) {
      const wrap = el('div', { class: 'section' }, el('h3', {}, '每天做什么'));
      for (const d of p.daily_plan) {
        wrap.append(el('div', { class: 'stat', style: 'text-align:left;margin-bottom:.5rem' },
          el('div', { style: 'font-weight:600;margin-bottom:.35rem' },
            `第 ${d.day} 天 · ${d.focus || ''}${d.minutes ? ` · 约 ${d.minutes} 分钟` : ''}`),
          el('ul', { style: 'margin:0;padding-left:1.3rem' },
            ...(d.tasks || []).map(t => el('li', {}, t))),
        ));
      }
      box.append(wrap);
    }

    if (p.score_strategy?.length) {
      box.append(el('div', { class: 'section' },
        el('h3', {}, '怎么提升成绩'),
        el('div', { class: 'ai-box' },
          el('ol', {}, ...p.score_strategy.map(s => el('li', {}, s))))));
    }

    if (p.encouragement) {
      box.append(el('div', { class: 'section' },
        el('div', { class: 'ai-box' }, p.encouragement)));
    }

    box.append(generateRow());

    // 历史记录永远拿得到，不必每次都重新烧一遍模型
    box.append(el('p', { class: 'hint' },
      '重新生成会覆盖显示，历史计划仍保存在数据库里。'));
  }

  return { load };
}