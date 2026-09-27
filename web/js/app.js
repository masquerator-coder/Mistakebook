/* 应用入口：装配各视图、切换标签、检查服务器可达性。 */

import { api, OfflineError } from './api.js';
import { toast, describeError } from './ui.js';
import { createList } from './views/list.js';
import { createDetail } from './views/detail.js';
import { createAnalysis } from './views/analysis.js';
import { createCoverage } from './views/coverage.js';
import { createPlan } from './views/plan.js';
import { createCapture } from './views/capture.js';

const state = {
  subjects: [],
  currentView: 'list',
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

  VIEWS[name]?.();
}

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