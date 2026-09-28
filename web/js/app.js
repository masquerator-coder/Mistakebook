/* 应用入口：装配各视图、切换标签、检查服务器可达性。 */

import { api, OfflineError } from './api.js';
import {
  toast, describeError, applyFontIndex, loadFontIndex, FONT_SIZES,
} from './ui.js';
import { createList } from './views/list.js';
import { createDetail } from './views/detail.js';
import { createAnalysis } from './views/analysis.js';
import { createCoverage } from './views/coverage.js';
import { createPlan } from './views/plan.js';
import { createCapture } from './views/capture.js';

const state = {
  subjects: [],
  currentView: 'list',
  listCollapsed: false,
};

const gradeSel = document.getElementById('grade-select');
const getGrade = () => gradeSel.value;

/* 当前科目取左栏筛选器的值 —— 让「学情/覆盖」跟着用户在「错题」页选的科目走，
   避免两处各有一个科目选择器导致对不上。 */
const getSubjectId = () => {
  const v = document.getElementById('subject-filter')?.value;
  return v ? Number(v) : (state.subjects[0]?.id ?? null);
};

const list = createList({
  onSelect: (m) => { if (m) detail.load(m.id); },
});

const detail = createDetail({
  onChanged: () => list.load(),
  onDeleted: (id) => list.removeLocal(id),
});

const analysis = createAnalysis({ getSubjectId, getGrade });
const coverageView = createCoverage({ getSubjectId, getGrade });
const plan = createPlan({ getGrade });

const capture = createCapture({
  getGrade,
  onSaved: async () => {
    await list.load();
  },
});

const VIEWS = {
  list: () => list.load({ mode: 'list' }),
  due: () => list.load({ mode: 'due' }),
  analysis: () => analysis.load(),
  coverage: () => coverageView.load(),
  plan: () => plan.load(),
};

function switchView(name) {
  state.currentView = name;
  for (const b of document.querySelectorAll('.tab')) {
    b.classList.toggle('active', b.dataset.view === name);
  }
  // 这几页不用左栏列表，占满宽度体验更好；「错题/待复习」保留双栏
  const fullWidth = ['analysis', 'coverage', 'plan'].includes(name);
  document.querySelector('.list-pane').style.display = fullWidth ? 'none' : '';
  document.querySelector('.layout').style.gridTemplateColumns = fullWidth ? '1fr' : '';

  document.body.classList.remove('show-detail');

  // 「学情/覆盖/计划」与「错题详情」共用右栏容器。切回列表页时必须清掉
  // 右栏残留的上一道错题，否则会出现「列表已刷新、详情还是上一题」的错配。
  if (name === 'list' || name === 'due') detail.clear();

  // 这些页面本来就不显示左栏，折叠按钮在这里没有意义，藏掉更清爽
  document.getElementById('btn-toggle-list').style.display = fullWidth ? 'none' : '';

  VIEWS[name]?.();
}

/* ── 左栏折叠（P3）─────────────────────────────────────
   平板横屏与手机竖屏上，做题时左栏会白白占掉三分之一宽度。
   折叠状态存 localStorage：孩子收起来之后，切标签页、
   刷新都不应该又自己弹回来。 */
const COLLAPSE_KEY = 'mistakebook.listCollapsed';

function setListCollapsed(collapsed) {
  state.listCollapsed = collapsed;
  document.body.classList.toggle('list-collapsed', collapsed);
  try { localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0'); } catch { /* 忽略 */ }
}

try { setListCollapsed(localStorage.getItem(COLLAPSE_KEY) === '1'); } catch { /* 忽略 */ }

document.getElementById('btn-toggle-list').addEventListener('click', () => {
  setListCollapsed(!state.listCollapsed);
});

/* ── 全站字号（P5）─────────────────────────────────────
   这里管的是整体基准字号；题干与讲解还有各自独立的字号控制
   （见 detail.js），因为「看题要大字」和「读讲解要大段文字」
   是两个不同的需求。 */
let fontIndex = loadFontIndex();

function refreshFontLabel() {
  applyFontIndex(fontIndex);
  const node = document.getElementById('font-value');
  if (node) node.textContent = ['小', '标准', '大', '更大', '最大'][fontIndex] || '标准';
}

document.getElementById('btn-font-up').addEventListener('click', () => {
  if (fontIndex >= FONT_SIZES.length - 1) return;
  fontIndex += 1;
  refreshFontLabel();
  toast('已放大字号');
});

document.getElementById('btn-font-down').addEventListener('click', () => {
  if (fontIndex <= 0) return;
  fontIndex -= 1;
  refreshFontLabel();
  toast('已缩小字号');
});

refreshFontLabel();

/* 窄屏上从详情返回列表 */
document.getElementById('btn-back').addEventListener('click', () => {
  document.body.classList.remove('show-detail');
});

for (const b of document.querySelectorAll('.tab')) {
  b.addEventListener('click', () => switchView(b.dataset.view));
}

document.getElementById('btn-capture').addEventListener('click', () => capture.open());

/* ── 服务器可达性 ──────────────────────────────────────
   电脑关机时孩子看到的不能是白屏（风险 R4），因此主动探测并给出可读提示。 */
const banner = document.getElementById('offline-banner');
const bannerDetail = document.getElementById('offline-detail');

function setOffline(offline, msg) {
  banner.classList.toggle('hidden', !offline);
  if (msg) bannerDetail.textContent = msg;
}

async function checkHealth() {
  try {
    await api.overview();
    setOffline(false);
    return true;
  } catch (e) {
    setOffline(true, e instanceof OfflineError
      ? '请确认运行服务的电脑已开机，且 Docker 正在运行。'
      : describeError(e));
    return false;
  }
}

document.getElementById('btn-retry').addEventListener('click', async () => {
  if (await checkHealth()) {
    toast('已连上服务器');
    init();
  }
});

/* ── 初始化 ─────────────────────────────────────────── */
async function init() {
  try {
    state.subjects = await api.subjects();
  } catch (e) {
    document.getElementById('mistake-list').replaceChildren(
      Object.assign(document.createElement('p'), {
        className: 'empty',
        textContent: describeError(e),
      })
    );
    setOffline(true, describeError(e));
    return;
  }

  capture.fillSubjects(state.subjects);
  list.fillSubjects(state.subjects);

  await list.load({ mode: 'list' });
  await updateDueBadge();
}

async function updateDueBadge() {
  try {
    const due = await api.dueMistakes(200);
    const badge = document.getElementById('due-badge');
    if (due.length) {
      badge.textContent = String(due.length);
      badge.classList.add('show');
    } else {
      badge.classList.remove('show');
    }
  } catch { /* 徽章是锦上添花，失败不提示 */ }
}

gradeSel.addEventListener('change', () => {
  // 年级变了，知识点缓存与各视图都得重来
  if (state.currentView === 'list' || state.currentView === 'due') {
    list.load({ mode: state.currentView });
  } else {
    VIEWS[state.currentView]?.();
  }
});

if (await checkHealth()) {
  await init();
}