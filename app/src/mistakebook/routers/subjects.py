"""科目与知识点 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from .. import db
from ..errors import ConflictError, NotFoundError
from ..models import KpCreate, KpOut, KpTreeOut, KpUpdate, SubjectOut

router = APIRouter(prefix="/api", tags=["subjects"])


@router.get("/subjects", response_model=list[SubjectOut])
def list_subjects() -> list[dict]:
    return db.fetch("SELECT id, name, sort_order FROM subject ORDER BY sort_order, id")


@router.get("/subjects/{subject_id}/kps", response_model=list[KpOut])
def list_kps(
    subject_id: int,
    grade: str | None = Query(default=None, description="如 七上；不传则返回全部"),
) -> list[dict]:
    return db.fetch(
        """
        SELECT id, subject_id, parent_id, name, code, grade, book,
               page_from, page_to, source, confidence, reviewed, sort_order
        FROM knowledge_point
        WHERE subject_id = %s AND (%s::text IS NULL OR grade = %s)
        ORDER BY sort_order, id
        """,
        (subject_id, grade, grade),
    )


@router.get("/subjects/{subject_id}/kp-tree", response_model=list[KpTreeOut])
def kp_tree(subject_id: int, grade: str | None = Query(default=None)) -> list[dict]:
    rows = list_kps(subject_id, grade)
    by_id: dict[int, dict] = {r["id"]: {**r, "children": []} for r in rows}
    roots: list[dict] = []
    for r in rows:
        node = by_id[r["id"]]
        parent = by_id.get(r["parent_id"]) if r["parent_id"] else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


@router.post("/kps", response_model=KpOut, status_code=201)
def create_kp(payload: KpCreate) -> dict:
    if db.fetchval("SELECT 1 FROM subject WHERE id = %s", (payload.subject_id,)) is None:
        raise NotFoundError("科目不存在")

    if payload.parent_id is not None:
        parent = db.fetchrow(
            "SELECT subject_id FROM knowledge_point WHERE id = %s", (payload.parent_id,)
        )
        if parent is None:
            raise NotFoundError("父知识点不存在")
        if parent["subject_id"] != payload.subject_id:
            raise ConflictError("父知识点不属于该科目")

    dup = db.fetchrow(
        """
        SELECT id FROM knowledge_point
        WHERE subject_id = %s AND parent_id IS NOT DISTINCT FROM %s AND name = %s
        """,
        (payload.subject_id, payload.parent_id, payload.name),
    )
    if dup is not None:
        raise ConflictError("同级下已有同名知识点", code="duplicate_kp")

    row = db.execute_returning(
        """
        INSERT INTO knowledge_point
          (subject_id, parent_id, name, code, grade, page_from, page_to, source, sort_order)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual', %s)
        RETURNING id, subject_id, parent_id, name, code, grade, book,
                  page_from, page_to, source, confidence, reviewed, sort_order
        """,
        (
            payload.subject_id, payload.parent_id, payload.name, payload.code,
            payload.grade, payload.page_from, payload.page_to, payload.sort_order,
        ),
    )
    return row


@router.patch("/kps/{kp_id}", response_model=KpOut)
def update_kp(kp_id: int, payload: KpUpdate) -> dict:
    existing = db.fetchrow("SELECT id FROM knowledge_point WHERE id = %s", (kp_id,))
    if existing is None:
        raise NotFoundError("知识点不存在")

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return db.fetchrow(
            """
            SELECT id, subject_id, parent_id, name, code, grade, book,
                   page_from, page_to, source, confidence, reviewed, sort_order
            FROM knowledge_point WHERE id = %s
            """,
            (kp_id,),
        )

    sets = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [kp_id]
    return db.execute_returning(
        f"""
        UPDATE knowledge_point SET {sets}
        WHERE id = %s
        RETURNING id, subject_id, parent_id, name, code, grade, book,
                  page_from, page_to, source, confidence, reviewed, sort_order
        """,
        params,
    )


@router.delete("/kps/{kp_id}", status_code=204)
def delete_kp(kp_id: int) -> None:
    n = db.execute("DELETE FROM knowledge_point WHERE id = %s", (kp_id,))
    if n == 0:
        raise NotFoundError("知识点不存在")