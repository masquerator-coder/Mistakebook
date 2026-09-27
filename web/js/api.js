/* API 客户端。所有网络请求都经过这里，好处是错误处理只有一处。
   错误形状由后端统一为 {detail:{error:{code,message}}}。 */

const TIMEOUT_MS = 60000;

export class ApiError extends Error {
  constructor(message, code, status) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

/* 服务器没响应（电脑关机 / Docker 没跑）与「服务器返回了错误」是两回事，
   必须分开，否则会把「连不上」显示成「请求失败」，家长无从排查。 */
export class OfflineError extends Error {
  constructor(message = '连不上服务器') {
    super(message);
    this.name = 'OfflineError';
  }
}

async function request(path, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), options.timeout ?? TIMEOUT_MS);

  let resp;
  try {
    resp = await fetch(path, { ...options, signal: ctrl.signal });
  } catch (e) {
    throw new OfflineError();
  } finally {
    clearTimeout(timer);
  }

  if (resp.status === 204) return null;

  const text = await resp.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { /* 非 JSON 响应 */ }
  }

  if (!resp.ok) {
    const err = data?.detail?.error;
    let msg = err?.message;
    if (!msg) {
      if (Array.isArray(data?.detail)) {
        // FastAPI 的参数校验错误
        msg = data.detail.map(d => `${d.loc?.slice(1).join('.')}: ${d.msg}`).join('；');
      } else if (typeof data?.detail === 'string') {
        msg = data.detail;
      } else {
        msg = `请求失败（HTTP ${resp.status}）`;
      }
    }
    throw new ApiError(msg, err?.code ?? 'http_error', resp.status);
  }

  return data;
}

const json = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  subjects: () => request('/api/subjects'),

  kps: (subjectId, grade) => {
    const q = new URLSearchParams();
    if (grade) q.set('grade', grade);
    const s = q.toString();
    return request(`/api/subjects/${subjectId}/kps${s ? '?' + s : ''}`);
  },

  kpTree: (subjectId, grade) => {
    const q = new URLSearchParams();
    if (grade) q.set('grade', grade);
    const s = q.toString();
    return request(`/api/subjects/${subjectId}/kp-tree${s ? '?' + s : ''}`);
  },

  createKp: (payload) => request('/api/kps', json('POST', payload)),
  updateKp: (id, payload) => request(`/api/kps/${id}`, json('PATCH', payload)),

  mistakes: (filters = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v !== '' && v !== null && v !== undefined) q.set(k, v);
    }
    const s = q.toString();
    return request(`/api/mistakes${s ? '?' + s : ''}`);
  },

  mistake: (id) => request(`/api/mistakes/${id}`),
  createMistake: (payload) => request('/api/mistakes', json('POST', payload)),
  updateMistake: (id, payload) => request(`/api/mistakes/${id}`, json('PATCH', payload)),
  deleteMistake: (id) => request(`/api/mistakes/${id}`, { method: 'DELETE' }),
  restoreMistake: (id) => request(`/api/mistakes/${id}/restore`, { method: 'POST' }),

  dueMistakes: (limit = 50) => request(`/api/mistakes/due?limit=${limit}`),
  reviews: (id) => request(`/api/mistakes/${id}/reviews`),
  submitReview: (id, payload) => request(`/api/mistakes/${id}/review`, json('POST', payload)),

  upload: async (file) => {
    const fd = new FormData();
    fd.append('file', file);
    return request('/api/uploads', { method: 'POST', body: fd, timeout: 120000 });
  },

  readQuestion: (imagePath) =>
    request('/api/ai/read-question', { ...json('POST', { image_path: imagePath }), timeout: 180000 }),

  explain: (mistakeId, force = false) =>
    request('/api/ai/explain', { ...json('POST', { mistake_id: mistakeId, force }), timeout: 180000 }),

  analyzeMistake: (id) => request(`/api/ai/analyze-mistake/${id}`, { method: 'POST' }),

  subjectAnalysis: (subjectId, grade) => {
    const q = new URLSearchParams();
    if (grade) q.set('grade', grade);
    const s = q.toString();
    return request(`/api/ai/analysis/subject/${subjectId}${s ? '?' + s : ''}`);
  },

  overview: () => request('/api/ai/analysis/overview'),

  generatePlan: (days = 7) =>
    request(`/api/plan/generate?days=${days}`, { method: 'POST', timeout: 240000 }),
  latestPlan: () => request('/api/plan/latest'),
};