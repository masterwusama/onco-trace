"""装载器的公共底座：拿版本、写幂等行、报数。

这一层存在的理由是"解析只有一份"：每个装载器 import 对应探针里抽出来的解析函数，
自己只负责把解析结果写成行。装载器再写一套解析，两边就会悄悄分叉，
而分叉的表现形式是"探针说这维够用，库里却没有数"。

三条约定：

1. 每行必须带出处五列（`source_id` / `dataset_release_id` / `extract_method` /
   `review_status` / `loaded_at`）。前两个由 `Ctx` 解析，后三个由装载器按实测填，
   `python db/tests/run.py status` 会扫这五列齐不齐。
2. 写库一律走 `upsert()` / `replace_scope()`，两者都以表上的唯一键为幂等依据，
   所以同一版发布重跑一百次，行数与第一次一样。
3. `dict` / `list` 值自动 `json.dumps`。MySQL 8 会把不合法的 JSON 串静默存成 NULL，
   一个都不报错，事后只能看出"这一列怎么全空"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import Connection

from .. import db, joblog, releases
from ..clock import now_ts
from ..probes.result import source_id as _source_id

# 一条 INSERT 语句带多少行。400 行按 trial 那种带长文本的行约 1 MB，留出十倍余量仍在
# max_allowed_packet 默认值以内；纯短行的表也就多跑几十次语句，秒级
CHUNK = 400


@dataclass
class LoadResult:
    """一次装载的产出。`written` 是 表名 → 本次touch过的行数。"""

    written: dict[str, int] = field(default_factory=dict)
    covered: int | None = None
    total: int | None = None
    message: str = ""

    @property
    def rows(self) -> int:
        return sum(self.written.values())

    def brief(self) -> str:
        cov = f"覆盖 {self.covered}/{self.total}；" if self.covered is not None else ""
        tables = "、".join(f"{t}={n}" for t, n in self.written.items()) or "0 行"
        return f"{cov}{tables}" + (f"；{self.message}" if self.message else "")


class Ctx:
    """一次 `load` 命令的上下文：跑法开关 + 三张查表缓存。

    缓存按命令生命周期走，不做模块级全局——同一次跑里 dataset_release 会被自己改写，
    跨命令复用会读到半旧的值。
    """

    def __init__(self, *, offline: bool, dry_run: bool, job: joblog.Job) -> None:
        self.offline = offline
        self.dry_run = dry_run
        self.job = job
        self._sources: dict[str, int] = {}
        self._releases: dict[tuple[str, str], int] = {}
        self._diseases: dict[str, int] | None = None

    def tx(self) -> Iterator[Connection]:
        return db.tx(dry_run=self.dry_run)

    def source_id(self, code: str) -> int:
        if code not in self._sources:
            self._sources[code] = _source_id(code)
        return self._sources[code]

    def register(
        self,
        conn: Connection,
        source_code: str,
        dataset_code: str,
        *,
        upstream_version: str = "",
        release_date: str | None = None,
        body_bytes: int | None = None,
        sha256: str | None = None,
        rows_seen: int | None = None,
        raw_path: str | None = None,
    ) -> int:
        """登记（或复用）一版发布，返回 `dataset_release.id`。"""
        rid = releases.register(
            conn,
            self.source_id(source_code),
            dataset_code,
            upstream_version=upstream_version,
            release_date=release_date,
            body_bytes=body_bytes,
            sha256=sha256,
            rows_seen=rows_seen,
            raw_path=raw_path,
        )
        self._releases[(source_code, dataset_code)] = rid
        return rid

    def latest_release(self, conn: Connection, source_code: str, dataset_code: str) -> int:
        """复用探针已登记的最近一版。取不到就说明这维还没实测过，装载器不该自己猜版本。"""
        key = (source_code, dataset_code)
        if key not in self._releases:
            row = releases.latest(conn, self.source_id(source_code), dataset_code)
            if not row:
                raise SystemExit(
                    f"{source_code} 的 {dataset_code} 没有登记过发布："
                    "这一维还没实测过，装载器不猜版本号。"
                    f"先跑 `ops\\etl.ps1 probe --code {source_code}`"
                )
            self._releases[key] = int(row[0])
        return self._releases[key]

    def disease_ids(self, conn: Connection) -> dict[str, int]:
        """`code → disease.id`。除 disease 装载器外每个装载器都要靠它拿外键。"""
        if self._diseases is None:
            rows = db.rows(conn, "SELECT `code`,`id` FROM `disease`")
            if not rows:
                raise SystemExit("disease 表是空的：先跑 `load --code disease`")
            self._diseases = {str(c): int(i) for c, i in rows}
        return self._diseases

    def disease_id(self, conn: Connection, code: str) -> int:
        ids = self.disease_ids(conn)
        if code not in ids:
            # 静默跳过会让"这一维 17/18"看起来像源缺数据，而真实原因是主档少了一行
            raise SystemExit(f"disease 里没有 code={code}，装载器没法给它挂数据")
        return ids[code]


def _prep(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
    return out


def upsert(conn: Connection, table: str, rows: list[dict]) -> int:
    """INSERT ... ON DUPLICATE KEY UPDATE，返回本次写过的行数。

    更新列是"行里带来的全部列"：装载器是源的本子，重跑就该把值刷新。
    行数按传入数计，不按 MySQL 的 affected rows——后者对"值没变的重复行"计 0，
    拿它报数会让人以为装载器少写了。
    批量执行（一次传 list[dict]）不是优化洁癖：GWAS 那种几十万行的装载逐条跑要几分钟。
    """
    if not rows:
        return 0
    cols = list(rows[0])
    for r in rows[1:]:
        if list(r) != cols:
            raise SystemExit(
                f"写 `{table}` 的行字段不一致：{[c for c in cols if c not in r]}"
                f" 缺 / {[c for c in r if c not in cols]} 多——少给一列就会被默认值悄悄填上"
            )
    sql = (
        f"INSERT INTO `{table}` (" + ", ".join(f"`{c}`" for c in cols) + ") VALUES (:"
        + ", :".join(cols)
        + ") ON DUPLICATE KEY UPDATE "
        + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
    )
    # 分批不是优化洁癖：trial 一行带着入排标准全文能到 3 KB，两万行拼成一条语句会顶到
    # MySQL 的 max_allowed_packet，而报出来的还是"连接断开"这种指不到根因的话
    batch = [_prep(r) for r in rows]
    for i in range(0, len(batch), CHUNK):
        conn.execute(db.text(sql), batch[i:i + CHUNK])
    return len(rows)


def replace_scope(conn: Connection, table: str, scope: dict, rows: list[dict]) -> int:
    """按 `scope` 先删后写：源里删掉的一行不该留在我们库里。

    只给"这一段完全由本次装载负责"的关系表用（`disease_anatomy`、`disease_histology`
    这类）。节点表（`anatomy_node` / `target` / `risk_factor`）不许走这里——
    它们的 id 被关系表引用，删重插会把引用打到别的行上。
    """
    if not scope:
        raise SystemExit(f"replace_scope(`{table}`) 不给 scope 等于全表删，拒绝执行")
    bind = {k: v for k, v in scope.items() if v is not None}
    where = " AND ".join(
        f"`{k}` IS NULL" if v is None else f"`{k}`=:{k}" for k, v in scope.items()
    )
    conn.execute(db.text(f"DELETE FROM `{table}` WHERE {where}"), bind)
    return upsert(conn, table, rows)


def prov(
    *,
    source_id: int,
    dataset_release_id: int | None,
    extract_method: str,
    review_status: str = "unreviewed",
) -> dict:
    """出处四列的快捷写法（`loaded_at` 由这里一起给，别让每个装载器自己想到）。"""
    return {
        "source_id": source_id,
        "dataset_release_id": dataset_release_id,
        "extract_method": extract_method,
        "review_status": review_status,
        "loaded_at": now_ts(),
    }
