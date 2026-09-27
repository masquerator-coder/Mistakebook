"""复习计划（功能 4 的产出）。

关键边界：喂给模型的**统计数字全部由 SQL 算好**，
提示词里明确要求模型不得自行重新统计。见 prompts.PLAN_SYSTEM 的约束理由。
"""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter

from .. import db
from ..config import settings
from ..errors import NotFoundError
from ..services import analysis, deepseek, prompts

router = APIRouter(prefix="/api", tags=["plan"])


@router.post("/plan/generate")
async def generate_plan(days: int = 7) -> dict:
    """生成未来 N 天的复习计划。

    没有错题时不调模型，直接返回空计划 —— 省一次调用，也避免模型
    在无数据时编造出听起来合理但没有依据的建议。
    """
    stats = analysis.mistake_stats()
    if stats["total"] == 0:
        return {
            "status": "done",
            "payload": {
                "priority_gaps": [],
                "daily_plan": [],
                "score_strategy": ["先记录几道错题，再来生成计划会更准确。"],
                "encouragement": "随时可以开始。",
            },
            "message": "还没有错题记录，已跳过 AI 调用。",
        }

    # 逐科收集已算好的薄弱点，绝不把原始题目丢给模型去数
    subjects = db.fetch("SELECT id, name FROM subject ORDER BY sort_order")
    per_subject = []
    for s in subjects:
        weak = analysis.weak_knowledge_points(s["id"], limit=5)
        if weak:
            per_subject.append(
                {
                    "subject": s["name"],
                    "weak_points": [
                        {
                            "name": w["name"],
                            "mistake_cnt": w["mistake_cnt"],
                            "mastered_cnt": w["mastered_cnt"],
                            "page": f"{w['page_from']}-{w['page_to']}" if w["page_from"] else None,
                        }
                        for w in weak
                    ],
                }
            )

    payload_in = {
        "today": dt.date.today().isoformat(),
        "plan_days": days,
        "daily_review_minutes": settings.daily_review_minutes,
        "overall": {
            "total_mistakes": stats["total"],
            "mastered": stats["mastered_cnt"],
            "reviewing": stats["reviewing_cnt"],
            "due_now": stats["due_cnt"],
            "wrong_reason_distribution": stats["by_reason"],
        },
        "subjects": per_subject,
        "recent_trend": analysis.review_trend(days=14)[-7:],
        "untouched_coverage": _untouched(subjects),
    }

    blocks = [
        deepseek.text_block(
            "以下是已经统计好的错题数据（数字准确，请直接引用）：\n"
            + json.dumps(payload_in, ensure_ascii=False, indent=2)
        )
    ]
    result = await deepseek.chat_json(prompts.PLAN_SYSTEM, blocks, "plan", max_tokens=4096)

    row = db.execute_returning(
        """
        INSERT INTO study_plan (period_start, period_end, payload)
        VALUES (%s, %s, %s)
        RETURNING id, generated_at, period_start, period_end, payload
        """,
        (
            dt.date.today(),
            dt.date.today() + dt.timedelta(days=days),
            json.dumps(result, ensure_ascii=False),
        ),
    )
    return {"status": "done", "payload": result, "plan_id": row["id"]}


def _untouched(subjects: list[dict]) -> list[dict]:
    """「还没复习到」的知识点 —— 本功能的差异化价值所在。"""
    out = []
    for s in subjects:
        summary = analysis.coverage_summary(s["id"])
        if summary["total_kp"] == 0:
            continue
        out.append(
            {
                "subject": s["name"],
                "total_kp": summary["total_kp"],
                "untouched_kp": summary["untouched_kp"],
                "weak_kp": summary["counts"]["weak"],
                "mastered_kp": summary["counts"]["covered"],
            }
        )
    return out


@router.get("/plan/latest")
def latest_plan() -> dict:
    row = db.fetchrow(
        """
        SELECT id, generated_at, period_start, period_end, payload
        FROM study_plan ORDER BY generated_at DESC LIMIT 1
        """
    )
    if row is None:
        raise NotFoundError("还没有生成过复习计划")
    return row


@router.get("/plan/history")
def plan_history(limit: int = 20) -> list[dict]:
    return db.fetch(
        """
        SELECT id, generated_at, period_start, period_end
        FROM study_plan ORDER BY generated_at DESC LIMIT %s
        """,
        (limit,),
    )