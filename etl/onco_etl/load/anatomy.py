"""器官树与组织学装载器：一台写 anatomy_node / disease_anatomy / histology_code / disease_histology。

一个装载器覆盖两维，是因为 SEER 的 sitetype.xlsx 本身就是一张 site recode × 形态学码的交叉表
（82 × 803），拆成两台要把同一份解析跑两遍。

亚部位是这一批里唯一带判断的部分。MONDO 的 ICD-9 xref 混着部位与细胞类型——
'main bronchus cancer'(162.2) 与 'small cell lung carcinoma'(162.9) 都会被 targets.icd9 命中，
全收就会把组织学亚型挂成器官。所以只收 ICD-9 为它单开了部位档的 term：
带小数点、末位不是 .8/.9（那两位是"其他/未特指"，不是新亚部位），
且这一档在同一病的候选 term 里只被它一个用到（多个 term 共用同一码说明该码标的是器官本身，
不是亚部位——肾的 7 个亚型 term 全挤在 189.0 就是这么露出来的）。
另加一条声明式排除：三台血病（leukemia / nhl / myeloma）整个跳过，
ICD-9 200–208 章编的是细胞类型而不是部位，从那一章捞不出亚部位。
实测 145 个候选 term 留下 51 个，逐条读过。

`icdo3_range` 在亚部位节点上取 term 自己的 ICD10CM xref：ICD-10 肿瘤章的 C 码就是 ICD-O-3
的拓扑码（同一套部位编号），所以这不是把两套系统混起来，而是源没给 ICD-O-3 时能拿到的
最接近的东西。51 个里 28 个带这个 xref，缺的 23 个留空，不猜。
"""
from __future__ import annotations

from .. import db, raw
from ..probes import icdo3_seer, mondo
from ..probes.codes import expand_icd9, expand_topo, icd9_matches
from ..targets import TARGETS
from .base import Ctx, LoadResult, prov, replace_scope, upsert

SEER = icdo3_seer.SOURCE
SEER_DATASET = icdo3_seer.DATASET
MONDO = mondo.SOURCE
MONDO_DATASET = mondo.DATASET

MALIGNANT = "3"
# 见模块注释：.8/.9 是 ICD-9 的兜底档，出现它不等于 term 有专属部位
RESIDUAL_LAST = ("8", "9")
HEMATOPOIETIC = ("leukemia", "nhl", "myeloma")
# 挂载关系逐条打印读过；码表是整表直取，657 行没人逐行看，如实记 unreviewed
REVIEW_LINKS = "spot_checked"
REVIEW_CODES = "unreviewed"


def _slots(term: mondo.MondoTerm, prefixes: set[str]) -> set[str]:
    """term 命中 targets.icd9 的那些"专属部位档"写法，如 {'162.2'}。"""
    out = set()
    for code in term.icd9:
        if "." not in code or not icd9_matches(code, prefixes):
            continue
        if code.rsplit(".", 1)[1][:1] in RESIDUAL_LAST:
            continue
        out.add(code)
    return out


def pick_subsites(scanned: mondo.MondoScan) -> dict[str, list[tuple[mondo.MondoTerm, str]]]:
    """按模块注释的规则挑亚部位 term，同时定下"凭哪个 ICD-9 档挂上去"。

    同一 term 有几个合格档时取字典序最小者：幂等不只靠唯一键，还靠这一列每次挑得一样。
    """
    out: dict[str, list[tuple[mondo.MondoTerm, str]]] = {}
    for t in TARGETS:
        if t.code in HEMATOPOIETIC:
            out[t.code] = []
            continue
        terms = scanned.subsites.get(t.code, [])
        picked = {term.id: _slots(term, expand_icd9(t.icd9)) for term in terms}
        counts: dict[str, int] = {}
        for got in picked.values():
            for code in got:
                counts[code] = counts.get(code, 0) + 1
        out[t.code] = [
            (term, min(unique))
            for term in terms
            if (unique := [c for c in picked[term.id] if counts[c] == 1])
        ]
    return out


