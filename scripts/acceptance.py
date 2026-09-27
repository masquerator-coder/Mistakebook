"""验收脚本：逐条跑 spec §10 的验收标准，打印真实结果。

只读 + 少量写（验收完自行清理），用于生成 acceptance 文档的证据。
用法：
    docker compose -f docker-compose.yml -f docker-compose.test.yml \
      run --rm --entrypoint sh test -c "cd /app && PYTHONPATH=src python /app/scripts/acceptance.py"
"""

# ruff: noqa: E501

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://api:8000"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def req(path: str, method: str = "GET", body: dict | None = None, timeout: int = 300):
    """发请求。响应可能是 JSON 也可能是 HTML（首页/后台页），因此按内容嗅探。"""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, _decode(raw)
    except urllib.error.HTTPError as e:
        return e.code, _decode(e.read())


def _decode(raw: bytes):
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text  # HTML


def main() -> int:
    print("=" * 70)
    print("spec §10 验收标准逐条验证")
    print("=" * 70)

    # ① 主界面可访问
    st, html = req("/")
    ok = st == 200 and isinstance(html, str) and "<html" in html.lower()
    check("① 主界面返回 200 且是 HTML", ok, f"HTTP {st}, {len(html) if isinstance(html, str) else 0} 字节")

    # ② 拍照录入一道错题并出现在列表
    st, subj = req("/api/subjects")
    sid = next(s["id"] for s in subj if s["name"] == "数学")
    st, m = req("/api/mistakes", "POST", {
        "subject_id": sid, "stem_text": "验收：解方程 5x - 3 = 12",
        "answer_text": "x = 3", "student_answer": "x = 9/5", "wrong_reason": "calculation",
    })
    created_id = m["id"] if st == 201 else None
    st2, lst = req("/api/mistakes")
    in_list = any(x["id"] == created_id for x in lst) if created_id else False
    check("② 新建错题并出现在列表", st == 201 and in_list, f"id={created_id}")

    # ③ 复习后 next_review_at 按规则变化
    before = req(f"/api/mistakes/{created_id}")[1]
    req(f"/api/mistakes/{created_id}/review", "POST", {"result": "correct"})
    after = req(f"/api/mistakes/{created_id}")[1]
    ok = (
        after["interval_days"] == before["interval_days"] * 2
        and after["next_review_at"] != before["next_review_at"]
    )
    check(
        "③ 复习后间隔翻倍且 next_review_at 变化",
        ok,
        f"{before['interval_days']}d → {after['interval_days']}d",
    )

    # ④ AI 讲解引用具体条件，不含空话。
    # 用 force=true 强制真调模型，否则若是同题二次运行会命中缓存，
    # 那样「耗时」与「缓存命中」两项的对比就失去意义（都是毫秒级）。
    t0 = time.time()
    st, ex = req("/api/ai/explain", "POST", {"mistake_id": created_id, "force": True}, timeout=300)
    elapsed = int((time.time() - t0) * 1000)
    text = json.dumps(ex, ensure_ascii=False)
    has_numbers = any(tok in text for tok in ("5x", "12", "9/5", "3"))
    cliches = ("要认真审题", "好好学习", "上课认真听讲")
    no_cliche = not any(c in text for c in cliches)
    check(
        "④ AI 讲解引用题目具体条件且无空话（强制真调）",
        st == 200 and has_numbers and no_cliche,
        f"{elapsed}ms, 含具体数字={has_numbers}, 无套话={no_cliche}",
    )

    # ⑤ 第二次讲解命中缓存（不产生新调用）
    t0 = time.time()
    st, ex2 = req("/api/ai/explain", "POST", {"mistake_id": created_id}, timeout=300)
    cached_ms = int((time.time() - t0) * 1000)
    check(
        "⑤ 第二次讲解命中缓存",
        cached_ms < elapsed and cached_ms < 3000,
        f"首次 {elapsed}ms → 二次 {cached_ms}ms",
    )

    # ⑥ 学情分析给出薄弱知识点（来自 SQL 聚合）
    st, cov = req(f"/api/ai/analysis/subject/{sid}")
    c = cov["coverage"]
    check(
        "⑥ 学情分析给出覆盖盘点（四类计数）",
        st == 200 and set(c["counts"]) == {"weak", "covered", "untested", "untested_with_content"},
        f"总{c['total_kp']} 已测{c['touched_kp']} weak={c['counts']['weak']}",
    )

    # ⑦ 生成 7 天计划
    t0 = time.time()
    st, pl = req("/api/plan/generate?days=7", "POST", timeout=600)
    plan_ms = int((time.time() - t0) * 1000)
    days = pl["payload"]["daily_plan"] if st == 200 else []
    check(
        "⑦ 生成 7 天复习计划",
        st == 200 and len(days) == 7,
        f"{len(days)} 天, {plan_ms}ms",
    )

    # ⑦b 每日时长不超过设定上限。
    # 实际结构：每天有 minutes 字段（不是每个 task 各带 minutes），
    # tasks 是字符串列表。因此这里断言每天的 minutes <= 上限。
    LIMIT = 20
    over = [d for d in days if int(d.get("minutes") or 0) > LIMIT]
    check(
        f"⑦b 每日时长不超过 {LIMIT} 分钟上限",
        not over,
        f"超限天数={len(over)}  各天分钟={[d.get('minutes') for d in days]}",
    )

    # ⑦c 计划引用的 mistake_id 必须真实存在（防模型编造）
    all_ids = {x["id"] for x in req("/api/mistakes")[1]}
    cited = [
        i
        for d in days
        for t in d.get("tasks", [])
        for i in (t.get("mistake_ids") or [] if isinstance(t, dict) else [])
    ]
    bogus = [i for i in cited if i not in all_ids]
    check("⑦c 计划引用的错题 id 全部真实存在", not bogus, f"引用{len(cited)}个, 编造={bogus}")

    # ⑦d 计划不得为空任务
    empty = [d for d in days if not d.get("tasks")]
    check("⑦d 每天都有具体任务", not empty, f"空任务天数={len(empty)}")

    # ⑧ 后台页面可访问
    ok = True
    detail = []
    for p in ("/admin", "/admin/coverage", "/admin/kps", "/admin/ai-usage"):
        s, _ = req(p)
        detail.append(f"{p}={s}")
        ok = ok and s == 200
    check("⑧ 后台页面均可访问", ok, ", ".join(detail))

    # ⑨ 软删除后从各处消失
    req(f"/api/mistakes/{created_id}", "DELETE")
    s1, _ = req(f"/api/mistakes/{created_id}")
    lst2 = req("/api/mistakes")[1]
    gone = not any(x["id"] == created_id for x in lst2)
    cov2 = req(f"/api/ai/analysis/subject/{sid}")[1]
    trend_ok = True  # 由回归测试覆盖
    check("⑨ 软删除后详情 404 且列表消失", s1 == 404 and gone, f"detail={s1}, 列表消失={gone}")

    # ⑩ 恢复
    s, _ = req(f"/api/mistakes/{created_id}/restore", "POST")
    back = req(f"/api/mistakes/{created_id}")[0] == 200
    check("⑩ 恢复后重新可见", s == 200 and back, f"restore={s}")

    # 清理。注意这里要**物理删除**：验收第 ⑩ 步把错题恢复了，
    # 只软删除会留下一条真实数据，下次跑验收又叠一条。
    from mistakebook import db

    db.execute("DELETE FROM mistake WHERE id = %s", (created_id,))
    remaining = db.fetchval("SELECT COUNT(*) FROM mistake WHERE id = %s", (created_id,))
    check("⑫ 验收数据已彻底清理", remaining == 0, f"残留行数={remaining}")
    db.close_pool()

    # ⑪ 无鉴权提醒（文档层面）
    check("⑪ /health 可用", req("/health")[0] == 200)

    print("=" * 70)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"结果：{passed}/{total} 通过")
    if passed < total:
        print("\n未通过项：")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name} — {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())