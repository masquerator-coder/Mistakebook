"""覆盖统计与分类的集成测试。

这里验证的是「哪些知识点还没复习到」的正确性 —— 也就是本项目的核心卖点。
最容易出的错是 LEFT JOIN 的过滤条件写错位置：把 `m.deleted_at IS NULL`
写进 WHERE 会把「没有错题的知识点」整行过滤掉，而那恰恰是我们要找的盲区。

所有断言都比对**本测试自己造成的增量**，不断言全库绝对值 ——
否则库里已有的真实数据会让断言莫名其妙地失败。
"""

from __future__ import annotations

import uuid


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def test_untested_kp_survives_left_join(client, subject, make_kp):
    """没有错题的知识点必须出现在覆盖列表里，状态是 untested。"""
    kp = make_kp(f"从没错过的知识点-{_tag()}")

    data = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()
    item = next((i for i in data["coverage"]["items"] if i["id"] == kp["id"]), None)
    assert item is not None, "没有错题的知识点被 JOIN 吃掉了"
    assert item["coverage"] == "untested"
    assert item["mistake_cnt"] == 0


def test_kp_with_page_from_is_untested_with_content(client, subject, make_kp):
    """有教材页码但没做过题 —— 必须与「完全没碰过」区分开。"""
    kp = make_kp(f"有教材定位的知识点-{_tag()}", page_from=42, page_to=48)

    data = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()
    item = next(i for i in data["coverage"]["items"] if i["id"] == kp["id"])
    assert item["coverage"] == "untested_with_content"


def test_weak_then_covered_transition(client, subject, make_kp, make_mistake):
    """错题未掌握是 weak，连对 3 次后应变成 covered。"""
    kp = make_kp(f"会转变状态的知识点-{_tag()}")
    m = make_mistake(kp_ids=[kp["id"]])

    data = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()
    item = next(i for i in data["coverage"]["items"] if i["id"] == kp["id"])
    assert item["coverage"] == "weak"
    assert item["mistake_cnt"] == 1
    assert item["mastered_cnt"] == 0

    for _ in range(3):
        client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"})

    data = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()
    item = next(i for i in data["coverage"]["items"] if i["id"] == kp["id"])
    assert item["coverage"] == "covered"
    assert item["mastered_cnt"] == 1


def test_deleted_mistake_excluded_from_coverage(client, subject, make_kp, make_mistake):
    """软删除的错题不能被算进覆盖统计，否则统计数字会虚高。"""
    kp = make_kp(f"软删除影响统计的知识点-{_tag()}")
    m = make_mistake(kp_ids=[kp["id"]])

    data = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()
    item = next(i for i in data["coverage"]["items"] if i["id"] == kp["id"])
    assert item["mistake_cnt"] == 1

    client.delete(f"/api/mistakes/{m['id']}")

    data = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()
    item = next(i for i in data["coverage"]["items"] if i["id"] == kp["id"])
    assert item["mistake_cnt"] == 0, "已删除的错题仍被统计进去了"
    assert item["coverage"] == "untested"


def test_mastery_rate_excludes_untested(client, subject, make_kp, make_mistake):
    """掌握率的分母只算有错题的知识点。

    否则「没做过」会被算成「没掌握」，对孩子是失真的负面评价。
    """
    tag = _tag()
    kp_tested = make_kp(f"掌握率A-{tag}")
    make_kp(f"掌握率B-{tag}")  # 故意造一个没考过的，用来验证它不进分母

    m = make_mistake(kp_ids=[kp_tested["id"]])

    before = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()["coverage"]
    item = next(i for i in before["items"] if i["id"] == kp_tested["id"])
    assert item["coverage"] == "weak", "有错题未掌握应为 weak"

    for _ in range(3):
        client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"})

    after = client.get(f"/api/ai/analysis/subject/{subject['id']}?grade=七上").json()["coverage"]

    # 关键点：没考过的知识点不能计入「考过」
    assert after["touched_kp"] < after["total_kp"], "没考过的知识点被算进了「考过」"
    # 本测试让一个知识点从 weak 变成 covered
    assert after["counts"]["covered"] == before["counts"]["covered"] + 1, \
        "连对 3 次后该知识点没有变成 covered"
    assert after["counts"]["weak"] == before["counts"]["weak"] - 1


def test_overview_stats_shape(client):
    data = client.get("/api/ai/analysis/overview").json()
    for key in ("total", "mastered_cnt", "reviewing_cnt", "due_cnt", "by_reason", "by_subject"):
        assert key in data, f"总览缺少字段 {key}"


def test_plan_skips_ai_when_no_mistakes(client):
    """没有错题时不应调用模型 —— 否则会生成听起来合理但毫无依据的建议。

    注意：只在库里真的没有错题时才验证「跳过」分支，否则验证的是别的行为。
    """
    overview = client.get("/api/ai/analysis/overview").json()
    r = client.post("/api/plan/generate?days=7")
    assert r.status_code == 200
    body = r.json()
    if overview["total"] == 0:
        assert "跳过" in (body.get("message") or "")
        assert body["payload"]["priority_gaps"] == []
    else:
        # 库里有数据时应正常生成，且必须带上优先项
        assert body["status"] == "done"