def matched_recodes(seer: icdo3_seer.IcdoScan) -> dict[str, list[tuple[str, str]]]:
    """target.code → [(site recode, 相交到的三位码)]，文件行序。挂载判据与探针同一条。"""
    out: dict[str, list[tuple[str, str]]] = {}
    for t in TARGETS:
        want = expand_topo(t.icdo3)
        out[t.code] = [
            (
                recode,
                ",".join(f"C{c:03d}" for c in sorted(want & seer.topo[recode])),
            )
            for recode in seer.sites
            if want & seer.topo[recode]
        ]
    return out


def _node_rows(seer, subsites, sid_seer, rid_seer, sid_mondo, rid_mondo) -> list[dict]:
    rows = [
        {
            "kind": "site_recode",
            "code": recode,
            "label": label,
            "icdo3_range": recode,  # 展开出的三位拓扑码不落节点，凭这一列随时重算
            "icd9": "",
            **prov(
                source_id=sid_seer,
                dataset_release_id=rid_seer,
                extract_method="l1_structured",
                review_status=REVIEW_CODES,
            ),
        }
        for recode, label in seer.sites.items()
    ]
    for code, picked in subsites.items():
        for term, slot in picked:
            rows.append(
                {
                    "kind": "subsite_term",
                    "code": term.id,
                    "label": term.name,
                    "icdo3_range": ",".join(term.xref("ICD10CM")),
                    "icd9": slot,
                    **prov(
                        source_id=sid_mondo,
                        dataset_release_id=rid_mondo,
                        extract_method="l2_rule",
                        review_status=REVIEW_LINKS,
                    ),
                }
            )
    return rows


def _anatomy_link_rows(
    hits, subsites, node_ids, ids, sid_seer, rid_seer, sid_mondo, rid_mondo
) -> list[dict]:
    rows = []
    for code, recodes in hits.items():
        for recode, matched in recodes:
            rows.append(
                {
                    "disease_id": ids[code],
                    "anatomy_node_id": node_ids[("site_recode", recode)],
                    "role": "primary",
                    "basis": "icdo3_overlap",
                    "matched_codes": matched,
                    **prov(
                        source_id=sid_seer,
                        dataset_release_id=rid_seer,
                        extract_method="l2_rule",
                        review_status=REVIEW_LINKS,
                    ),
                }
            )
    for code, picked in subsites.items():
        for term, slot in picked:
            rows.append(
                {
                    "disease_id": ids[code],
                    "anatomy_node_id": node_ids[("subsite_term", term.id)],
                    "role": "subsite",
                    "basis": "mondo_icd9",
                    "matched_codes": slot,
                    **prov(
                        source_id=sid_mondo,
                        dataset_release_id=rid_mondo,
                        extract_method="l2_rule",
                        review_status=REVIEW_LINKS,
                    ),
                }
            )
    return rows


def _hist_code_rows(seer, sid_seer, rid_seer) -> list[dict]:
    """只收 /3：基准 18 病全是恶性，良性/原位/动态未定的码混进来是噪声。"""
    return [
        {
            "code": hc.code,
            "behavior": hc.behavior,
            "code_behavior": hc.code_behavior,
            "label": hc.label,
            "group_code": hc.group_code,
            "group_label": hc.group_label,
            **prov(
                source_id=sid_seer,
                dataset_release_id=rid_seer,
                extract_method="l1_structured",
                review_status=REVIEW_CODES,
            ),
        }
        for hc in seer.codes.values()
        if hc.behavior == MALIGNANT
    ]


def _hist_link_rows(seer, hits, code_ids, ids, sid_seer, rid_seer) -> list[dict]:
    rows = []
    for code, recodes in hits.items():
        seen: set[str] = set()
        for recode, _matched in recodes:
            for key in seer.site_codes[recode]:
                # 唯一键是 (disease, code)：同一码在同一病的多个 recode 下都合法时只记头一个来源
                if key in code_ids and key not in seen:
                    seen.add(key)
                    rows.append(
                        {
                            "disease_id": ids[code],
                            "histology_code_id": code_ids[key],
                            "via_recode": recode,
                            "basis": "via_site_recode",
                            **prov(
                                source_id=sid_seer,
                                dataset_release_id=rid_seer,
                                extract_method="l2_rule",
                                review_status=REVIEW_CODES,
                            ),
                        }
                    )
    return rows


