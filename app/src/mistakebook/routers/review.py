"""复习端：提交作答结果，驱动 SRS 推进。

**并发安全性（重要）**：本端点是「读当前状态 → 算新状态 → 写回」的模式。
如果不加锁，两个并发请求会读到同一份旧状态，后写的覆盖先写的，
`correct_streak` 与 `interval_days` 被静默丢弃 —— 表现为「连点两下做对了，
间隔却没涨」，复习退化成每天一次。

实测过：三个并发 correct 请求，不加锁时最终 streak=1/interval=2（应为 3/8）。
因此这里用**单事务 + SELECT ... FOR UPDATE** 行锁串行化同一道题的复习提交。
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter

from .. import db
from ..errors import NotFoundError
from ..models import MistakeOut, ReviewSubmit
from ..services import srs

router = APIRouter(prefix="/api", tags=["review"])


@router.post("/mistakes/{mistake_id}/review", response_model=MistakeOut)
def submit_review(mistake_id: int, payload: ReviewSubmit) -> dict:
    now = dt.datetime.now(dt.timezone.utc)

    # 读与写必须在同一事务内，且 SELECT 要加 FOR UPDATE 行锁：
    #   - 同一事务：保证状态更新与历史记录同进同退；
    #   - FOR UPDATE：保证「读—算—写」期间没有其他事务插进来改同一行。
    # 锁的粒度是单行，不同错题之间不会互相阻塞。
    with db.transaction() as cur:
        cur.execute(
            """
            SELECT id, mastery, error_count, correct_streak, interval_days
            FROM mistake
            WHERE id = %s AND deleted_at IS NULL
            FOR UPDATE
            """,
            (mistake_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError("错题不存在或已删除")

        # 间隔从库里读，不能用常量默认值 —— 否则 correct 分支的「翻倍」永远
        # 从 1 开始，间隔永远涨不上去。
        st = srs.next_state(
            payload.result,
            mastery=row["mastery"],
            error_count=row["error_count"],
            correct_streak=row["correct_streak"],
            interval_days=row["interval_days"],
            now=now,
        )

        cur.execute(
            """
            UPDATE mistake
            SET mastery = %s, error_count = %s, correct_streak = %s,
                interval_days = %s, last_review_at = %s, next_review_at = %s
            WHERE id = %s
            """,
            (
                st.mastery, st.error_count, st.correct_streak, st.interval_days,
                now, st.next_review_at, mistake_id,
            ),
        )
        cur.execute(
            """
            INSERT INTO review_log (mistake_id, result, duration_sec, note)
            VALUES (%s, %s, %s, %s)
            """,
            (mistake_id, payload.result, payload.duration_sec, payload.note),
        )

    from .mistakes import _get_mistake

    return _get_mistake(mistake_id)