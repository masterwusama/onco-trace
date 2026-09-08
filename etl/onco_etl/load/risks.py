"""危险因素装载器：GWAS 遗传关联 + GBD CRA 可干预暴露清单，一台写 `risk_factor` 与 `disease_risk_factor`。

两源合一台，因为它们填的是同一对表的同一批列，而这一对表的意义全在"两类并排、缺口各自可见"
（`role` 与那一列 `paf` 建而不填）。拆两台会让人以为两边的数可以相加。

六条口径：

1. **一行是一个关联，不是一个位点。** GWAS 的幂等键是 `assoc_key`＝sha1(病码|研究号|SNPS|
   最强 SNP-等位|P-VALUE)，实测 6,210 命中行按它落 6,208 行——差的 2 行是同一条关联的两种
   连接号写法。按位点或按论文构造都会出事（那两个口径的代价写在列注释里）。撞键时留哪一行
   由 `Assoc.keep_rank` 定：主条目档优先、有可解析区间优先、显著性高优先、文件行序在前。
2. **基因不拆节点。** 一行多个基因是分号（偶尔逗号）相连的一个字符串，整串当一个 label：
   拆开要把同一条关联复制成几行，行数虚增，而"这个位点映射到哪几个基因"本就是源的一句合写。
   `MAPPED_GENE` 整列为空的 613 行退到 SNPS（形如 `chr17:43124027`），退化后仍为空的就中止——
   没 label 的节点在反查里是一个谁都点不到的空档。
3. **档位分开落。** `uri_tier` 记这一行挂在主条目还是 `targets.GWAS_URI` 的声明档上。
   只认主条目是 14/18 病达标，算上声明档才是 18/18——落库按后者，前者留在探针裁定里。
   CRA 的对应关系没有档位概念，那一列留 NULL。
4. **`effect_kind` 整列未判定。** 源把 OR 与 β 装进同一列，方向只写在 CI 文本的
   unit increase/decrease 注记里，实测分不开（列注释写着数）。宁可不判，也不按"多数是 OR"猜。
5. **CRA 只落清单，不落强度。** 33 个可干预暴露 × 71 条病因对应关系，四列度量在源里只是
   "这个组合有数"的标记，PAF 整个在 IHME 授权门后。页面文案因此不许写成"危险因素排行"。
6. **中文名列建而不填**，同器官名：没有可匿名取回的中文名源。

目测的是两份规则产物（哪些表型名算这个病 / 剔聚合档后剩下的都是可干预暴露），声明在
`gwas_catalog.EYEBALL` 与 `gbd_cra.EYEBALL`，落库前逐病核对数与名单，核不上就中止。
"""
from __future__ import annotations

from .. import db
from ..probes import gbd_cra, gwas_catalog
from ..targets import TARGETS
from .base import Ctx, LoadResult, prov, replace_scope, upsert

GWAS = gwas_catalog.SOURCE
GWAS_DATASET = gwas_catalog.DATASET
CRA = gbd_cra.SOURCE
CRA_DATASET = gbd_cra.DATASET

GENETIC, EXPOSURE = "genetic_locus", "exposure"
# 关联行的判断只有"这一行算不算这个病"，那两件事目测过；label 是源列原样、效应量与 p 值是
# 源里抄的数，没有可判的东西，所以节点整批如实记 unreviewed
REVIEW_ALIGN, REVIEW_PLAIN = "spot_checked", "unreviewed"


def _text(s: str) -> str:
    """GWAS 的文本列：`NR` / `NA` / `-` 这些占位符统一成空串。

    列都是 NOT NULL DEFAULT ''，写 None 会被拒；把占位符原样存进库里，前端就得自己认得
    GWAS Catalog 用哪几种写法表示"没有"。
    """
    v = (s or "").strip()
    return "" if v in gwas_catalog.NULLS else v


def _num(s: str) -> float | None:
    v = _text(s)
    try:
        return float(v)
    except ValueError:
        return None


def kept_assocs(assocs: list[gwas_catalog.Assoc]) -> list[gwas_catalog.Assoc]:
    """按 `assoc_key` 收拢成"一关联一行"，撞键的按 `keep_rank` 挑一条。"""
    kept: dict[str, gwas_catalog.Assoc] = {}
    for a in assocs:
        k = a.assoc_key()
        if k not in kept or a.keep_rank < kept[k].keep_rank:
            kept[k] = a
    return sorted(kept.values(), key=lambda a: (a.code, a.assoc_key()))


def _label(a: gwas_catalog.Assoc) -> str:
    gene, snps = _text(a.gene), _text(a.snps)
    if not gene and not snps:
        raise SystemExit(
            f"GWAS {a.study} 这一行 MAPPED_GENE 与 SNPS 都是空的，节点没法定 label")
    return gene or snps


