"""复习提交的改判（P2）。

背景（用户反馈）：「做对了/半对/又错了」每点一次就新增一条记录。
平板上的误触、看错按钮都会凭空多出记录，`error_count` 与正确率统计随之失真，
而家长完全看不出数字为什么不对。

约定：改判由**客户端显式声明** `redo: true`，服务端只校验是否还在宽限期内。
不做成「服务端按时间自动判断」的原因见 routers/review.py 的模块 docstring：
自动推断会把程序化调用者连续提交的多次复习也误当成重复点击而静默丢弃。

  - `redo: true` 且在宽限期内 → 替换上一条记录、撤回其 SRS 推进、按新结果重算；
  - `redo: true` 但已超宽限期 → 退化成一次普通的新复习（不报错）；
  - 不带 redo（默认）→ 永远是新的一次复习，行为与改动前完全一致。

这些测试都直接读 `review_log` 的行数与 `mistake` 的状态来断言 ——
只看接口返回的字段会漏掉「历史里悄悄多了一条」这种真实故障。
"""

from __future__ import annotations

import datetime as dt

from mistakebook import db
from mistakebook.routers.review import REDO_GRACE_SECONDS

BIG = REDO_GRACE_SECONDS + 5


def _logs(client, mid: int) -> list[dict]:
    return client.get(f"/api/mistakes/{mid}/reviews").json()


def _db_logs(mid: int) -> list[dict]:
    return db.fetch(
        "SELECT id, result FROM review_log WHERE mistake_id = %s ORDER BY id", (mid,)
    )


def _backdate_last_review(mid: int, seconds: int) -> None:
    """把最近一次复习往前推，模拟「已经过了宽限期」。

    比 sleep(11) 快得多，也不会让测试套件整体慢下来。
    mistake.last_review_at 与 review_log.reviewed_at 必须一起推：
    前端看前者决定要不要带 redo，服务端看后者判断是否在窗口内。
    """
    db.execute(
        """
        UPDATE review_log SET reviewed_at = reviewed_at - (%s || ' seconds')::interval
        WHERE id = (SELECT id FROM review_log WHERE mistake_id = %s
                    ORDER BY reviewed_at DESC, id DESC LIMIT 1)
        """,
        (str(seconds), mid),
    )
    db.execute(
        "UPDATE mistake SET last_review_at = last_review_at - (%s || ' seconds')::interval "
        "WHERE id = %s",
        (str(seconds), mid),
    )


# ── 不声明 redo 时，行为与改动前完全一致 ────────────────────────────


def test_without_redo_every_submit_is_new(client, make_mistake):
    """不带 redo 的提交永远是新的一次 —— 这是回归保护。

    程序化调用者（回归测试、stress_review.py、验收脚本）都走这条路，
    它们连续提交的多次复习绝不能被当成重复点击而丢掉。
    """
    m = make_mistake()
    mid = m["id"]

    r1 = client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"}).json()
    r2 = client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"}).json()
    r3 = client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"}).json()

    assert [r["interval_days"] for r in (r1, r2, r3)] == [2, 4, 8], "连对必须持续翻倍"
    assert r3["correct_streak"] == 3
    assert all(r["redo"] is False for r in (r1, r2, r3))
    assert len(_db_logs(mid)) == 3, "不声明 redo 时每次提交都该留下记录"


def test_concurrent_without_redo_keeps_all(client, make_mistake):
    """并发提交（都不带 redo）不能被判定成重复而丢失。"""
    import concurrent.futures as cf

    m = make_mistake()
    mid = m["id"]

    def submit(_i):
        return client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})

    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(submit, range(3)))

    assert all(r.status_code == 200 for r in results)
    final = client.get(f"/api/mistakes/{mid}").json()
    assert final["correct_streak"] == 3, f"streak={final['correct_streak']}，应为 3"
    assert len(_db_logs(mid)) == 3


# ── 声明 redo：宽限期内替换 ─────────────────────────────────────────


def test_redo_wrong_to_correct(client, make_mistake):
    """点错成「又错了」，立刻改点「做对了」：只留一条记录，状态按做对了算。"""
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "wrong"})
    after_wrong = client.get(f"/api/mistakes/{mid}").json()
    assert after_wrong["error_count"] == 2, "做错应该让错次 +1"
    assert after_wrong["interval_days"] == 1

    r = client.post(
        f"/api/mistakes/{mid}/review", json={"result": "correct", "redo": True}
    ).json()
    assert r["redo"] is True

    logs = _db_logs(mid)
    assert len(logs) == 1, f"改判后应只有一条记录，实际 {len(logs)} 条"
    assert logs[0]["result"] == "correct"

    after = client.get(f"/api/mistakes/{mid}").json()
    assert after["error_count"] == 1, (
        f"改判后错次应回到 1（撤回那次 wrong），实际 {after['error_count']}"
    )
    assert after["correct_streak"] == 1
    assert after["interval_days"] == 2, (
        f"改判成做对后间隔应为 2，实际 {after['interval_days']}"
    )


