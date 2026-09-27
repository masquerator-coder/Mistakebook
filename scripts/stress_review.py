"""并发安全验证脚本（不属于 pytest 套件）。

用途：验证 `POST /api/mistakes/{id}/review` 在真并发下不丢失更新。
用真线程 + Barrier 让请求同时到达，比 TestClient 更能暴露竞态
（TestClient 内部可能串行化请求，导致竞态测不出来）。

用法：
    python scripts/stress_review.py [并发数] [轮数]
"""

from __future__ import annotations

import concurrent.futures as cf
import sys
import threading
import urllib.request
import json

BASE = "http://localhost:8000"


def _post(url: str) -> int:
    req = urllib.request.Request(
        url, data=b'{"result":"correct"}', headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    failures = 0
    for rnd in range(1, rounds + 1):
        body = json.dumps({"subject_id": 2, "stem_text": f"并发压测-{rnd}"}).encode()
        req = urllib.request.Request(
            f"{BASE}/api/mistakes", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            mid = json.loads(r.read().decode())["id"]

        barrier = threading.Barrier(n)

        def worker(_i: int) -> None:
            barrier.wait()  # 让所有线程尽量同时发出请求
            try:
                _post(f"{BASE}/api/mistakes/{mid}/review")
            except Exception as e:  # noqa: BLE001
                print(f"    请求异常: {e}")

        with cf.ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(worker, range(n)))

        final = _get(f"{BASE}/api/mistakes/{mid}")
        logs = _get(f"{BASE}/api/mistakes/{mid}/reviews")
        ok = final["correct_streak"] == n and len(logs) == n
        if not ok:
            failures += 1
        print(
            f"  第 {rnd} 轮: streak={final['correct_streak']}/{n} "
            f"interval={final['interval_days']} logs={len(logs)}/{n} "
            + ("PASS" if ok else "*** 丢失更新 ***")
        )

        # 清理
        req = urllib.request.Request(f"{BASE}/api/mistakes/{mid}", method="DELETE")
        urllib.request.urlopen(req, timeout=30).read()

    print(f"\n{rounds - failures}/{rounds} 轮通过")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())