/* 录入错题。

设计要点（风险 R3 的对策）：录入摩擦是这个产品成败的关键。
因此：
  1. 必填项只有「科目」和「题干或图片二选一」；
  2. 图片先上传，AI 读题把题干/答案/错因/知识点**预填**好，孩子只需核对；
  3. 知识点用勾选，不要求输入文字。
如果孩子只拍照不选知识点，SQL 就没有可聚合的维度，功能 2 和 4 会退化成空话。
所以这里刻意让「AI 猜的知识点」直接高亮勾上，降低确认成本。 */

import { api } from '../api.js';
import { el, clear, toast, describeError, REASON_LABEL } from '../ui.js';
import { createCropper } from './crop.js';

export function createCapture({ getGrade, onSaved }) {
  const modal = document.getElementById('capture-modal');
  const subjectSel = document.getElementById('cap-subject');
  const fileInput = document.getElementById('cap-file');
  const fileHint = document.getElementById('cap-file-hint');
  const preview = document.getElementById('cap-preview');
  const btnRead = document.getElementById('btn-read');
  const btnCrop = document.getElementById('btn-crop');
  const readStatus = document.getElementById('cap-read-status');
  const stemTa = document.getElementById('cap-stem');
  const answerIn = document.getElementById('cap-answer');
  const studentAnswerIn = document.getElementById('cap-student-answer');
  const reasonSel = document.getElementById('cap-reason');
  const kpBox = document.getElementById('cap-kps');
  const errP = document.getElementById('cap-error');

  let uploadedPath = null;
  let kpCache = {};       // subjectId -> kp list
  let suggestedNames = [];
  // 最近一次选择的原始文件：裁剪后要重新上传，但原始文件仍保留，
  // 好让孩子裁剪得不满意时能「重选整张」再来一次。
  let lastFile = null;

  const cropper = createCropper({
    onDone: (blob) => {
      // 裁剪结果按新文件重新走一遍上传：服务端的 imaging 会统一压到
      // 最长边 2000px 并修正 EXIF 方向，绕过它直接写文件会破坏这个约定。
      const name = (lastFile?.name || 'crop').replace(/\.\w+$/, '') + '-crop.jpg';
      const f = new File([blob], name, { type: 'image/jpeg' });
      lastFile = f;
      doUpload(f);
    },
  });

  /**
   * 填充科目下拉。
   *
   * subjects 必须**作为参数传入**，不能在构造时用闭包捕获：
   * 构造发生在 app.js 顶层，那时科目还没从接口取回来，捕获到的永远是空数组，
   * 结果就是下拉框永远是空的、选不了科目。
   * （这与 list.js 的 fillSubjects(subjects) 保持同一套约定。）
   */
  function fillSubjects(subjects) {
    const list = subjects || [];
    const prev = subjectSel.value;
    clear(subjectSel);
    for (const s of list) {
      subjectSel.append(el('option', { value: s.id }, s.name));
    }
    if (list.length === 0) {
      subjectSel.append(el('option', { value: '' }, '（没有科目，请检查后台）'));
      subjectSel.disabled = true;
      // 显式清空，避免残留上一个已被移除的科目 id ——
      // 那种情况下提交会打到不存在的科目上，报错还很难懂。
      subjectSel.value = '';
      return;
    }
    subjectSel.disabled = false;
    // 尽量保留原选择，避免刷新科目列表时把用户已选的科目重置掉；
    // 若原选择已不存在，明确落到第一个选项，不留失效值。
    const keep = prev && list.some((s) => String(s.id) === String(prev));
    subjectSel.value = keep ? prev : String(list[0].id);
  }

  function reset() {
    uploadedPath = null;
    suggestedNames = [];
    lastFile = null;
    fileInput.value = '';
    fileHint.textContent = '未选择';
    preview.classList.add('hidden');
    preview.removeAttribute('src');
    btnRead.classList.add('hidden');
    btnCrop.classList.add('hidden');
    readStatus.textContent = '';
    stemTa.value = '';
    answerIn.value = '';
    studentAnswerIn.value = '';
    reasonSel.value = '';
    errP.textContent = '';
    kpBox.replaceChildren(el('p', { class: 'hint' }, '选择科目后加载…'));
  }

  async function open() {
    reset();
    modal.classList.remove('hidden');
    await loadKps();
  }

  function close() {
    modal.classList.add('hidden');
  }

  async function loadKps() {
    const sid = subjectSel.value;
    if (!sid) return;
    kpBox.replaceChildren(el('p', { class: 'hint' }, '加载中…'));
    try {
      let list = kpCache[sid];
      if (!list) {
        list = await api.kps(sid, getGrade() || undefined);
        kpCache[sid] = list;
      }
      renderKps(list);
    } catch (e) {
      kpBox.replaceChildren(el('p', { class: 'hint' }, describeError(e)));
    }
  }

  function renderKps(list) {
    clear(kpBox);
    if (!list.length) {
      kpBox.append(el('p', { class: 'hint' },
        '这个科目还没有知识点。可以先不选，稍后在后台补充。'));
      return;
    }
    // 扁平列表按 parent 分组展示，够用且不必递归渲染树
    const byParent = new Map();
    for (const k of list) {
      const key = k.parent_id ?? 0;
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(k);
    }
    const roots = list.filter(k => !k.parent_id);

    const addItem = (k, indent = false) => {
      const id = `kp-${k.id}`;
      const cb = el('input', { type: 'checkbox', value: String(k.id), id });
      cb.checked = suggestedNames.includes(k.name);
      const row = el('div', {
        class: 'kp-item' + (suggestedNames.includes(k.name) ? ' suggested' : ''),
        style: indent ? 'padding-left:1.4rem' : '',
      }, cb, el('label', { for: id }, k.name));
      kpBox.append(row);
    };

    for (const root of roots) {
      addItem(root);
      for (const child of byParent.get(root.id) || []) addItem(child, true);
    }
    // 父节点不存在的孤儿节点也要显示，否则会静默丢数据
    const rootsIds = new Set(roots.map(r => r.id));
    for (const k of list) {
      if (k.parent_id && !rootsIds.has(k.parent_id)) addItem(k);
    }
  }

  async function doUpload(file) {
    fileHint.textContent = '上传中…';
    try {
      const res = await api.upload(file);
      uploadedPath = res.image_path;
      fileHint.textContent = `已上传（${res.width}×${res.height}）`;
      preview.src = res.url;
      preview.classList.remove('hidden');
      btnRead.classList.remove('hidden');
      btnCrop.classList.remove('hidden');
      errP.textContent = '';
    } catch (e) {
      fileHint.textContent = '上传失败';
      errP.textContent = describeError(e);
    }
  }

  async function doRead() {
    if (!uploadedPath) return;
    btnRead.disabled = true;
    readStatus.textContent = 'AI 正在读题，大约十几秒…';
    try {
      const r = await api.readQuestion(uploadedPath);
      if (r.stem_text) stemTa.value = r.stem_text;
      if (r.answer_text) answerIn.value = r.answer_text;
      if (r.raw_text) studentAnswerIn.value = r.raw_text;
      if (r.wrong_reason_suggestion && !reasonSel.value) {
        reasonSel.value = r.wrong_reason_suggestion;
      }
      suggestedNames = r.kp_suggestions || [];
      await loadKps();

      const bits = [];
      if (suggestedNames.length) bits.push(`建议知识点：${suggestedNames.join('、')}`);
      readStatus.textContent =
        '识别完成，请**核对**后再保存' + (bits.length ? '。' + bits.join('') : '。');
      readStatus.textContent = `识别完成，请核对后再保存。${bits.join('')}`;
    } catch (e) {
      readStatus.textContent = describeError(e);
    } finally {
      btnRead.disabled = false;
    }
  }

  async function save() {
    const stem = stemTa.value.trim();
    if (!stem && !uploadedPath) {
      errP.textContent = '题干和题目照片至少要有一个。';
      return;
    }
    const kpIds = [...kpBox.querySelectorAll('input[type=checkbox]:checked')]
      .map(cb => Number(cb.value));

    const payload = {
      subject_id: Number(subjectSel.value),
      stem_source: uploadedPath ? 'photo' : 'typed',
      kp_ids: kpIds,
    };
    if (stem) payload.stem_text = stem;
    if (uploadedPath) payload.image_path = uploadedPath;
    if (answerIn.value.trim()) payload.answer_text = answerIn.value.trim();
    if (studentAnswerIn.value.trim()) payload.student_answer = studentAnswerIn.value.trim();
    if (reasonSel.value) payload.wrong_reason = reasonSel.value;

    const btn = document.getElementById('btn-save');
    btn.disabled = true;
    try {
      const created = await api.createMistake(payload);
      close();
      toast('已记录，1 天后安排首次复习');
      onSaved?.(created);
    } catch (e) {
      errP.textContent = describeError(e);
    } finally {
      btn.disabled = false;
    }
  }

  // ── 事件绑定 ──
  document.getElementById('capture-close').addEventListener('click', close);
  document.getElementById('capture-cancel').addEventListener('click', close);
  document.getElementById('btn-save').addEventListener('click', save);
  document.getElementById('btn-camera').addEventListener('click', () => fileInput.click());
  btnRead.addEventListener('click', doRead);
  btnCrop.addEventListener('click', () => {
    // 用最近一次选的文件重新进裁剪，而不是拿已经上传过的图片再裁 ——
    // 上传后的图已被压到 2000px，二次裁剪会再损失一次画质。
    if (lastFile) cropper.open(lastFile);
  });
  subjectSel.addEventListener('change', () => { suggestedNames = []; loadKps(); });
  fileInput.addEventListener('change', () => {
    const f = fileInput.files?.[0];
    if (!f) return;
    lastFile = f;
    // 拍照后**先裁再传**：原图往往整页都是内容，直接上传再让 AI 读，
    // 会把旁边的题一起读进来。裁剪是读题准确率的前提，不是可选项，
    // 因此这里主动打开裁剪层；不想裁的话点「全选」即可，成本极低。
    cropper.open(f);
  });
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  return { open, close, fillSubjects };
}