def test_redo_correct_to_wrong(client, make_mistake):
    """反向：手快点成「做对了」，改点「又错了」。"""
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})
    assert client.get(f"/api/mistakes/{mid}").json()["interval_days"] == 2

    r = client.post(
        f"/api/mistakes/{mid}/review", json={"result": "wrong", "redo": True}
    ).json()
    assert r["redo"] is True
    assert len(_db_logs(mid)) == 1

    after = client.get(f"/api/mistakes/{mid}").json()
    assert after["interval_days"] == 1, "改判成做错后应回到 1 天"
    assert after["correct_streak"] == 0
    assert after["error_count"] == 2
    assert after["mastery"] == "reviewing"


def test_redo_keeps_interval_from_before_that_review(client, make_mistake):
    """改判必须回到那次复习**之前**的间隔，而不是回到初始值。

    这是 interval_before 这个字段存在的全部理由：连对到 4 天之后手快点了
    「又错了」，再改回「做对了」，间隔应当是 4→8，而不是 1→2。
    用常量兜底会静默退化成「每次改判都重置进度」。
    """
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})   # 1→2
    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})   # 2→4
    assert client.get(f"/api/mistakes/{mid}").json()["interval_days"] == 4

    # 点错了：这是一次独立的新复习，间隔被重置到 1，但它记下了改判前的 4
    client.post(f"/api/mistakes/{mid}/review", json={"result": "wrong"})
    assert client.get(f"/api/mistakes/{mid}").json()["interval_days"] == 1

    # 立刻改回「做对了」：这是改判，间隔要从 4 继续翻倍到 8（而不是 1→2）
    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct", "redo": True})
    after = client.get(f"/api/mistakes/{mid}").json()
    assert after["interval_days"] == 8, (
        f"改判时应从改判前的 4 天继续翻倍到 8，实际 {after['interval_days']}"
    )
    assert after["error_count"] == 1, "被改判掉的那次 wrong 的错次没撤回"
    assert len(_db_logs(mid)) == 3, "更早的两条历史必须留着"


def test_repeated_redo_keeps_original_interval(client, make_mistake):
    """在同一个宽限期内反复改判，间隔基准必须始终是「这一轮复习之前」的那个值。

    这是最容易写错的一环：改判会删掉上一条记录并写一条新的，如果新记录的
    interval_before 取自被删记录（而记录本身已经是被改判过的），
    基准就会一次次往前塌陷，连对的进度被静默吃掉。
    """
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})   # 1→2
    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})   # 2→4
    assert client.get(f"/api/mistakes/{mid}").json()["interval_days"] == 4

    # 一轮真正的复习，并在宽限期内反复改判：错 → 半对 → 对
    client.post(f"/api/mistakes/{mid}/review", json={"result": "wrong"})
    client.post(f"/api/mistakes/{mid}/review", json={"result": "partial", "redo": True})
    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct", "redo": True})

    after = client.get(f"/api/mistakes/{mid}").json()
    assert after["interval_days"] == 8, (
        f"反复改判后仍应从复习前的 4 天翻倍到 8，实际 {after['interval_days']}"
    )
    assert after["error_count"] == 1, (
        f"中间那次 wrong 的错次没被撤掉：{after['error_count']}"
    )
    assert len(_db_logs(mid)) == 3, (
        f"应只留 3 条（连对两条 + 本轮一条），实际 {len(_db_logs(mid))}"
    )


def test_redo_chain_only_keeps_one_log(client, make_mistake):
    """反复改判始终只留一条本轮记录，状态跟着最后一次走。"""
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})
    for result in ["partial", "wrong", "correct"]:
        client.post(f"/api/mistakes/{mid}/review", json={"result": result, "redo": True})

    logs = _db_logs(mid)
    assert len(logs) == 1, f"反复改判累积了 {len(logs)} 条记录"
    assert logs[0]["result"] == "correct"

    after = client.get(f"/api/mistakes/{mid}").json()
    assert after["error_count"] == 1, "错次被中间那次 wrong 留下来了"
    assert after["interval_days"] == 2


