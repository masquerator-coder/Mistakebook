/* 拍照后的裁剪层（零依赖，纯 canvas）。
 *
 * 为什么必须裁剪：手机/平板直接拍一张作业纸，画面里有题干、有草稿、有旁边的题，
 * 还常常是斜的、带大片桌面。原图送进模型有两个实际后果：
 *   1. 读题会把旁边的题一起读进来，题干串成一团（实测最容易出的错）；
 *   2. 无谓地撑大请求体，慢且浪费。
 * 因此录入流程里裁剪不是「锦上添花」，而是读题准不准的前提。
 *
 * 实现选择：不引入 cropperjs 之类的库。本仓库前端刻意保持「无构建、零依赖」，
 * 引一个库就要考虑离线托管与版本升级；而这里需要的功能（拖动选框 + 四角缩放 +
 * 从显示坐标换算回原图坐标）大约两百行，自己写更可控。
 *
 * 坐标换算（最容易写错的地方）：
 *   显示的 canvas 尺寸是 CSS 像素，而图片有原始像素。所有指针坐标先换算成
 *   「相对 canvas 的 0..1 比例」，再乘原图尺寸。这样与 CSS 尺寸、DPR 都无关，
 *   不会出现高分屏下裁错位置的问题。
 */

const HANDLE_HIT = 22;   // 手指比鼠标粗，四角的命中半径要放大
const MIN_SIDE = 24;     // 选框最小边长（CSS 像素），再小就没法操作了

