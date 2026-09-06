"""数据库边界：一个带 pool_pre_ping 的引擎 + 两个语义明确的事务入口。

刻意不用 ORM：表结构是 etl 与后端的唯一契约，映射类会变成第二个真相源，
而 db/schema.sql 改一列时它不会自己跟着改。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, create_engine, text
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
    """pool_pre_ping：MySQL 8 默认 wait_timeout=28800s，隔一夜再用死连接必报错。"""
    global _engine
    if _engine is None:
        s = settings()
        _engine = create_engine(
            make_url(s.url), pool_pre_ping=True, pool_size=2, max_overflow=0, echo=False
        )
    return _engine


@contextmanager
def tx() -> Iterator[Connection]:
    """写事务：正常退出才 commit，抛异常整笔回滚——批次要么全落要么零写入。"""
    conn = engine().connect()
    trans = conn.begin()
    try:
        yield conn
        trans.commit()
    except BaseException:
        trans.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def ro() -> Iterator[Connection]:
    """只读连接。写操作不许走这里，好让"谁能改数"这个问题只有一个答案。"""
    conn = engine().connect()
    try:
        yield conn
    finally:
        conn.close()


def dispose() -> None:
    """短命令退出前放掉连接池，别让一次探针跑完留两条 TIME_WAIT。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def scalars(conn: Connection, sql: str, params: dict | None = None) -> list:
    return [r[0] for r in conn.execute(text(sql), params or {}).all()]


def rows(conn: Connection, sql: str, params: dict | None = None) -> list[tuple]:
    return conn.execute(text(sql), params or {}).all()


def one(conn: Connection, sql: str, params: dict | None = None):
    return conn.execute(text(sql), params or {}).first()
