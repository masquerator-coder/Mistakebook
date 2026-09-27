"""图片处理：把手机/平板拍来的原图规范化为适合存档与送模型的形式。

两件事：
  1. 限制最长边（默认 2000px）—— 手机直出 4000px+ 的图对读题没有增益，
     只会撑大存储与请求体。
  2. 统一转成 JPEG 并修正 EXIF 旋转 —— 否则竖拍的照片在浏览器里会躺倒。
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import pathlib
import secrets

from PIL import Image, ImageOps

from ..config import settings

log = logging.getLogger(__name__)

MAX_EDGE = 2000
JPEG_QUALITY = 88


class InvalidImagePath(ValueError):
    """图片路径非法（越界、绝对路径等）。

    单独定义而不是复用 ValueError：调用方的 except 只应捕获「路径非法」，
    若用裸 ValueError，将来 imaging 内部其他处的 ValueError 会被误吞成
    「非法图片路径」，掩盖真实故障。
    """


class UnsupportedImage(ValueError):
    """上传的内容不是可识别的图片。"""


def normalise_and_save(raw: bytes, root: pathlib.Path | None = None) -> tuple[str, int, int]:
    """规范化并保存图片，返回 (相对路径, 宽, 高)。

    相对路径形如 '2026/09/27/ab12cd34.jpg'，相对 upload 根目录。
    """
    root = root or settings.upload_path
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:  # noqa: BLE001 —— Pillow 会抛多种异常，统一转成可读错误
        raise UnsupportedImage(f"无法识别图片格式：{e}") from e

    # 按 EXIF 方向信息摆正，否则竖拍照片会侧躺
    img = ImageOps.exif_transpose(img)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    w, h = img.size
    scale = MAX_EDGE / max(w, h)
    if scale < 1:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    now = dt.datetime.now()
    rel_dir = pathlib.Path(f"{now:%Y/%m/%d}")
    (root / rel_dir).mkdir(parents=True, exist_ok=True)

    name = f"{secrets.token_hex(8)}.jpg"
    rel_path = rel_dir / name
    out = root / rel_path

    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return rel_path.as_posix(), img.size[0], img.size[1]


def resolve_image(rel_path: str) -> pathlib.Path:
    """把数据库里的相对路径解析成绝对路径。

    做目录穿越防护：解析后的路径必须落在 upload 根目录内。
    用 is_relative_to 而不是字符串 startswith —— 后者会把
    `/app/data/uploads_backup/x.jpg` 这类同前缀的兄弟目录误判为「在 root 内」。
    """
    root = settings.upload_path.resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        raise InvalidImagePath(f"非法的图片路径：{rel_path}")
    return target