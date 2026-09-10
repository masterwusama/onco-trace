"""五路反查：从一个器官 / 症状 / 危险因素 / 靶点 / 药出发，取它关联的疾病清单。

正向页（`/api/diseases/{code}/…`）答"这一病有什么"，反查页答"这个东西挂在哪些病上"。
两边读同一份库、走同一道 `NOT_REJECTED` 过滤，所以同一对 (病, 实体) 在正向与反查不会
一个看得见一个看不见——这条由 `api/tests/run.py` 的 `check_reverse` 逐条比对守。

路径参数挑的是实体的稳定标识而不是名字：
- anatomy_node 用 id（整数，kind+code 也能定，但整数短且唯一键已经建了）
- symptom 用 name + lang 查询参数（symptom 没有独立标识列，(name_lang, name) 是
  idx_symptom_lookup 的前缀，也是唯一键的一半）
- risk_factor 用 id（整数，(kind, label) 也能定）
- target 用 ot_id（Open Targets 的 Ensembl 形 id，全库唯一）
- drug 用 drug_id（CHEMBL 码；同一码可以跨病、跨阶段重复出现）

疾病列表一律按 disease.code ASC（`utf8mb4_0900_ai_ci`）排：这一序与列表页、
维度页一致，前端不重排。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Connection

from ..db import get_conn, one, rows
from ..dimensions import NOT_REJECTED, identities
from ..serialize import PROV_COLS, Refs, decode_json, hydrate

router = APIRouter(prefix="/api", tags=["reverse"])

_DISEASE_BRIEF = ("id", "code", "name_zh", "category", "sex")


def _disease_list(conn: Connection, ids: list[int]) -> list[dict]:
    """按病码排的简要清单。ids 为空时直接回空列表，不发一句 WHERE id IN ()。"""
    if not ids:
        return []
    all_dis = identities(conn)
    picked = [all_dis[c] for c in sorted(all_dis, key=lambda c: c)
              if all_dis[c]["id"] in set(ids)]
    return [{k: r[k] for k in _DISEASE_BRIEF} for r in picked]


_ANODE = ("id", "kind", "code", "label", "label_zh", "icdo3_range", "icd9") + PROV_COLS


@router.get("/anatomy/{node_id}/diseases")
def anatomy_reverse(
    node_id: int = Path(..., description="anatomy_node.id"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """一个器官节点挂在哪些病上。"""
    node = one(
        conn,
        f"SELECT {', '.join('`' + c + '`' for c in _ANODE)} FROM anatomy_node WHERE id = :id",
        {"id": node_id},
    )
    if node is None:
        n = int(rows(conn, "SELECT COUNT(*) AS n FROM anatomy_node")[0]["n"])
        raise HTTPException(404, f"没有 id={node_id} 这个器官节点。全库共 {n} 个节点，"
                                 "id 见正向器官页每一行的 mounted.id")
    refs = Refs(conn)
    node_out = hydrate(refs, "anatomy_node", node)
    mounts = rows(
        conn,
        f"SELECT disease_id, {', '.join('`' + c + '`' for c in ('role', 'basis', 'matched_codes') + PROV_COLS)} "
        "FROM disease_anatomy WHERE anatomy_node_id = :nid "
        "ORDER BY disease_id",
        {"nid": node_id},
    )
    diseases = _disease_list(conn, [int(m["disease_id"]) for m in mounts])
    return {
        "entity": {"table": "anatomy_node", **node_out},
        "diseases": diseases,
        "n_diseases": len(diseases),
        "conventions": {
            "reverse_of": "/api/diseases/{code}/anatomy",
            "basis": "mounted.basis 是这一病把这个节点算进来的依据（icdo3_overlap 或 mondo_icd9），"
                     "与正向页同一条规则",
        },
    }


@router.get("/symptoms/{name}/diseases")
def symptom_reverse(
    name: str = Path(..., description="symptom.name，URL 编码后传"),
    lang: str = Query("en", description="name_lang：en 或 zh。中英文同名不常见但允许，分开查"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """一个症状名挂在哪些病上。按源分块回：三源各说各的，不并。"""
    if lang not in ("en", "zh"):
        raise HTTPException(400, f"lang 只接受 en 或 zh，得到 {lang!r}")
    sample = one(
        conn,
        "SELECT name, name_lang FROM symptom "
        f"WHERE name = :n AND name_lang = :l AND {NOT_REJECTED} LIMIT 1",
        {"n": name, "l": lang},
    )
    if sample is None:
        have = [r["name"] for r in rows(
            conn,
            "SELECT DISTINCT name FROM symptom "
            f"WHERE name_lang = :l AND {NOT_REJECTED} ORDER BY name LIMIT 8",
            {"l": lang},
        )]
        raise HTTPException(
            404,
            f"lang={lang} 下没有 name={name!r} 这个症状（判非的条目不进响应）。"
            f"前 8 个可取值：{have}",
        )
    refs = Refs(conn)
    rowset = rows(
        conn,
        f"SELECT {', '.join('`' + c + '`' for c in ('id', 'disease_id', 'source_id', 'heading', 'source_url', 'anchor', 'extract_kind', 'page_lastmod', 'freq_band', 'provenance') + PROV_COLS)} "
        f"FROM symptom WHERE name = :n AND name_lang = :l AND {NOT_REJECTED} "
        "ORDER BY source_id, disease_id",
        {"n": name, "l": lang},
    )
    disease_ids = sorted({int(r["disease_id"]) for r in rowset})
    diseases = _disease_list(conn, disease_ids)

    blocks: dict[str, list[dict]] = {}
    for r in rowset:
        item = hydrate(refs, "symptom", r)
        item["derive_marker"] = r["provenance"]
        src_code = item["provenance"]["source"]["code"]
        blocks.setdefault(src_code, []).append(item)

    return {
        "entity": {"table": "symptom", "name": name, "name_lang": lang},
        "diseases": diseases,
        "n_diseases": len(diseases),
        "sources": [{"source_code": k, "n_items": len(v), "items": v}
                    for k, v in blocks.items()],
        "conventions": {
            "reverse_of": "/api/diseases/{code}/symptoms",
            "per_source": "同一症状名在不同源里算不同条目（中文与英文分开查，不并表）",
        },
    }


_RNODE = ("id", "kind", "label", "label_zh") + PROV_COLS


@router.get("/risk-factors/{factor_id}/diseases")
def risk_factor_reverse(
    factor_id: int = Path(..., description="risk_factor.id"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """一个危险因素（位点或暴露）关联到哪几个病。genetic 与 exposure 两层分列。"""
    factor = one(
        conn,
        f"SELECT {', '.join('`' + c + '`' for c in _RNODE)} FROM risk_factor WHERE id = :id",
        {"id": factor_id},
    )
    if factor is None:
        n = int(rows(conn, "SELECT COUNT(*) AS n FROM risk_factor")[0]["n"])
        raise HTTPException(404, f"没有 id={factor_id} 这个危险因素。全库共 {n} 个，"
                                 "id 见正向危险因素页每一行的 factor.id")
    refs = Refs(conn)
    factor_out = hydrate(refs, "risk_factor", factor)
    assocs = rows(
        conn,
        "SELECT disease_id, role FROM disease_risk_factor "
        "WHERE risk_factor_id = :fid ORDER BY role, disease_id",
        {"fid": factor_id},
    )
    by_role: dict[str, list[int]] = {"genetic": [], "exposure": []}
    for a in assocs:
        by_role.setdefault(a["role"], []).append(int(a["disease_id"]))
    return {
        "entity": {"table": "risk_factor", **factor_out},
        "genetic": _disease_list(conn, sorted(set(by_role.get("genetic", [])))),
        "exposure": _disease_list(conn, sorted(set(by_role.get("exposure", [])))),
        "n_genetic": len(set(by_role.get("genetic", []))),
        "n_exposure": len(set(by_role.get("exposure", []))),
        "conventions": {
            "reverse_of": "/api/diseases/{code}/risk-factors",
            "two_layers": "genetic 与 exposure 两层分列，与正向页同一道区分",
            "no_rejected_filter": "disease_risk_factor 没有 review_status 列（只有 symptom / "
                                  "stat_fact / survival 有），所以反查不过滤——装载器写进来就算",
        },
    }


_TARGET = ("id", "ot_id", "approved_symbol", "approved_name") + PROV_COLS


@router.get("/targets/{ot_id}/diseases")
def target_reverse(
    ot_id: str = Path(..., description="target.ot_id（Ensembl 形，如 ENSG00000146648）"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """一个靶点关联到哪几个病，带每一病的合成分。"""
    target = one(
        conn,
        f"SELECT {', '.join('`' + c + '`' for c in _TARGET)} FROM target "
        "WHERE ot_id = :oid LIMIT 1",
        {"oid": ot_id},
    )
    if target is None:
        sample = [r["ot_id"] for r in rows(
            conn, "SELECT DISTINCT ot_id FROM target ORDER BY ot_id LIMIT 5")]
        raise HTTPException(404, f"没有 ot_id={ot_id!r} 这个靶点。前 5 个可取值：{sample}")
    refs = Refs(conn)
    target_out = hydrate(refs, "target", target)
    # 同一 ot_id 在不同 source 下各有一行 target，反查要收所有 target_id 的挂载
    target_ids = [r["id"] for r in rows(
        conn, "SELECT id FROM target WHERE ot_id = :oid", {"oid": ot_id})]
    placeholders = ", ".join(str(t) for t in target_ids)
    mounts = rows(
        conn,
        "SELECT dt.disease_id, dt.score, dt.novelty, dt.node_used "
        f"FROM disease_target dt WHERE dt.target_id IN ({placeholders}) "
        f"AND dt.{NOT_REJECTED} ORDER BY dt.score DESC, dt.disease_id",
    )
    disease_ids = [int(m["disease_id"]) for m in mounts]
    diseases = _disease_list(conn, disease_ids)
    # 每一病附上合成分：反查页最要紧的数就是"这个靶点在这一病上排第几"。
    # ORDER BY score DESC 保证第一笔是最高分；同一病多行时只取第一笔
    scores: dict[int, dict] = {}
    for m in mounts:
        did = int(m["disease_id"])
        if did not in scores:
            scores[did] = {
                "score": float(m["score"]) if m["score"] is not None else None,
                "novelty": float(m["novelty"]) if m["novelty"] is not None else None,
                "node_used": m["node_used"],
            }
    for d in diseases:
        d.update(scores.get(d["id"], {}))
    return {
        "entity": {"table": "target", **target_out},
        "diseases": diseases,
        "n_diseases": len(diseases),
        "conventions": {
            "reverse_of": "/api/diseases/{code}/targets",
            "order": "疾病按合成分降序（与正向页一致），同分再按病码升序",
        },
    }


@router.get("/drugs/{drug_id}/diseases")
def drug_reverse(
    drug_id: str = Path(..., description="drug.drug_id（CHEMBL 码，如 CHEMBL803）"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """一个药（按 CHEMBL 码）在哪些病上做到哪一期。"""
    sample = one(
        conn,
        "SELECT drug_id, MIN(drug_name) AS drug_name FROM drug "
        f"WHERE drug_id = :did AND {NOT_REJECTED} GROUP BY drug_id",
        {"did": drug_id},
    )
    if sample is None:
        have = [r["drug_id"] for r in rows(
            conn,
            f"SELECT DISTINCT drug_id FROM drug WHERE {NOT_REJECTED} "
            "AND drug_id <> '' ORDER BY drug_id LIMIT 5",
        )]
        raise HTTPException(404, f"没有 drug_id={drug_id!r} 这个药（判非的不进响应）。"
                                 f"前 5 个可取值：{have}")
    rowset = rows(
        conn,
        f"SELECT disease_id, phase, moa FROM drug "
        f"WHERE drug_id = :did AND {NOT_REJECTED} "
        "ORDER BY disease_id, phase",
        {"did": drug_id},
    )
    disease_ids = sorted({int(r["disease_id"]) for r in rowset})
    diseases = _disease_list(conn, disease_ids)
    # 每一病附上阶段与机制：反查要回答的是"这个药在哪些病上做到哪一期"
    per_disease: dict[int, list[dict]] = {}
    for r in rowset:
        entry = decode_json("drug", dict(r))
        per_disease.setdefault(int(r["disease_id"]), []).append({
            "phase": entry["phase"],
            "moa": entry.get("moa"),
        })
    for d in diseases:
        d["entries"] = per_disease.get(d["id"], [])
    return {
        "entity": {"table": "drug", "drug_id": drug_id, "drug_name": sample["drug_name"]},
        "diseases": diseases,
        "n_diseases": len(diseases),
        "conventions": {
            "reverse_of": "/api/diseases/{code}/drugs",
            "per_disease": "每一病附 entries[]：这一病上这个药有几个 (阶段, 机制) 条目——"
                           "同一药同一病可以有多期并行",
        },
    }
