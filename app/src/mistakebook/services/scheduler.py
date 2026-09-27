"""定时任务：让「定期归纳总结」真正自动发生。

为什么需要它：功能 4 是「定期归纳总结」。如果只能靠用户手动点按钮，
那就不是「定期」——实际结果一定是永远不点。定时任务把它变成默认行为。

两个任务的时间点选择理由：
  - 每晚 21:00 检查今天的复习情况。这个点孩子已经写完作业，
    数据是全天的，且此时间点发通知不算打扰。
  - 每周日 20:00 生成下周计划。周日晚上是家长和孩子一起看计划的自然时机，
    周一一早就有可执行的东西。

时区固定 Asia/Shanghai：容器默认 UTC，不显式指定会让「晚上 9 点」
变成北京时间凌晨 5 点。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import db

log = logging.getLogger("mistakebook.scheduler")

TZ = "Asia/Shanghai"

scheduler = AsyncIOScheduler(timezone=TZ)


# ── 任务实现 ────────────────────────────────────────────────────────


async def nightly_summary() -> dict:
    """每晚汇总：今天复习了多少、还有多少到期、哪些薄弱点在恶化。

    只做统计与提醒，不调模型 —— 每晚都烧一次 token 不值得，
    真正需要 AI 的周计划任务已经覆盖了「给出方案」这件事。
    """
    from ..services import analysis

    stats = analysis.mistake_stats()
    due = db.fetchval(
        "SELECT COUNT(*) FROM mistake WHERE deleted_at IS NULL AND next_review_at <= now()"
    )
    today = dt.date.today().isoformat()
    reviewed_today = db.fetchval(
        """
        SELECT COUNT(*) FROM review_log rl
        JOIN mistake m ON m.id = rl.mistake_id AND m.deleted_at IS NULL
        WHERE rl.reviewed_at::date = %s
        """,
        (today,),
    )

    result = {
        "date": today,
        "total": stats["total"],
        "mastered": stats["mastered_cnt"],
        "due": int(due or 0),
        "reviewed_today": int(reviewed_today or 0),
    }
    log.info(
        "夜间汇总 %s：共 %s 题，已掌握 %s，待复习 %s，今日已复习 %s",
        today, result["total"], result["mastered"], result["due"], result["reviewed_today"],
    )
    return result


async def weekly_plan() -> dict:
    """每周生成一份复习计划。

    调用的是与手动触发**完全相同**的代码路径（generate_plan），
    避免「自动生成的计划」和「手动生成的计划」走两套逻辑而逐渐不一致。
    """
    from ..routers.plan import generate_plan

    try:
        out = await generate_plan(days=7)
    except Exception as e:  # noqa: BLE001 —— 定时任务失败绝不能拖垮进程
        log.warning("周计划生成失败：%s", e)
        return {"ok": False, "error": str(e)}

    log.info("周计划已生成：status=%s", out.get("status"))
    return {"ok": True, "status": out.get("status")}


# ── 注册与生命周期 ──────────────────────────────────────────────────


def start() -> None:
    """注册任务并启动。重复调用是安全的（幂等）。"""
    if scheduler.running:
        log.info("定时任务已在运行，跳过重复启动")
        return

    scheduler.add_job(
        nightly_summary,
        CronTrigger(hour=21, minute=0, timezone=TZ),
        id="nightly_summary",
        replace_existing=True,
        misfire_grace_time=3600,  # 容器在 21:00 时没起来，一小时内补跑
    )
    scheduler.add_job(
        weekly_plan,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=TZ),
        id="weekly_plan",
        replace_existing=True,
        misfire_grace_time=7200,
    )
    scheduler.start()

    jobs = ", ".join(f"{j.id}@{j.next_run_time:%Y-%m-%d %H:%M}" for j in scheduler.get_jobs())
    log.info("定时任务已启动（%s）：%s", TZ, jobs)


async def shutdown() -> None:
    """停止调度器（必须 await）。

    为什么是 async：AsyncIOScheduler._shutdown 带 @run_in_event_loop 装饰器，
    它用 call_soon 把真正的停止动作**排进事件循环**，shutdown() 本身立即返回。
    所以调用后 `running` 仍是 True，必须让出控制权给事件循环才会真正停止。

    实测踩过的坑：在同步函数里调 shutdown() 之后 `running` 永远为 True，
    再次 start() 会因内部状态错乱抛 SchedulerNotRunningError。
    也不能用 time.sleep 等 —— 那恰好会阻塞需要的那个循环。
    """
    if not scheduler.running:
        return

    with contextlib.suppress(Exception):
        scheduler.shutdown(wait=False)

    # 让出控制权，等 call_soon 排队的停止回调真正执行
    for _ in range(50):
        if not scheduler.running:
            break
        await asyncio.sleep(0.01)

    if scheduler.running:
        log.warning("定时任务未能在预期时间内停止")
    else:
        log.info("定时任务已停止")