# ── 声明 redo 但已超宽限期：退化成新复习，不报错 ────────────────────


def test_redo_after_grace_becomes_new_review(client, make_mistake):
    """过了宽限期再带 redo 提交，应当作为新的一次记录，而不是报错。

    用户看到的就是「点了个按钮」，不该因为超过 10 秒而失败。
    """
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})
    _backdate_last_review(mid, BIG)

    r = client.post(
        f"/api/mistakes/{mid}/review", json={"result": "wrong", "redo": True}
    ).json()
    assert r["redo"] is False, "超出宽限期仍被当成改判，会覆盖掉真正的历史"

    logs = _db_logs(mid)
    assert len(logs) == 2, f"应保留两条独立记录，实际 {len(logs)}"
    assert [x["result"] for x in logs] == ["correct", "wrong"]

    after = client.get(f"/api/mistakes/{mid}").json()
    assert after["error_count"] == 2, "新的一次做错应当让错次 +1"
    assert after["interval_days"] == 1


def test_redo_on_first_ever_review_is_new(client, make_mistake):
    """第一次复习就带 redo（没有任何历史可改）：当作正常提交，不报错。"""
    m = make_mistake()
    mid = m["id"]

    r = client.post(
        f"/api/mistakes/{mid}/review", json={"result": "correct", "redo": True}
    ).json()
    assert r["redo"] is False
    assert len(_db_logs(mid)) == 1
    assert r["interval_days"] == 2


# ── 响应形状与统计一致性 ────────────────────────────────────────────


def test_response_shape_is_backward_compatible(client, make_mistake):
    """复习提交的响应必须仍带错题字段 —— 改动前就是这样，老调用方在读它。"""
    m = make_mistake()
    r = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"}).json()

    for key in (
        "id", "subject_id", "mastery", "error_count", "correct_streak",
        "interval_days", "next_review_at", "kps",
    ):
        assert key in r, f"复习响应缺少错题字段 {key}"

    # id 必须是**错题的** id，不能被复习记录的 id 顶掉
    assert r["id"] == m["id"], "响应里的 id 不是错题 id，前端拿它去查询会 404"

    # 新加的字段也要在
    for key in ("redo", "updated", "history", "last_review"):
        assert key in r, f"复习响应缺少新字段 {key}"
    assert len(r["history"]) == 1
    assert r["last_review"]["result"] == "correct"
    assert r["last_review"]["id"] != r["id"], "last_review.id 应是复习记录 id"


def test_redo_does_not_leave_gap_in_db(client, subject, make_mistake):
    """改判的实现是「删旧行 + 插新行」。只插不删会让趋势统计凭空多算一次，
    而界面上只看得到一条记录 —— 这种不一致没人能发现。"""
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "wrong"})
    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct", "redo": True})

    rows = _db_logs(mid)
    assert len(rows) == 1, f"库里残留了 {len(rows)} 行"
    assert rows[0]["result"] == "correct"


def test_interval_before_is_recorded(client, make_mistake):
    """interval_before 必须落库 —— 改判靠它还原间隔，缺了就退化成常量兜底。"""
    m = make_mistake()
    mid = m["id"]

    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})  # 1→2
    client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})  # 2→4

    row = db.fetchrow(
        """
        SELECT interval_before FROM review_log
        WHERE mistake_id = %s ORDER BY reviewed_at DESC, id DESC LIMIT 1
        """,
        (mid,),
    )
    assert row["interval_before"] == 2, (
        f"最后一条记录之前的间隔应为 2，实际 {row['interval_before']}"
    )


def test_reviews_endpoint_still_works(client, make_mistake):
    """复习历史端点换过实现，字段形状不能变 —— 前端历史表格依赖它。"""
    m = make_mistake()
    mid = m["id"]
    client.post(f"/api/mistakes/{mid}/review", json={"result": "partial", "note": "差最后一步"})

    logs = _logs(client, mid)
    assert len(logs) == 1
    for key in ("id", "reviewed_at", "result", "duration_sec", "note"):
        assert key in logs[0], f"复习历史缺少字段 {key}"
    assert logs[0]["note"] == "差最后一步"


def test_reviews_of_missing_mistake_is_404(client):
    assert client.get("/api/mistakes/99999999/reviews").status_code == 404


def test_redo_of_missing_mistake_is_404(client):
    assert client.post(
        "/api/mistakes/99999999/review", json={"result": "correct", "redo": True}
    ).status_code == 404