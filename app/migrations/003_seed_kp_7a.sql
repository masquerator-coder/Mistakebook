-- 003_seed_kp_7a.sql —— 七年级上册知识点骨架（数学/语文/英语）
--
-- 说明：这是**课标级公共信息**的知识点骨架 + 教材页码定位，
-- 不含任何教材原文，可安全入库与分发（见 spec §12.4 版权边界）。
-- 页码按人教版七年级上册常规教材编排，供定位参考，允许在后台修正。
--
-- 用 ON CONFLICT DO NOTHING 保证可重复执行。

-- ── 数学 七上 ────────────────────────────────────────────────
WITH s AS (SELECT id FROM subject WHERE name = '数学')
INSERT INTO knowledge_point (subject_id, parent_id, name, code, grade, book, page_from, page_to, source, confidence, reviewed, sort_order)
SELECT s.id, NULL, v.name, v.code, '七上', '人教版', v.pf, v.pt, 'manual', 1.0, true, v.so
FROM s, (VALUES
  ('有理数',              'math7a.rational',        1,  45,  10),
  ('整式的加减',          'math7a.polynomial',     53,  76,  20),
  ('一元一次方程',        'math7a.linear1',        77, 112,  30),
  ('几何图形初步',        'math7a.geometry',      113, 147,  40)
) AS v(name, code, pf, pt, so)
ON CONFLICT (subject_id, parent_id, name) DO NOTHING;

WITH s AS (SELECT id FROM subject WHERE name = '数学'),
     p AS (SELECT id, code FROM knowledge_point WHERE code IN ('math7a.rational','math7a.polynomial','math7a.linear1','math7a.geometry'))
INSERT INTO knowledge_point (subject_id, parent_id, name, code, grade, book, page_from, page_to, source, confidence, reviewed, sort_order)
SELECT s.id, p.id, v.name, v.code, '七上', '人教版', v.pf, v.pt, 'manual', 1.0, true, v.so
FROM s, p, (VALUES
  ('math7a.rational',   '正数和负数',        'math7a.rational.neg',    2,   4,  11),
  ('math7a.rational',   '有理数的加减法',    'math7a.rational.add',   21,  30,  12),
  ('math7a.rational',   '有理数的乘除法',    'math7a.rational.mul',   31,  38,  13),
  ('math7a.rational',   '有理数的乘方',      'math7a.rational.pow',   41,  45,  14),
  ('math7a.polynomial', '整式',              'math7a.poly.mono',      54,  59,  21),
  ('math7a.polynomial', '整式的加减',        'math7a.poly.addsub',    62,  70,  22),
  ('math7a.linear1',    '从算式到方程',      'math7a.linear1.setup',  78,  83,  31),
  ('math7a.linear1',    '解一元一次方程',    'math7a.linear1.solve',  85,  98,  32),
  ('math7a.linear1',    '实际问题与方程',    'math7a.linear1.apply', 100, 112,  33),
  ('math7a.geometry',   '几何图形',          'math7a.geo.shape',     114, 120,  41),
  ('math7a.geometry',   '直线射线线段',      'math7a.geo.line',      125, 132,  42),
  ('math7a.geometry',   '角',                'math7a.geo.angle',     134, 147,  43)
) AS v(pcode, name, code, pf, pt, so)
WHERE p.code = v.pcode
ON CONFLICT (subject_id, parent_id, name) DO NOTHING;

-- ── 语文 七上 ────────────────────────────────────────────────
WITH s AS (SELECT id FROM subject WHERE name = '语文')
INSERT INTO knowledge_point (subject_id, parent_id, name, code, grade, book, page_from, page_to, source, confidence, reviewed, sort_order)
SELECT s.id, NULL, v.name, v.code, '七上', '统编版', v.pf, v.pt, 'manual', 1.0, true, v.so
FROM s, (VALUES
  ('现代文阅读',   'chn7a.modern',    1,  80,  10),
  ('古诗文',       'chn7a.classic',  81, 130,  20),
  ('写作',         'chn7a.writing', 131, 170,  30),
  ('语言基础',     'chn7a.basic',   171, 200,  40)
) AS v(name, code, pf, pt, so)
ON CONFLICT (subject_id, parent_id, name) DO NOTHING;

