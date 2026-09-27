"""定时任务的测试。

定时任务的典型失败模式是「注册成功但一跑就抛异常」，而且因为是后台
异步执行，异常只出现在日志里，没人发现。所以这里直接调用任务函数体，
断言它真的能跑通并返回合理结果。

不测 APScheduler 本身的调度（那是第三方库的事），只测我们写的那两个函数
以及注册是否把任务真的加进去了。
"""

from __future__ import annotations


async def test_nightly_summary_runs(client):
    """夜间汇总必须能跑通并返回计数。

    `client` fixture 保证迁移已执行、连接池已建立 —— 任务函数自己
    不建池，直接裸调会在真实运行环境之外失败。
    """
    from mistakebook.services import scheduler

    out = await scheduler.nightly_summary()

    for key in ("date", "total", "mastered", "due", "reviewed_today"):
        assert key in out, f"汇总结果缺少字段 {key}"
    assert isinstance(out["total"], int)
    assert out["total"] >= 0
    # 计数不能是负数（SQL 写错很容易出现 -1）
    for k in ("mastered", "due", "reviewed_today"):
        assert out[k] >= 0, f"{k} 为负数：{out[k]}"


async def test_nightly_summary_counts_today_review(client, make_mistake):
    """今天提交的复习必须被算进 reviewed_today。"""
    from mistakebook.services import scheduler

    before = (await scheduler.nightly_summary())["reviewed_today"]

    m = make_mistake()
    client.post(f"/api/mistakes/{m['id']}/review", json={"result": "correct"})

    after = (await scheduler.nightly_summary())["reviewed_today"]
    assert after == before + 1, f"今日复习数没有增加：{before} → {after}"


def _cron_field(trigger, name):
    """取出 cron 触发器中某个字段的第一个表达式值。

    按字段名查而不是按下标，避免依赖 APScheduler 内部的字段顺序。
    """
    for f in trigger.fields:
        if f.name == name:
            return f.expressions[0]
    raise AssertionError(f"触发器没有字段 {name}")


async def test_scheduler_registers_both_jobs(client):
    """两个任务都必须被注册，且时间点正确。

    时间点写错（例如漏了时区）会让「晚上 9 点」变成凌晨 5 点，
    是那种不报错但完全没用的 bug。

    必须是 async 测试：AsyncIOScheduler.start() 要求当前线程有运行中的
    事件循环（内部调 asyncio.get_running_loop()）。这与生产一致 ——
    main.py 的 startup 钩子本身就是 async 的。
    """
    from mistakebook.services import scheduler

    scheduler.start()
    try:
        jobs = {j.id: j for j in scheduler.scheduler.get_jobs()}
        assert "nightly_summary" in jobs, "夜间汇总任务未注册"
        assert "weekly_plan" in jobs, "周计划任务未注册"

        nightly = jobs["nightly_summary"].trigger
        # 用 trigger 对象断言，不要断言 str(trigger) —— APScheduler 的
        # 字符串形式**不含时区**（实测 `cron[hour='21', minute='0']`），
        # 用字符串找时区是个永远为假的断言。
        assert nightly.timezone.key == "Asia/Shanghai", f"夜间汇总时区不对：{nightly.timezone}"
        assert _cron_field(nightly, "hour").first == 21, f"夜间汇总小时不对：{nightly}"
        assert _cron_field(nightly, "minute").first == 0

        weekly = jobs["weekly_plan"].trigger
        assert weekly.timezone.key == "Asia/Shanghai", f"周计划时区不对：{weekly.timezone}"
        # APScheduler 把 'sun' 归一化成数字（周一=0，故周日=6）。
        # 直接断言 6，并顺带验证它就是"周日"而不是别的星期。
        assert _cron_field(weekly, "day_of_week").first == 6, (
            f"周计划日期不对（应为周日=6）：{weekly}"
        )
        assert _cron_field(weekly, "hour").first == 20, f"周计划小时不对：{weekly}"

        assert scheduler.scheduler.running
    finally:
        await scheduler.shutdown()


async def test_start_is_idempotent(client):
    """重复 start 不应抛异常，也不应注册出重复任务。"""
    from mistakebook.services import scheduler

    scheduler.start()
    try:
        scheduler.start()  # 第二次应被跳过
        ids = [j.id for j in scheduler.scheduler.get_jobs()]
        assert sorted(ids) == ["nightly_summary", "weekly_plan"], f"任务重复了：{ids}"
    finally:
        await scheduler.shutdown()


async def test_shutdown_then_start_again(client):
    """停掉之后必须能再启动。

    这是实测踩过的 bug：AsyncIOScheduler.shutdown() 带 @run_in_event_loop，
    真正的停止是 call_soon 排进事件循环的，同步调用后 `running` 仍为 True，
    再 start() 会因内部状态错乱抛 SchedulerNotRunningError。
    因此 shutdown 必须是 async 且要 await 到 running 真的变 False。
    """
    from mistakebook.services import scheduler

    scheduler.start()
    await scheduler.shutdown()
    assert not scheduler.scheduler.running, "shutdown 之后 running 仍为 True（异步停止没生效）"

    scheduler.start()
    try:
        assert scheduler.scheduler.running
        assert len(scheduler.scheduler.get_jobs()) == 2
    finally:
        await scheduler.shutdown()


async def test_shutdown_is_idempotent(client):
    """重复 shutdown 不应抛异常 —— 退出钩子可能被调用多次。"""
    from mistakebook.services import scheduler

    scheduler.start()
    await scheduler.shutdown()
    await scheduler.shutdown()  # 不应抛 SchedulerNotRunningError
    assert not scheduler.scheduler.running


async def test_weekly_plan_job_survives_ai_failure(client, monkeypatch):
    """周计划任务在模型不可用时必须吞掉异常并如实报告，不能抛出去。

    否则 APScheduler 会把异常记进日志而任务本身「看起来正常」，
    或者更糟：异常冒泡影响调度器。
    """
    from mistakebook.routers import plan as plan_router
    from mistakebook.services import scheduler

    async def boom(days: int = 7):
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(plan_router, "generate_plan", boom)

    out = await scheduler.weekly_plan()
    assert out["ok"] is False
    assert "模型不可用" in out["error"]