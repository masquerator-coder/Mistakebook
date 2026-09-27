/* 共用的小工具与展示片段。 */

export const MASTERY_LABEL = {
  new: '新录入',
  reviewing: '复习中',
  mastered: '已掌握',
};

/* 覆盖状态的措辞。这里的用词是硬约束：
   untested / untested_with_content 绝不能写成「已掌握」——
   没做过不等于学会了。 */
export const COVERAGE_LABEL = {
  weak: '有错题未掌握',
  covered: '错题已掌握',
  untested: '还没做过题',
  untested_with_content: '未考过（有教材内容）',
};

export const REASON_LABEL = {
  concept: '概念不清',
  calculation: '计算错误',
  misread: '看错题',
  method: '方法不会',
  careless: '粗心',
  time: '来不及',
  unknown: '说不清',
};

export const COVERAGE_ORDER = ['weak', 'covered', 'untested_with_content', 'untested'];

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (k === 'html') node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function pill(text, kind) {
  return el('span', { class: `pill ${kind}` }, text);
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/* 相对时间：复习计划里「还有几天该复习」比绝对日期更直观。 */
export function relDue(iso) {
  if (!iso) return '未安排';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '未安排';
  const now = new Date();
  const diffDays = Math.round((d - now) / 86400000);
  if (diffDays < 0) return `已逾期 ${-diffDays} 天`;
  if (diffDays === 0) return '今天';
  if (diffDays === 1) return '明天';
  return `${diffDays} 天后`;
}

export function truncate(text, n = 60) {
  if (!text) return '';
  const t = text.replace(/\s+/g, ' ').trim();
  return t.length > n ? t.slice(0, n) + '…' : t;
}

let toastTimer = null;
export function toast(msg, ms = 2600) {
  const node = document.getElementById('toast');
  if (!node) return;
  node.textContent = msg;
  node.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add('hidden'), ms);
}

/* 统一的错误提示：连不上服务器与业务错误分开讲，前者家长才知道要开电脑。 */
export function describeError(e) {
  if (e?.name === 'OfflineError') {
    return '连不上服务器，请确认电脑已开机且 Docker 正在运行。';
  }
  return e?.message || '出错了，请重试。';
}

export function loading(text = '加载中…') {
  return el('p', { class: 'empty' }, text);
}

export function empty(text) {
  return el('p', { class: 'empty' }, text);
}