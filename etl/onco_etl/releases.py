"""dataset_release 的登记与取用：探针与装载器共用一份。

版本号留空串而不是 NULL 是这张表的命门——唯一键里放 NULL，MySQL 允许无限多行 NULL，
同一份发布会被重复登记，而装载器是按 release 幂等重写的，键一裂就会攒出双份数据。

装载器不自己造 release：它要么复用探针已登记的那一行，要么在真的取回载荷后调
`register()` 补一行。`dataset_release_id` 指向一个不存在的版本比指向空更难查。
"""
from __future__ import annotations

from sqlalchemy import Connection

from . import db
from .clock import now_ts


def register(
    conn: Connection,
    source_id: int,
    dataset_code: str,
    *,
    upstream_version: str = "",
    release_date: str | None = None,
    body_bytes: int | None = None,
    sha256: str | None = None,
    rows_seen: int | None = None,
    raw_path: str | None = None,
) -> int:
    """按 (source, dataset, version) upsert 一行，返回它的 id。

    同一版本重复登记用 upsert 而不是 INSERT IGNORE：后者会把"重跑刷新了 fetched_at"
    这件事一起吞掉。COALESCE 是给 release_date 的——某次没解析出发布日不许把已有的抹成 NULL。
    """
    ver = upstream_version or ""
    conn.execute(
        db.text(
            "INSERT INTO `dataset_release`"
            " (`source_id`,`dataset_code`,`upstream_version`,`release_date`,`fetched_at`,"
            "  `bytes`,`sha256`,`rows_seen`,`raw_path`)"
            " VALUES (:sid,:ds,:ver,:rd,:ts,:bytes,:sha,:rows,:raw)"
            " ON DUPLICATE KEY UPDATE"
            " `release_date`=COALESCE(VALUES(`release_date`),`release_date`),"
            " `fetched_at`=VALUES(`fetched_at`),"
            " `bytes`=VALUES(`bytes`),`sha256`=VALUES(`sha256`),"
            " `rows_seen`=VALUES(`rows_seen`),`raw_path`=VALUES(`raw_path`)"
        ),
        {
            "sid": source_id,
            "ds": dataset_code,
            "ver": ver,
            "rd": release_date,
            "ts": now_ts(),
            "bytes": body_bytes,
            "sha": sha256,
            "rows": rows_seen,
            "raw": raw_path,
        },
    )
    row = db.one(
        conn,
        "SELECT `id` FROM `dataset_release`"
        " WHERE `source_id`=:sid AND `dataset_code`=:ds AND `upstream_version`=:ver",
        {"sid": source_id, "ds": dataset_code, "ver": ver},
    )
    if not row:
        raise SystemExit(f"dataset_release 登记后取不到 id：source={source_id} {dataset_code}/{ver}")
    return int(row[0])


def latest(conn: Connection, source_id: int, dataset_code: str) -> tuple | None:
    """该源该数据集最近登记的一版；没有返回 None。

    取 `id` 最大而不是 `fetched_at` 最新：重跑老版本会刷新 fetched_at，
    按时间挑会挑中那一版重跑，而装载器要的"最近一版"是登记顺序上的最新。
    """
    return db.one(
        conn,
        "SELECT `id`,`upstream_version`,`raw_path`,`sha256`,`bytes`,`release_date`"
        " FROM `dataset_release` WHERE `source_id`=:sid AND `dataset_code`=:ds"
        " ORDER BY `id` DESC LIMIT 1",
        {"sid": source_id, "ds": dataset_code},
    )
