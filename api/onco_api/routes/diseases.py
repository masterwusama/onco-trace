"""疾病列表与详情。

列表页要回答的是"这一病哪几维有数"，所以每行带一份逐维度量而不是一个总分——
给总分就会有人把"症状 12 条 + 靶点 3,000 条"读成同一个东西的两个档。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Connection

from .. import gaps as G
from ..db import get_conn, rows
from ..dimensions import DIMS, counts_by_code
from ..serialize import Refs, hydrate
from . import get_disease

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


@router.get("/diseases")
def list_diseases(
    conn: Connection = Depends(get_conn),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    per_code = counts_by_code(conn)
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
    dis = get_disease(conn, code)
    raw = rows(conn, "SELECT * FROM disease WHERE id = :id", {"id": dis["id"]})
    if not raw:  # 身份查得到却取不到整行：主档在两查询之间被改了，如实报错
        raise HTTPException(500, f"{code} 在 disease 里取不到整行，请检查装载是否跑完")
    refs = Refs(conn)
    counts = counts_by_code(conn).get(dis["code"])
    if counts is None:  # 主档有这一病但计数没长出来：装载器只写了一半，如实报错而不是回空页
        raise HTTPException(500, f"{code} 在 disease 里有行但没有逐维计数，请检查装载是否跑完")
    out = hydrate(refs, "disease", raw[0])
    out["dims"] = dim_block(counts)
    out["gaps"] = G.gaps_for_disease(counts, out)
    return out
