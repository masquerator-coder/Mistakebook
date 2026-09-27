"""数据库访问层：连接池 + 迁移执行器。

设计选择：不引入 ORM（SQLAlchemy）和迁移框架（Alembic）。
本项目表结构小（~10 张表）、查询以聚合为主，裸 SQL 更直接也更好审查；
迁移用一个按文件名排序、记录到 schema_migrations 的极简执行器即可。
"""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger(__name__)

_pool: ConnectionPool | None = None

_here = pathlib.Path(__file__).resolve()

# 迁移目录的候选位置。之所以要列多个：
#   - 源码树里跑时：<repo>/app/src/mistakebook/db.py -> <repo>/app/migrations
#   - 容器里 pip install 后，包被装到 site-packages，
#     相对 __file__ 的 parents[2] 会指向 /usr/local/lib/python3.13/ 而不是 /app，
#     因此必须额外看 /app/migrations 和 MIGRATIONS_DIR 环境变量。
_CANDIDATES = [
    _here.parents[2] / "migrations",      # 源码树
    pathlib.Path("/app/migrations"),      # 容器内（Dockerfile 拷到这里）
    pathlib.Path.cwd() / "migrations",    # 在 app/ 目录下直接跑
]
if os.getenv("MIGRATIONS_DIR"):
    _CANDIDATES.insert(0, pathlib.Path(os.environ["MIGRATIONS_DIR"]))


def _find_migrations_dir() -> pathlib.Path | None:
    for c in _CANDIDATES:
        if c.is_dir() and any(c.glob("*.sql")):
            return c
    return None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        _pool.open(wait=True, timeout=30)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def fetch(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def fetchrow(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def fetchval(sql: str, params: Sequence[Any] | None = None) -> Any:
    row = fetchrow(sql, params)
    if row is None:
        return None
    return next(iter(row.values()))


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    """执行写语句，返回受影响行数。"""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def execute_returning(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def execute_many(sql: str, params_seq: Iterable[Sequence[Any]]) -> None:
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params_seq)


@contextlib.contextmanager
def transaction():
    """把多条写语句放进一个事务，任一步抛错则整体回滚。

    为什么需要它：psycopg 的 `with pool.connection()` 每次都是独立事务，
    因此「先插主表、再插关联表」的两步操作在中途失败时会留下孤儿行 ——
    接口返回 422，但主表已经落库。这类 bug 在只跑 happy path 的测试里
    看不出来，必须在事务里做。
    """
    conn = get_pool().getconn()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                yield cur
    finally:
        get_pool().putconn(conn)


def run_migrations() -> list[str]:
    """按文件名顺序执行 migrations/*.sql，已执行的跳过。返回本次执行的文件名。

    找不到迁移目录时**抛错而不是静默跳过**：静默跳过会让应用带着空库启动，
    随后所有请求都以莫名其妙的缺表错误失败，排查成本远高于直接失败。
    """
    migrations_dir = _find_migrations_dir()
    if migrations_dir is None:
        tried = "\n  ".join(str(c) for c in _CANDIDATES)
        raise RuntimeError(f"找不到迁移目录，已尝试：\n  {tried}")

    log.info("迁移目录：%s", migrations_dir)

    applied: list[str] = []
    files = sorted(migrations_dir.glob("*.sql"))

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename   text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT filename FROM schema_migrations")
            done = {r["filename"] for r in cur.fetchall()}

            for f in files:
                if f.name in done:
                    continue
                log.info("执行迁移 %s", f.name)
                cur.execute(f.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (f.name,)
                )
                applied.append(f.name)

    return applied