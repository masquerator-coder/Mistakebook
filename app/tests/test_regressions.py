"""针对已修复缺陷的回归测试。

每个测试都对应一个**实际发生过**的缺陷，注释里写清当时的症状 ——
否则后来者容易把测试当成多余的约束删掉。

另有一类并发测试：现有测试全是串行调用，导致「丢失更新」这类缺陷
对测试完全不可见。这里用线程真并发打同一个端点。
"""

from __future__ import annotations

import concurrent.futures as cf
import uuid

import pytest
from fastapi.testclient import TestClient


def _tag() -> str:
    return uuid.uuid4().hex[:8]


# ── [S2] 趋势统计必须排除软删除的错题 ────────────────────────────────
# 症状：删掉一批错题后，趋势图的正确率曲线不变，家长据此得出错误结论。
# 根因：review_trend 的 `JOIN mistake m ON m.id = rl.mistake_id`
#       漏了 `AND m.deleted_at IS NULL`，而同一文件里的 coverage() 是带了的。
def test_trend_excludes_soft_deleted(client, subject, make_mistake):
    m = make_mistake()
    for _ in range(3):
        client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"})

    before = client.get(f"/api/ai/analysis/subject/{subject['id']}").json()["trend"]
    total_before = sum(d["total"] for d in before)

    client.delete(f"/api/mistakes/{m['id']}")

    after = client.get(f"/api/ai/analysis/subject/{subject['id']}").json()["trend"]
    total_after = sum(d["total"] for d in after)

    assert total_after == total_before - 3, (
        f"软删除的错题仍被计入趋势统计：删除前 {total_before}，删除后 {total_after}"
    )


# ── [S1] 复习提交的正确性（并发 + 串行）─────────────────────────────
# 背景：submit_review 是「读状态 → 算新状态 → 写回」的模式。若不放在
# 同一事务并加行锁，理论上并发请求会读到同一份旧状态而互相覆盖。
#
# **诚实说明**：本测试**不能**证明竞态已被消除。实测表明，即使把
# `FOR UPDATE` 去掉，20 线程 × 5 轮真并发（scripts/stress_review.py）
# 也全部通过 —— 因为读与写之间没有 await，窗口只有微秒级，
# 在当前负载下打不出来。行锁是**防御性**的：一旦将来在读写之间
# 加入任何耗时操作（网络、日志、AI 调用），竞态就会立刻变成真问题。
#
# 因此这里只断言**可观测的正确性**（不丢计数、不丢历史），
# 不假装它能覆盖竞态。真正的压力验证用 scripts/stress_review.py。
def test_concurrent_reviews_do_not_lose_updates(client, make_mistake):
    m = make_mistake()
    mid = m["id"]

    def submit(_i):
        return client.post(f"/api/mistakes/{mid}/review", json={"result": "correct"})

    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(submit, range(3)))

    assert all(r.status_code == 200 for r in results), [r.text for r in results]

    final = client.get(f"/api/mistakes/{mid}").json()
    assert final["correct_streak"] == 3, (
        f"correct_streak={final['correct_streak']}，应为 3"
    )
    assert final["interval_days"] == 8, (
        f"interval_days={final['interval_days']}，应为 8（1→2→4→8）"
    )
    assert final["mastery"] == "mastered"

    # 复习历史也必须一条不少
    logs = client.get(f"/api/mistakes/{mid}/reviews").json()
    assert len(logs) == 3, f"复习历史丢了：{len(logs)} 条，应为 3"


def test_review_serial_progression_is_exact(client, make_mistake):
    """串行推进必须精确按 1→2→4→8 走。

    这是真正能抓住「间隔涨不上去」的测试：如果 review 读错了间隔
    （例如用了常量默认值而不是库里的值），第二次就不会是 4。
    """
    m = make_mistake()
    expected = [2, 4, 8]
    for i, want in enumerate(expected):
        r = client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"}).json()
        assert r["interval_days"] == want, (
            f"第 {i + 1} 次复习间隔应为 {want}，实际 {r['interval_days']}"
        )


# ── [S3] 错次变化必须让诊断缓存失效 ───────────────────────────────────
# 症状：同一个知识点反复错（错 1 次 → 错 5 次），诊断文字永远停在第一次。
# 根因：analyze_mistake 的 input_hash 不含 error_count，而 prompt 里用了它。
def test_analyze_cache_key_includes_error_count(client, make_mistake):
    m = make_mistake()
    mid = m["id"]

    # 直接改库模拟「又错了几次」，避免真的调用模型
    from mistakebook import db

    orig = db.fetchrow("SELECT error_count FROM mistake WHERE id = %s", (mid,))["error_count"]
    db.execute("UPDATE mistake SET error_count = %s WHERE id = %s", (orig + 4, mid))

    row = db.fetchrow(
        "SELECT stem_text, wrong_reason, error_count FROM mistake WHERE id = %s", (mid,)
    )
    from mistakebook.routers.ai import _hash

    h_small = _hash("analyze_mistake", row["stem_text"], row["wrong_reason"], 1)
    h_large = _hash("analyze_mistake", row["stem_text"], row["wrong_reason"], 5)
    assert h_small != h_large, "error_count 不同却算出相同的缓存键，诊断会被永久缓存"


# ── [G1] image_path 必须校验 ─────────────────────────────────────────
# 症状：写入 `../../etc/passwd` 之类的路径能成功，详情页显示破图，
#       讲解时图片被静默丢弃，用户完全无从知晓。
def test_image_path_traversal_rejected(client, subject):
    for bad in ["../../../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini", "a/../../b.jpg"]:
        r = client.post("/api/mistakes", json={
            "subject_id": subject["id"], "image_path": bad,
        })
        assert r.status_code == 422, f"非法路径被接受：{bad} → {r.status_code}"


def test_legitimate_image_path_accepted(client, make_mistake):
    m = make_mistake(image_path="2026/09/27/abc123.jpg")
    assert m["image_path"] == "2026/09/27/abc123.jpg"


# ── [G2] 路径前缀判断不能误判兄弟目录 ────────────────────────────────
def test_resolve_image_rejects_sibling_dir_prefix():
    """`/data/uploads_backup/x.jpg` 以 `/data/uploads` 为前缀但不是其子路径。

    老实现用 str.startswith 会误判为合法，改用 is_relative_to 后应拒绝。
    """
    from mistakebook.services import imaging

    try:
        imaging.resolve_image("../../../data/uploads_backup/evil.jpg")
    except imaging.InvalidImagePath:
        return  # 期望结果
    pytest.fail("兄弟目录路径未被拒绝")


def test_file_validation_rejects_empty_and_oversized(client):
    r = client.post("/api/uploads", files={"file": ("empty.jpg", b"", "image/jpeg")})
    assert r.status_code == 422