"""行的出参形状：JSON 列解码、出处五列收拢、Decimal 交给 FastAPI 编码。

不改列名。列名就是 db/schema.sql 里的名字，接口、文档、页面三处按同一个名字对齐；
改名等于在表结构之外再造一份契约，而 README 约定 1 说的唯一契约是表结构。
也不给行建 pydantic 模型——与 etl 侧不用 ORM 同一条理由。

出处五列收进每行的 `provenance` 子对象：`source_id` / `dataset_release_id` /
`extract_method` / `review_status` / `loaded_at` 摊在行里会被读成业务字段，而收拢之后
`provenance` 里同时带上源名、许可与数据集版本——"这个数哪来的"在响应里就是一块，
前端不必拿 id 再去别处查一遍，而那张表本身就是站点要展示的口径。
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from .db import rows

PROV_COLS = ("source_id", "dataset_release_id", "extract_method", "review_status", "loaded_at")

# 表 → 该表里以 JSON 存的列。pymysql 把 JSON 列当文本回，所以出参前必须解码；
# 列清单对着 db/schema.sql 数：漏一列就是前端收到一个形如 "[…]" 的字符串。
JSON_COLS: dict[str, tuple[str, ...]] = {
    "source": ("dimensions",),
    "source_probe_log": ("fields_seen", "sample"),
    "etl_job_log": ("stats",),
    "disease": ("xrefs", "gwas_uris", "search_terms", "pdq_pages"),
    "trial": (
        "phases", "design_info", "conditions", "interventions", "arm_groups",
        "eligibility", "collaborators", "location_countries", "publications", "matched_terms",
    ),
    "publication": ("matched_terms",),
    "disease_target": ("datasource_scores",),
    "drug": ("moa",),
}


def json_safe(v: Any) -> Any:
    """Decimal → float；日期与字符串原样交给 FastAPI 编码。"""
    if isinstance(v, Decimal):
        f = float(v)
        # 计数与年份这类整值比率不该在 JSON 里带一串 .0
        return int(f) if f.is_integer() and abs(f) < 1e15 else f
    return v


def decode_json(table: str, row: dict) -> dict:
    out = dict(row)
    for col in JSON_COLS.get(table, ()):
        v = out.get(col)
        if isinstance(v, (str, bytes)):
            try:
                out[col] = json.loads(v)
            except (ValueError, UnicodeDecodeError):
                pass  # 装载器写坏一列时原样吐出去，比在接口层静默改成 None 好查
    return out


class Refs:
    """一次请求内把 source 与 dataset_release 查齐。

    21 行源、几十行数据集版本，全表读一次比每行两条子查询便宜；这两张表是站点的
    "口径字典"，本来就要随行出现在响应里。
    """

    def __init__(self, conn: Connection) -> None:
        self.sources = {
            r["id"]: r
            for r in (
                {k: json_safe(v) for k, v in row.items()}
                for row in rows(
                    conn,
                    "SELECT id, code, name, org, license, home_url, download_url, auth, "
                    "commercial_use, attribution_required, status FROM source",
                )
            )
        }
        rels = rows(
            conn,
            "SELECT r.id, r.source_id, r.dataset_code, r.upstream_version, "
            "r.release_date, r.fetched_at, r.rows_seen, r.note, s.code AS source_code "
            "FROM dataset_release r JOIN source s ON s.id = r.source_id",
        )
        self.releases = {r["id"]: {k: json_safe(v) for k, v in r.items()} for r in rels}

    def provenance(self, row: dict) -> dict | None:
        """出处五列 + 源与版本的身份。行里没有这些列就回 None，不补占位值。"""
        if not any(k in row for k in PROV_COLS):
            return None
        prov: dict[str, Any] = {}
        src = self.sources.get(row.get("source_id"))
        if src:
            prov["source"] = {k: src[k] for k in
                              ("code", "name", "org", "license", "home_url",
                               "commercial_use", "attribution_required")}
        rel = self.releases.get(row.get("dataset_release_id"))
        if rel:
            prov["dataset"] = {
                "source_code": rel["source_code"],
                "code": rel["dataset_code"],
                "upstream_version": rel["upstream_version"],
                "release_date": rel["release_date"],
                "fetched_at": rel["fetched_at"],
            }
        for col in ("extract_method", "review_status", "loaded_at"):
            if col in row:
                prov[col] = json_safe(row[col])
        return prov


def hydrate(refs: Refs, table: str, row: dict) -> dict:
    """一行 → {业务列…, provenance}。JSON 列解码，出处列从行里挪走。"""
    r = decode_json(table, row)
    prov = refs.provenance(r)
    out = {k: json_safe(v) for k, v in r.items() if k not in PROV_COLS}
    if prov:
        out["provenance"] = prov
    return out
