"""词表维读侧：器官、组织学、症状、危险因素四屏，一台接口答一屏。

这四维的共同点是"一行是一个词，不是一个数"，所以都不折序列、不求和：页面要的是清单
加上"这条凭什么算给这一病"。也因此每台都带回两份出处——关系行一份、节点行一份。
把 JOIN 出来的宽行只挂一份出处，等于把"挂载依据"或"节点本身"其中一个说成没有来源。

四台的口径各自不同，分开写而不是共用一台的原因就在响应形状上：

- 器官：primary 1–5 条与 subsite 0–12 条两档，库里没有父子边（`anatomy_node` 无 parent 列，
  subsite 的挂载依据是这一病的 ICD-9 档而不是某个 recode 的下级），所以平铺两档、不补层级。
- 组织学：一病 129–212 个形态学码收在 32–63 个三位组码下，默认只回组档与条数——组档是
  聚合，聚合不冒充一行事实，要码清单用 `?group=` 取那一档的行。
- 症状：三个源、两种语言，中文那两路只覆盖 7/18 病。按源分块回，并表或去重都会让另外
  11 病看起来也有中文名（实测跨源没有重复的 (病, 名称)，所以并起来看着"刚好不冲突"，
  那是巧合不是同一批症状）。
- 危险因素：两层形状不同，遗传关联一行是一个关联（研究 × 位点）不是一个位点，
  实测最多一病 2,151 行只对应 1,061 个位点，所以行数、去重度点数、研究号数三个数一起回，
  榜按 `-log10(p)` 排并截断；`uri_tier` 两档各回自己的数（库内 16/18 病有主条目行、
  4/18 病有声明档行、两档都有的是 2 病，合成一个数就把这三档抹平了）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Connection

from ..db import get_conn, rows
from ..dimensions import NOT_REJECTED
from ..serialize import PROV_COLS, Refs, aliased, cols, hydrate
from . import get_disease, shell

router = APIRouter(prefix="/api", tags=["vocabulary"])

_NODE = ("id", "kind", "code", "label", "label_zh", "icdo3_range", "icd9") + PROV_COLS
_MOUNT = ("id", "role", "basis", "matched_codes") + PROV_COLS
_HCODE = ("id", "code", "behavior", "code_behavior", "label", "group_code",
          "group_label") + PROV_COLS
_HMOUNT = ("id", "via_recode", "basis") + PROV_COLS
_SYMPTOM = ("id", "name", "name_lang", "heading", "source_url", "anchor", "extract_kind",
            "page_lastmod", "freq_band", "provenance") + PROV_COLS
_ASSOC = ("id", "role", "uri_tier", "trait_label", "trait_uri", "snps", "risk_allele",
          "chr_id", "chr_pos", "risk_allele_freq", "p_value_text", "pvalue_mlog", "or_beta",
          "effect_kind", "ci95_text", "pubmedid", "study_accession", "initial_sample",
          "replication_sample", "paf", "paf_basis") + PROV_COLS
_RNODE = ("id", "kind", "label", "label_zh") + PROV_COLS

GENETIC, EXPOSURE = "genetic", "exposure"


@router.get("/diseases/{code}/anatomy")
def disease_anatomy(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """关联器官：器官级分组与亚部位两档平铺，每条带挂载依据。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    rowset = rows(
        conn,
        f"SELECT {cols('n', _NODE)}, {cols('m', _MOUNT)} "
        "FROM disease_anatomy m JOIN anatomy_node n ON n.id = m.anatomy_node_id "
        "WHERE m.disease_id = :did "
        "ORDER BY FIELD(m.role, 'primary', 'subsite'), n.kind, n.label",
        {"did": dis["id"]},
    )
    tiers: dict[str, list[dict]] = {"primary": [], "subsite": []}
    for r in rowset:
        item = hydrate(refs, "anatomy_node", aliased(r, "n"))
        mounted = hydrate(refs, "disease_anatomy", aliased(r, "m"))
        item["mounted"] = mounted
        tiers[mounted["role"]].append(item)
    out = shell(conn, code, dis, "anatomy", {
        "roles": "primary 是器官级分组（页面「这一病长在哪里」显示它）；subsite 是 MONDO 的亚部位 "
                 "term，只做下钻，两档不混排也不相加",
        "basis": "matched_codes 回答「凭什么把这条算给肺」：primary 是与该 site recode 的拓扑码"
                 "集合相交（icdo3_overlap），subsite 是 MONDO term 的 ICD-9 xref 命中本病声明"
                 "（mondo_icd9）",
        "no_parent_edge": "库里没有父子边：anatomy_node 没有 parent 列，亚部位的挂载依据是这一病"
                          "的 ICD-9 档，不是某个 site recode 的下级。所以两档分开平铺，"
                          "读侧不补一条不存在的层级",
    })
    return {**out, "primary": tiers["primary"], "subsite": tiers["subsite"]}


