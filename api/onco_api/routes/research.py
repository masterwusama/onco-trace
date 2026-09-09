"""研究层读侧：试验、文献、靶点、在研药四屏，一台一屏且四台都分页。

前三批的读接口都是"这一病这一维几十到几百行，一次全回"。这一层不是：实测一病
试验 292–3,368 行、靶点关联 226–3,321 行、文献固定 500 行、在研药 20–1,023 行，
而一行试验折算 2,419 字节、带满两段重文本是 5,863 字节——把试验整维一次回完是
132.6 MB。所以这一批有两件事：分页，以及**分页之后还能说清整维有多大**。
`page.total_rows` 说的是过滤后还剩几行，
`source_hit` 说的是源那头命中几行，两个数谁也不能替谁：实测 trial / target / drug
三维 18/18 病两数相等，publication 0/18 相等（每病固定 500 行而命中 10,373–139,780）。

四条都不发明排序，因为库里没有相关度分值列：

- 试验按 `nct_id`（一病内实测唯一，0 组重复），是注册号序不是重要度序；
- 文献按 `id`，即装载序，而装载序就是 EPMC 返回的相关度序——拿归档
  `data/raw/europepmc/rows-*/pub-*.json.gz` 与库里逐位对过三个病各 500 行，0 处不一致；
- 靶点按 `score` 降序 + `ot_id` 兜并列（同一病内 4,391 组同分、最狠一组 194 行，
  不兜底翻页会重行或漏行）；
- 在研药按 `drug_name`（一病内药名实测唯一，所以没有"同药两期"要排）。

过滤只开在真列上，值域由当次分面现算；值不在其中回 404 并列出可取值，而不是静默回
空集——"这一病没有 PHASE4 的在招试验"和"PHASE4 不是这一病能取的值"是两句话。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Connection

from ..db import get_conn, rows
from ..dimensions import NOT_REJECTED
from ..serialize import PROV_COLS, Refs, aliased, cols, hydrate
from . import get_disease, shell

router = APIRouter(prefix="/api", tags=["research"])

MAX_LIMIT = 200
# 源报的命中数落在另一张表：stat_fact 里 estimate_basis='query_count' 那一行。
# 研究层四张表自己只有落库行数，把"库里有几行"说成"源里有几条"就是从这一列开始的。
HIT_METRIC = {"trial": "trial_count", "publication": "publication_count",
              "target": "target_count", "drug": "drug_count"}
# 源没给 phase 档位的行（trial.phases 为 NULL，实测全库 5,573/23,705）归到这档，
# 选它等价于 `phases IS NULL`。CT 自己的六档取值里没有带括号的写法，不会撞。
PHASE_NONE = "(none)"

# 两段重自由文本默认不带：eligibility 行行都有（均长 2,883 字符、最长 20,404），
# publications 只有 4,709/23,705 行有（那些行均长 2,777 字符、最长 116,996）。
# 一行全站折算 2,419 字节（含出处五列），带满两段是 5,863，整维逐行加总 132.6 MB。
_TRIAL_BLOBS = ("eligibility", "publications")
_TRIAL = ("id", "disease_id", "dataset_code", "nct_id", "brief_title", "title",
          "overall_status", "status_bucket", "study_type", "phases", "enrollment",
          "design_info", "conditions", "interventions", "arm_groups", "primary_outcome",
          "elig_sex", "healthy_volunteers", "lead_sponsor", "collaborators",
          "location_countries", "fda_regulated", "why_stopped", "matched_terms") + PROV_COLS
_PUB = ("id", "disease_id", "dataset_code", "ext_key", "pmid", "doi", "title", "journal",
        "pub_year", "is_oa", "in_epmc", "has_pdf", "has_abstract", "matched_terms") + PROV_COLS
_DRUG = ("id", "disease_id", "dataset_code", "drug_id", "drug_name", "phase",
         "moa") + PROV_COLS
_REL = ("id", "disease_id", "target_id", "dataset_code", "score", "novelty",
        "datasource_scores", "node_used") + PROV_COLS
_NODE = ("id", "ot_id", "approved_symbol", "approved_name", "dataset_code") + PROV_COLS


def _facet(conn: Connection, sql: str, did: int, params: dict | None = None) -> list[dict]:
    """这一病在这一维的取值分布：页面渲染筛选项用，也是过滤值合法性的唯一判据。

    分面一律按**未过滤**的整维算，所以它回答"还能筛什么、各剩几行"，不跟着过滤条件
    缩水——跟着缩水的分面选一次就自删一个选项，第二筛就没有回退路。
    """
    return [{"value": r["value"], "rows": int(r["rows_"] or 0)}
            for r in rows(conn, sql, {"did": did, **(params or {})})]


def _pin(value, facet: list[dict], what: str) -> None:
    """过滤值必须落在这一病已有的取值里，否则 404 把可取的值列出来。"""
    if value is not None and value not in [f["value"] for f in facet]:
        raise HTTPException(
            404, f"{what}={value!r} 不在这一病的取值里。可取："
                 f"{[f['value'] for f in facet]}（同一接口的 facets 带着这些值与各自行数）")


def _page(select: str, table: str, where: str, order: str, bind: dict, conn: Connection,
          limit: int, offset: int) -> tuple[dict, list[dict]]:
    """COUNT 与一页行分两条语句：一条 SQL 里的总数会跟着 LIMIT 走，翻页时会自骗。"""
    total = int(rows(conn, f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", bind)[0]["n"] or 0)
    got = rows(conn, f"SELECT {select} FROM {table} WHERE {where} ORDER BY {order}"
                    " LIMIT :__lim OFFSET :__off", {**bind, "__lim": limit, "__off": offset})
    return {"limit": limit, "offset": offset, "total_rows": total, "returned": len(got),
            "has_more": offset + len(got) < total}, got


def _source_hit(conn: Connection, refs: Refs, did: int, dim: str, stored: int) -> dict:
    """源那头命中多少条与库里存了多少条并排回，谁也不替谁说"取满了"。

    `captured` 逐病现算，不写死：装载器对 trial / target / drug 逐病比过
    len(rows)==count 这个等式（见 db/schema.sql），publication 则固定截在 500。
    """
    metric = HIT_METRIC[dim]
    found = rows(
        conn,
        f"SELECT metric, unit, value, dataset_code, cohort_note, {', '.join(PROV_COLS)}"
        f" FROM stat_fact WHERE disease_id = :did AND metric = :m"
        f" AND estimate_basis = 'query_count' AND {NOT_REJECTED}",
        {"did": did, "m": metric},
    )
    if not found:  # 命中数没落库（只装到一半）：宁可回未知，也不补一个 0 让它看着"取满了"
        return {"metric": metric, "value": None, "stored_rows": stored, "captured": None,
                "note": "stat_fact 里没有这一病的 query_count 行，源命中数未知"}
    r = found[0]
    value = int(r["value"])
    return {
        "metric": r["metric"], "unit": r["unit"], "value": value,
        "dataset_code": r["dataset_code"], "cohort_note": r["cohort_note"],
        "stored_rows": stored, "captured": value == stored,
        "not_captured": max(value - stored, 0),
        "provenance": refs.provenance(r),
    }


@router.get("/diseases/{code}/trials")
def disease_trials(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    status_bucket: str = Query(None, description="trial.status_bucket：active/idle/completed/other"),
    phase: str = Query(None, description=f"trial.phases 数组含这一档；{PHASE_NONE}＝源没给档位的行"),
    include: str = Query(None, description=f"逗号分隔，点名要默认不带的大字段：{list(_TRIAL_BLOBS)}"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT, description="本页最多几行"),
    offset: int = Query(0, ge=0, description="跳过几行"),
    conn: Connection = Depends(get_conn),
) -> dict:
    """在招试验：按 NCT 号分页，可按状态档与分期档筛。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    did = dis["id"]
    buckets = _facet(conn, "SELECT status_bucket AS value, COUNT(*) AS rows_ FROM trial"
                           " WHERE disease_id = :did GROUP BY value ORDER BY value", did)
    phases = _facet(
        conn,
        "SELECT COALESCE(p.phase, :none) AS value, COUNT(DISTINCT t.id) AS rows_"
        " FROM trial t LEFT JOIN JSON_TABLE(t.phases, '$[*]'"
        " COLUMNS (phase VARCHAR(24) PATH '$')) p ON TRUE"
        " WHERE t.disease_id = :did GROUP BY value ORDER BY rows_ DESC, value",
        did, {"none": PHASE_NONE},
    )
    _pin(status_bucket, buckets, "status_bucket")
    _pin(phase, phases, "phase")
    where, bind = "disease_id = :did", {"did": did}
    if status_bucket is not None:
        where += " AND status_bucket = :sb"
        bind["sb"] = status_bucket
    if phase is not None:
        if phase == PHASE_NONE:
            where += " AND phases IS NULL"
        else:
            where += " AND JSON_CONTAINS(phases, JSON_QUOTE(:ph))"
            bind["ph"] = phase
    extra = [c for c in (include or "").replace(" ", "").split(",") if c]
    if bad := [c for c in extra if c not in _TRIAL_BLOBS]:
        raise HTTPException(404, f"include 只接受 {list(_TRIAL_BLOBS)}，传进来的是 {bad}")
    page, got = _page(", ".join(_TRIAL + tuple(extra)), "trial", where, "nct_id", bind,
                      conn, limit, offset)
    out = shell(conn, code, dis, "trial", {
        "row_is": "一行是一个试验命中一个病，不是一个试验：全库 23,705 行只有 19,254 个 NCT，"
                  "2,469 个试验跨病出现（最多一个试验挂在 17 病上），所以 measures.rows 与"
                  " measures.nct 要分开读。一病之内 nct_id 实测唯一",
        "order": "按 nct_id 升序＝注册号序。源没给相关度分值、库里也没有排名列，所以这里不排"
                 "重要度；这个序在一病之内全序，翻页不会重行也不会漏行",
        "only_active": "status_bucket 整列都是 active：装载器只取在招三档（RECRUITING /"
                       " NOT_YET_RECRUITING / ENROLLING_BY_INVITATION），其余三档要等接全状态"
                       " 才有值。所以这一档分面不能当「整库试验的状态分布」读",
        "phase_facet": f"phases 是数组，一档多行的研究在每个档各计一次，所以分面合计会大于"
                       f" total_rows（实测最多一病 3,368 行、分面合计 3,599）；{PHASE_NONE}"
                       f" 那一档就是 phases IS NULL 的行（全库 5,573 行），选它等于筛"
                       f"「源没给分期档」",
        "blob_fields": "eligibility（入排标准整段自由文本，行行都有、均长 2,883 字符）与"
                       " publications（试验引出的文献，只有 4,709/23,705 行有、那些行均长"
                       " 2,777 字符）默认不带：lung 的第一页实测 112 KB，带上两段是 230 KB"
                       "（全站均值折算是 118 KB / 286 KB）。点名要写"
                       " ?include=eligibility,publications",
        "matched_terms": "命中理由是各病声明词的子集，NULL 不是解析失败：全库 4,872/23,705 行为"
                         " NULL，逐行查过它们的 conditions 与标题，0 行出现过本病声明的任何一个词"
                         "（每病 26–925 行）——那一批是靠 CT 主题词自带的 MeSH 展开进来的，"
                         "页面上「凭什么是这一病」到这一层为止",
        "page_vs_hit": "page.total_rows 是过滤后这一维还剩几行，source_hit.value 是源那头命中"
                       " 几条。两数相等只说明「这一次查询取满了」，不说明源没有更多",
        "no_country_filter": "location_countries 是国家名数组原样存（CT 匿名侧没有地理过滤器，"
                             "只能取回自己数，见 db/schema.sql），读侧也不开这个筛：数组里是"
                             " \"Turkey (Türkiye)\" 这种写法，按名字筛会漏",
    })
    out["source_hit"] = _source_hit(conn, refs, did, "trial", out["measures"]["rows"])
    return {
        **out,
        "filters": {"status_bucket": status_bucket, "phase": phase, "include": extra},
        "facets": {"status_bucket": buckets, "phase": phases},
        "page": page,
        "items": [hydrate(refs, "trial", r) for r in got],
    }


