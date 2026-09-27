"""学情分析：覆盖率与薄弱点。

设计要点（spec §6.3 / §12.3）：**所有计数与聚合都由 SQL 完成**。
模型只负责把已经算好的数字讲成人话，不参与统计。
这条边界是「功能 2 有没有用」的分水岭 —— 让模型自己数数会得到无法察觉的错误。
"""

from __future__ import annotations

from typing import Any

from .. import db

# 知识点四种覆盖状态。命名即语义，前端直接用它决定颜色与措辞。
COVERAGE_CATEGORIES = (
    "weak",                 # 有错题且未全部掌握 —— 真正要下功夫的
    "covered",              # 有错题且已全部掌握 —— 已修复，需定期回访
    "untested",             # 无错题，也没有教材定位 —— 完全没碰过
    "untested_with_content",  # 无错题，但有教材页码 —— 未考过，风险未知
)


def _categorise(mistake_cnt: int, mastered_cnt: int, page_from: int | None) -> str:
    if mistake_cnt > 0:
        return "covered" if mastered_cnt >= mistake_cnt else "weak"
    return "untested_with_content" if page_from is not None else "untested"


def coverage(subject_id: int, grade: str | None = None) -> list[dict[str, Any]]:
    """每个知识点的错题数、掌握数、最近复习时间，并给出覆盖分类。

    注意 LEFT JOIN 的写法：`m.deleted_at IS NULL` 这类过滤条件必须写在
    JOIN 的 ON 里，不能写在 WHERE 里 —— 写在 WHERE 会把「没有错题的知识点」
    整行过滤掉，而那恰恰是我们要找的盲区。
    """
    sql = """
        SELECT
          kp.id, kp.name, kp.code, kp.grade, kp.book,
          kp.page_from, kp.page_to, kp.parent_id, kp.reviewed, kp.sort_order,
          COUNT(DISTINCT m.id)                                        AS mistake_cnt,
          COUNT(DISTINCT m.id) FILTER (WHERE m.mastery = 'mastered')   AS mastered_cnt,
          COUNT(DISTINCT m.id) FILTER (WHERE m.mastery = 'reviewing')  AS reviewing_cnt,
          MAX(rl.reviewed_at)                                         AS last_reviewed_at
        FROM knowledge_point kp
        LEFT JOIN mistake_kp mk ON mk.kp_id = kp.id
        LEFT JOIN mistake   m  ON m.id = mk.mistake_id AND m.deleted_at IS NULL
        LEFT JOIN review_log rl ON rl.mistake_id = m.id
        WHERE kp.subject_id = %s
          AND (%s::text IS NULL OR kp.grade = %s)
        GROUP BY kp.id
        ORDER BY kp.sort_order, kp.id
    """
    rows = db.fetch(sql, (subject_id, grade, grade))

    out: list[dict[str, Any]] = []
    for r in rows:
        mistake_cnt = int(r["mistake_cnt"] or 0)
        mastered_cnt = int(r["mastered_cnt"] or 0)
        out.append(
            {
                **r,
                "mistake_cnt": mistake_cnt,
                "mastered_cnt": mastered_cnt,
                "reviewing_cnt": int(r["reviewing_cnt"] or 0),
                "coverage": _categorise(mistake_cnt, mastered_cnt, r["page_from"]),
            }
        )
    return out


def coverage_summary(subject_id: int, grade: str | None = None) -> dict[str, Any]:
    """按科目汇总四类覆盖率，供「哪些知识点还没复习到」的总览展示。"""
    rows = coverage(subject_id, grade)

    counts = {k: 0 for k in COVERAGE_CATEGORIES}
    for r in rows:
        counts[r["coverage"]] += 1

    total = len(rows)
    # 「已涉及」= 有错题的知识点。注意不含 untested*，见下方语义警告。
    touched = counts["weak"] + counts["covered"]
    mastered_all = counts["covered"]

    return {
        "total_kp": total,
        "counts": counts,
        "touched_kp": touched,
        "untouched_kp": counts["untested"] + counts["untested_with_content"],
        # 「掌握率」只对有错题的知识点计算 —— 分母用 touched 而不是 total。
        # 用 total 当分母会把「没做过」算成「没掌握」，对孩子的评价是失真的。
        "mastery_rate": round(mastered_all / touched, 3) if touched else None,
        "items": rows,
    }