def check_gwas(rows: list[gwas_catalog.Assoc]) -> list[str]:
    """核对逐病目测的三个数；返回目测过的病码（没在 EYEBALL 里的病不冒领 spot_checked）。"""
    for t in TARGETS:
        got = [a for a in rows if a.code == t.code]
        want = gwas_catalog.EYEBALL.get(t.code)
        have = (len(got), len({a.locus for a in got}), len({a.trait for a in got}))
        if want and have != want:
            raise SystemExit(
                f"GWAS {t.code} 关联漂移：现在是 {have}（关联/位点/表型名），"
                f"EYEBALL 声明的是 {want}——重读那份清单再改声明")
    return [t.code for t in TARGETS if t.code in gwas_catalog.EYEBALL]


def check_cra(factors: list[gbd_cra.Factor]) -> list[str]:
    names = {f.rei_name for f in factors}
    if names != set(gbd_cra.REI_EYEBALL):
        add = sorted(names - set(gbd_cra.REI_EYEBALL))
        gone = sorted(set(gbd_cra.REI_EYEBALL) - names)
        raise SystemExit(
            f"CRA 风险因素清单变了：多出 {add}、少了 {gone}——"
            f"逐条目测过的那 33 个名不再成立，重读后改 REI_EYEBALL")
    for t in TARGETS:
        n = sum(1 for f in factors if f.code == t.code)
        if t.code in gbd_cra.EYEBALL and n != gbd_cra.EYEBALL[t.code]:
            raise SystemExit(
                f"CRA {t.code} 条数漂移：{n} vs EYEBALL 的 {gbd_cra.EYEBALL[t.code]}")
    return [t.code for t in TARGETS if t.code in gbd_cra.EYEBALL]


def gwas_nodes(rows, sid, rid) -> list[dict]:
    return [
        {"kind": GENETIC, "label": lab, "label_zh": None,
         **prov(source_id=sid, dataset_release_id=rid,
                extract_method="l1_structured", review_status=REVIEW_PLAIN)}
        for lab in sorted({_label(a) for a in rows})
    ]


def cra_nodes(factors, sid, rid) -> list[dict]:
    return [
        {"kind": EXPOSURE, "label": lab, "label_zh": None,
         **prov(source_id=sid, dataset_release_id=rid,
                extract_method="l1_structured", review_status=REVIEW_PLAIN)}
        for lab in sorted({f.rei_name for f in factors})
    ]


def gwas_links(rows, rf, ids, sid, rid, eyeballed) -> list[dict]:
    return [
        {
            "disease_id": ids[a.code],
            "risk_factor_id": rf[(GENETIC, _label(a).lower())],
            "role": "genetic",
            "assoc_key": a.assoc_key(),
            "uri_tier": a.tier,
            "trait_label": _text(a.trait),
            "trait_uri": a.tail,
            "snps": _text(a.snps),
            "risk_allele": _text(a.risk_allele),
            "chr_id": _text(a.chr_id),
            "chr_pos": _text(a.chr_pos),
            "risk_allele_freq": _num(a.freq),
            "p_value_text": _text(a.p_value),
            "pvalue_mlog": _num(a.mlog),
            "or_beta": _num(a.or_beta),
            "effect_kind": "unknown",
            "ci95_text": _text(a.ci),
            "pubmedid": int(a.pubmed) if a.pubmed.strip().isdigit() else 0,
            "study_accession": _text(a.study),
            "initial_sample": _text(a.initial),
            "replication_sample": _text(a.replication),
            **prov(source_id=sid, dataset_release_id=rid, extract_method="l2_rule",
                   review_status=REVIEW_ALIGN if a.code in eyeballed else REVIEW_PLAIN),
        }
        for a in rows
    ]


def cra_links(factors, rf, ids, sid, rid, eyeballed) -> list[dict]:
    """CRA 一支只有病因 × 风险因素这一件事可记，GWAS 专属那批列一律留空。"""
    return [
        {
            "disease_id": ids[f.code],
            "risk_factor_id": rf[(EXPOSURE, f.rei_name.lower())],
            "role": "exposure",
            "assoc_key": f.assoc_key(),
            "uri_tier": None,
            "trait_label": f.cause_name,
            "trait_uri": f"GBD:{f.cause_id}",
            "snps": "", "risk_allele": "", "chr_id": "", "chr_pos": "",
            "risk_allele_freq": None, "p_value_text": "", "pvalue_mlog": None,
            "or_beta": None, "effect_kind": "unknown", "ci95_text": "",
            "pubmedid": 0, "study_accession": "", "initial_sample": "",
            "replication_sample": "",
            **prov(source_id=sid, dataset_release_id=rid, extract_method="l2_rule",
                   review_status=REVIEW_ALIGN if f.code in eyeballed else REVIEW_PLAIN),
        }
        for f in sorted(factors, key=lambda x: (x.code, x.rei_name))
    ]


