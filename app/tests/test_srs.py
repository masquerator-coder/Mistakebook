"""SRS 引擎单测。

复习算法是纯函数，穷举成本极低，而它的错误会直接导致「该复习的没安排」。
因此这里是测试覆盖的重点。
"""

from __future__ import annotations

import datetime as dt

import pytest

from mistakebook.services import srs

NOW = dt.datetime(2026, 9, 27, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_initial_state_is_one_day_out():
    st = srs.initial_state(NOW)
    assert st.interval_days == 1
    assert st.next_review_at == NOW + dt.timedelta(days=1)
    assert st.mastery == "new"
    assert st.correct_streak == 0
    assert st.error_count == 1


def test_wrong_resets_to_one_day_and_clears_streak():
    st = srs.next_state(
        "wrong", mastery="reviewing", error_count=2, correct_streak=2,
        interval_days=8, now=NOW,
    )
    assert st.interval_days == 1
    assert st.correct_streak == 0
    assert st.error_count == 3
    assert st.mastery == "reviewing"
    assert st.next_review_at == NOW + dt.timedelta(days=1)


def test_wrong_from_mastered_demotes():
    """已掌握的题再次做错，必须降级回 reviewing，否则会被当成学会了。"""
    st = srs.next_state(
        "wrong", mastery="mastered", error_count=1, correct_streak=3,
        interval_days=30, now=NOW,
    )
    assert st.mastery == "reviewing"
    assert st.interval_days == 1
    assert st.error_count == 2


def test_partial_keeps_interval_but_clears_streak():
    st = srs.next_state(
        "partial", mastery="reviewing", error_count=1, correct_streak=2,
        interval_days=4, now=NOW,
    )
    assert st.interval_days == 4
    assert st.correct_streak == 0
    assert st.error_count == 1
    assert st.next_review_at == NOW + dt.timedelta(days=4)


def test_correct_doubles_interval():
    st = srs.next_state(
        "correct", mastery="reviewing", error_count=1, correct_streak=0,
        interval_days=1, now=NOW,
    )
    assert st.interval_days == 2
    assert st.correct_streak == 1


def test_interval_capped_at_max():
    st = srs.next_state(
        "correct", mastery="reviewing", error_count=0, correct_streak=5,
        interval_days=30, now=NOW,
    )
    assert st.interval_days == srs.MAX_INTERVAL_DAYS


def test_three_correct_streak_with_few_errors_masters():
    st = srs.next_state(
        "correct", mastery="reviewing", error_count=1, correct_streak=2,
        interval_days=4, now=NOW,
    )
    assert st.mastery == "mastered"
    assert st.correct_streak == 3


def test_many_errors_blocks_mastery_despite_streak():
    """错次太多说明反复错，即使连对 3 次也不算掌握。"""
    st = srs.next_state(
        "correct", mastery="reviewing", error_count=3, correct_streak=2,
        interval_days=4, now=NOW,
    )
    assert st.mastery == "reviewing"


def test_unknown_result_raises():
    with pytest.raises(ValueError):
        srs.next_state(
            "maybe", mastery="new", error_count=0, correct_streak=0,
            interval_days=1, now=NOW,
        )


def test_sequence_of_wrong_then_correct_recovers():
    """错一次再连对三次：错次 2 <= 2，应当能重新掌握。"""
    now = NOW
    st = srs.initial_state(now)
    st = srs.next_state("wrong", st.mastery, st.error_count, st.correct_streak,
                        st.interval_days, now)
    assert st.error_count == 2
    for _ in range(3):
        st = srs.next_state("correct", st.mastery, st.error_count, st.correct_streak,
                            st.interval_days, now)
    assert st.mastery == "mastered"