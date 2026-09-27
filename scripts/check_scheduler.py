"""诊断脚本：确认定时任务的生命周期行为（不是测试，用于人工核对）。

在单个事件循环里跑一遍 start → shutdown → start，
确认 shutdown 真的能停、再 start 真的能起。
"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from mistakebook.services import scheduler as S  # noqa: E402


async def main() -> None:
    S.start()
    print("start 后 running =", S.scheduler.running)
    print("jobs =", [j.id for j in S.scheduler.get_jobs()])
    job = S.scheduler.get_job("nightly_summary")
    print("nightly trigger =", job.trigger)
    print("nightly tz      =", job.trigger.timezone)
    print("weekly trigger  =", S.scheduler.get_job("weekly_plan").trigger)

    await S.shutdown()
    print("shutdown 后 running =", S.scheduler.running)

    S.start()
    print("再 start 后 running =", S.scheduler.running, "jobs =", len(S.scheduler.get_jobs()))
    await S.shutdown()
    print("最终 running =", S.scheduler.running)


asyncio.run(main())