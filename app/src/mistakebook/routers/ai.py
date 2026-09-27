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
from ..models import AiJobOut, ExplainIn, ReadQuestionIn, ReadQuestionOut
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

    # 知识点名称参与指纹，因此必须排序：否则同一组知识点换个返回顺序
    # 就会算出不同的 hash，导致缓存白白失效、重复烧 token。
    # （SQL 里已加 ORDER BY，这里再排一次是为了不依赖调用方。）
    input_hash = _hash("explain", row["stem_text"], row["answer_text"],
                       row["student_answer"], row["wrong_reason"], sorted(kp_names))
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