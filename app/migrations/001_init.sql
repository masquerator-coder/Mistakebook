-- 001_init.sql —— 核心业务表
-- 说明：本文件由 db.run_migrations() 按文件名顺序执行，已执行过的会跳过。
-- 全部语句幂等（IF NOT EXISTS / DO $$ 块），重复执行安全。

-- 科目：预置语文/数学/英语等，可增删
CREATE TABLE IF NOT EXISTS subject (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  sort_order  INT  NOT NULL DEFAULT 0
);

-- 知识点：树形，按科目归属。例：数学 > 一元二次方程 > 判别式
CREATE TABLE IF NOT EXISTS knowledge_point (
  id          SERIAL PRIMARY KEY,
  subject_id  INT  NOT NULL REFERENCES subject(id) ON DELETE CASCADE,
  parent_id   INT  REFERENCES knowledge_point(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_id, parent_id, name)
);
CREATE INDEX IF NOT EXISTS idx_kp_subject ON knowledge_point (subject_id);
CREATE INDEX IF NOT EXISTS idx_kp_parent ON knowledge_point (parent_id);

-- 试卷 / 作业批次（功能 1 的「每次」）
CREATE TABLE IF NOT EXISTS paper (
  id         BIGSERIAL PRIMARY KEY,
  subject_id INT NOT NULL REFERENCES subject(id),
  title      TEXT NOT NULL,
  done_on    DATE NOT NULL,
  score      NUMERIC(5,1),
  full_score NUMERIC(5,1),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_paper_subject ON paper (subject_id, done_on DESC);

-- 掌握状态枚举
DO $$ BEGIN
  CREATE TYPE mastery_status AS ENUM ('new', 'reviewing', 'mastered');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 错题主体
CREATE TABLE IF NOT EXISTS mistake (
  id             BIGSERIAL PRIMARY KEY,
  subject_id     INT  NOT NULL REFERENCES subject(id),
  image_path     TEXT,
  stem_text      TEXT,
  stem_source    TEXT NOT NULL DEFAULT 'typed',
  answer_text    TEXT,
  student_answer TEXT,
  wrong_reason   TEXT,
  note           TEXT,
  mastery        mastery_status NOT NULL DEFAULT 'new',
  error_count    INT  NOT NULL DEFAULT 1,
  correct_streak INT  NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_review_at TIMESTAMPTZ,
  next_review_at TIMESTAMPTZ,
  interval_days  INT NOT NULL DEFAULT 1,
  deleted_at     TIMESTAMPTZ,
  paper_id       BIGINT REFERENCES paper(id) ON DELETE SET NULL
);
-- 软删除：所有查询都要带 deleted_at IS NULL，索引也按此建部分索引
CREATE INDEX IF NOT EXISTS idx_mistake_subject_next
  ON mistake (subject_id, next_review_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mistake_mastery
  ON mistake (mastery) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mistake_next_review
  ON mistake (next_review_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_mistake_paper
  ON mistake (paper_id) WHERE deleted_at IS NULL;

-- 错题 ↔ 知识点：多对多
CREATE TABLE IF NOT EXISTS mistake_kp (
  mistake_id BIGINT NOT NULL REFERENCES mistake(id) ON DELETE CASCADE,
  kp_id      INT    NOT NULL REFERENCES knowledge_point(id) ON DELETE CASCADE,
  PRIMARY KEY (mistake_id, kp_id)
);
CREATE INDEX IF NOT EXISTS idx_mistake_kp_kp ON mistake_kp (kp_id);

-- 复习作答记录
CREATE TABLE IF NOT EXISTS review_log (
  id           BIGSERIAL PRIMARY KEY,
  mistake_id   BIGINT NOT NULL REFERENCES mistake(id) ON DELETE CASCADE,
  reviewed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  result       TEXT NOT NULL,
  duration_sec INT,
  note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_log_mistake ON review_log (mistake_id, reviewed_at DESC);

-- AI 产物类型
DO $$ BEGIN
  CREATE TYPE ai_kind AS ENUM ('explain', 'analyze_mistake', 'analyze_period', 'plan', 'read_question');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- AI 生成内容缓存
CREATE TABLE IF NOT EXISTS ai_artifact (
  id          BIGSERIAL PRIMARY KEY,
  kind        ai_kind NOT NULL,
  input_hash  TEXT NOT NULL,
  mistake_id  BIGINT REFERENCES mistake(id) ON DELETE CASCADE,
  payload     JSONB NOT NULL,
  model       TEXT NOT NULL,
  tokens_in   INT,
  tokens_out  INT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (kind, input_hash)
);

-- 复习计划
CREATE TABLE IF NOT EXISTS study_plan (
  id             BIGSERIAL PRIMARY KEY,
  generated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  period_start   DATE NOT NULL,
  period_end     DATE NOT NULL,
  payload        JSONB NOT NULL,
  ai_artifact_id BIGINT REFERENCES ai_artifact(id)
);

-- AI 调用成本流水
CREATE TABLE IF NOT EXISTS ai_usage (
  id         BIGSERIAL PRIMARY KEY,
  kind       ai_kind NOT NULL,
  model      TEXT NOT NULL,
  tokens_in  INT NOT NULL DEFAULT 0,
  tokens_out INT NOT NULL DEFAULT 0,
  cost_cny   NUMERIC(10,4) NOT NULL DEFAULT 0,
  ok         BOOLEAN NOT NULL,
  error      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_created ON ai_usage (created_at DESC);

-- 预置科目
INSERT INTO subject (name, sort_order) VALUES
  ('语文', 10), ('数学', 20), ('英语', 30), ('物理', 40), ('化学', 50),
  ('生物', 60), ('历史', 70), ('地理', 80), ('道德与法治', 90)
ON CONFLICT (name) DO NOTHING;