def load(ctx: Ctx) -> LoadResult:
    body, version, origin, existing, _obs = icdo3_seer.load_payload(ctx.offline)
    seer = icdo3_seer.scan(body)
    seer_path = existing or raw.archive(SEER, version or "unknown", icdo3_seer.FILENAME, body)
    m_body, m_origin, m_existing, _m_obs = mondo.load_payload(ctx.offline)
    scanned = mondo.scan(m_body)
    m_key = mondo.version_key(scanned.version)
    m_path = m_existing or raw.archive(MONDO, m_key, mondo.FILENAME, m_body)

    subsites = pick_subsites(scanned)
    n_sub = sum(len(v) for v in subsites.values())

    with ctx.tx() as conn:
        sid_seer, sid_mondo = ctx.source_id(SEER), ctx.source_id(MONDO)
        rid_seer = ctx.register(
            conn,
            SEER,
            SEER_DATASET,
            upstream_version=version,
            release_date=icdo3_seer.version_key(version),
            body_bytes=len(body),
            sha256=raw.sha256_file(seer_path),
            rows_seen=seer.n_rows,
            raw_path=raw.rel(seer_path),
        )
        rid_mondo = ctx.register(
            conn,
            MONDO,
            MONDO_DATASET,
            upstream_version=scanned.version,
            release_date=m_key,
            body_bytes=len(m_body),
            sha256=raw.sha256_file(m_path),
            rows_seen=scanned.n_terms,
            raw_path=raw.rel(m_path),
        )
        ids = ctx.disease_ids(conn)
        hits = matched_recodes(seer)

        n_node = upsert(
            conn, "anatomy_node",
            _node_rows(seer, subsites, sid_seer, rid_seer, sid_mondo, rid_mondo),
        )
        node_ids = {
            (str(k), str(c)): int(i)
            for k, c, i in db.rows(conn, "SELECT `kind`,`code`,`id` FROM `anatomy_node`")
        }
        links = _anatomy_link_rows(
            hits, subsites, node_ids, ids, sid_seer, rid_seer, sid_mondo, rid_mondo
        )
        n_da = replace_scope(
            conn, "disease_anatomy", {"source_id": sid_seer},
            [r for r in links if r["source_id"] == sid_seer],
        )
        n_da += replace_scope(
            conn, "disease_anatomy", {"source_id": sid_mondo},
            [r for r in links if r["source_id"] == sid_mondo],
        )

        n_hc = upsert(conn, "histology_code", _hist_code_rows(seer, sid_seer, rid_seer))
        code_ids = {
            str(cb): int(i)
            for cb, i in db.rows(conn, "SELECT `code_behavior`,`id` FROM `histology_code`")
        }
        n_dh = replace_scope(
            conn,
            "disease_histology",
            {"source_id": sid_seer},
            _hist_link_rows(seer, hits, code_ids, ids, sid_seer, rid_seer),
        )

    covered = len({r["disease_id"] for r in links})
    with_sub = sum(1 for v in subsites.values() if v)
    msg = (
        f"site recode {len(seer.sites)} / 恶性形态学码 {n_hc} / 亚部位 term {n_sub}"
        f"（{origin} + {m_origin}）"
    )
    if with_sub < len(TARGETS):
        msg += f"；{len(TARGETS) - with_sub} 病没有合格亚部位（含血病 3 病按声明跳过）"
    ctx.job.set(written=n_node + n_da + n_hc + n_dh)
    return LoadResult(
        written={
            "anatomy_node": n_node,
            "disease_anatomy": n_da,
            "histology_code": n_hc,
            "disease_histology": n_dh,
        },
        covered=covered,
        total=len(TARGETS),
        message=msg,
    )
