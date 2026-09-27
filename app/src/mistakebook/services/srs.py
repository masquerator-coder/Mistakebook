"""间隔重复（SRS）引擎。

刻意做成**纯函数**：不碰数据库、不读全局状态、不调时钟。
这样复习算法可以被穷举测试，而它的错误会直接导致「该复习的没安排」，
是整条链路上最值得单测的一环。

规则（见 spec §6.4）：
  首次录入      → interval=1 天
  wrong         → 重置为 1 天，连续答对清零，错次 +1，状态降回 reviewing
  partial       → 保持当前间隔（不奖励也不惩罚），连续答对清零
  correct       → 间隔翻倍，上限 MAX_INTERVAL_DAYS
  掌握判定      → 连续答对 >= MASTER_STREAK 且 错次 <= MASTER_MAX_ERRORS
"""

from __future__ import annotations

import dataclasses
import datetime as dt

INITIAL_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 30
MASTER_STREAK = 3
MASTER_MAX_ERRORS = 2

VALID_RESULTS = ("correct", "wrong", "partial")


@dataclasses.dataclass(frozen=True)
class SrsResult:
    mastery: str
    error_count: int
    correct_streak: int
    interval_days: int
    next_review_at: dt.datetime


def next_state(
    result: str,
    mastery: str,
    error_count: int,
    correct_streak: int,
    interval_days: int,
    now: dt.datetime,
) -> SrsResult:
    """根据本次作答结果算出新的复习状态。

    result: 'correct' | 'wrong' | 'partial'
    now:    当前时间（必须由调用方注入，便于测试）
    """
    if result not in VALID_RESULTS:
        raise ValueError(f"未知的作答结果：{result!r}，应为 {VALID_RESULTS} 之一")

    if result == "wrong":
        error_count += 1
        correct_streak = 0
        interval_days = INITIAL_INTERVAL_DAYS
    elif result == "partial":
        # 保持间隔不变：既不算掌握进展，也不当作遗忘
        correct_streak = 0
        interval_days = max(interval_days, INITIAL_INTERVAL_DAYS)
    else:  # correct
        correct_streak += 1
        interval_days = min(max(interval_days, INITIAL_INTERVAL_DAYS) * 2, MAX_INTERVAL_DAYS)

    if correct_streak >= MASTER_STREAK and error_count <= MASTER_MAX_ERRORS:
        mastery = "mastered"
    else:
        mastery = "reviewing"

    return SrsResult(
        mastery=mastery,
        error_count=error_count,
        correct_streak=correct_streak,
        interval_days=interval_days,
        next_review_at=now + dt.timedelta(days=interval_days),
    )


def initial_state(now: dt.datetime) -> SrsResult:
    """新录入错题的初始复习状态：1 天后首次复习。"""
    return SrsResult(
        mastery="new",
        error_count=1,
        correct_streak=0,
        interval_days=INITIAL_INTERVAL_DAYS,
        next_review_at=now + dt.timedelta(days=INITIAL_INTERVAL_DAYS),
    )