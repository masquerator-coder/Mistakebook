"""大模型客户端（OpenAI 兼容 /chat/completions）。

本机端点是 **sglang** 服务，实测结论见 spec §1.1：
  1. 图片块绝不能带 `detail` 字段 —— 带了直接 HTTP 400。
     该 400 的报错文本是 pydantic 对 union 成员逐个失败的 27 条 validation error，
     **完全不会提到 detail**，很容易被误判成 type 写错。改这里前先读 spec §1.1。
  2. 图片放 system 消息里也能用（与官方云 API 不同），但本模块统一放 user，
     以便日后切回官方云 API 时不用改代码。
  3. `response_format={"type":"json_object"}` 可用。
"""

from __future__ import annotations

import base64
import json
import logging
import pathlib
import re
from typing import Any

import httpx

from ..config import settings
from ..errors import AiError

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 32 * 1024 * 1024
REQUEST_TIMEOUT = 180.0

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def image_block(image_path: str | pathlib.Path) -> dict[str, Any]:
    """构造图片内容块。

    绝不添加 "detail" 字段：本机 sglang 端点带 detail 会返回 HTTP 400。
    """
    p = pathlib.Path(image_path)
    if not p.is_file():
        raise AiError("题目图片不存在", code="image_missing")
    raw = p.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raise AiError("图片超过 32MB，无法送入模型", code="image_too_large")

    suffix = p.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    b64 = base64.b64encode(raw).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/{suffix};base64,{b64}"},
    }


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def _network_hint(e: Exception) -> str:
    """把底层网络异常翻译成能照着做的提示。

    实测踩过的坑：容器里用裸主机名 fq-dgx-01 会 DNS 解析失败
    （Docker 内置 DNS 不继承宿主机 DNS 后缀），而报错原文只说
    "Name or service not known"，看不出该改什么。
    """
    text = str(e)
    if "Name or service not known" in text or "getaddrinfo" in text:
        host = settings.deepseek_base_url.split("//")[-1].split("/")[0].split(":")[0]
        return (
            f"模型服务主机名解析失败：{host}\n"
            "容器内的 Docker DNS 不会继承宿主机的 DNS 后缀，因此裸主机名不可用。\n"
            "把 DEEPSEEK_BASE_URL 改成全限定名（例如 http://fq-dgx-01.local:8888/v1）"
            "或直接用 IP 地址，然后 docker compose up -d api。"
        )
    if "Connection refused" in text:
        return f"模型服务拒绝连接：{settings.deepseek_base_url}，确认服务已在运行。"
    if "timed out" in text or "Timeout" in text:
        return f"连接模型服务超时：{settings.deepseek_base_url}"
    return f"无法连接模型服务：{text}"


async def chat_json(
    system_text: str,
    user_blocks: list[dict[str, Any]],
    kind: str,
    *,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """调用模型并要求返回 JSON 对象。

    图片必须已由调用方放进 user_blocks（用 image_block() 构造）。
    成功与失败都会写 ai_usage 流水。
    """
    messages: list[dict[str, Any]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_blocks})

    body = {
        "model": settings.deepseek_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }

    tokens_in = tokens_out = 0
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as e:
        _log_usage(kind, 0, 0, ok=False, error=f"网络错误：{e}")
        raise AiError(_network_hint(e), code="ai_unreachable") from e

    if resp.status_code != 200:
        snippet = resp.text[:500]
        _log_usage(kind, 0, 0, ok=False, error=f"HTTP {resp.status_code}")
        # 400 往往意味着请求体结构不对（例如误加了 image_block 的 detail 字段）
        raise AiError(
            f"模型服务返回 HTTP {resp.status_code}：{snippet}", code="ai_bad_request"
        )

    try:
        data = resp.json()
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        _log_usage(kind, 0, 0, ok=False, error="响应结构异常")
        raise AiError(f"模型响应结构异常：{e}", code="ai_bad_response") from e

    try:
        parsed = json.loads(_strip_fence(content))
    except json.JSONDecodeError as e:
        _log_usage(kind, tokens_in, tokens_out, ok=False, error="JSON 解析失败")
        raise AiError(f"模型未返回合法 JSON：{e}", code="ai_bad_json") from e

    if not isinstance(parsed, dict):
        _log_usage(kind, tokens_in, tokens_out, ok=False, error="顶层不是对象")
        raise AiError("模型返回的 JSON 顶层不是对象", code="ai_bad_json")

    _log_usage(kind, tokens_in, tokens_out, ok=True)
    return parsed


async def chat_text(
    system_text: str,
    user_blocks: list[dict[str, Any]],
    kind: str,
    *,
    max_tokens: int = 2048,
) -> str:
    """与 chat_json 相同，但不要求 JSON 输出，直接返回文本。"""
    messages: list[dict[str, Any]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": user_blocks})

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.deepseek_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.4,
                },
            )
    except httpx.HTTPError as e:
        _log_usage(kind, 0, 0, ok=False, error=f"网络错误：{e}")
        raise AiError(_network_hint(e), code="ai_unreachable") from e

    if resp.status_code != 200:
        _log_usage(kind, 0, 0, ok=False, error=f"HTTP {resp.status_code}")
        raise AiError(f"模型服务返回 HTTP {resp.status_code}", code="ai_bad_request")

    data = resp.json()
    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    _log_usage(kind, tokens_in, tokens_out, ok=True)
    return data["choices"][0]["message"]["content"]


async def confirm_image_support() -> bool:
    """启动自检：用一张 1x1 的 JPEG 真调一次，确认端点能收图。

    只打印警告，不阻塞启动 —— 模型不可用时应用仍应能记录错题。
    """
    tiny = (
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
        "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAB"
        "AAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAA"
        "AAD/2gAIAQEAAD8AKp//2Q=="
    )
    blocks = [
        text_block("这张图里有什么？只用一个词回答。"),
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tiny}"}},
    ]
    try:
        await chat_text("你是一个图像识别助手。", blocks, "read_question", max_tokens=32)
        return True
    except AiError as e:
        log.warning("图片自检失败，读题功能将不可用：%s", e.message)
        return False


def _log_usage(
    kind: str, tokens_in: int, tokens_out: int, *, ok: bool, error: str | None = None
) -> None:
    """写 ai_usage 流水。写流水失败绝不能影响主流程，因此全捕获。"""
    try:
        from .. import db

        db.execute(
            """
            INSERT INTO ai_usage (kind, model, tokens_in, tokens_out, ok, error)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (kind, settings.deepseek_model, tokens_in, tokens_out, ok, error),
        )
    except Exception as e:  # noqa: BLE001 —— 流水失败不能拖垮业务
        log.debug("写 ai_usage 失败（忽略）：%s", e)