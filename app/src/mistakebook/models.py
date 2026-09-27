"""Pydantic 请求/响应模型。

编码原则：**必填字段尽量少**。孩子的录入摩擦是产品成败的关键（spec §6.1、风险 [R3]），
因此只有「科目 + 题干或图片至少一个」是硬性的，其余一律可空。
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

WrongReason = Literal["concept", "calculation", "misread", "method", "careless", "time", "unknown"]
StemSource = Literal["photo", "typed", "import"]
ReviewResult = Literal["correct", "wrong", "partial"]


class SubjectOut(BaseModel):
    id: int
    name: str
    sort_order: int


class KpOut(BaseModel):
    id: int
    subject_id: int
    parent_id: int | None = None
    name: str
    code: str | None = None
    grade: str | None = None
    book: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    source: str | None = None
    confidence: float | None = None
    reviewed: bool = False
    sort_order: int = 0


class KpTreeOut(KpOut):
    children: list["KpTreeOut"] = Field(default_factory=list)


class KpCreate(BaseModel):
    subject_id: int
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    code: str | None = None
    grade: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    sort_order: int = 0


class KpUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    reviewed: bool | None = None
    page_from: int | None = None
    page_to: int | None = None
    sort_order: int | None = None


class MistakeCreate(BaseModel):
    subject_id: int
    stem_text: str | None = None
    image_path: str | None = None
    stem_source: StemSource = "typed"
    answer_text: str | None = None
    student_answer: str | None = None
    wrong_reason: WrongReason | None = None
    note: str | None = None
    kp_ids: list[int] = Field(default_factory=list)
    paper_id: int | None = None

    @field_validator("image_path")
    @classmethod
    def _validate_image_path(cls, v: str | None) -> str | None:
        """只接受 uploads 目录下的相对路径。

        不校验的后果不是安全漏洞（读取侧 resolve_image 会挡住），而是
        用户看到破图和「讲解没提图」却毫无提示 —— 静默降级比报错更难排查。
        """
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if v.startswith("/") or v.startswith("\\") or ":" in v:
            raise ValueError("image_path 必须是相对路径")
        parts = v.replace("\\", "/").split("/")
        if any(p == ".." for p in parts):
            raise ValueError("image_path 不能包含 ..")
        return v

    @model_validator(mode="after")
    def _need_stem_or_image(self):
        if not (self.stem_text and self.stem_text.strip()) and not self.image_path:
            raise ValueError("题干文字与题目图片至少要有一个")
        return self


class MistakeUpdate(BaseModel):
    stem_text: str | None = None
    answer_text: str | None = None
    student_answer: str | None = None
    wrong_reason: WrongReason | None = None
    note: str | None = None
    kp_ids: list[int] | None = None


class MistakeOut(BaseModel):
    id: int
    subject_id: int
    subject_name: str | None = None
    image_path: str | None = None
    stem_text: str | None = None
    stem_source: str | None = None
    answer_text: str | None = None
    student_answer: str | None = None
    wrong_reason: str | None = None
    note: str | None = None
    mastery: str
    error_count: int
    correct_streak: int
    interval_days: int = 1
    created_at: dt.datetime
    last_review_at: dt.datetime | None = None
    next_review_at: dt.datetime | None = None
    kps: list[KpOut] = Field(default_factory=list)


class ReviewSubmit(BaseModel):
    result: ReviewResult
    duration_sec: int | None = None
    note: str | None = None


class ReviewLogOut(BaseModel):
    id: int
    reviewed_at: dt.datetime
    result: str
    duration_sec: int | None = None
    note: str | None = None


class UploadOut(BaseModel):
    image_path: str
    width: int
    height: int
    url: str


class ReadQuestionIn(BaseModel):
    image_path: str


class ReadQuestionOut(BaseModel):
    stem_text: str = ""
    answer_text: str = ""
    wrong_reason_suggestion: str | None = None
    kp_suggestions: list[str] = Field(default_factory=list)
    raw_text: str = ""


class ExplainIn(BaseModel):
    mistake_id: int
    force: bool = False


class AiJobOut(BaseModel):
    status: Literal["done", "pending", "failed"]
    payload: dict | None = None
    message: str | None = None