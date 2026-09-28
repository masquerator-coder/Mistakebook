"""错题 CRUD 与图片上传。

软删除约定（spec §6.5）：删除只写 deleted_at，**所有查询都必须带
`deleted_at IS NULL`**。这是本项目最容易漏的地方，改任何查询时先检查它。
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, File, Query, Response, UploadFile

from .. import db
from ..config import settings
from ..errors import AppError, NotFoundError, ValidationError
from ..models import (
    MistakeCreate,
    MistakeOut,
    MistakeUpdate,
    UploadOut,
)
from ..services import imaging, srs

router = APIRouter(prefix="/api", tags=["mistakes"])

_MISTAKE_SELECT = """
    SELECT m.id, m.subject_id, s.name AS subject_name, m.image_path, m.stem_text,
           m.stem_source, m.answer_text, m.student_answer, m.wrong_reason, m.note,
           m.mastery, m.error_count, m.correct_streak, m.interval_days, m.created_at,
           m.last_review_at, m.next_review_at
    FROM mistake m
    JOIN subject s ON s.id = m.subject_id
"""

_KP_SELECT = """
    SELECT kp.id, kp.subject_id, kp.parent_id, kp.name, kp.code, kp.grade, kp.book,
           kp.page_from, kp.page_to, kp.source, kp.confidence, kp.reviewed, kp.sort_order
    FROM knowledge_point kp
    JOIN mistake_kp mk ON mk.kp_id = kp.id
    WHERE mk.mistake_id = %s
    ORDER BY kp.sort_order, kp.id
"""


def _attach_kps(rows: list[dict]) -> list[dict]:
    """批量装配知识点，避免 N+1 查询。"""
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    kp_rows = db.fetch(
        """
        SELECT mk.mistake_id, kp.id, kp.subject_id, kp.parent_id, kp.name, kp.code,
               kp.grade, kp.book, kp.page_from, kp.page_to, kp.source, kp.confidence,
               kp.reviewed, kp.sort_order
        FROM mistake_kp mk
        JOIN knowledge_point kp ON kp.id = mk.kp_id
        WHERE mk.mistake_id = ANY(%s)
        ORDER BY kp.sort_order, kp.id
        """,
        (ids,),
    )
    grouped: dict[int, list[dict]] = {i: [] for i in ids}
    for kp in kp_rows:
        mid = kp.pop("mistake_id")
        grouped[mid].append(kp)
    for r in rows:
        r["kps"] = grouped.get(r["id"], [])
    return rows


def _get_mistake(mistake_id: int) -> dict:
    row = db.fetchrow(_MISTAKE_SELECT + " WHERE m.id = %s AND m.deleted_at IS NULL", (mistake_id,))
    if row is None:
        raise NotFoundError("错题不存在或已删除")
    return _attach_kps([row])[0]


@router.get("/mistakes", response_model=list[MistakeOut])
def list_mistakes(
    subject_id: int | None = None,
    mastery: str | None = None,
    due_only: bool = False,
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
) -> list[dict]:
    sql = _MISTAKE_SELECT + " WHERE m.deleted_at IS NULL"
    params: list = []
    if subject_id is not None:
        sql += " AND m.subject_id = %s"
        params.append(subject_id)
    if mastery is not None:
        sql += " AND m.mastery = %s"
        params.append(mastery)
    if due_only:
        sql += " AND m.next_review_at IS NOT NULL AND m.next_review_at <= now()"
    sql += " ORDER BY COALESCE(m.next_review_at, m.created_at) ASC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    return _attach_kps(db.fetch(sql, tuple(params)))


@router.get("/mistakes/due", response_model=list[MistakeOut])
def due_mistakes(limit: int = Query(default=50, le=500)) -> list[dict]:
    """今日待复习。这是「功能 4」每天真正被用到的入口。"""
    rows = db.fetch(
        _MISTAKE_SELECT
        + """
        WHERE m.deleted_at IS NULL
          AND m.next_review_at IS NOT NULL
          AND m.next_review_at <= now()
        ORDER BY m.next_review_at ASC
        LIMIT %s
        """,
        (limit,),
    )
    return _attach_kps(rows)


@router.get("/mistakes/{mistake_id}", response_model=MistakeOut)
def get_mistake(mistake_id: int) -> dict:
    return _get_mistake(mistake_id)


@router.post("/mistakes", response_model=MistakeOut, status_code=201)
def create_mistake(payload: MistakeCreate) -> dict:
    if db.fetchval("SELECT 1 FROM subject WHERE id = %s", (payload.subject_id,)) is None:
        raise NotFoundError("科目不存在")

    # 先校验知识点再落库：否则校验失败会留下一条没有知识点的孤儿错题
    # （接口返回 422，主表却已经写进去了）。
    _assert_kps_exist(payload.kp_ids)

    now = dt.datetime.now(dt.timezone.utc)
    st = srs.initial_state(now)

    # 插入主表与关联表放在同一事务里，任一步失败整体回滚
    with db.transaction() as cur:
        cur.execute(
            """
            INSERT INTO mistake
              (subject_id, image_path, stem_text, stem_source, answer_text, student_answer,
               wrong_reason, note, mastery, error_count, correct_streak,
               interval_days, next_review_at, paper_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                payload.subject_id, payload.image_path, payload.stem_text, payload.stem_source,
                payload.answer_text, payload.student_answer, payload.wrong_reason, payload.note,
                st.mastery, st.error_count, st.correct_streak, st.interval_days,
                st.next_review_at, payload.paper_id,
            ),
        )
        mistake_id = cur.fetchone()["id"]

        if payload.kp_ids:
            cur.executemany(
                "INSERT INTO mistake_kp (mistake_id, kp_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(mistake_id, k) for k in payload.kp_ids],
            )

    return _get_mistake(mistake_id)