WITH s AS (SELECT id FROM subject WHERE name = '语文'),
     p AS (SELECT id, code FROM knowledge_point WHERE code IN ('chn7a.modern','chn7a.classic','chn7a.writing','chn7a.basic'))
INSERT INTO knowledge_point (subject_id, parent_id, name, code, grade, book, page_from, page_to, source, confidence, reviewed, sort_order)
SELECT s.id, p.id, v.name, v.code, '七上', '统编版', v.pf, v.pt, 'manual', 1.0, true, v.so
FROM s, p, (VALUES
  ('chn7a.modern',  '记叙文阅读',      'chn7a.modern.narrative',  1,  40,  11),
  ('chn7a.modern',  '散文阅读',        'chn7a.modern.prose',     41,  80,  12),
  ('chn7a.classic', '文言文阅读',      'chn7a.classic.wenyan',   81, 105,  21),
  ('chn7a.classic', '古诗词鉴赏',      'chn7a.classic.poem',    106, 130,  22),
  ('chn7a.basic',   '字音字形',        'chn7a.basic.zi',        171, 180,  41),
  ('chn7a.basic',   '病句辨析与修改',  'chn7a.basic.bingju',    181, 188,  42),
  ('chn7a.basic',   '标点符号',        'chn7a.basic.biaodian',  189, 193,  43),
  ('chn7a.basic',   '词语成语运用',    'chn7a.basic.ciyu',      194, 200,  44)
) AS v(pcode, name, code, pf, pt, so)
WHERE p.code = v.pcode
ON CONFLICT (subject_id, parent_id, name) DO NOTHING;

-- ── 英语 七上 ────────────────────────────────────────────────
WITH s AS (SELECT id FROM subject WHERE name = '英语')
INSERT INTO knowledge_point (subject_id, parent_id, name, code, grade, book, page_from, page_to, source, confidence, reviewed, sort_order)
SELECT s.id, NULL, v.name, v.code, '七上', '外研版', v.pf, v.pt, 'manual', 1.0, true, v.so
FROM s, (VALUES
  ('语法',       'eng7a.grammar',   1, 120, 10),
  ('词汇',       'eng7a.vocab',   121, 160, 20),
  ('听说读写',   'eng7a.skills',  161, 200, 30)
) AS v(name, code, pf, pt, so)
ON CONFLICT (subject_id, parent_id, name) DO NOTHING;

WITH s AS (SELECT id FROM subject WHERE name = '英语'),
     p AS (SELECT id, code FROM knowledge_point WHERE code IN ('eng7a.grammar','eng7a.vocab','eng7a.skills'))
INSERT INTO knowledge_point (subject_id, parent_id, name, code, grade, book, page_from, page_to, source, confidence, reviewed, sort_order)
SELECT s.id, p.id, v.name, v.code, '七上', '外研版', v.pf, v.pt, 'manual', 1.0, true, v.so
FROM s, p, (VALUES
  ('eng7a.grammar', 'be 动词',           'eng7a.grammar.be',      1,  20,  11),
  ('eng7a.grammar', '一般现在时',        'eng7a.grammar.present', 21,  50,  12),
  ('eng7a.grammar', '名词单复数',        'eng7a.grammar.plural',  51,  70,  13),
  ('eng7a.grammar', '代词',              'eng7a.grammar.pron',    71, 120,  14),
  ('eng7a.vocab',   'Starter 词汇',      'eng7a.vocab.starter',   1,  40,  21),
  ('eng7a.vocab',   'Unit 1-5 词汇',     'eng7a.vocab.u1_5',     41, 100,  22),
  ('eng7a.skills',  '听力理解',          'eng7a.skills.listen',    1,  70,  31),
  ('eng7a.skills',  '阅读理解',          'eng7a.skills.read',     71, 140,  32),
  ('eng7a.skills',  '书面表达',          'eng7a.skills.write',   141, 200,  33)
) AS v(pcode, name, code, pf, pt, so)
WHERE p.code = v.pcode
ON CONFLICT (subject_id, parent_id, name) DO NOTHING;