export function createCropper({ onDone }) {
  const modal = document.getElementById('crop-modal');
  const stage = document.getElementById('crop-stage');
  const canvas = document.getElementById('crop-canvas');
  const selection = document.getElementById('crop-selection');
  const errP = document.getElementById('crop-error');
  const hint = document.getElementById('crop-hint');

  const ctx = canvas.getContext('2d');

  // 原图（未旋转）与当前显示用的位图（可能已旋转）
  let sourceImage = null;
  let bitmap = null;      // 旋转后的 ImageBitmap/Canvas，canvas 上画的就是它
  let rotation = 0;       // 0 / 90 / 180 / 270
  let view = { w: 0, h: 0 };              // canvas 的 CSS 尺寸
  // 选框：以「相对 canvas 的比例」存储，避免和像素尺寸耦合
  let rect = { x: 0, y: 0, w: 1, h: 1 };
  let drag = null;        // { mode: 'move'|'nw'|... , startX, startY, rect }

  /* ── 打开与关闭 ─────────────────────────────────────── */

  async function open(file) {
    errP.textContent = '';
    modal.classList.remove('hidden');
    hint.textContent = '正在读取照片…';
    try {
      sourceImage = await loadImage(file);
    } catch (e) {
      errP.textContent = '这张图片读不出来，请换一张或重拍。';
      hint.textContent = '';
      return;
    }
    rotation = 0;
    await rebuild();
    hint.textContent =
      '拖动方框调整范围，拖四角改大小。只留题目那部分，拍得越干净 AI 读题越准。';
  }

  function close() {
    modal.classList.add('hidden');
    drag = null;
  }

  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('decode failed')); };
      img.src = url;
    });
  }

  /* ── 布局：把（可能旋转过的）位图缩放铺进 stage ─────── */

  async function rebuild() {
    if (!sourceImage) return;

    // 旋转用一块离屏 canvas 完成，而不是在绘制时用 transform ——
    // transform 方式下 getImageData 的坐标会很绕，容易裁错。
    const swapped = rotation % 180 !== 0;
    const bw = swapped ? sourceImage.naturalHeight : sourceImage.naturalWidth;
    const bh = swapped ? sourceImage.naturalWidth : sourceImage.naturalHeight;

    const off = document.createElement('canvas');
    off.width = bw;
    off.height = bh;
    const octx = off.getContext('2d');
    octx.translate(bw / 2, bh / 2);
    octx.rotate((rotation * Math.PI) / 180);
    octx.drawImage(sourceImage, -sourceImage.naturalWidth / 2, -sourceImage.naturalHeight / 2);
    bitmap = off;

    // 让图片在 stage 里完整可见（contain），并按 DPR 提升清晰度
    const stageW = stage.clientWidth || 320;
    const stageH = Math.max(200, Math.min(window.innerHeight * 0.5, 520));
    const scale = Math.min(stageW / bw, stageH / bh);
    view = { w: Math.round(bw * scale), h: Math.round(bh * scale) };

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(view.w * dpr);
    canvas.height = Math.round(view.h * dpr);
    canvas.style.width = `${view.w}px`;
    canvas.style.height = `${view.h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.drawImage(bitmap, 0, 0, view.w, view.h);

    stage.style.width = `${view.w}px`;
    stage.style.height = `${view.h}px`;

    drawSelection();
  }

  function drawSelection() {
    selection.style.left = `${rect.x * view.w}px`;
    selection.style.top = `${rect.y * view.h}px`;
    selection.style.width = `${rect.w * view.w}px`;
    selection.style.height = `${rect.h * view.h}px`;
  }

  /* ── 指针交互 ───────────────────────────────────────── */

  function pointerPos(ev) {
    const box = canvas.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(box.width, ev.clientX - box.left)) / box.width,
      y: Math.max(0, Math.min(box.height, ev.clientY - box.top)) / box.height,
    };
  }

  function handleAt(pt) {
    // 命中判断用 CSS 像素，因为 HANDLE_HIT 是按手指大小定的
    const px = pt.x * view.w;
    const py = pt.y * view.h;
    const left = rect.x * view.w;
    const top = rect.y * view.h;
    const right = (rect.x + rect.w) * view.w;
    const bottom = (rect.y + rect.h) * view.h;

    const near = (x, y) => Math.abs(px - x) <= HANDLE_HIT && Math.abs(py - y) <= HANDLE_HIT;
    if (near(left, top)) return 'nw';
    if (near(right, top)) return 'ne';
    if (near(left, bottom)) return 'sw';
    if (near(right, bottom)) return 'se';

    const inside = px >= left && px <= right && py >= top && py <= bottom;
    return inside ? 'move' : null;
  }

  function onDown(ev) {
    const pt = pointerPos(ev);
    const mode = handleAt(pt);
    if (!mode) return;
    ev.preventDefault();
    drag = { mode, start: pt, origin: { ...rect } };
    canvas.setPointerCapture?.(ev.pointerId);
  }

  function onMove(ev) {
    if (!drag) return;
    ev.preventDefault();
    const pt = pointerPos(ev);
    const dx = pt.x - drag.start.x;
    const dy = pt.y - drag.start.y;
    const o = drag.origin;

    if (drag.mode === 'move') {
      // 移动整框，并夹在画布内
      rect = {
        ...o,
        x: Math.max(0, Math.min(1 - o.w, o.x + dx)),
        y: Math.max(0, Math.min(1 - o.h, o.y + dy)),
      };
    } else {
      const minW = MIN_SIDE / view.w;
      const minH = MIN_SIDE / view.h;
      let { x, y, w, h } = o;
      // 拖角只改动与该角相邻的两条边，对边保持不动
      if (drag.mode.includes('w')) {
        const nx = Math.max(0, Math.min(o.x + o.w - minW, o.x + dx));
        w = o.x + o.w - nx;
        x = nx;
      }
      if (drag.mode.includes('e')) {
        w = Math.max(minW, Math.min(1 - o.x, o.w + dx));
      }
      if (drag.mode.includes('n')) {
        const ny = Math.max(0, Math.min(o.y + o.h - minH, o.y + dy));
        h = o.y + o.h - ny;
        y = ny;
      }
      if (drag.mode.includes('s')) {
        h = Math.max(minH, Math.min(1 - o.y, o.h + dy));
      }
      rect = { x, y, w, h };
    }
    drawSelection();
  }

  function onUp(ev) {
    if (!drag) return;
    drag = null;
    canvas.releasePointerCapture?.(ev.pointerId);
  }

  /* ── 产出裁好的图片 ─────────────────────────────────── */

  async function apply() {
    if (!bitmap) return;
    errP.textContent = '';

    // 从「比例选框」换算回位图像素
    const sx = Math.round(rect.x * bitmap.width);
    const sy = Math.round(rect.y * bitmap.height);
    const sw = Math.max(1, Math.round(rect.w * bitmap.width));
    const sh = Math.max(1, Math.round(rect.h * bitmap.height));

    const out = document.createElement('canvas');
    out.width = sw;
    out.height = sh;
    out.getContext('2d').drawImage(bitmap, sx, sy, sw, sh, 0, 0, sw, sh);

    const blob = await new Promise((r) => out.toBlob(r, 'image/jpeg', 0.92));
    if (!blob) {
      errP.textContent = '裁剪失败，请重试。';
      return;
    }
    close();
    onDone?.(blob, { width: sw, height: sh });
  }

  /* ── 事件绑定 ───────────────────────────────────────── */

  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  // 悬停时给出手势提示，键盘/鼠标用户才知道角上能拖
  canvas.addEventListener('pointermove', (ev) => {
    if (drag) return;
    const mode = handleAt(pointerPos(ev));
    canvas.style.cursor =
      mode === 'move' ? 'move'
        : mode === 'nw' || mode === 'se' ? 'nwse-resize'
          : mode === 'ne' || mode === 'sw' ? 'nesw-resize'
            : 'default';
  });

  // 双指缩放最容易误触页面缩放，统一阻止
  stage.addEventListener('touchmove', (e) => e.preventDefault(), { passive: false });

  document.getElementById('crop-close').addEventListener('click', close);
  document.getElementById('crop-cancel').addEventListener('click', close);
  document.getElementById('crop-apply').addEventListener('click', apply);
  document.getElementById('crop-full').addEventListener('click', () => {
    rect = { x: 0, y: 0, w: 1, h: 1 };
    drawSelection();
  });
  document.getElementById('crop-reset').addEventListener('click', () => {
    rect = { x: 0, y: 0, w: 1, h: 1 };
    drawSelection();
  });
  document.getElementById('crop-rotate').addEventListener('click', async () => {
    rotation = (rotation + 90) % 360;
    rect = { x: 0, y: 0, w: 1, h: 1 };
    await rebuild();
  });
  modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

  window.addEventListener('resize', () => {
    if (!modal.classList.contains('hidden')) rebuild();
  });

  return { open, close };
}