def load(ctx: Ctx) -> LoadResult:
    gp = gwas_catalog.load_payload(ctx.offline)
    if gp.blocked:
        raise SystemExit(f"GWAS 关联包没取成（{gp.blocked.verdict}）：{gp.blocked.message}")
    cp = gbd_cra.load_payload(ctx.offline)
    if cp.blocked:
        raise SystemExit(f"CRA 对照表没取成（{cp.blocked.verdict}）：{cp.blocked.message}")

    rows = kept_assocs(gp.scan.assocs)
    factors = cp.cra.factors
    eye_g = check_gwas(rows)
    eye_c = check_cra(factors)

    with ctx.tx() as conn:
        sid_g, sid_c = ctx.source_id(GWAS), ctx.source_id(CRA)
        # 复用探针登记的那一版：这一台的解析就是探针那一份，再登记一版只会造出
        # 同一份字节的第二个版本号
        rid_g = ctx.latest_release(conn, GWAS, GWAS_DATASET)
        rid_c = ctx.latest_release(conn, CRA, CRA_DATASET)
        ids = ctx.disease_ids(conn)

        n_rf = upsert(conn, "risk_factor", gwas_nodes(rows, sid_g, rid_g))
        n_rf += upsert(conn, "risk_factor", cra_nodes(factors, sid_c, rid_c))
        # 节点表只 upsert 不删重插（id 被关系行引用），所以按 (kind,label) 把 id 读回来挂外键。
        # 键取小写：`uk_risk_factor` 用的 utf8mb4_0900_ai_ci 大小写不敏感，Python 侧同口径
        # 才不会"库里有一行、字典里说没有"
        rf = {(str(k), str(l).lower()): int(i)
              for k, l, i in db.rows(conn, "SELECT `kind`,`label`,`id` FROM `risk_factor`")}
        links = gwas_links(rows, rf, ids, sid_g, rid_g, eye_g) + \
            cra_links(factors, rf, ids, sid_c, rid_c, eye_c)
        n_g = replace_scope(conn, "disease_risk_factor", {"source_id": sid_g},
                            [r for r in links if r["source_id"] == sid_g])
        n_c = replace_scope(conn, "disease_risk_factor", {"source_id": sid_c},
                            [r for r in links if r["source_id"] == sid_c])

    covered = len({r["disease_id"] for r in links})
    ctx.job.set(written=n_rf + n_g + n_c)
    main = sum(1 for a in rows if a.tier == "main")
    deaths = sum(1 for f in factors if f.deaths)
    msg = (
        f"GWAS {n_g} 行遗传关联（{len({a.code for a in rows})}/18 病，主条目 {main} 行 + "
        f"声明档 {n_g - main} 行；位点 {len({a.locus for a in rows})}、研究 "
        f"{len({a.study for a in rows})}；效应量 {sum(1 for a in rows if _text(a.or_beta))} 行、"
        f"p 值全有）/ CRA {n_c} 行可干预暴露（"
        f"{len({f.code for f in factors})}/18 病、{len({f.rei_name for f in factors})} 个暴露。"
        f"源里那四列度量标记在这 {n_c} 条上全是 1（带 Deaths 的 {deaths} 条就是全部）——"
        "它标的是「这个组合有数」，不是强度）。\n"
        f"节点 {n_rf} 个（genetic_locus {len({_label(a) for a in rows})} + exposure "
        f"{len({f.rei_name for f in factors})}），基因整串当一个节点、"
        f"MAPPED_GENE 空的 {sum(1 for a in rows if not _text(a.gene))} 行退到 SNPS。\n"
        "不写的三列：`paf`/`paf_basis`（归因强度整个在 IHME 授权门后，vizhub 数据面四个路由全 401）、"
        "`label_zh`（没有可匿名取回的中文名源）；`effect_kind` 整列 unknown（OR 与 β 在源里"
        "共列且实测分不开）。genetic 是遗传易感性、不是可干预暴露，页面不许把两类混成排行。"
        f"两份归档 GWAS {gp.version} / CRA {cp.version}。"
    )
    return LoadResult(
        written={"risk_factor": n_rf, "disease_risk_factor": n_g + n_c},
        covered=covered, total=len(TARGETS), message=msg,
    )
