-- 002_knowledge_base.sql —— 知识库：知识点骨架 + 教材资源登记
-- 注意：知识点的 grade 字段是「七上」这类学段标识，覆盖统计按 grade 过滤。

ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS code       TEXT;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS grade      TEXT;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS book       TEXT;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS page_from  INT;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS page_to    INT;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS source     TEXT;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS confidence REAL;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS reviewed   BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE knowledge_point ADD COLUMN IF NOT EXISTS sort_order INT NOT NULL DEFAULT 0;

-- code 在同一科目内唯一（唯一索引而非约束，便于日后放宽）
CREATE UNIQUE INDEX IF NOT EXISTS idx_kp_code
  ON knowledge_point (subject_id, code) WHERE code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kp_grade ON knowledge_point (subject_id, grade);
CREATE INDEX IF NOT EXISTS idx_kp_reviewed ON knowledge_point (reviewed) WHERE reviewed = false;

-- 教材资源登记表：只登记元数据，PDF 本身不入库、不进版本库（见 spec §12.4 版权边界）
CREATE TABLE IF NOT EXISTS book_resource (
  id         SERIAL PRIMARY KEY,
  subject_id INT NOT NULL REFERENCES subject(id),
  grade      TEXT NOT NULL,
  book       TEXT NOT NULL,
  title      TEXT NOT NULL,
  file_path  TEXT NOT NULL,
  has_text   BOOLEAN NOT NULL,
  pages      INT,
  added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (subject_id, grade, title)
);
CREATE INDEX IF NOT EXISTS idx_book_subject_grade ON book_resource (subject_id, grade);