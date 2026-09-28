"""讲解追问（P6）与 AI 自动归类（P7）的集成测试。

这两条链路都要调模型，而模型在测试环境里不一定可用（也可能慢）。
因此测试策略是**把模型调用替换掉**，只验证：
  - 路由、鉴权式的输入校验、落库与读回；
  - 上下文拼装是否带上了题目信息与历史；
  - 归类结果的**校验逻辑**（模型编造的知识点必须被丢弃）。

真实的模型输出质量由 scripts/acceptance.py 与人工验收覆盖，不在这里断言 ——
对生成文本做断言会得到一堆随机失败的测试。
"""

from __future__ import annotations

import pytest

from mistakebook.routers import ai as ai_router


@pytest.fixture
def fake_model(monkeypatch):
    """替换 deepseek.chat_text / chat_json，记录调用参数供断言。"""
    calls = {"text": [], "json": []}

    async def fake_chat_text(system_text, user_blocks, kind, *, max_tokens=2048):
        calls["text"].append({"kind": kind, "blocks": user_blocks, "system": system_text})
        return "这一步是因为等式两边同时减去 3，所以右边变成 12 - 3 = 9。"

    async def fake_chat_json(system_text, user_blocks, kind, *, max_tokens=2048):
        calls["json"].append({"kind": kind, "blocks": user_blocks, "system": system_text})
        return calls.get("json_result") or {"matched": False, "picked": []}

    monkeypatch.setattr(ai_router.deepseek, "chat_text", fake_chat_text)
    monkeypatch.setattr(ai_router.deepseek, "chat_json", fake_chat_json)
    return calls


def _text_of(blocks) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


# ── P6：追问 ────────────────────────────────────────────────────────


def test_chat_is_empty_before_first_question(client, make_mistake):
    """没问过时返回空列表，且**不建**会话行 —— 否则每道题都会多一个空会话。"""
    m = make_mistake()
    r = client.get(f"/api/ai/explain/{m['id']}/chat")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == []
    assert body["chat_id"] is None

    from mistakebook import db

    n = db.fetchval("SELECT COUNT(*) FROM explain_chat WHERE mistake_id = %s", (m["id"],))
    assert n == 0, "只是看一眼就建了会话行"


