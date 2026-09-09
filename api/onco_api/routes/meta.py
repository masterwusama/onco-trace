"""站点元信息：数据截至哪一版、各维实测多少条、整维级的缺口在哪。

页面的页脚（"数据来源与口径"）与疾病列表页的覆盖标记都读这一份，所以它一次请求
要能答完三个问题：什么时候的数（etl_job_log 最近一次运行 + dataset_release 版本）、
有多少（各维合计）、缺什么（整维空态）。

各表行数是现算的 COUNT(*)，不用 information_schema.TABLE_ROWS——那一列对 InnoDB
是估算值，最多能差出一个数量级，而这份响应就是页面上"数据截至"那句话的依据。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Connection

from .. import gaps as G
from ..db import get_conn, rows
from ..dimensions import DIMS, totals
from ..serialize import Refs

router = APIRouter(prefix="/api", tags=["meta"])

BUSINESS_TABLES = (
    "disease", "anatomy_node", "disease_anatomy", "histology_code", "disease_histology",
    "symptom", "risk_factor", "disease_risk_factor", "stat_fact", "survival",
    "trial", "publication", "target", "disease_target", "drug",
)
INFRA_TABLES = ("source", "dataset_release", "source_probe_log", "etl_job_log")

_LATEST_RUN = (
    "SELECT job_name, started_at, finished_at, status FROM ("
    "  SELECT job_name, started_at, finished_at, status,"
    "         ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY started_at DESC) AS rn"
    "  FROM etl_job_log) t WHERE rn = 1 ORDER BY job_name"
)

_LATEST_RELEASE = (
    "SELECT s.code AS source_code, r.dataset_code, r.upstream_version, r.release_date,"
    "       r.fetched_at, r.rows_seen FROM ("
    "  SELECT source_id, dataset_code, upstream_version, release_date, fetched_at, rows_seen,"
    "         ROW_NUMBER() OVER (PARTITION BY source_id, dataset_code ORDER BY fetched_at DESC) AS rn"
    "  FROM dataset_release) r JOIN source s ON s.id = r.source_id WHERE r.rn = 1"
    " ORDER BY s.code, r.dataset_code"
)


@router.get("/meta")
def meta(conn: Connection = Depends(get_conn)) -> dict:
    refs = Refs(conn)
    tot = totals(conn)
    tables = {
        t: int(rows(conn, f"SELECT COUNT(*) AS n FROM `{t}`")[0]["n"])
        for t in BUSINESS_TABLES + INFRA_TABLES
    }
    return {
        "site": {
            "name": "onco-trace",
            "diseases": tables["disease"],
            "empty_state_label": G.EMPTY_LABEL,
            "disclaimer": "本站不做诊断；症状反查输出的是参考排序。",
        },
        "loads": rows(conn, _LATEST_RUN),
        "tables": tables,
        "dims": [
            {"key": d.key, "label": d.label, "note": d.note, "table": d.table,
             "rows": tot[d.key]["table_rows"], **{k: v for k, v in tot[d.key].items() if k != "table_rows"}}
            for d in DIMS
        ],
        "sources": [
            {k: v for k, v in s.items() if k not in ("id",)}
            for s in sorted(refs.sources.values(), key=lambda x: x["code"])
        ],
        "releases": rows(conn, _LATEST_RELEASE),
        "gaps": G.global_gaps(conn),
    }