def weak_knowledge_points(
    subject_id: int, grade: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """按「错得多 + 错得集中」排序的薄弱知识点，用于复习优先级。"""
    items = [r for r in coverage(subject_id, grade) if r["coverage"] == "weak"]
    items.sort(
        key=lambda r: (r["mistake_cnt"] - r["mastered_cnt"], r["mistake_cnt"]),
        reverse=True,
    )
    return items[:limit]


def mistake_stats(subject_id: int | None = None) -> dict[str, Any]:
    """错题总览统计。全部由 SQL 聚合，供阶段归纳使用。"""
    where = "WHERE deleted_at IS NULL"
    params: tuple[Any, ...] = ()
    if subject_id is not None:
        where += " AND subject_id = %s"
        params = (subject_id,)

    row = db.fetchrow(
        f"""
        SELECT
          COUNT(*)                                                  AS total,
          COUNT(*) FILTER (WHERE mastery = 'new')                    AS new_cnt,
          COUNT(*) FILTER (WHERE mastery = 'reviewing')              AS reviewing_cnt,
          COUNT(*) FILTER (WHERE mastery = 'mastered')               AS mastered_cnt,
          COUNT(*) FILTER (WHERE next_review_at <= now())            AS due_cnt,
          COALESCE(SUM(error_count), 0)                              AS total_errors
        FROM mistake
        {where}
        """,
        params,
    ) or {}

    by_reason = db.fetch(
        f"""
        SELECT COALESCE(wrong_reason, 'unknown') AS reason, COUNT(*) AS cnt
        FROM mistake
        {where}
        GROUP BY 1
        ORDER BY cnt DESC
        """,
        params,
    )

    by_subject = db.fetch(
        """
        SELECT s.id, s.name,
               COUNT(m.id)                                              AS total,
               COUNT(m.id) FILTER (WHERE m.mastery = 'mastered')         AS mastered_cnt,
               COUNT(m.id) FILTER (WHERE m.next_review_at <= now())      AS due_cnt
        FROM subject s
        LEFT JOIN mistake m ON m.subject_id = s.id AND m.deleted_at IS NULL
        GROUP BY s.id, s.name
        ORDER BY s.sort_order
        """
    )

    return {
        "total": int(row.get("total") or 0),
        "new_cnt": int(row.get("new_cnt") or 0),
        "reviewing_cnt": int(row.get("reviewing_cnt") or 0),
        "mastered_cnt": int(row.get("mastered_cnt") or 0),
        "due_cnt": int(row.get("due_cnt") or 0),
        "total_errors": int(row.get("total_errors") or 0),
        "by_reason": [{"reason": r["reason"], "count": int(r["cnt"])} for r in by_reason],
        "by_subject": [
            {
                "id": r["id"],
                "name": r["name"],
                "total": int(r["total"] or 0),
                "mastered_cnt": int(r["mastered_cnt"] or 0),
                "due_cnt": int(r["due_cnt"] or 0),
            }
            for r in by_subject
        ],
    }


def review_trend(subject_id: int | None = None, days: int = 14) -> list[dict[str, Any]]:
    """近 N 天复习作答趋势，用于阶段性归纳里的「有没有变好」。"""
    where = "WHERE reviewed_at >= now() - (%s || ' days')::interval"
    params: list[Any] = [str(days)]
    if subject_id is not None:
        where += " AND m.subject_id = %s"
        params.append(subject_id)

    rows = db.fetch(
        f"""
        SELECT date_trunc('day', rl.reviewed_at)::date AS day,
               COUNT(*)                                       AS total,
               COUNT(*) FILTER (WHERE rl.result = 'correct')  AS correct_cnt
        FROM review_log rl
        -- deleted_at 过滤必须写在 JOIN 条件里（软删除的错题不算进趋势），
        -- 漏掉它会让趋势图把已删除的错题继续统计进去，家长据此得出错误结论。
        JOIN mistake m ON m.id = rl.mistake_id AND m.deleted_at IS NULL
        {where}
        GROUP BY 1
        ORDER BY 1
        """,
        tuple(params),
    )
    return [
        {
            "day": r["day"].isoformat(),
            "total": int(r["total"]),
            "correct_cnt": int(r["correct_cnt"]),
            "accuracy": round(int(r["correct_cnt"]) / int(r["total"]), 3) if r["total"] else 0.0,
        }
        for r in rows
    ]