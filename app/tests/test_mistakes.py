"""错题 CRUD 与复习流程的集成测试（打真实数据库）。

重点覆盖两类容易出错的地方：
  1. 软删除后绝不能出现在任何列表里 —— 漏一个 WHERE 就会「删了还在」；
  2. 复习后 interval_days 必须真的增长 —— 否则复习永远停在每天一次。
"""

from __future__ import annotations

import uuid


def test_subjects_seeded(client):
    subs = client.get("/api/subjects").json()
    names = [s["name"] for s in subs]
    assert "语文" in names and "数学" in names and "英语" in names


def test_create_requires_stem_or_image(client, subject):
    """题干与图片都没有时必须被拒 —— 否则会存进一条空错题。"""
    r = client.post("/api/mistakes", json={"subject_id": subject["id"]})
    assert r.status_code == 422


def test_create_requires_existing_subject(client):
    r = client.post("/api/mistakes", json={"subject_id": 999999, "stem_text": "x"})
    assert r.status_code == 404


def test_create_minimal_then_read_back(client, make_mistake):
    m = make_mistake(stem_text="解方程 3x + 5 = 20")
    assert m["stem_text"] == "解方程 3x + 5 = 20"
    assert m["mastery"] == "new"
    assert m["interval_days"] == 1
    assert m["error_count"] == 1

    got = client.get(f"/api/mistakes/{m['id']}").json()
    assert got["id"] == m["id"]


def test_soft_delete_hides_from_all_lists(client, make_mistake):
    m = make_mistake()

    assert client.delete(f"/api/mistakes/{m['id']}").status_code == 204

    ids = [x["id"] for x in client.get("/api/mistakes").json()]
    assert m["id"] not in ids, "软删除的错题仍出现在列表里"

    assert client.get(f"/api/mistakes/{m['id']}").status_code == 404

    due_ids = [x["id"] for x in client.get("/api/mistakes/due").json()]
    assert m["id"] not in due_ids


def test_restore_brings_it_back(client, make_mistake):
    m = make_mistake()
    client.delete(f"/api/mistakes/{m['id']}")
    assert client.post(f"/api/mistakes/{m['id']}/restore").status_code == 200
    assert client.get(f"/api/mistakes/{m['id']}").status_code == 200


def test_delete_twice_is_404(client, make_mistake):
    m = make_mistake()
    assert client.delete(f"/api/mistakes/{m['id']}").status_code == 204
    assert client.delete(f"/api/mistakes/{m['id']}").status_code == 404


def test_review_correct_doubles_interval(client, make_mistake):
    """这是 SRS 的核心行为：连对必须让间隔增长，否则复习退化成每天一次。"""
    m = make_mistake()
    assert m["interval_days"] == 1

    r1 = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"}).json()
    assert r1["interval_days"] == 2
    assert r1["correct_streak"] == 1

    r2 = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"}).json()
    assert r2["interval_days"] == 4, "第二次连对应该翻倍到 4 天"

    r3 = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"}).json()
    assert r3["interval_days"] == 8
    assert r3["mastery"] == "mastered", "连对 3 次且错次 <= 2 应判定为已掌握"


def test_review_wrong_resets_interval(client, make_mistake):
    m = make_mistake()
    client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"})
    client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"})
    r = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "wrong"}).json()
    assert r["interval_days"] == 1
    assert r["correct_streak"] == 0
    assert r["error_count"] == 2


def test_review_log_is_recorded(client, make_mistake):
    m = make_mistake()
    client.post(f"/api/mistakes/{m['id']}/review", json={"result": "wrong", "note": "又错在符号"})
    logs = client.get(f"/api/mistakes/{m['id']}/reviews").json()
    assert len(logs) == 1
    assert logs[0]["result"] == "wrong"
    assert logs[0]["note"] == "又错在符号"


def test_invalid_review_result_rejected(client, make_mistake):
    m = make_mistake()
    r = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "maybe"})
    assert r.status_code == 422


def test_kp_link_and_unknown_kp_rejected(client, subject, make_kp, make_mistake):
    # 名字必须唯一：预置数据里有「一元一次方程」等同名知识点，
    # 用固定名字会撞唯一约束，测试就变成了「依赖库里恰好没有这条数据」。
    name = f"一元一次方程-{uuid.uuid4().hex[:6]}"
    kp = make_kp(name)

    m = make_mistake(kp_ids=[kp["id"]])
    assert [k["name"] for k in m["kps"]] == [name]

    # 传不存在的 kp_id 必须报错，而不是静默忽略。
    # 注意：这里走 make_mistake 而不是直接 client.post —— 断言失败时
    # 它期望 201，而我们期望 422，所以用一个显式创建+清理的写法。
    r = client.post("/api/mistakes", json={
        "subject_id": subject["id"], "stem_text": f"坏知识点-{uuid.uuid4().hex[:8]}",
        "kp_ids": [999999],
    })
    assert r.status_code == 422
    assert r.json()["detail"]["error"]["code"] == "unknown_kp"

    # 这条请求必须**没有**留下任何行（校验失败发生在插入之前）
    leaked = _find_mistakes_by_stem(client, "坏知识点-")
    assert not leaked, f"校验失败的请求留下了数据：{leaked}"


def _find_mistakes_by_stem(client, prefix: str) -> list[int]:
    """按题干前缀查错题 id，用于断言「失败请求不该留下痕迹」。"""
    from mistakebook import db

    rows = db.fetch(
        "SELECT id FROM mistake WHERE stem_text LIKE %s", (prefix + "%",)
    )
    return [r["id"] for r in rows]


def test_duplicate_kp_name_rejected(client, subject, make_kp):
    name = f"重复知识点测试-{uuid.uuid4().hex[:6]}"
    make_kp(name)  # 第一个由 fixture 负责清理
    r2 = client.post("/api/kps", json={"subject_id": subject["id"], "name": name})
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"]["code"] == "duplicate_kp"


def test_update_kp_reviewed_flag(client, make_kp):
    kp = make_kp()
    assert kp["reviewed"] is False
    r = client.patch(f"/api/kps/{kp['id']}", json={"reviewed": True}).json()
    assert r["reviewed"] is True


def test_kp_tree_nests_children(client, subject, make_kp):
    parent = make_kp()
    child = make_kp(parent_id=parent["id"])

    tree = client.get(f"/api/subjects/{subject['id']}/kp-tree?grade=七上").json()
    node = next((n for n in tree if n["id"] == parent["id"]), None)
    assert node is not None, "父节点没出现在树的根层"
    assert child["id"] in [c["id"] for c in node["children"]], "子节点没有挂到父节点下"


def test_error_shape_is_stable(client):
    """前端统一读 detail.error.message，因此错误形状必须稳定。"""
    r = client.get("/api/mistakes/99999999")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body["detail"]
    assert "code" in body["detail"]["error"]
    assert "message" in body["detail"]["error"]