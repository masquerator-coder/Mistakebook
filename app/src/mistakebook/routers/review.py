"""复习端：提交作答结果，驱动 SRS 推进。

**并发安全性（重要）**：本端点是「读当前状态 → 算新状态 → 写回」的模式。
如果不加锁，两个并发请求会读到同一份旧状态，后写的覆盖先写的，
`correct_streak` 与 `interval_days` 被静默丢弃 —— 表现为「连点两下做对了，
间隔却没涨」，复习退化成每天一次。

实测过：三个并发 correct 请求，不加锁时最终 streak=1/interval=2（应为 3/8）。
因此这里用**单事务 + SELECT ... FOR UPDATE** 行锁串行化同一道题的复习提交。

**改判（payload.redo）**：孩子的正常操作是「点错了立刻再点正确的那个」——
平板上的误触、看错按钮、手抖都算。若每次点击都记一条历史，
一次复习会凭空多出两三条记录，`error_count` 与正确率统计全部失真，
而家长完全看不出数字为什么不对。

改判由**客户端显式声明**（`redo: true`），服务端只负责校验是否还在宽限期内。
为什么不做成「服务端按时间自动判断」：那样会让所有程序化调用者
（回归测试、scripts/stress_review.py、验收脚本）连续提交的多次复习
被误当成重复点击而静默丢弃 —— 实测确实踩到了，正确性测试也红了。
自动推断「用户是不是手滑」本来就不可靠，而界面**知道**自己处在哪个状态
（上一次复习刚发生、且用户点的是另一个按钮），让它显式说明最准确。
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter

from .. import db
from ..errors import NotFoundError
from ..models import ReviewLogOut, ReviewSubmit, ReviewSubmitOut
from ..services import srs

router = APIRouter(prefix="/api", tags=["review"])

# 宽限期：手机上「点错了马上改」通常在 3 秒内完成，10 秒足够，
# 又远短于任何一次真实复习的间隔，不会把两次真正的复习合并掉。
REDO_GRACE_SECONDS = 10


def _history(cur, mistake_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT id, reviewed_at, result, duration_sec, note
        FROM review_log WHERE mistake_id = %s ORDER BY reviewed_at DESC, id DESC
        """,
        (mistake_id,),
    )
    return cur.fetchall()


def _invert(prev_result: str, state: dict, *, interval_before: int) -> dict:
    """撤掉上一次复习对 SRS 状态的影响，回到那次复习**之前**的状态。

    能精确还原是因为影响的只有 `error_count`（wrong 才 +1）与
    `correct_streak`（correct/partial 会清零）。`mastery` 与 `next_review_at`
    在下面按新结果重算，因此不必在这里猜。

    不能还原的情形：错次计数在别处也被改过（例如人工改库）。
    那种情况下这里只会少减一次，不会变成负数 —— 有 max(0, ...) 兜底。
    """
    out = dict(state)
    if prev_result == "wrong":
        out["error_count"] = max(0, int(state["error_count"]) - 1)
    out["interval_days"] = interval_before
    return out


@router.post("/mistakes/{mistake_id}/review", response_model=ReviewSubmitOut)
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

        # 取最新一条复习记录：判断本次是改判还是新增，以及改判时要撤掉什么。
        cur.execute(
            """
            SELECT id, result, reviewed_at, interval_before, duration_sec
            FROM review_log
            WHERE mistake_id = %s
            ORDER BY reviewed_at DESC, id DESC
            LIMIT 1
            """,
            (mistake_id,),
        )
        last = cur.fetchone()

        redo = False
        state = {
            "mastery": row["mastery"],
            "error_count": row["error_count"],
            "correct_streak": row["correct_streak"],
            "interval_days": row["interval_days"],
        }

        if last is not None and payload.redo:
            age = (now - last["reviewed_at"]).total_seconds()
            if age <= REDO_GRACE_SECONDS:
                # 客户端要求改判，且上一条确实刚写下 —— 替换它，而不是新增。
                redo = True
                # 撤回到这次复习之前：间隔取那一条记下的 interval_before，
                # 错次在结果是 wrong 时减回去。
                state = _invert(
                    last["result"], state,
                    interval_before=last["interval_before"] or srs.INITIAL_INTERVAL_DAYS,
                )
                cur.execute("DELETE FROM review_log WHERE id = %s", (last["id"],))
            # 超出宽限期就退回成一次普通的新复习 —— 不报错，
            # 因为用户看到的就是「点了按钮」，不该因为超过 10 秒而失败。
            # redo 保持 False，前端据此提示「已作为新的一次记录」。

        # 间隔从库里读，不能用常量默认值 —— 否则 correct 分支的「翻倍」永远
        # 从 1 开始，间隔永远涨不上去。
        st = srs.next_state(
            payload.result,
            mastery=state["mastery"],
            error_count=state["error_count"],
            correct_streak=state["correct_streak"],
            interval_days=state["interval_days"],
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
            INSERT INTO review_log (mistake_id, result, duration_sec, note, interval_before)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, reviewed_at, result, duration_sec, note
            """,
            (
                mistake_id, payload.result, payload.duration_sec, payload.note,
                state["interval_days"],
            ),
        )
        created = cur.fetchone()
        history = _history(cur, mistake_id)

    # 事务之后再取错题：字段形状与改动前保持一致（老调用方读 interval_days 等），
    # 同时补上本次是新增还是改判、以及整条历史。
    #
    # 注意合并顺序：created（review_log 那条）里的 id 是**复习记录的 id**，
    # 而响应的 id 必须是**错题的 id**（老调用方与前端都按错题 id 理解它）。
    # 因此把 created 的字段改名为 last_review，避开 id 冲突 ——
    # 若直接 {**mistake, **created}，错题的 id 会被复习记录的 id 覆盖，
    # 前端随后用它去 load 就会 404。
    from .mistakes import _get_mistake

    return {
        **_get_mistake(mistake_id),
        "last_review": created,
        "redo": redo,
        "updated": True,
        "history": history,
    }


@router.get("/mistakes/{mistake_id}/reviews", response_model=list[ReviewLogOut])
def list_reviews(mistake_id: int) -> list[dict]:
    """复习历史。查询逻辑与 mistakes.list_reviews 共用同一份实现。"""
    from .mistakes import _get_mistake

    _get_mistake(mistake_id)
    return db.fetch(
        """
        SELECT id, reviewed_at, result, duration_sec, note
        FROM review_log WHERE mistake_id = %s ORDER BY reviewed_at DESC, id DESC
        """,
        (mistake_id,),
    )