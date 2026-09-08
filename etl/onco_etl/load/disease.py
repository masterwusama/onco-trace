"""疾病主档装载器：`targets.py` 那 18 行的库内镜像 + MONDO 解析出来的三列。

这张表不产生新事实——`code` 与声明一一对应，人不直接改它，装载器每次按声明覆盖。
唯一从源取回的是 `mondo_name` / `ncit_id` / `xrefs`，所以整行的出处记 MONDO 那一版发布，
`extract_method` 却是 `declared`：行本身来自仓库内声明，这是 `disease` 与其余事实表唯一的区别。

主条目解析不出来时不丢行：声明列照常写，MONDO 三列留空并在消息里点名。
丢掉一行会让下游装载器直接报错（它按 code 拿外键），而"这一病暂时没解析到 xref"
本该是可见的缺口而不是缺失的主档。
"""
from __future__ import annotations

from .. import raw
from ..probes import mondo
from ..targets import GWAS_URI, OT_NODE, TARGETS
from .base import Ctx, LoadResult, prov, upsert

SOURCE = mondo.SOURCE
DATASET = mondo.DATASET
# 18 个主条目是逐病人工比对语义范围后声明的（选取原则写在 targets.py 的 mondo_id 注释），
# 不是抽查——这一行的"人看过没有"答案是看过，且看的就是这一行
REVIEW = "confirmed"


def _xref_map(term: mondo.MondoTerm) -> dict:
    """除 NCIT 外的 xref 原样存。NCIT 有自己的列，重复存一份会有两边不一致的余地。"""
    out: dict[str, list[str]] = {}
    for x in term.xrefs:
        pre, _, val = x.partition(":")
        if pre.upper() == "NCIT":
            continue
        out.setdefault(pre, []).append(val)
    return {k: sorted(v) for k, v in sorted(out.items())}


def build_rows(release_id: int, source_id: int, scanned: mondo.MondoScan) -> list[dict]:
    rows = []
    for t in TARGETS:
        term = scanned.anchors.get(t.code)
        ncit = term.xref("NCIT") if term else ()
        rows.append(
            {
                "code": t.code,
                "name_zh": t.name_zh,
                "name_en": t.name_en,
                "category": t.category,
                "sex": t.sex,
                "icd10": t.icd10,
                "icdo3": t.icdo3,
                "icd9": t.icd9,
                "mondo_id": t.mondo_id,
                "mondo_name": term.name if term else "",
                "ncit_id": ncit[0] if ncit else "",
                "xrefs": _xref_map(term) if term else [],
                "ot_node": OT_NODE.get(t.code, t.mondo_id),
                "gwas_uris": list(GWAS_URI.get(t.code, ())),
                "gbd_cause": t.gbd_cause,
                "gco_today": t.gco_today,
                "gco_time": t.gco_time,
                "search_terms": list(t.search_terms),
                "pdq_pages": list(t.pdq_pages),
                **prov(
                    source_id=source_id,
                    dataset_release_id=release_id,
                    extract_method="declared",
                    review_status=REVIEW,
                ),
            }
        )
    return rows


def load(ctx: Ctx) -> LoadResult:
    body, origin, existing, _obs = mondo.load_payload(ctx.offline)
    scanned = mondo.scan(body)
    key = mondo.version_key(scanned.version)
    path = existing or raw.archive(SOURCE, key, mondo.FILENAME, body)

    with ctx.tx() as conn:
        sid = ctx.source_id(SOURCE)
        rid = ctx.register(
            conn,
            SOURCE,
            DATASET,
            upstream_version=scanned.version,
            release_date=key,
            body_bytes=len(body),
            sha256=raw.sha256_file(path),
            rows_seen=scanned.n_terms,
            raw_path=raw.rel(path),
        )
        rows = build_rows(rid, sid, scanned)
        n = upsert(conn, "disease", rows)

    unresolved = sorted(set(scanned.anchors) ^ {t.code for t in TARGETS})
    no_ncit = [t.code for t in TARGETS if t.code in scanned.anchors and not scanned.anchors[t.code].xref("NCIT")]
    msg = f"{len(TARGETS)} 行按声明覆盖，MONDO 解析 {len(scanned.anchors)}/{len(TARGETS)}（{origin}）"
    if unresolved:
        msg += f"；主条目没解析到：{', '.join(unresolved)}——MONDO 三列留空"
    if no_ncit:
        msg += f"；缺 NCIT：{', '.join(no_ncit)}"
    ctx.job.set(written=n)
    return LoadResult(
        written={"disease": n},
        covered=len(scanned.anchors),
        total=len(TARGETS),
        message=msg,
    )
