"""探针结果的统一形状与落库。

probe-reach 只回答"能不能连上"，专项探针回答"取回来的东西够不够填这一维"。
两者都写 source_probe_log，靠 dataset_code 区分：可达性用 'reach'，
专项探针用真实数据集名（'sitetype-icdo3'、'mondo.obo'），
这样同一个源的多份数据集不会互相覆盖裁定。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import db
from ..clock import now_ts


@dataclass
class ProbeResult:
    verdict: str  # ok | partial | empty | dead | blocked | unlicensed
    message: str
    criteria: str = ""
    rows_seen: int | None = None
    diseases_covered: int | None = None
    diseases_total: int | None = None
    fields_seen: list = field(default_factory=list)
    sample: list = field(default_factory=list)
    raw_path: str | None = None
    # 这一趟数据是怎么来的。默认 direct 是给"没联网概念"的探针用的；
    # 真联网的要填 fetch 回来的三态，离线重放必须填 offline——
    # 写成 direct 等于宣称"直连取到了"，而 scheduler 恰恰要按这一列决定配不配代理
    reachability: str = "direct"
    http_status: int | None = None
    latency_ms: int | None = None
    # 以下只在本次真的取回了完整数据集时填，用来登记 dataset_release
    dataset_code: str = ""
    upstream_version: str = ""
    release_date: str | None = None
    release_bytes: int | None = None
    release_sha256: str | None = None


def source_id(code: str) -> int:
    with db.ro() as conn:
        row = db.one(conn, "SELECT `id` FROM `source` WHERE `code`=:c", {"c": code})
    if not row:
        raise SystemExit(f"source 表里没有 {code}，先跑 seed-sources")
    return int(row[0])


def record(code: str, res: ProbeResult) -> None:
    sid = source_id(code)
    with db.tx() as conn:
        conn.execute(
            db.text(
                "INSERT INTO `source_probe_log`"
                " (`source_id`,`dataset_code`,`probed_at`,`http_status`,`reachability`,"
                "  `latency_ms`,`bytes`,`rows_seen`,`diseases_covered`,`diseases_total`,"
                "  `fields_seen`,`sample`,`verdict`,`criteria`,`message`,`raw_path`)"
                " VALUES (:sid,:ds,:ts,:http,:reach,:ms,:bytes,:rows,:cov,:total,"
                "  :fields,:sample,:verdict,:crit,:msg,:raw)"
            ),
            {
                "sid": sid,
                "ds": res.dataset_code or "probe",
                "ts": now_ts(),
                "http": res.http_status,
                "reach": res.reachability,
                "ms": res.latency_ms,
                "bytes": res.release_bytes,
                "rows": res.rows_seen,
                "cov": res.diseases_covered,
                "total": res.diseases_total,
                # JSON 列必须自己 dumps：MySQL 8 会把不合法的串静默存成 NULL
                "fields": json.dumps(res.fields_seen, ensure_ascii=False),
                "sample": json.dumps(res.sample, ensure_ascii=False, default=str),
                "verdict": res.verdict,
                "crit": res.criteria,
                "msg": res.message,
                "raw": res.raw_path,
            },
        )
        if res.release_sha256:
            # 同一版本重复登记会撞唯一键，用 upsert 而不是 INSERT IGNORE：
            # 后者会把"重跑刷新了 fetched_at"这件事一起吞掉
            conn.execute(
                db.text(
                    "INSERT INTO `dataset_release`"
                    " (`source_id`,`dataset_code`,`upstream_version`,`release_date`,"
                    "  `fetched_at`,`bytes`,`sha256`,`rows_seen`,`raw_path`)"
                    " VALUES (:sid,:ds,:ver,:rd,:ts,:bytes,:sha,:rows,:raw)"
                    " ON DUPLICATE KEY UPDATE"
                    # COALESCE：某次探针没解析出发布日时不许把已有的日期抹成 NULL
                    " `release_date`=COALESCE(VALUES(`release_date`),`release_date`),"
                    " `fetched_at`=VALUES(`fetched_at`),"
                    " `bytes`=VALUES(`bytes`),`sha256`=VALUES(`sha256`),"
                    " `rows_seen`=VALUES(`rows_seen`),`raw_path`=VALUES(`raw_path`)"
                ),
                {
                    "sid": sid,
                    "ds": res.dataset_code,
                    "ver": res.upstream_version or "",
                    "rd": res.release_date,
                    "ts": now_ts(),
                    "bytes": res.release_bytes,
                    "sha": res.release_sha256,
                    "rows": res.rows_seen,
                    "raw": res.raw_path,
                },
            )
