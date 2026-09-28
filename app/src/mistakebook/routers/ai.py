"""AI 相关端点：读题、讲解、单题诊断、阶段归纳。

缓存策略：ai_artifact 以 (kind, input_hash) 唯一。同一道题重复点「讲解」
不会重复烧 token —— 这在孩子反复点按钮时很关键。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter

from .. import db
from ..config import settings
from ..errors import AiError, NotFoundError, ValidationError
from ..models import (
    AiJobOut,
    ChatAskIn,
    ChatMessageOut,
    ChatOut,
    ExplainIn,
    ReadQuestionIn,
    ReadQuestionOut,
    SuggestKpsIn,
    SuggestKpsOut,
)
from ..services import analysis, deepseek, prompts

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str).encode())
    return h.hexdigest()[:40]


def _cached(kind: str, input_hash: str) -> dict | None:
    row = db.fetchrow(
        "SELECT payload FROM ai_artifact WHERE kind = %s AND input_hash = %s",
        (kind, input_hash),
    )
    return row["payload"] if row else None


def _store(kind: str, input_hash: str, payload: dict, mistake_id: int | None = None) -> None:
    db.execute(
        """
        INSERT INTO ai_artifact (kind, input_hash, mistake_id, payload, model)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (kind, input_hash) DO UPDATE SET payload = EXCLUDED.payload
        """,
        (kind, input_hash, mistake_id, json.dumps(payload, ensure_ascii=False), settings.deepseek_model),
    )


_READ_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "stem_text": {"type": "string"},
        "answer_text": {"type": "string"},
        "student_answer": {"type": "string"},
        "wrong_reason": {"type": "string"},
        "kp_suggestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["stem_text"],
}


@router.post("/read-question", response_model=ReadQuestionOut)
async def read_question(payload: ReadQuestionIn) -> dict:
    """把题目照片转成结构化题干。识别结果必须经用户确认后才落库。"""
    from ..services import imaging

    try:
        abs_path = imaging.resolve_image(payload.image_path)
    except imaging.InvalidImagePath as e:
        raise ValidationError(str(e), code="bad_image_path") from e
    if not abs_path.is_file():
        raise NotFoundError("题目图片不存在")

    blocks = [
        deepseek.image_block(abs_path),
        deepseek.text_block(
            "请识别这张作业/试卷照片，按约定 JSON 输出。\n"
            f"可用 JSON 结构：{json.dumps(_READ_QUESTION_SCHEMA, ensure_ascii=False)}"
        ),
    ]
    result = await deepseek.chat_json(prompts.READ_QUESTION_SYSTEM, blocks, "read_question")

    reason = (result.get("wrong_reason") or "").strip()
    if reason not in (
        "concept", "calculation", "misread", "method", "careless", "time", "unknown"
    ):
        reason = "unknown"

    kps = result.get("kp_suggestions") or []
    if not isinstance(kps, list):
        kps = []

    return {
        "stem_text": (result.get("stem_text") or "").strip(),
        "answer_text": (result.get("answer_text") or "").strip(),
        "wrong_reason_suggestion": reason,
        "kp_suggestions": [str(k) for k in kps][:3],
        "raw_text": (result.get("student_answer") or "").strip(),
    }


def _explain_hash(row: dict, kp_names: list[str]) -> str:
    """讲解缓存的指纹。生成与查缓存两处必须用同一份算法。

    知识点名称参与指纹，因此必须排序：否则同一组知识点换个返回顺序
    就会算出不同的 hash，导致缓存白白失效、重复烧 token。
    """
    return _hash("explain", row["stem_text"], row["answer_text"],
                 row["student_answer"], row["wrong_reason"], sorted(kp_names))


@router.get("/explain/{mistake_id}/cached", response_model=AiJobOut)
def explain_cached(mistake_id: int) -> dict:
    """**只查缓存**，绝不调模型。

    存在的理由：详情页希望在打开时就把已经生成过的讲解显示出来，
    但 `POST /explain` 在不带 force 时也会在**缓存未命中时真的调用模型**。
    若前端用 POST 来做这件事，每次打开一道没讲解过的题都会烧一次 token，
    而且在孩子没要求讲解的情况下弹出一段十几秒的等待。

    因此把「查缓存」和「生成讲解」拆成两个端点，语义明确不会误用。
    """
    row = db.fetchrow(
        """
        SELECT stem_text, answer_text, student_answer, wrong_reason
        FROM mistake WHERE id = %s AND deleted_at IS NULL
        """,
        (mistake_id,),
    )
    if row is None:
        raise NotFoundError("错题不存在或已删除")

    kp_names = db.fetch(
        """
        SELECT kp.name FROM mistake_kp mk
        JOIN knowledge_point kp ON kp.id = mk.kp_id
        WHERE mk.mistake_id = %s ORDER BY kp.id
        """,
        (mistake_id,),
    )

    # 指纹算法必须与 explain() 完全一致，否则永远查不到缓存 ——
    # 两边共用一个 _explain_hash，避免改漏一处。
    input_hash = _explain_hash(row, [k["name"] for k in kp_names])
    hit = _cached("explain", input_hash)
    if hit is None:
        return {"status": "pending", "payload": None}
    return {"status": "done", "payload": hit}


@router.post("/explain", response_model=AiJobOut)
async def explain(payload: ExplainIn) -> dict:
    row = db.fetchrow(
        """
        SELECT id, stem_text, image_path, answer_text, student_answer, wrong_reason, note
        FROM mistake WHERE id = %s AND deleted_at IS NULL
        """,
        (payload.mistake_id,),
    )
    if row is None:
        raise NotFoundError("错题不存在或已删除")

    kps = db.fetch(
        """
        SELECT kp.name FROM mistake_kp mk
        JOIN knowledge_point kp ON kp.id = mk.kp_id
        WHERE mk.mistake_id = %s
        ORDER BY kp.id
        """,
        (payload.mistake_id,),
    )
    kp_names = [k["name"] for k in kps]

    input_hash = _explain_hash(row, kp_names)
    if not payload.force:
        hit = _cached("explain", input_hash)
        if hit is not None:
            return {"status": "done", "payload": hit}

    parts: list[str] = []
    if row["stem_text"]:
        parts.append(f"题目：{row['stem_text']}")
    if row["answer_text"]:
        parts.append(f"正确答案：{row['answer_text']}")
    if row["student_answer"]:
        parts.append(f"学生的错误答案：{row['student_answer']}")
    if row["wrong_reason"]:
        parts.append(f"错因分类：{row['wrong_reason']}")
    if kp_names:
        parts.append(f"涉及知识点：{'、'.join(kp_names)}")

    blocks: list[dict[str, Any]] = []
    # 有图就带上图：题目里的图形、表格、几何图形靠文字描述不出来
    if row["image_path"]:
        try:
            from ..services import imaging

            p = imaging.resolve_image(row["image_path"])
            if p.is_file():
                blocks.append(deepseek.image_block(p))
        except imaging.InvalidImagePath:
            log.warning("错题 %s 的图片路径非法，跳过图片", row["id"])

    blocks.append(
        deepseek.text_block(
            "\n".join(parts) if parts else "（这道题只有图片，没有文字信息，请看图讲解）"
        )
    )

    result = await deepseek.chat_json(prompts.EXPLAIN_SYSTEM, blocks, "explain", max_tokens=3072)
    _store("explain", input_hash, result, mistake_id=row["id"])
    return {"status": "done", "payload": result}


@router.post("/analyze-mistake/{mistake_id}", response_model=AiJobOut)
async def analyze_mistake(mistake_id: int) -> dict:
    row = db.fetchrow(
        """
        SELECT id, stem_text, wrong_reason, error_count FROM mistake
        WHERE id = %s AND deleted_at IS NULL
        """,
        (mistake_id,),
    )
    if row is None:
        raise NotFoundError("错题不存在或已删除")

    # error_count 必须进哈希：下面的 prompt 里用到了它，而「错 1 次」和
    # 「错 5 次」应当得到不同的诊断。指纹漏掉它会导致第二次命中缓存后
    # 永久返回第一次的过期诊断。
    input_hash = _hash(
        "analyze_mistake", row["stem_text"], row["wrong_reason"], row["error_count"]
    )
    hit = _cached("analyze_mistake", input_hash)
    if hit is not None:
        return {"status": "done", "payload": hit}

    blocks = [deepseek.text_block(
        f"题目：{row['stem_text'] or '（仅图片）'}\n"
        f"错因：{row['wrong_reason'] or 'unknown'}\n"
        f"累计错次：{row['error_count']}"
    )]
    result = await deepseek.chat_json(prompts.ANALYZE_MISTAKE_SYSTEM, blocks, "analyze_mistake")
    _store("analyze_mistake", input_hash, result, mistake_id=mistake_id)
    return {"status": "done", "payload": result}


@router.get("/analysis/subject/{subject_id}")
def subject_analysis(subject_id: int, grade: str | None = None) -> dict:
    """科目学情分析。统计全由 SQL 完成，不调模型。"""
    if db.fetchval("SELECT 1 FROM subject WHERE id = %s", (subject_id,)) is None:
        raise NotFoundError("科目不存在")
    summary = analysis.coverage_summary(subject_id, grade)
    trend = analysis.review_trend(subject_id)
    return {
        "coverage": summary,
        "weak_points": analysis.weak_knowledge_points(subject_id, grade),
        "trend": trend,
    }


@router.get("/analysis/overview")
def overview() -> dict:
    return analysis.mistake_stats()


# ── 讲解追问：聊天式多轮对话 ──────────────────────────────────────────
#
# 设计取舍：**不做流式输出**。chat_text 是一次性返回的，追加 SSE/WebSocket
# 会让这个无构建的原生前端复杂一大截，而单条回答在十几秒内就能返回，
# 前端用「老师正在想…」占位即可。真要改成流式时，这个端点是唯一改动点。
#
# 上下文只保留最近 MAX_HISTORY 轮：模型窗口有限，且越早的对话与当前
# 追问的相关性越低。截断**从最旧的一侧**丢，保留最近的上下文。

MAX_HISTORY = 12          # 最多带 12 条历史消息（约 6 轮问答）
MAX_QUESTION_CHARS = 500  # 单条提问长度上限


def _get_chat(cur_or_none, mistake_id: int) -> int:
    """取该错题的会话 id，没有就建一个。

    一条错题一个会话，不提供多会话 —— 孩子问的就是这道题，
    多会话只会让他找不到上次问到哪。
    """
    row = db.fetchrow("SELECT id FROM explain_chat WHERE mistake_id = %s", (mistake_id,))
    if row:
        return row["id"]
    created = db.execute_returning(
        "INSERT INTO explain_chat (mistake_id) VALUES (%s) RETURNING id", (mistake_id,)
    )
    return created["id"]


def _mistake_context(mistake_id: int) -> dict:
    row = db.fetchrow(
        """
        SELECT id, stem_text, image_path, answer_text, student_answer, wrong_reason
        FROM mistake WHERE id = %s AND deleted_at IS NULL
        """,
        (mistake_id,),
    )
    if row is None:
        raise NotFoundError("错题不存在或已删除")
    return row


@router.get("/explain/{mistake_id}/chat", response_model=ChatOut)
def get_chat(mistake_id: int) -> dict:
    """取这道题的追问记录。没聊过时返回空列表，不建会话。"""
    _mistake_context(mistake_id)
    row = db.fetchrow("SELECT id FROM explain_chat WHERE mistake_id = %s", (mistake_id,))
    if row is None:
        return {"chat_id": None, "messages": []}
    msgs = db.fetch(
        """
        SELECT id, role, content, created_at
        FROM explain_message WHERE chat_id = %s ORDER BY created_at, id
        """,
        (row["id"],),
    )
    return {"chat_id": row["id"], "messages": msgs}


@router.delete("/explain/{mistake_id}/chat", status_code=204)
def clear_chat(mistake_id: int) -> None:
    """清空追问记录。只删消息，会话行保留（避免 id 反复重建）。"""
    row = db.fetchrow("SELECT id FROM explain_chat WHERE mistake_id = %s", (mistake_id,))
    if row is not None:
        db.execute("DELETE FROM explain_message WHERE chat_id = %s", (row["id"],))


@router.post("/explain/{mistake_id}/chat", response_model=ChatOut)
async def ask_followup(mistake_id: int, payload: ChatAskIn) -> dict:
    """孩子追问一句，AI 回答一句。问答都会落库，下次打开还在。"""
    question = payload.question.strip()
    if not question:
        raise ValidationError("请输入想问的问题")
    if len(question) > MAX_QUESTION_CHARS:
        raise ValidationError(f"问题太长，请控制在 {MAX_QUESTION_CHARS} 字以内")

    m = _mistake_context(mistake_id)
    chat_id = _get_chat(None, mistake_id)

    prev = db.fetch(
        """
        SELECT role, content FROM explain_message
        WHERE chat_id = %s ORDER BY created_at DESC, id DESC LIMIT %s
        """,
        (chat_id, MAX_HISTORY),
    )
    prev.reverse()  # 取的是最近的 N 条，回正后按时间正序拼进对话

    # 题目信息只作为**每次请求都带的系统上下文**，不进可滚动的历史 ——
    # 否则截断历史时可能把题目本身截掉，模型就开始凭空回答。
    context_parts = ["【这道题的背景】"]
    if m["stem_text"]:
        context_parts.append(f"题目：{m['stem_text']}")
    if m["answer_text"]:
        context_parts.append(f"正确答案：{m['answer_text']}")
    if m["student_answer"]:
        context_parts.append(f"学生的错误答案：{m['student_answer']}")
    if not m["stem_text"] and m["image_path"]:
        context_parts.append("（这道题只有图片，没有文字题干）")

    blocks: list[dict[str, Any]] = []
    # 带图：孩子追问经常是「图上这个地方为什么这样」，没有图就答不了
    if m["image_path"]:
        try:
            from ..services import imaging

            p = imaging.resolve_image(m["image_path"])
            if p.is_file():
                blocks.append(deepseek.image_block(p))
        except imaging.InvalidImagePath:
            log.warning("错题 %s 的图片路径非法，追问时不带图", m["id"])

    lines = list(context_parts)
    if prev:
        lines.append("\n【之前的对话】")
        for p in prev:
            who = "学生" if p["role"] == "user" else "你（老师）"
            lines.append(f"{who}：{p['content']}")
    lines.append(f"\n【学生这次的问题】\n{question}")

    blocks.append(deepseek.text_block("\n".join(lines)))

    answer = await deepseek.chat_text(prompts.CHAT_SYSTEM, blocks, "explain_chat", max_tokens=1024)

    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO explain_message (chat_id, role, content) VALUES (%s, 'user', %s)",
            (chat_id, question),
        )
        cur.execute(
            "INSERT INTO explain_message (chat_id, role, content) VALUES (%s, 'assistant', %s)",
            (chat_id, answer),
        )

    msgs = db.fetch(
        """
        SELECT id, role, content, created_at
        FROM explain_message WHERE chat_id = %s ORDER BY created_at, id
        """,
        (chat_id,),
    )
    return {"chat_id": chat_id, "answer": answer, "messages": msgs}


# ── AI 自动归类到知识点 ───────────────────────────────────────────────
#
# 关键约束：模型只能从**该科目已有的知识点**里挑，不能自创名称。
# 自由生成的名字会导致同一知识点每次写法不同，归类结果无法聚合，
# 「按类复习」也就落不了地。因此候选清单由 SQL 给出，模型只做选择题。


def _candidate_kps(subject_id: int, grade: str | None, limit: int = 60) -> list[dict]:
    """候选知识点。优先该年级的，不够再补该科目其他年级的。"""
    rows = db.fetch(
        """
        SELECT id, name, grade, parent_id FROM knowledge_point
        WHERE subject_id = %s AND (%s::text IS NULL OR grade = %s)
        ORDER BY sort_order, id LIMIT %s
        """,
        (subject_id, grade, grade, limit),
    )
    if len(rows) < 5 and grade:
        more = db.fetch(
            """
            SELECT id, name, grade, parent_id FROM knowledge_point
            WHERE subject_id = %s AND (grade IS DISTINCT FROM %s)
            ORDER BY sort_order, id LIMIT %s
            """,
            (subject_id, grade, limit - len(rows)),
        )
        rows.extend(more)
    return rows


@router.post("/suggest-kps", response_model=SuggestKpsOut)
async def suggest_kps(payload: SuggestKpsIn) -> dict:
    """让模型从现有知识点里挑出最匹配的若干个。**不写库**，交给用户确认。"""
    m = db.fetchrow(
        """
        SELECT m.id, m.subject_id, m.stem_text, m.image_path, m.answer_text,
               m.student_answer, m.wrong_reason, s.name AS subject_name
        FROM mistake m JOIN subject s ON s.id = m.subject_id
        WHERE m.id = %s AND m.deleted_at IS NULL
        """,
        (payload.mistake_id,),
    )
    if m is None:
        raise NotFoundError("错题不存在或已删除")

    # 年级取已关联知识点的年级，取不到就不过滤（宁可候选多，不可候选空）
    grade = db.fetchval(
        """
        SELECT kp.grade FROM mistake_kp mk JOIN knowledge_point kp ON kp.id = mk.kp_id
        WHERE mk.mistake_id = %s AND kp.grade IS NOT NULL LIMIT 1
        """,
        (payload.mistake_id,),
    )

    cands = _candidate_kps(m["subject_id"], grade)
    if not cands:
        return {
            "matched": False,
            "picked": [],
            "reason": f"「{m['subject_name']}」还没有知识点，请先在后台补充。",
            "candidates": [],
        }

    # 候选名称清单是模型的「选项」，必须原样回传名称；这里建索引用于校验与回填 id
    by_name = {c["name"]: c for c in cands}
    names = [c["name"] for c in cands]

    parts = [f"科目：{m['subject_name']}"]
    if grade:
        parts.append(f"年级：{grade}")
    if m["stem_text"]:
        parts.append(f"题目：{m['stem_text']}")
    if m["answer_text"]:
        parts.append(f"正确答案：{m['answer_text']}")
    if m["student_answer"]:
        parts.append(f"学生的错误答案：{m['student_answer']}")

    blocks: list[dict[str, Any]] = []
    if m["image_path"]:
        try:
            from ..services import imaging

            p = imaging.resolve_image(m["image_path"])
            if p.is_file():
                blocks.append(deepseek.image_block(p))
        except imaging.InvalidImagePath:
            log.warning("错题 %s 的图片路径非法，归类时不带图", m["id"])

    blocks.append(
        deepseek.text_block(
            "\n".join(parts)
            + "\n\n【候选知识点，只能从这里面选，名称必须原样复制】\n"
            + "\n".join(f"- {n}" for n in names)
        )
    )

    result = await deepseek.chat_json(prompts.CATEGORIZE_SYSTEM, blocks, "categorize")

    raw_picked = result.get("picked") or []
    if not isinstance(raw_picked, list):
        raw_picked = []

    # 校验：模型必须从候选里选。凭空造出来的名字直接丢弃并记日志 ——
    # 静默接受会让库里出现无法聚合的野知识点，而且没人会发现。
    picked: list[dict] = []
    rejected: list[str] = []
    for name in raw_picked[: max(1, payload.limit)]:
        cleaned = str(name).strip()
        if cleaned in by_name:
            picked.append(
                {"id": by_name[cleaned]["id"], "name": cleaned,
                 "grade": by_name[cleaned]["grade"]}
            )
        elif cleaned:
            rejected.append(cleaned)

    if rejected:
        log.warning("归类返回了候选外的知识点，已丢弃：%s", rejected)

    return {
        "matched": bool(picked),
        "picked": picked,
        "reason": (result.get("reason") or "").strip(),
        "candidates": [{"id": c["id"], "name": c["name"]} for c in cands],
        "rejected": rejected,
    }


@router.post("/auto-categorize/{mistake_id}", response_model=SuggestKpsOut)
async def auto_categorize(mistake_id: int) -> dict:
    """一键归类：让 AI 挑知识点并**直接写入**关联表。

    与 suggest-kps 的区别是它会写库。用户点「自动归类」时是在明确要求
    「你来定」，因此这里不必再确认一次；结果会回显，不满意可以再手工改。
    """
    out = await suggest_kps(SuggestKpsIn(mistake_id=mistake_id, limit=3))
    if not out["matched"]:
        return out

    kp_ids = [p["id"] for p in out["picked"]]
    with db.transaction() as cur:
        cur.executemany(
            """
            INSERT INTO mistake_kp (mistake_id, kp_id) VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            [(mistake_id, k) for k in kp_ids],
        )
    return out