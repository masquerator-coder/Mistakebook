"""统一错误类型与 HTTP 异常映射。

前端约定：所有错误响应的 JSON 形状固定为
    {"detail": {"error": {"code": "...", "message": "..."}}}
这样前端只需读 data.detail.error.message 就能拿到可展示文案。
"""

from __future__ import annotations


class AppError(Exception):
    """业务错误的基类：带稳定的机器可读 code 和面向用户的中文 message。"""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class AiError(AppError):
    """调用大模型失败。上层据此给用户展示「AI 暂时不可用」而不是 500。"""

    status_code = 502
    code = "ai_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message, code=code)