@router.get("/diseases/{code}/histology")
def disease_histology(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    group: str = Query(None, description="三位组码（如 801）。给了才回这一档下的形态学码行"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """组织学：默认按三位组码收档回条数，`?group=` 才取那一档的码行。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    if group is not None:
        rowset = rows(
            conn,
            f"SELECT {cols('h', _HCODE)}, {cols('d', _HMOUNT)} "
            "FROM disease_histology d JOIN histology_code h ON h.id = d.histology_code_id "
            "WHERE d.disease_id = :did AND h.group_code = :g ORDER BY h.code_behavior",
            {"did": dis["id"], "g": group},
        )
        if not rowset:
            have = [str(r["group_code"]) for r in rows(
                conn,
                "SELECT h.group_code FROM disease_histology d"
                " JOIN histology_code h ON h.id = d.histology_code_id"
                " WHERE d.disease_id = :did GROUP BY h.group_code ORDER BY h.group_code",
                {"did": dis["id"]})]
            raise HTTPException(
                404, f"这一病没有 group_code={group!r} 这一档。可取的组码见本接口不带 group 的响应"
                     f"（{len(have)} 档，例如 {have[:3]}）")
        out = shell(conn, code, dis, "histology", {
            "one_group": f"这一档 {group} 下的码行；每行两份出处——码表一行、逐病展开一行",
            "via_recode": "这一档是从哪些 site recode 展开来的（多对一时逐条留着，不合并）",
        })
        items = []
        for r in rowset:
            item = hydrate(refs, "histology_code", aliased(r, "h"))
            item["mounted"] = hydrate(refs, "disease_histology", aliased(r, "d"))
            items.append(item)
        return {**out, "group": group, "n_codes": len(items), "codes": items}

    # 只按 group_code 聚：一个三位组码在源文件里可以带两个组名（804 既是 SMALL CELL
    # CARCINOMA, NOS 也是 NON-SMALL CELL CARCINOMA, NOS；854、897 同）。按 (组码, 组名)
    # 聚会把一档拆成两档，而 via_recodes 那几个 DISTINCT 数在两边各算一遍就是重计。
    grouped = rows(
        conn,
        "SELECT h.group_code, MIN(h.group_label) AS group_label,"
        " COUNT(DISTINCT h.group_label) AS label_variants, COUNT(*) AS codes,"
        " COUNT(DISTINCT d.via_recode) AS via_recodes,"
        " COUNT(DISTINCT d.dataset_release_id) AS mount_releases,"
        " COUNT(DISTINCT h.dataset_release_id) AS code_releases,"
        " COUNT(DISTINCT d.basis) AS bases"
        " FROM disease_histology d JOIN histology_code h ON h.id = d.histology_code_id"
        " WHERE d.disease_id = :did"
        " GROUP BY h.group_code ORDER BY h.group_code",
        {"did": dis["id"]},
    )
    out = shell(conn, code, dis, "histology", {
        "grouped": "一病 129–212 个形态学码收在 32–63 个三位组码下，默认只回组档与条数；"
                   "组档是聚合，不冒充一行事实，所以出处不在这一层——要某一档的码行用 ?group=",
        "why_no_provenance": "每档带的 codes / via_recodes / mount_releases / code_releases 是"
                             "这一档在库里实际有几个码、来自几个 recode、几个数据集版本；"
                             "两个 releases 都是 1 才谈得上「这是一次取数」",
        "label_variants": "组名不是一档一个：码表里 172 个三位组码对 173 个组名，804、854、897"
                          " 三档各带两个组名（804 同时是 SMALL CELL CARCINOMA, NOS 与 NON-SMALL"
                          " CELL CARCINOMA, NOS）。所以这里回变体数，group_label 只是其中一个写法，"
                          "两个名字都在 ?group= 的码行上",
        "basis": "整维 basis='via_site_recode'：这份逐病清单是我们从 SEER 的 site recode ×"
                 "形态学码交叉表推出来的，不是任何源说过「这一病有这些组织学类型」",
    })
    return {**out, "groups": [{k: v for k, v in g.items()} for g in grouped],
            "n_groups": len(grouped)}


@router.get("/diseases/{code}/symptoms")
def disease_symptoms(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """症状：按源分块回，三源两语不合表；判非的条目不进响应。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    rowset = rows(
        conn,
        f"SELECT {', '.join('`' + c + '`' for c in _SYMPTOM)} FROM symptom "
        f"WHERE disease_id = :did AND {NOT_REJECTED} "
        "ORDER BY source_id, heading, name",
        {"did": dis["id"]},
    )
    blocks: dict[int, dict] = {}
    for r in rowset:
        item = hydrate(refs, "symptom", r)
        # DDL 里有一列就叫 `provenance`（L3 兜底标记，本轮没有一行用到），
        # 与响应里收拢出处的那个键同名。换个名字带出来，不让 hydrate 把它盖掉。
        item["derive_marker"] = r["provenance"]
        prov = item["provenance"]
        key = prov["source"]["code"]
        b = blocks.setdefault(key, {
            "source": prov["source"], "dataset": prov.get("dataset"),
            "extract_method": prov["extract_method"],
            "name_lang": item["name_lang"], "headings": [], "items": [],
        })
        if item["heading"] and item["heading"] not in b["headings"]:
            b["headings"].append(item["heading"])
        b["items"].append(item)
    for b in blocks.values():
        b["n_items"] = len(b["items"])
    out = shell(conn, code, dis, "symptom", {
        "per_source": "一个源一块，不并表也不跨源去重：中文那两路只覆盖 7/18 病，"
                      "并起来会让另外 11 病看起来也有中文名",
        "name_as_written": "name 是源里的说法原样存，没有规范成同义词表；heading 是它所在小节，"
                           "source_url 与 anchor 能点回那一段",
        "rejected": "判为非症状的条目不在响应里（它们留在库里做留痕，"
                    "条数看 /api/meta 的 table_rows 与 measures 之差）",
    })
    return {**out, "sources": list(blocks.values()), "n_sources": len(blocks)}


@router.get("/diseases/{code}/risk-factors")
def disease_risk_factors(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    limit: int = Query(100, ge=1, le=500, description="遗传关联榜的取行数上限"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """危险因素：遗传关联（带效应量、榜要截断）与可干预暴露（带 PAF，全回）两层分开。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    did = dis["id"]

    tiers = rows(
        conn,
        "SELECT d.uri_tier, COUNT(*) AS rows_, COUNT(DISTINCT d.risk_factor_id) AS loci,"
        " COUNT(DISTINCT d.study_accession) AS studies,"
        " COUNT(DISTINCT d.dataset_release_id) AS releases"
        " FROM disease_risk_factor d WHERE d.disease_id = :did AND d.role = 'genetic'"
        " GROUP BY d.uri_tier ORDER BY d.uri_tier",
        {"did": did},
    )
    total = int(sum(int(t["rows_"]) for t in tiers))
    ranked = rows(
        conn,
        f"SELECT {cols('a', _RNODE)}, {cols('d', _ASSOC)} "
        "FROM disease_risk_factor d JOIN risk_factor a ON a.id = d.risk_factor_id "
        "WHERE d.disease_id = :did AND d.role = 'genetic' "
        "ORDER BY d.pvalue_mlog DESC, a.label, d.id LIMIT :lim",
        {"did": did, "lim": limit},
    )
    exposed = rows(
        conn,
        f"SELECT {cols('a', _RNODE)}, {cols('d', _ASSOC)} "
        "FROM disease_risk_factor d JOIN risk_factor a ON a.id = d.risk_factor_id "
        "WHERE d.disease_id = :did AND d.role = 'exposure' ORDER BY a.label, d.id",
        {"did": did},
    )

    def pair(r: dict) -> dict:
        item = hydrate(refs, "disease_risk_factor", aliased(r, "d"))
        item["factor"] = hydrate(refs, "risk_factor", aliased(r, "a"))
        return item

    out = shell(conn, code, dis, "risk", {
        "two_layers": "genetic 与 exposure 两栏分列不相加：一边是位点级的关联（带 OR/β 与 p 值、"
                      "给不出暴露语义），一边是可干预暴露（带人群归因分数 PAF）",
        "row_is": "genetic 一行是一个关联（研究 × 位点 × p 值），不是一个位点：所以榜同时回"
                  "rows_ / loci / studies 三个数（实测最多一病 2,151 行只对应 1,061 个位点、"
                  "99 次研究录入）",
        "tiers": "uri_tier 两档各回自己的数：main＝关联挂在病的 MONDO 主条目上（库内 16/18 病有行），"
                 "declared＝挂在 targets.GWAS_URI 声明的同级组织学档上（4/18 病有行，"
                 "两档都有的 2 病各回各的数）。合并成一个数就把「只认主条目」那个口径盖掉了",
        "order": "榜按 pvalue_mlog（源自己算好的 -log10(p)）降序，截断在 limit；"
                 "effect_kind 整列 unknown 不是解析漏了——源把 OR 与 β 装进同一列，"
                 "方向只写在 CI 文本的注记里",
        "paf": "exposure 每行带年龄标化 PAF（GBD 2023 Deaths，paf_basis 写明口径；×100 的百分数，"
               "负值＝保护方向，实测值域 −7.33–100.00）。PAF 是人群归因分数，与 genetic 层的 "
               "OR/p 不是同一种数，两榜不混排；brain 无暴露行，连 PAF 一起缺席",
    })
    return {
        **out,
        "limit": limit,
        "genetic": {
            "tiers": [{k: v for k, v in t.items()} for t in tiers],
            "total_rows": total,
            "returned": len(ranked),
            "truncated": total > len(ranked),
            "items": [pair(r) for r in ranked],
        },
        "exposure": {"returned": len(exposed), "items": [pair(r) for r in exposed]},
    }
