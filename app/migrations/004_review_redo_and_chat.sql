-- 004_review_redo_and_chat.sql —— 复习改判 + 讲解追问
--
-- 背景（对应两条用户反馈）：
--   1. 「做对了/半对/又错了」每点一次就新增一条记录，误触会让统计失真。
--      对策是宽限期内**改判**：删除上一次那条记录、撤回它的 SRS 推进、按新结果重算。
--      要撤回推进，就必须知道那一次复习之前的间隔，因此加 interval_before。
--   2. 讲解之后孩子还想追问，需要按消息留存的对话。

ALTER TABLE review_log ADD COLUMN IF NOT EXISTS interval_before INT;

-- 宽限期判断要取「该题最新一条记录」，按 (mistake_id, reviewed_at DESC) 走索引即可；
-- 同一毫秒内的两条记录靠 id 兜底排序，因此索引带上 id。
DROP INDEX IF EXISTS idx_review_log_mistake;
CREATE INDEX IF NOT EXISTS idx_review_log_mistake
  ON review_log (mistake_id, reviewed_at DESC, id DESC);

-- 历史数据没有 interval_before，用当前间隔兜底只能算近似；
-- 留 NULL 并在改判时按 INITIAL_INTERVAL_DAYS 处理 —— 宁可少还原一次间隔，
-- 也不要凭空写一个看起来精确、实际是猜的值。
COMMENT ON COLUMN review_log.interval_before IS
  '这次复习之前的 interval_days，用于宽限期内改判时撤回 SRS 推进；老数据为 NULL';

-- ── AI 讲解追问 ─────────────────────────────────────────────────────
-- 一条错题对应一个会话（不需要多会话：孩子问的就是这道题）。
CREATE TABLE IF NOT EXISTS explain_chat (
  id          BIGSERIAL PRIMARY KEY,
  mistake_id  BIGINT NOT NULL REFERENCES mistake(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_explain_chat_mistake ON explain_chat (mistake_id);

CREATE TABLE IF NOT EXISTS explain_message (
  id         BIGSERIAL PRIMARY KEY,
  chat_id    BIGINT NOT NULL REFERENCES explain_chat(id) ON DELETE CASCADE,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_explain_message_chat
  ON explain_message (chat_id, created_at, id);

-- ── ai_usage.kind 新增两种调用类型 ──────────────────────────────────
-- AI 自动归类（categorize）与讲解追问（explain_chat）。
-- 不加这两个值的话，这两条链路调用模型时会因为枚举不含该值而写流水失败 ——
-- 流水失败被 _log_usage 全捕获了，所以**不会报错**，只会静默丢掉成本记录，
-- 后台的用量页从此少算一部分调用。
ALTER TYPE ai_kind ADD VALUE IF NOT EXISTS 'categorize';
ALTER TYPE ai_kind ADD VALUE IF NOT EXISTS 'explain_chat';