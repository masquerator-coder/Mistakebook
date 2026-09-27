"""测试夹具。

设计原则：**测试绝不能污染真实数据**。
这些测试打的是真实 Postgres（不是内存库），所以清理必须彻底：
  - 错题走 API 的软删除后，还要**物理删除**，否则 dev 库会越跑越脏；
  - 通过 API 建的知识点要在 teardown 里删掉。
所有 teardown 都放在 fixture 的 yield 之后，并且用 try/finally 兜底，
保证断言失败时也会清理。
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

# 必须在 import mistakebook 之前设好环境，settings 是模块级单例
os.environ.setdefault("DEEPSEEK_BASE_URL", "http://fq-dgx-01.local:8888/v1")
os.environ.setdefault("DEEPSEEK_MODEL", "deepseek-v4.1-flash")
os.environ.setdefault("DEEPSEEK_API_KEY", "fq-dgx")


@pytest.fixture(scope="session")
def client():
    from mistakebook import db
    from mistakebook.main import app

    with TestClient(app) as c:
        yield c
    db.close_pool()


@pytest.fixture
def subject(client):
    """取一个已存在的科目用于挂载测试数据。

    不新建科目：科目由迁移预置，测试不需要造它，建了还要收拾。
    """
    subs = client.get("/api/subjects").json()
    assert subs, "迁移没有预置任何科目"
    # 优先用数学，取不到就用第一个
    return next((s for s in subs if s["name"] == "数学"), subs[0])


@pytest.fixture
def make_kp(client, subject):
    """通过 API 建知识点，teardown 时删除。"""
    created: list[int] = []

    def _make(name: str | None = None, **over):
        payload = {
            "subject_id": subject["id"],
            "name": name or f"测试知识点-{uuid.uuid4().hex[:8]}",
            "grade": "七上",
        }
        payload.update(over)
        r = client.post("/api/kps", json=payload)
        assert r.status_code == 201, r.text
        data = r.json()
        created.append(data["id"])
        return data

    yield _make

    for kp_id in created:
        try:
            client.delete(f"/api/kps/{kp_id}")
        except Exception:  # noqa: BLE001 —— 清理失败不应掩盖真正的测试失败
            pass


@pytest.fixture
def make_mistake(client, subject):
    """通过 API 建错题，teardown 时**物理删除**（不只是软删除）。"""
    created: list[int] = []

    def _make(**over):
        payload = {
            "subject_id": subject["id"],
            "stem_text": f"测试题-{uuid.uuid4().hex[:8]}",
            "stem_source": "typed",
        }
        payload.update(over)
        r = client.post("/api/mistakes", json=payload)
        assert r.status_code == 201, r.text
        data = r.json()
        created.append(data["id"])
        return data

    yield _make

    # API 只能软删除，测试数据要真正清掉，否则库里会堆积 deleted_at 不为空的行
    from mistakebook import db

    for mid in created:
        try:
            db.execute("DELETE FROM mistake WHERE id = %s", (mid,))
        except Exception:  # noqa: BLE001
            pass