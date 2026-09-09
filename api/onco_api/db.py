"""只读数据库边界：一个会话级 READ ONLY 的引擎 + 请求级连接。

刻意不用 ORM，也不给 pydantic 行模型：db/schema.sql 是采集层与后端唯一的契约，
映射类会变成第二个真相源，而它改一列时不会自己跟着改（这条与 etl 侧同一理由）。

引擎在 connect 事件里把会话设成 TRANSACTION READ ONLY。后端读的是 .env 里那个账号，
多半就是装载器用的 root——只靠"我们没写 POST 接口"来保证不改数太薄，一条手滑的
UPDATE 落在这份唯一的数据上就是真改，而会话级只读会让 MySQL 直接拒掉它。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.engine import make_url

from .config import Settings, load_settings

_engine: Engine | None = None
_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def engine() -> Engine:
    """pool_pre_ping：MySQL 8 默认 wait_timeout=28800s，隔一夜再用死连接必报错。

    池子给到 10：一个疾病页会并发打十来个维度接口，每个各占一条连接。
    """
    global _engine
    if _engine is None:
        s = settings()
        eng = create_engine(
            make_url(s.url),
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=5,
            pool_recycle=3600,
            echo=False,
        )

        @event.listens_for(eng, "connect")
        def _read_only(dbapi_conn, _record):  # noqa: ANN001
            with dbapi_conn.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")

        _engine = eng
    return _engine


@contextmanager
def ro() -> Iterator[Connection]:
    conn = engine().connect()
    try:
        yield conn
    finally:
        conn.close()


def get_conn() -> Iterator[Connection]:
    """FastAPI 依赖入口：一个请求一条连接，出作用域即归还。"""
    with ro() as conn:
        yield conn


def dispose() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def rows(conn: Connection, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def one(conn: Connection, sql: str, params: dict | None = None) -> dict | None:
    r = conn.execute(text(sql), params or {}).mappings().first()
    return dict(r) if r is not None else None


def scalar(conn: Connection, sql: str, params: dict | None = None):
    return conn.execute(text(sql), params or {}).scalar()