@router.get("/diseases/{code}/publications")
def disease_publications(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    year: int = Query(None, description="publication.pub_year，源标的出版年"),
    is_oa: int = Query(None, ge=0, le=1, description="记录级全文开放标记：1 有、0 无"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> dict:
    """前沿文献：按源返回的相关度序分页，可按年份与全文开放筛。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    did = dis["id"]
    years = _facet(conn, "SELECT pub_year AS value, COUNT(*) AS rows_ FROM publication"
                         " WHERE disease_id = :did GROUP BY value ORDER BY value DESC", did)
    oas = _facet(conn, "SELECT is_oa AS value, COUNT(*) AS rows_ FROM publication"
                       " WHERE disease_id = :did GROUP BY value ORDER BY value DESC", did)
    _pin(year, years, "year")
    _pin(is_oa, oas, "is_oa")
    where, bind = "disease_id = :did", {"did": did}
    if year is not None:
        where += " AND pub_year = :y"
        bind["y"] = year
    if is_oa is not None:
        where += " AND is_oa = :oa"
        bind["oa"] = is_oa
    page, got = _page(", ".join(_PUB), "publication", where, "id", bind, conn, limit, offset)
    out = shell(conn, code, dis, "publication", {
        "order": "按 id 升序＝装载序＝EPMC 返回的相关度序（拿归档 pub-*.json.gz 与库里逐位对过"
                 " lung / breast_female / bladder 各 500 行，0 处不一致）。那是源在查询当时给的"
                 " 序，库里没有相关度分值列，重新装载还会变，所以它不是一个可以长期引用的排名",
        "cap": "每病固定 500 行是上限样本不是全量：全量取满要七百多页、约 5 GB（pageSize 硬上限"
               " 1000），所以只取相关度前 500。差额逐病回在 source_hit 里——实测命中"
               " 10,373–139,780，18/18 病都不等于 500，页面要说「库内 500 篇、源命中 N 篇」"
               " 而不是「这一病有 N 篇文献」",
        "pub_year": "pub_year 跨 2021–2027，最大那一年是在印记录提前给的年份、不是脏数据，所以"
                    "「最近一年」不能按它截，也不该拿它当筛选项的默认值",
        "ids": "ext_key 是业务键（pmid 优先、退 doi、再退标题哈希）：实测 9,000 行里 8,303 有"
               " pmid、540 只有 doi、157 只能哈希，所以 pmid 不能当主键用，回查要带 ext_key",
        "is_oa": "is_oa 是记录级标记，本批实测 3,464/9,000＝38.5% 为 1（0 行为 NULL）；整维"
                 " 「全文可得率」另有 EPMC 查询 facet 口径 50.8%，两个数不同源，页面用哪个要写明",
        "no_review_gate": "研究层四张表都没有 review_status 过滤基线（symptom / stat_fact /"
                          " survival 才有）：这一层本轮一行都没判过非，加过滤等于筛一个不存在的档",
    })
    out["source_hit"] = _source_hit(conn, refs, did, "publication", out["measures"]["rows"])
    return {
        **out,
        "filters": {"year": year, "is_oa": is_oa},
        "facets": {"pub_year": years, "is_oa": oas},
        "page": page,
        "items": [hydrate(refs, "publication", r) for r in got],
    }


@router.get("/diseases/{code}/targets")
def disease_targets(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> dict:
    """靶点榜：按合成分降序分页，每行两份出处（关联行 + 靶点行）。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    did = dis["id"]
    page, got = _page(f"{cols('d', _REL)}, {cols('t', _NODE)}",
                      "disease_target d JOIN target t ON t.id = d.target_id",
                      "d.disease_id = :did", "d.score DESC, t.ot_id", {"did": did},
                      conn, limit, offset)
    out = shell(conn, code, dis, "target", {
        "order": "按 score（Open Targets 的合成分，0–1）降序，并列用 ot_id 兜底：并列不是解析"
                 " 问题，同一病内实测有 4,391 个 (病, 分数) 组里不止一行、最狠的一组 194 行同分"
                 "（按全库 score 数是另一组数：3,822 组 / 最大 384 行），不兜底翻页就会重行或漏行。"
                 " score 是各数据源分数合成的，不代表「这一靶点多重要」",
        "row_is": "一行是一个 (病, 靶点) 关联，不是一个靶点：全库 28,919 行只有 7,098 个靶点，"
                  "一个靶点最多挂在 18 个病上；一病之内由唯一键保证不重复",
        "two_provenance": "每行两份出处——关联行（分数、novelty、用的哪个 MONDO 节点）一份，"
                          "靶点行（符号与名字）一份。两者来自同一次 OT 下载，但是两张表的事实",
        "datasource_scores": "逐数据源分数是 [{id, score}] 数组（1–14 项，实测 6,042 行只有一项、"
                             " 单行最长 737 字符）：「有遗传学或临床支持」与「只是文献共现」的"
                             " 区分全靠它，合成分一个数看不出来",
        "threshold": "只收 score ≥ 0.1：库里 0 行低于它、恰好压在 0.1 上的 33 行，阈下那近 20 万条"
                     " 是共现级没进库。所以这一维的「取满」指的是 ≥0.1 的全部，source_hit 与"
                     " stored_rows 相等说的是这一句",
        "node_used": "查询用的 MONDO 节点原样带出（18 病 18 个节点、0 行为空）：乳腺癌用的是宽档"
                     " MONDO_0007254 而主条目是 MONDO_0004379，不记这一笔就没法解释为什么这一病"
                     " 条数比别家多",
        "symbol_not_key": "靶点符号全库有 13 个重名（同一 approved_symbol 对多个 ot_id），所以行的"
                          " 身份是 ot_id，前端不要把符号当 key",
        "novelty": "novelty 是 OT 的新颖度比例（0–1），实测 23,990/28,919 行有值，NULL 表示源没给"
                   " 而不是 0",
    })
    out["source_hit"] = _source_hit(conn, refs, did, "target", out["measures"]["rows"])
    items = []
    for r in got:
        item = hydrate(refs, "target", aliased(r, "t"))
        item["mounted"] = hydrate(refs, "disease_target", aliased(r, "d"))
        items.append(item)
    return {**out, "page": page, "items": items}


@router.get("/diseases/{code}/drugs")
def disease_drugs(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    phase: str = Query(None, description="drug.phase，OT 的 maxClinicalStage 原文"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> dict:
    """在研药：按药名分页，分期档只作分面与筛选项，不作排序。"""
    dis = get_disease(conn, code)
    refs = Refs(conn)
    did = dis["id"]
    phases = _facet(conn, "SELECT phase AS value, COUNT(*) AS rows_ FROM drug"
                          " WHERE disease_id = :did GROUP BY value ORDER BY rows_ DESC, value", did)
    _pin(phase, phases, "phase")
    where, bind = "disease_id = :did", {"did": did}
    if phase is not None:
        where += " AND phase = :ph"
        bind["ph"] = phase
    page, got = _page(", ".join(_DRUG), "drug", where, "drug_name", bind, conn, limit, offset)
    out = shell(conn, code, dis, "drug", {
        "phase_not_ordered": "phase 是 OT 的 maxClinicalStage 原文（实测 11 个取值，形如 PHASE_2 /"
                             " PHASE_1_2 / EARLY_PHASE_1），源自己没排过序，所以按它筛、按它分面，"
                             "但不按它排榜：字典序会把 PHASE_1_2 排在 PHASE_1 前面，那是字母表的功劳",
        "order": "按 drug_name 升序（只 strip 不改大小写）。一病之内药名实测唯一，所以这一维的"
                 " rows 与去重药名数相等，不存在同一药在两期各一行的情况。这个序是 MySQL 的 "
                 "utf8mb4_0900_ai_ci 排序规则序，不是码位序：实测 '.ALPHA.-TOCOPHERYLOXYACETIC "
                 "ACID' 排在 '(R)-PFI-2' 前面，而 JS 默认 sort 的结果正相反——前端不要自己重排，"
                 "否则翻页会看着跳行",
        "row_is": "唯一键是 (病, 源, 药名, 阶段)，但实测 6,309 行里 (病, 药名) 0 组重复；跨病看"
                  " 全库 6,309 行只有 2,437 个药名，一个药最多挂在 18 个病上",
        "moa": "moa 是机制数组（4,128/6,309 行有值、单行最多 15 条）：装载器把同 (病, 药, 阶段)"
               " 的多行机制去重并起来，没机制是 NULL 不是空数组，页面上这两者不是一句话",
        "not_trial_phase": "这里的 PHASE_2 与试验页的 PHASE2 不是一套词表（一个是 OT 的临床阶段上限、"
                           " 一个是 CT 的 protocolSection.phases），拼成同一个筛选项会一半筛空",
    })
    out["source_hit"] = _source_hit(conn, refs, did, "drug", out["measures"]["rows"])
    return {**out, "filters": {"phase": phase}, "facets": {"phase": phases},
            "page": page, "items": [hydrate(refs, "drug", r) for r in got]}
