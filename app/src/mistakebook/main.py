"""应用入口：装配 FastAPI、静态前端、后台页面与迁移。

启动顺序：先建连接池并跑迁移，再挂路由 —— 否则第一次请求会打在无表的库上。
"""

from __future__ import annotations

import logging
import os
import pathlib

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import settings
from .errors import AppError
from .routers import admin, ai, mistakes, plan, review, subjects

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("mistakebook")

app = FastAPI(title="错题本", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """统一错误响应形状：{"detail": {"error": {"code", "message"}}}"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"error": {"code": exc.code, "message": exc.message}}},
    )


for r in (subjects.router, mistakes.router, review.router, ai.router, plan.router, admin.router):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": settings.deepseek_model}


# ── 静态资源 ──────────────────────────────────────────────────────────
# 上传的题目图片：挂 upload 目录。/uploads/{rel_path}
settings.upload_path.mkdir(parents=True, exist_ok=True)
settings.book_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(settings.upload_path)), name="uploads")

_web = settings.web_path
if _web.is_dir():
    app.mount("/css", StaticFiles(directory=str(_web / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(_web / "js")), name="js")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(_web / "index.html"))

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        # 让平板「添加到主屏幕」后以独立窗口打开，像 App 一样
        return FileResponse(str(_web / "manifest.webmanifest"))
else:
    log.warning("前端目录不存在：%s，仅提供 API", _web)


@app.on_event("startup")
async def on_startup() -> None:
    log.info("连接数据库并执行迁移…")
    applied = db.run_migrations()
    if applied:
        log.info("本次执行的迁移：%s", ", ".join(applied))
    else:
        log.info("数据库已是最新，无需迁移")

    log.info("模型端点：%s（model=%s）", settings.deepseek_base_url, settings.deepseek_model)

    # 启动时自检模型可达性。失败只告警不阻塞启动 ——
    # 模型不可用时孩子仍应能记录错题，只是讲解功能用不了。
    from .services import deepseek

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{settings.deepseek_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            )
        if r.status_code == 200:
            log.info("模型服务连通正常")
        else:
            log.warning(
                "模型服务返回 HTTP %s，读题与讲解可能不可用。检查 DEEPSEEK_API_KEY。",
                r.status_code,
            )
    except Exception as e:  # noqa: BLE001
        log.warning("模型服务自检失败：%s", deepseek._network_hint(e))

    # 定时任务放在最后启动：即使它出问题，也不影响补丁迁移与模型自检。
    # 失败只告警不阻塞 —— 定时任务挂了，手动生成计划仍然可用。
    try:
        from .services import scheduler

        scheduler.start()
    except Exception as e:  # noqa: BLE001
        log.warning("定时任务启动失败：%s（手动生成计划不受影响）", e)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    try:
        from .services import scheduler

        await scheduler.shutdown()
    except Exception as e:  # noqa: BLE001
        log.warning("停止定时任务时出错（不影响退出）：%s", e)
    db.close_pool()


def main() -> None:
    uvicorn.run(
        "mistakebook.main:app",
        host="0.0.0.0",
        port=8000,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()