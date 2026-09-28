"""提示词语气的回归测试。

为什么需要这个文件：讲解的语气是**产品需求**，不是随便写写的措辞。
用户明确提过：讲解要通俗易懂，不能过于正式，否则孩子反感。
但语气是最容易在后续调优中被无声改坏的东西 —— 改提示词时看到
「用初一学生能懂的话」还在，就以为什么都没变，而决定语气的那几条
（禁用书面语清单、学长口吻、不评价孩子）可能在重构里被删掉，
且**没有任何测试会变红**。

因此这里分两层：
  1. 提示词文本层（离线、必跑）：禁用词清单必须还在，口吻设定必须还在。
  2. 真实输出层（需要模型、默认跳过）：把真跑出来的讲解做一遍禁用词与
     套话扫描。文本层只能保证「我们要求了」，这一层才能发现「模型没照做」。
     用 MB_RUN_MODEL_TESTS=1 打开。
"""

from __future__ import annotations

import os
import re

import pytest

from mistakebook.services import prompts

# ── 必须出现的「防官腔」约束 ──────────────────────────────────────────

# 这些词是模型默认就会写的书面语。它们出现在**禁用清单**里是被允许的
# （提示词要指名道姓地禁掉它们），但不允许作为普通正文出现。
BANNED_PHRASES = [
    "由题意可知",
    "综上所述",
    "本题考查",
    "值得注意的是",
    "该生",
    "有待加强",
    "掌握不牢",
    "建议加强",
    "鉴于",
]

# 每套提示词都必须带的口吻设定。
STUDENT_VOICE_PROMPTS = {
    "EXPLAIN_SYSTEM": prompts.EXPLAIN_SYSTEM,
    "CHAT_SYSTEM": prompts.CHAT_SYSTEM,
    "ANALYZE_MISTAKE_SYSTEM": prompts.ANALYZE_MISTAKE_SYSTEM,
    "PLAN_SYSTEM": prompts.PLAN_SYSTEM,
}


def test_all_student_facing_prompts_ban_officialese():
    """每套会出文字的提示词都必须明确禁用书面语。

    只写「说人话」不够 —— 模型会理解成「技术上正确即可」。
    必须把具体禁用词列出来，它才会真的避开。
    """
    for name, text in STUDENT_VOICE_PROMPTS.items():
        assert "书面语" in text or "官方说法" in text, (
            f"{name} 没有要求避免书面语，模型会退回默认的正式腔"
        )
        banned_here = [p for p in BANNED_PHRASES if p in text]
        assert len(banned_here) >= 2, (
            f"{name} 的禁用词太少（只有 {banned_here}）。"
            "「不要用书面语」这种笼统说法对模型无效，要指名道姓。"
        )


def test_prompts_use_warm_not_formal_persona():
    """口吻必须是「大几届的学长/姐姐」，不能退回「学习诊断助手」。"""
    for name, text in STUDENT_VOICE_PROMPTS.items():
        assert "学长" in text or "姐姐" in text, f"{name} 没有设定同龄人身份"
        assert "学习诊断助手" not in text, f"{name} 退回了没有人味的助手身份"
        assert "初中老师" not in text, (
            f"{name} 用的是老师身份 —— 老师口吻容易写成课堂上那种书面语"
        )


def test_prompts_address_student_as_you():
    """必须用「你」直接对话，而不是第三人称「该学生」。"""
    for name in ("EXPLAIN_SYSTEM", "CHAT_SYSTEM", "ANALYZE_MISTAKE_SYSTEM"):
        text = STUDENT_VOICE_PROMPTS[name]
        assert "用「你」" in text, f"{name} 没有要求用第二人称"


def test_prompts_forbid_judging_the_child():
    """禁止评价孩子本人 —— 被评判一次，孩子就不再来问了。"""
    for name in ("EXPLAIN_SYSTEM", "CHAT_SYSTEM", "ANALYZE_MISTAKE_SYSTEM"):
        text = STUDENT_VOICE_PROMPTS[name]
        assert "说事不说人" in text or "不评价" in text or "不训人" in text, (
            f"{name} 没有禁止评价孩子本人"
        )


def test_prompts_forbid_platitudes():
    """禁止空话套话。空话比没有更伤。"""
    assert "鸡汤" in prompts.EXPLAIN_SYSTEM
    assert "鸡汤" in prompts.CHAT_SYSTEM
    # 周计划的鼓励语是最容易变成空话的字段，必须要求引用具体事实
    assert "具体事实" in prompts.PLAN_SYSTEM
    assert "继续努力" in prompts.PLAN_SYSTEM, (
        "PLAN_SYSTEM 没有点名禁止「继续努力」这类套话"
    )