def test_ask_and_read_back(client, make_mistake, fake_model):
    """问一句、答一句，两边都要落库，重新拉取还能看到。"""
    m = make_mistake(stem_text="解方程 3x + 5 = 20")
    mid = m["id"]

    r = client.post(f"/api/ai/explain/{mid}/chat", json={"question": "为什么要先移项？"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "为什么要先移项？"
    assert body["messages"][1]["role"] == "assistant"
    assert body["answer"]

    again = client.get(f"/api/ai/explain/{mid}/chat").json()
    assert len(again["messages"]) == 2, "重新打开后对话丢了"
    assert again["messages"][0]["content"] == "为什么要先移项？"


def test_chat_context_includes_question_and_history(client, make_mistake, fake_model):
    """第二次提问的上下文里必须同时有：题目信息、上一轮问答、本次问题。

    缺了题目 → 模型不知道在讲哪道题；缺了历史 → 每次都是新对话，
    孩子没法追问「那刚才那一步呢」。
    """
    m = make_mistake(stem_text="解方程 3x + 5 = 20", answer_text="x = 5")
    mid = m["id"]

    client.post(f"/api/ai/explain/{mid}/chat", json={"question": "第一问"})
    client.post(f"/api/ai/explain/{mid}/chat", json={"question": "那再讲讲第二步"})

    last_call = fake_model["text"][-1]
    text = _text_of(last_call["blocks"])
    assert "解方程 3x + 5 = 20" in text, "题目信息没进上下文"
    assert "x = 5" in text, "正确答案没进上下文"
    assert "第一问" in text, "上一轮的问题没进上下文"
    assert "那再讲讲第二步" in text, "本次问题没进上下文"
    assert last_call["kind"] == "explain_chat"


def test_chat_rejects_empty_question(client, make_mistake, fake_model):
    m = make_mistake()
    for bad in ["", "   ", "\n\t "]:
        r = client.post(f"/api/ai/explain/{m['id']}/chat", json={"question": bad})
        assert r.status_code == 422, f"空问题被接受了：{bad!r}"

    from mistakebook import db

    # 只数这道题的会话 —— 直接 COUNT(*) 整张表会数到别的用例留下的行，
    # 那样的断言在单独跑时通过、整套跑时失败，是最难查的一类假红。
    n = db.fetchval(
        """
        SELECT COUNT(*) FROM explain_message msg
        JOIN explain_chat c ON c.id = msg.chat_id
        WHERE c.mistake_id = %s
        """,
        (m["id"],),
    )
    assert n == 0, "被拒绝的提问仍然写进了库"
    assert fake_model["text"] == [], "空问题也调用了模型"


def test_chat_rejects_overlong_question(client, make_mistake, fake_model):
    """超长问题要挡住：它既会撑爆请求体，也多半是误粘贴。"""
    m = make_mistake()
    r = client.post(f"/api/ai/explain/{m['id']}/chat", json={"question": "x" * 2000})
    assert r.status_code == 422
    assert "字以内" in r.json()["detail"]["error"]["message"]


def test_chat_of_missing_mistake_is_404(client, fake_model):
    assert client.get("/api/ai/explain/99999999/chat").status_code == 404
    assert client.post(
        "/api/ai/explain/99999999/chat", json={"question": "在吗"}
    ).status_code == 404


def test_chat_of_soft_deleted_mistake_is_404(client, make_mistake, fake_model):
    """软删除的错题不能再追问 —— 否则孩子会对着已删除的题聊天。"""
    m = make_mistake()
    client.delete(f"/api/mistakes/{m['id']}")
    assert client.post(
        f"/api/ai/explain/{m['id']}/chat", json={"question": "在吗"}
    ).status_code == 404


def test_clear_chat(client, make_mistake, fake_model):
    m = make_mistake()
    mid = m["id"]
    client.post(f"/api/ai/explain/{mid}/chat", json={"question": "问题一"})
    assert len(client.get(f"/api/ai/explain/{mid}/chat").json()["messages"]) == 2

    assert client.delete(f"/api/ai/explain/{mid}/chat").status_code == 204
    assert client.get(f"/api/ai/explain/{mid}/chat").json()["messages"] == []


def test_chat_history_truncated_from_oldest(client, make_mistake, fake_model):
    """历史过长时从最旧的一侧截断，且题目信息必须仍在上下文里。

    截断如果把题目本身丢掉，模型就会开始凭空回答，
    而界面上完全看不出来 —— 这是最隐蔽的一类故障。
    """
    m = make_mistake(stem_text="标志性题干-ZZZ")
    mid = m["id"]

    for i in range(ai_router.MAX_HISTORY):
        client.post(f"/api/ai/explain/{mid}/chat", json={"question": f"第{i}问"})

    text = _text_of(fake_model["text"][-1]["blocks"])
    assert "标志性题干-ZZZ" in text, "截断历史时把题目信息一起丢掉了"
    assert "第0问" not in text, "最旧的历史没有被截掉，上下文会无限增长"
    assert f"第{ai_router.MAX_HISTORY - 1}问" in text, "最近的一轮被截掉了"


# ── P7：自动归类 ────────────────────────────────────────────────────


def test_suggest_kps_maps_names_to_ids(client, subject, make_kp, make_mistake, fake_model):
    """模型返回名称，接口必须换成真实的知识点 id。"""
    tag = "归类测试"
    kp = make_kp(f"{tag}-一元一次方程")
    m = make_mistake(stem_text="解方程 3x + 5 = 20")

    fake_model["json_result"] = {
        "matched": True,
        "picked": [f"{tag}-一元一次方程"],
        "reason": "题目在解一元一次方程",
    }

    r = client.post("/api/ai/suggest-kps", json={"mistake_id": m["id"], "limit": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] is True
    assert [p["id"] for p in body["picked"]] == [kp["id"]]
    assert body["picked"][0]["name"] == f"{tag}-一元一次方程"


def test_suggest_kps_rejects_hallucinated_names(client, subject, make_kp, make_mistake, fake_model):
    """模型编造候选之外的知识点时，必须丢弃并把 matched 判为 false。

    这是本功能最关键的一道防线：静默接受会让库里出现无法聚合的野知识点，
    而且没有任何人会察觉「学情」为什么统计不到。
    """
    make_kp("真实存在的知识点-归类")
    m = make_mistake(stem_text="解方程 3x + 5 = 20")

    fake_model["json_result"] = {
        "matched": True,
        "picked": ["一元二次方程（库里没有这个）", "勾股定理"],
        "reason": "硬套",
    }

    body = client.post("/api/ai/suggest-kps", json={"mistake_id": m["id"]}).json()
    assert body["picked"] == [], f"编造的知识点被接受了：{body['picked']}"
    assert body["matched"] is False
    assert len(body["rejected"]) == 2, "被拒绝的名称没有回传，无从排查"


def test_suggest_kps_does_not_write(client, subject, make_kp, make_mistake, fake_model):
    """suggest 只给建议，绝不能写库 —— 用户要先确认。"""
    kp = make_kp("只建议不写入-归类")
    m = make_mistake()
    fake_model["json_result"] = {"matched": True, "picked": ["只建议不写入-归类"]}

    client.post("/api/ai/suggest-kps", json={"mistake_id": m["id"]})

    got = client.get(f"/api/mistakes/{m['id']}").json()
    assert got["kps"] == [], "suggest-kps 擅自写入了知识点"


def test_auto_categorize_writes_links(client, subject, make_kp, make_mistake, fake_model):
    """auto-categorize 要真的建立关联，并能在详情里读回来。"""
    kp = make_kp("自动归类写入-归类")
    m = make_mistake()
    fake_model["json_result"] = {
        "matched": True, "picked": ["自动归类写入-归类"], "reason": "匹配",
    }

    body = client.post(f"/api/ai/auto-categorize/{m['id']}").json()
    assert body["matched"] is True

    got = client.get(f"/api/mistakes/{m['id']}").json()
    assert [k["id"] for k in got["kps"]] == [kp["id"]]


def test_auto_categorize_does_not_duplicate(client, subject, make_kp, make_mistake, fake_model):
    """重复点自动归类不能把同一个知识点关联两次。"""
    kp = make_kp("重复归类-归类")
    m = make_mistake(kp_ids=[kp["id"]])
    fake_model["json_result"] = {"matched": True, "picked": ["重复归类-归类"]}

    client.post(f"/api/ai/auto-categorize/{m['id']}")
    client.post(f"/api/ai/auto-categorize/{m['id']}")

    got = client.get(f"/api/mistakes/{m['id']}").json()
    assert [k["id"] for k in got["kps"]] == [kp["id"]], "知识点被关联了多次"


def test_suggest_kps_no_candidates_is_graceful(client, make_mistake, fake_model):
    """科目一个知识点都没有时，要给出可读提示，且**不要白调一次模型**。"""
    m = make_mistake(subject_id=_empty_subject_id(client))
    body = client.post("/api/ai/suggest-kps", json={"mistake_id": m["id"]}).json()
    assert body["matched"] is False
    assert body["picked"] == []
    assert "知识点" in body["reason"], f"提示不可读：{body['reason']}"
    assert fake_model["json"] == [], "没有任何候选时仍然调用了模型，白烧一次 token"


def _empty_subject_id(client) -> int:
    """找一个当前没有任何知识点的科目。"""
    from mistakebook import db

    row = db.fetchrow(
        """
        SELECT s.id FROM subject s
        LEFT JOIN knowledge_point kp ON kp.subject_id = s.id
        WHERE kp.id IS NULL ORDER BY s.sort_order LIMIT 1
        """
    )
    if row is None:
        pytest.skip("所有科目都有知识点，无法验证空候选分支")
    return row["id"]


def test_categorize_candidates_are_scoped_to_subject(client, subject, make_kp, make_mistake, fake_model):
    """候选知识点必须限定在本科目 —— 否则数学题会被归到英语知识点上。"""
    make_kp("本来的科目知识点-归类")
    m = make_mistake(stem_text="解方程")

    body = client.post("/api/ai/suggest-kps", json={"mistake_id": m["id"]}).json()
    text = _text_of(fake_model["json"][-1]["blocks"])
    assert "本来的科目知识点-归类" in text, "本科目知识点没进候选"

    from mistakebook import db

    other = db.fetchrow(
        """
        SELECT kp.name FROM knowledge_point kp
        WHERE kp.subject_id <> %s LIMIT 1
        """,
        (subject["id"],),
    )
    if other:
        # 别的科目若有同名知识点才会误命中，这里只断言候选清单来自本科目
        assert body["candidates"], "候选清单为空"


def test_suggest_kps_of_missing_mistake_is_404(client, fake_model):
    r = client.post("/api/ai/suggest-kps", json={"mistake_id": 99999999})
    assert r.status_code == 404


def test_categorize_limit_is_validated(client, make_mistake, fake_model):
    m = make_mistake()
    assert client.post(
        "/api/ai/suggest-kps", json={"mistake_id": m["id"], "limit": 0}
    ).status_code == 422
    assert client.post(
        "/api/ai/suggest-kps", json={"mistake_id": m["id"], "limit": 99}
    ).status_code == 422