def _assert_kps_exist(kp_ids: list[int]) -> None:
    """校验知识点存在。静默忽略不存在的 kp_id 会掩盖前端传错。"""
    if not kp_ids:
        return
    rows = db.fetch("SELECT id FROM knowledge_point WHERE id = ANY(%s)", (kp_ids,))
    found = {r["id"] for r in rows}
    missing = [k for k in kp_ids if k not in found]
    if missing:
        raise ValidationError(f"知识点不存在：{missing}", code="unknown_kp")


def _link_kps(cur, mistake_id: int, kp_ids: list[int]) -> None:
    cur.executemany(
        "INSERT INTO mistake_kp (mistake_id, kp_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        [(mistake_id, k) for k in kp_ids],
    )


@router.patch("/mistakes/{mistake_id}", response_model=MistakeOut)
def update_mistake(mistake_id: int, payload: MistakeUpdate) -> dict:
    _get_mistake(mistake_id)
    fields = payload.model_dump(exclude_unset=True)
    kp_ids = fields.pop("kp_ids", None)

    _assert_kps_exist(kp_ids or [])

    with db.transaction() as cur:
        if fields:
            sets = ", ".join(f"{k} = %s" for k in fields)
            cur.execute(
                f"UPDATE mistake SET {sets} WHERE id = %s AND deleted_at IS NULL",
                list(fields.values()) + [mistake_id],
            )

        if kp_ids is not None:
            # 先删后加：允许用 PATCH 覆盖知识点集合
            cur.execute("DELETE FROM mistake_kp WHERE mistake_id = %s", (mistake_id,))
            _link_kps(cur, mistake_id, kp_ids)

    return _get_mistake(mistake_id)


@router.delete("/mistakes/{mistake_id}", status_code=204)
def delete_mistake(mistake_id: int) -> None:
    """软删除：只标记 deleted_at，图片与复习历史都保留，便于误删恢复。"""
    n = db.execute(
        "UPDATE mistake SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL",
        (mistake_id,),
    )
    if n == 0:
        raise NotFoundError("错题不存在或已删除")


@router.post("/mistakes/{mistake_id}/restore", response_model=MistakeOut)
def restore_mistake(mistake_id: int) -> dict:
    n = db.execute("UPDATE mistake SET deleted_at = NULL WHERE id = %s", (mistake_id,))
    if n == 0:
        raise NotFoundError("错题不存在")
    return _get_mistake(mistake_id)


@router.post("/uploads", response_model=UploadOut, status_code=201)
async def upload_image(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise ValidationError("上传的文件为空")
    if len(raw) > 20 * 1024 * 1024:
        raise ValidationError("图片超过 20MB，请压缩后重试", code="file_too_large")

    try:
        rel_path, w, h = imaging.normalise_and_save(raw)
    except imaging.UnsupportedImage as e:
        raise ValidationError(str(e), code="bad_image") from e

    return {
        "image_path": rel_path,
        "width": w,
        "height": h,
        "url": f"/uploads/{rel_path}",
    }