"""后台管理页面与接口（服务端渲染，Jinja2）。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import db
from ..services import analysis

router = APIRouter(prefix="/admin", tags=["admin"])

import pathlib

templates = Jinja2Templates(
    directory=str(pathlib.Path(__file__).resolve().parents[1] / "templates")
)


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    stats = analysis.mistake_stats()
    usage = db.fetchrow(
        """
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(tokens_in), 0)  AS tokens_in,
               COALESCE(SUM(tokens_out), 0) AS tokens_out,
               COUNT(*) FILTER (WHERE NOT ok) AS failed
        FROM ai_usage
        WHERE created_at >= now() - interval '30 days'
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {"stats": stats, "usage": usage},
    )


@router.get("/coverage", response_class=HTMLResponse)
def coverage_page(request: Request, subject_id: int = 0, grade: str = "") -> HTMLResponse:
    subjects = db.fetch("SELECT id, name FROM subject ORDER BY sort_order")
    if not subject_id and subjects:
        subject_id = subjects[0]["id"]

    summary = analysis.coverage_summary(subject_id, grade or None) if subject_id else None
    return templates.TemplateResponse(
        request,
        "admin/coverage.html",
        {
            "subjects": subjects,
            "subject_id": subject_id,
            "grade": grade,
            "summary": summary,
        },
    )


@router.get("/ai-usage", response_class=HTMLResponse)
def ai_usage_page(request: Request) -> HTMLResponse:
    rows = db.fetch(
        """
        SELECT kind, model,
               COUNT(*) AS calls,
               COALESCE(SUM(tokens_in), 0) AS tokens_in,
               COALESCE(SUM(tokens_out), 0) AS tokens_out,
               COUNT(*) FILTER (WHERE NOT ok) AS failed
        FROM ai_usage
        WHERE created_at >= now() - interval '30 days'
        GROUP BY kind, model
        ORDER BY calls DESC
        """
    )
    recent = db.fetch(
        """
        SELECT kind, model, tokens_in, tokens_out, ok, error, created_at
        FROM ai_usage ORDER BY created_at DESC LIMIT 50
        """
    )
    return templates.TemplateResponse(
        request, "admin/ai_usage.html", {"rows": rows, "recent": recent}
    )


@router.get("/kps", response_class=HTMLResponse)
def kps_page(request: Request, subject_id: int = 0, unreviewed_only: bool = False) -> HTMLResponse:
    subjects = db.fetch("SELECT id, name FROM subject ORDER BY sort_order")
    if not subject_id and subjects:
        subject_id = subjects[0]["id"]

    sql = """
        SELECT id, name, code, grade, book, page_from, page_to, source, confidence, reviewed
        FROM knowledge_point WHERE subject_id = %s
    """
    params: list = [subject_id]
    if unreviewed_only:
        sql += " AND reviewed = false"
    sql += " ORDER BY confidence NULLS FIRST, sort_order, id LIMIT 500"

    rows = db.fetch(sql, tuple(params)) if subject_id else []
    return templates.TemplateResponse(
        request,
        "admin/kps.html",
        {
            "subjects": subjects,
            "subject_id": subject_id,
            "unreviewed_only": unreviewed_only,
            "rows": rows,
        },
    )


@router.post("/kps/{kp_id}/review")
def mark_kp_reviewed(kp_id: int) -> dict:
    n = db.execute("UPDATE knowledge_point SET reviewed = true WHERE id = %s", (kp_id,))
    return {"ok": n > 0}