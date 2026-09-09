"""疾病列表与详情。

列表页要回答的是"这一病哪几维有数"，所以每行带一份逐维度量而不是一个总分——
给总分就会有人把"症状 12 条 + 靶点 3,000 条"读成同一个东西的两个档。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Connection

from .. import gaps as G
from ..db import get_conn, rows
from ..dimensions import DIMS, counts_by_disease
from ..serialize import Refs, hydrate

router = APIRouter(prefix="/api", tags=["disease"])

_IDENTITY = ("code", "name_zh", "name_en", "category", "sex", "icd10", "icdo3",
             "mondo_id", "mondo_name", "ncit_id", "ot_node")


def dim_block(counts: dict[str, dict[str, int]]) -> dict:
    return {
        d.key: {
            "label": d.label,
            "note": d.note,
            "count": counts[d.key].get(d.count_measure, 0),
            "available": counts[d.key].get(d.count_measure, 0) > 0,
            "measures": counts[d.key],
        }
        for d in DIMS
    }


def _by_code(conn: Connection) -> dict[str, dict]:
    diseases = rows(conn, "SELECT id, code FROM disease ORDER BY code")
    counts = counts_by_disease(conn)
    return {r["code"]: counts[r["id"]] for r in diseases if r["id"] in counts}


@router.get("/diseases")
def list_diseases(
    conn: Connection = Depends(get_conn),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    per_code = _by_code(conn)
    items = []
    for raw in rows(
        conn,
        f"SELECT {', '.join('`' + c + '`' for c in _IDENTITY)} FROM disease "
        "ORDER BY code LIMIT :limit OFFSET :offset",
        {"limit": limit, "offset": offset},
    ):
        counts = per_code[raw["code"]]
        items.append({
            **raw,
            "dims": dim_block(counts),
            "gaps": G.gaps_for_disease(counts, raw),
        })
    total = int(rows(conn, "SELECT COUNT(*) AS n FROM disease")[0]["n"])
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/diseases/{code}")
def disease_detail(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    conn: Connection = Depends(get_conn),
) -> dict:
    raw = rows(conn, "SELECT * FROM disease WHERE code = :code", {"code": code})
    if not raw:
        raise HTTPException(404, f"没有这个疾病码：{code}。全部可用值见 /api/diseases")
    refs = Refs(conn)
    counts = counts_by_disease(conn).get(raw[0]["id"])
    if counts is None:  # 主档有这一病但计数没长出来：装载器只写了一半，如实报错而不是回空页
        raise HTTPException(500, f"{code} 在 disease 里有行但没有逐维计数，请检查装载是否跑完")
    out = hydrate(refs, "disease", raw[0])
    out["dims"] = dim_block(counts)
    out["gaps"] = G.gaps_for_disease(counts, out)
    return out
