"""直接打真实端点的取证脚本（不属于 pytest 套件）。

用途：确认 deepseek.py 的 image_block 在真实 sglang 端点上是可用的，
尤其是「不带 detail 字段」这条。跑法：

    cd D:\\Apps\\Mistakebook\\app
    $env:PYTHONPATH="src"; python tests/probe_vision.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from mistakebook.services import deepseek  # noqa: E402


async def main() -> int:
    print("模型：", __import__("mistakebook.config", fromlist=["settings"]).settings.deepseek_model)
    print("端点：", __import__("mistakebook.config", fromlist=["settings"]).settings.deepseek_base_url)

    img = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(
        r"D:\Apps\_probe\vision_test.png"
    )
    if not img.is_file():
        print(f"[跳过] 测试图片不存在：{img}")
        return 2

    blocks = [
        deepseek.image_block(img),
        deepseek.text_block("识别图中的全部文字，原样输出，不要解题。"),
    ]

    print("\n--- image_block 结构（不得含 detail 键）---")
    b = blocks[0]
    print("keys:", list(b.keys()), "| image_url keys:", list(b["image_url"].keys()))
    assert "detail" not in b["image_url"], "image_url 里出现了 detail，会导致 400！"
    print("OK: 无 detail 字段")

    print("\n--- 纯文本调用 ---")
    try:
        out = await deepseek.chat_text("只回复两个字。", [deepseek.text_block("回复：正常")], "read_question", max_tokens=16)
        print("OK:", out.strip())
    except Exception as e:
        print("FAIL:", e)
        return 1

    print("\n--- 视觉调用 ---")
    try:
        out = await deepseek.chat_text("你是图像识别助手。", blocks, "read_question", max_tokens=200)
        print("OK:", out.strip())
    except Exception as e:
        print("FAIL:", e)
        return 1

    print("\n--- 视觉 + JSON 模式 ---")
    try:
        parsed = await deepseek.chat_json(
            "只输出 JSON。",
            [deepseek.image_block(img), deepseek.text_block(
                '输出 JSON：{"stem": "题干原文", "answer": "答案"}'
            )],
            "read_question",
        )
        print("OK:", parsed)
    except Exception as e:
        print("FAIL:", e)
        return 1

    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))