def test_explain_forbids_guessing_where_child_went_wrong():
    """讲错因时不许罗列「可能是A，也可能是B」。

    猜错会让孩子觉得「你根本没看懂我写的」，比不猜更糟。
    """
    text = prompts.EXPLAIN_SYSTEM
    assert "不要罗列" in text or "可能是 A" in text or "可能是A" in text, (
        "没有禁止罗列猜测性的错因"
    )
    assert "不要编" in text, "没有要求看不出来时如实说，而不是编一个错因"


def test_plan_requires_rest_days():
    """计划必须留休息日 —— 排满了执行不下去，等于没有计划。"""
    assert "休息日" in prompts.PLAN_SYSTEM
    assert "排满" in prompts.PLAN_SYSTEM, "没有说明排满的后果，模型容易排满"


def test_prompts_keep_output_contracts():
    """语气改归语气，JSON 字段契约不能动 —— 前端按字段名取值。"""
    assert '"summary"' in prompts.EXPLAIN_SYSTEM
    assert '"error_analysis"' in prompts.EXPLAIN_SYSTEM
    assert '"steps"' in prompts.EXPLAIN_SYSTEM
    assert '"key_point"' in prompts.EXPLAIN_SYSTEM
    assert '"hint"' in prompts.EXPLAIN_SYSTEM
    assert '"similar_hint"' in prompts.EXPLAIN_SYSTEM

    assert '"diagnosis"' in prompts.ANALYZE_MISTAKE_SYSTEM
    assert '"gap_type"' in prompts.ANALYZE_MISTAKE_SYSTEM
    assert '"practice_advice"' in prompts.ANALYZE_MISTAKE_SYSTEM

    for field in ('"priority_gaps"', '"daily_plan"', '"score_strategy"', '"encouragement"'):
        assert field in prompts.PLAN_SYSTEM, f"周计划少了字段 {field}"

    assert "不要输出 JSON" in prompts.CHAT_SYSTEM, "追问会输出 JSON，前端会显示出一堆括号"


def test_read_question_prompt_unchanged_in_substance():
    """读题提示词是纯转录任务，不涉及语气 —— 但它的「不许猜」约束不能丢。"""
    text = prompts.READ_QUESTION_SYSTEM
    assert "不要猜" in text
    assert "只做识别" in text
    assert "kp_suggestions" in text


# ── 真实输出层的检查（需要模型）────────────────────────────────────────


def _contains_officialese(text: str) -> list[str]:
    """找出正文里出现的官腔词。

    注意：这里扫描的是**模型输出**，不是提示词。提示词里出现这些词
    是刻意的（在禁用清单里点名）。
    """
    hits = []
    for p in BANNED_PHRASES:
        if p in text:
            hits.append(p)
    # 「首先…其次…最后」这种排比也是典型报告腔
    if re.search(r"首先.{0,40}其次", text, re.S):
        hits.append("首先…其次…的排比")
    return hits


def _collect_strings(obj) -> list[str]:
    """把 JSON 输出里的所有字符串捞出来，逐个扫禁用词。"""
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out += _collect_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _collect_strings(v)
    return out


@pytest.mark.skipif(
    os.environ.get("MB_RUN_MODEL_TESTS") != "1",
    reason="需要模型服务；设 MB_RUN_MODEL_TESTS=1 打开",
)
def test_real_explanation_has_no_officialese(client, make_mistake):
    """真跑一次讲解，扫描输出里有没有官腔。

    这是唯一能发现「我们要求了但模型没照做」的检查。
    默认跳过，因为它是慢测试且依赖模型可用性。
    """
    import time

    m = make_mistake(
        stem_text="解方程 3x + 5 = 20", answer_text="x = 5",
        student_answer="x = 25", wrong_reason="calculation",
    )
    client.post("/api/ai/explain", json={"mistake_id": m["id"], "force": True})

    payload = None
    for _ in range(60):
        time.sleep(2)
        out = client.get(f"/api/ai/explain/{m['id']}/cached").json()
        if out["status"] == "done":
            payload = out["payload"]
            break
    assert payload is not None, "讲解一直没生成完"

    all_text = "\n".join(_collect_strings(payload))
    hits = _contains_officialese(all_text)
    assert not hits, (
        f"讲解里出现了官腔：{hits}\n"
        f"这些词会让孩子觉得在被说教。全文：\n{all_text}"
    )

    # 口语痕迹的检查。
    # 注意别用「这题/你看/咱/坑」这种具体词去匹配 —— 实测模型会用
    # 「你想想」「把 +5 弄走」「对上了」这类同样口语但不在词表里的说法，
    # 按词表匹配会误报。改成检查更本质的信号：第二人称 + 短句。
    assert "你" in all_text, f"讲解里没有对学生说话，可能退回了客观陈述：\n{all_text}"

    sentences = [s for s in re.split(r"[。！？\n]", all_text) if s.strip()]
    if sentences:
        avg_len = sum(len(s) for s in sentences) / len(sentences)
        assert avg_len < 45, (
            f"平均句长 {avg_len:.0f} 字，太长了 —— 长句是书面语的典型特征：\n{all_text}"
        )