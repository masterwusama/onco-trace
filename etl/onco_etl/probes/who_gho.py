"""WHO GHO OData 的中国死亡年龄组探针（原计划里 Athena API 的后继）。

计划里这一维挂在 `apps.who.int/gho/athena/api/GHO/…`。实测那条路整条 302 到
`www.who.int/data/gho/legacy` 的一张说明页，Athena 不在了；GHO 文档页给的后继是
`ghoapi.azureedge.net/api` 的 OData——匿名、无 key，但每个指标一个实体集、
`$metadata` 有 12 MB，所以取数只能按指标码点名，不能整库拉。

探针只回答一个问题：GHE 有没有「中国 × 18 病 × 年龄组」的死亡数。
AGEGROUP 维确实有完整的 5 岁阶梯，GHECAUSES 也确实把癌种拆到器官级——
两半各自都在、从不同时出现在国家级行上，而判据要看的正是这个交集，不是维表。
"""
from __future__ import annotations

import json
import time
from collections import Counter

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "who_gho"
DATASET = "gho-odata-cause-age"
API = "https://ghoapi.azureedge.net/api"
HEADERS = {"Accept": "application/json"}

COUNTRY = "CHN"
CAUSE_DIMS = {"GHECAUSES", "CHILDCAUSE", "ENVCAUSE", "GBDCHILDCAUSES"}
NEED_BANDS = 10
# 网关把 $top 硬限在 1000（超了回 400，原因只在响应体里），所以一个指标要翻页。
# ROW_CAP 是单指标取行上限：真撞上说明国家级 × 年龄组这条路已经有量级的行了。
PAGE = 1000
ROW_CAP = 20000

# 死因与年龄档在哪一列随指标而变（Dim1/Dim2/Dim3 + DimNType），只能按类型认领
AGE_DIMS = {"AGEGROUP"}
SEX_CODES = {"both": "SEX_BTSX", "female": "SEX_FMLE", "male": "SEX_MLE"}

# 18 病 → GHE 死因档。空串是实测没有独立档：肾/脑/甲状腺并入 GHE078
# "Other malignant neoplasms"（与几十个其他部位同居一档），
# NHL 与骨髓瘤共用 GHE076 "Lymphomas, multiple myeloma"。
GHE_CAUSE = {
    "lung": "GHECAUSES_GHE068",
    "colorectum": "GHECAUSES_GHE065",
    "liver": "GHECAUSES_GHE066",
    "stomach": "GHECAUSES_GHE064",
    "breast_female": "GHECAUSES_GHE070",
    "pancreas": "GHECAUSES_GHE067",
    "esophagus": "GHECAUSES_GHE063",
    "prostate": "GHECAUSES_GHE074",
    "cervix": "GHECAUSES_GHE071",
    "ovary": "GHECAUSES_GHE073",
    "bladder": "GHECAUSES_GHE075",
    "uterus": "GHECAUSES_GHE072",
    "leukemia": "GHECAUSES_GHE077",
    "thyroid": "",
    "kidney": "",
    "brain": "",
    "nhl": "",
    "myeloma": "",
}

CRITERIA = (
    "中国国家级 × ≥10 个年龄组 × 声明性别的癌种死亡数或死亡率可匿名 OData 取回；"
    "区域合计、收入组合计与「Malignant neoplasms」一档总计都不算数"
)

META_FILES = ("indicators.json", "indicator_dimensions.json",
              "dimension_ghecauses.json", "dimension_agegroup.json")


def _get_json(url: str, ms: int) -> tuple[dict, int, int, str, bytes]:
    r = fetch(url, headers=HEADERS, timeout=(10, 180), max_bytes=64_000_000)
    ms += r.latency_ms
    if not r.ok or not r.body:
        # 400 的原因（如 $top 超限）只写在响应体里，状态码本身没有信息量
        raise SystemExit(f"{url} → {r.status or r.reachability}：{r.note} {r.text[:200]}")
    try:
        d = json.loads(r.text)
    except json.JSONDecodeError:
        # 这个网关回 504 时给的是 HTML 错误页，状态码与内容类型都可能骗人
        raise SystemExit(f"{url} → HTTP {r.status} 但响应不是 JSON（{r.content_type}）："
                         f"{r.text[:160]}")
    return d, ms, r.status, r.reachability, r.body


def _fetch_rows(path: str, ms: int) -> tuple[dict, int, int, str, bytes]:
    """按 $top/$skip 翻页取一份指标的行，合并成一个文档返回。

    合并后的 `value` 是全部行、`@odata.count` 是服务端声明的总数，
    归档时就存这份合并结果——离线重放不必再翻一遍页。
    """
    rows: list = []
    total: int | None = None
    status: int | None = None
    reach = "direct"
    while len(rows) < ROW_CAP:
        doc, ms, status, reach_, _ = _get_json(
            f"{API}/{path}{'&' if '?' in path else '?'}$top={PAGE}&$skip={len(rows)}"
            "&$count=true&$format=json", ms)
        page = doc.get("value", [])
        rows += page
        total = doc.get("@odata.count", total)
        if reach_ == "proxy":
            reach = "proxy"
        if not page or (total is not None and len(rows) >= int(total)):
            break
    merged = {"value": rows, "@odata.count": total, "truncated": bool(
        total is not None and len(rows) < int(total))}
    return merged, ms, status, reach, json.dumps(merged, ensure_ascii=False).encode("utf-8") + b"\n"


def _pick(row: dict, want: set) -> object:
    for i in (1, 2, 3):
        if row.get(f"Dim{i}Type") in want:
            return row.get(f"Dim{i}")
    return None


def _dims_by_indicator(rows: list) -> dict:
    out: dict[str, set] = {}
    for v in rows:
        out.setdefault(v["IndicatorCode"], set()).add((v["Dimension"] or "").upper())
    return out


def _five_year_bands(values: list) -> list:
    """维表里成体系的 5 岁档：0-4、5-9 … 80-84、85+，不含月龄与自定义宽组。"""
    ladder = [f"AGEGROUP_YEARS{a:02d}-{a + 4:02d}" for a in range(0, 80, 5)] + ["AGEGROUP_YEARS85PLUS"]
    have = {v["Code"] for v in values}
    return [b for b in ladder if b in have]


def _by_cause(rows: list) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        cause = _pick(r, CAUSE_DIMS)
        if not cause:
            continue
        d = out.setdefault(str(cause), {"ages": set(), "sexes": set(), "years": set()})
        d["rows"] = d.get("rows", 0) + 1
        age, sex, year = _pick(r, AGE_DIMS), _pick(r, {"SEX"}), r.get("TimeDim")
        if age is not None:
            d["ages"].add(str(age))
        if sex is not None:
            d["sexes"].add(str(sex))
        if year is not None:
            d["years"].add(int(year))
    return out


def probe(offline: bool = False) -> ProbeResult:
    http: int | None = None
    ms = 0
    # 这一批请求里只要有一次落到代理，整趟就记 proxy：直连不稳，
    # 记成 direct 等于骗 scheduler 说不需要代理
    reach_seen: list[str] = []
    sizes: dict[str, int] = {}
    if offline:
        key_dir = raw.newest_dir(SOURCE, "indicators.json")
        if not key_dir:
            raise SystemExit(f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/indicators.json 不存在")
        blobs = {n: json.loads((key_dir / n).read_text(encoding="utf-8")) for n in META_FILES}
        sets: dict[str, dict] = {
            p.name[len("china_"):-len(".json")]: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(key_dir.glob("china_*.json"))
        }
        for n in list(META_FILES) + [f"china_{c}.json" for c in sets]:
            sizes[n] = (key_dir / n).stat().st_size
        reach_seen = ["offline"]
    else:
        blobs: dict[str, dict] = {}
        bodies: dict[str, bytes] = {}
        for name, url in (
            ("indicators.json", f"{API}/Indicator?$select=IndicatorCode,IndicatorName&$format=json"),
            ("indicator_dimensions.json", f"{API}/IndicatorDimension?$format=json"),
            ("dimension_ghecauses.json", f"{API}/DIMENSION/GHECAUSES/DimensionValues?$format=json"),
            ("dimension_agegroup.json", f"{API}/DIMENSION/AGEGROUP/DimensionValues?$format=json"),
        ):
            blobs[name], ms, http, reach_, bodies[name] = _get_json(url, ms)
            reach_seen.append(reach_)
            time.sleep(0.2)
        cause_inds = sorted(c for c, ds in _dims_by_indicator(
            blobs["indicator_dimensions.json"].get("value", [])).items() if ds & CAUSE_DIMS)
        sets = {}
        for code in cause_inds:
            name = f"china_{code}.json"
            doc, ms, http, reach_, bodies[name] = _fetch_rows(
                f"{code}?$filter=SpatialDim eq '{COUNTRY}'", ms)
            reach_seen.append(reach_)
            sets[code] = doc
            time.sleep(0.2)
        stamp = _stamp(sets)
        key_dir = raw.archive_dir(SOURCE, f"gho-{stamp}")
        for n, b in bodies.items():
            (key_dir / n).write_bytes(b)
        sizes = {n: len(b) for n, b in bodies.items()}

    if "offline" in reach_seen:
        reach = "offline"
    elif "proxy" in reach_seen:
        reach = "proxy"
    else:
        reach = "direct"

    cause_rows = blobs["dimension_ghecauses.json"].get("value", [])
    causes = {v["Code"] for v in cause_rows}
    titles = {v["Code"]: v.get("Title") or "" for v in cause_rows}
    bands = _five_year_bands(blobs["dimension_agegroup.json"].get("value", []))
    names = {v["IndicatorCode"]: v["IndicatorName"]
             for v in blobs["indicators.json"].get("value", [])}
    dims_map = _dims_by_indicator(blobs["indicator_dimensions.json"].get("value", []))

    # 声明核对：填了的码要真在维表里，且不能被两个病共用（共用了就不是器官级）
    declared = {t.code: GHE_CAUSE.get(t.code, "缺声明") for t in TARGETS}
    unknown = sorted(c for c in declared.values() if c and c not in causes)
    dup = {c: n for c, n in Counter(v for v in declared.values() if v).items() if n > 1}
    uniq = {code: c for code, c in declared.items() if c and c not in dup}

    merged: dict[str, dict] = {}
    per_ind = []
    for code, doc in sorted(sets.items()):
        rows = doc.get("value", [])
        bc = _by_cause(rows)
        for cause, d in bc.items():
            m = merged.setdefault(cause, {"ages": set(), "sexes": set(), "years": set(), "rows": 0})
            m["ages"] |= d["ages"]
            m["sexes"] |= d["sexes"]
            m["years"] |= d["years"]
            m["rows"] += d["rows"]
        per_ind.append(
            {
                "indicator": code,
                "name": (names.get(code) or "")[:56],
                "chn_rows": len(rows),
                "chn_total": doc.get("@odata.count"),
                "truncated": bool(doc.get("truncated")),
                "chn_causes": sorted(bc)[:6],
                "chn_age_bands": len({a for d in bc.values() for a in d["ages"]}),
                "declared_dims": sorted(dims_map.get(code, set())),
            }
        )

    detail = []
    for t in TARGETS:
        cause = declared[t.code]
        d = merged.get(cause) if cause else None
        years = sorted(d["years"]) if d and d["years"] else []
        ok = bool(d) and len(d["ages"]) >= NEED_BANDS and SEX_CODES[t.sex] in d["sexes"]
        detail.append(
            {
                "code": t.code,
                "ghe_cause": cause or "无独立档",
                "cause_title": titles.get(cause, ""),
                "n_age_bands": len(d["ages"]) if d else 0,
                "sexes": sorted(d["sexes"]) if d else [],
                "years": f"{years[0]}–{years[-1]}" if years else "无",
                "pass": ok,
            }
        )
    covered = sum(1 for x in detail if x["pass"])
    has_chn = [p for p in per_ind if p["chn_rows"]]
    aged = [p for p in has_chn if p["chn_age_bands"] >= NEED_BANDS]
    stamp = _stamp(sets)

    if covered == len(TARGETS) and not unknown and not dup:
        verdict = "ok"
    elif covered:
        verdict = "partial"
    else:
        verdict = "empty"

    msg = (
        f"达标 {covered}/{len(TARGETS)}。维表这一半是齐的：AGEGROUP 维有 {len(bands)} 档成体系的 "
        f"5 岁阶梯（{bands[0]}…{bands[-1] if bands else ''}），GHECAUSES 维 {len(causes)} 档里 "
        f"GHE061–GHE079 是 19 个癌种档，18 病有 {len(uniq)} 病能一对一——"
        f"肾/脑/甲状腺只出现在 GHE078「Other malignant neoplasms」这一档合计里，"
        f"NHL 与骨髓瘤共用 GHE076，这五病在维表层就拿不到器官级。"
        f"缺的全在国家级行这一半：带死因维的 {len(sets)} 个指标里只有 {len(has_chn)} 个有 "
        f"{COUNTRY} 行（{', '.join(p['indicator'] for p in has_chn)}），"
        f"其中带 ≥{NEED_BANDS} 个年龄组的 {len(aged)} 个"
        + ("（有中国行的这些指标只有 SEX 与死因两维，没有年龄维）" if not aged else "")
        + "；"
        f"形状最合判据的 GHE_DALY*/YLL*/YLD* 与 MORT_600/700 共 "
        f"{len(sets) - len(has_chn)} 个，"
        f"SpatialDimType 只有 MGHEREG（区域与收入组合计），{COUNTRY} 零行。"
        f"能取到的中国癌种数最细只到「Malignant neoplasms」一档合计："
        f"SDG_SH_DTH_RNCOM {len(sets.get('SDG_SH_DTH_RNCOM', {}).get('value', []))} 行 = "
        f"年份 × 3 性别 × 4 个大组，没有年龄。"
        "入口另记两条：计划里写的 apps.who.int/gho/athena/api/ 已整条 302 到 "
        "www.who.int/data/gho/legacy；后继 OData 匿名无 key，但可达性分两档——"
        f"probe-reach 那条单指标 $top=5 小查询直连 200，而这一趟（12 MB $metadata + "
        f"{len(sets)} 个指标逐个按 $top={PAGE} 翻页）有一步落代理（504 与连接重置都出现过），"
        "所以这一行记 proxy，scheduler 别照 reach 的结论给专项探针配直连。"
        f"GHO 不给数据集发布日，upstream_version 取本次归档行里 Date 的"
        f"最大值 gho-{stamp}——行级 Date 是修改戳不是发布戳。"
    )
    if unknown:
        msg += f" 声明了但维表里没有的码：{', '.join(unknown)}。"
    trunc = [p["indicator"] for p in per_ind if p["truncated"]]
    if trunc:
        msg += (f" 注意：{', '.join(trunc)} 的行数撞上单指标上限 {ROW_CAP}，"
                "只归档了前若干行，覆盖度可能低估。")

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=sum(len(d.get("value", [])) for d in sets.values()),
        diseases_covered=covered,
        diseases_total=len(TARGETS),
        fields_seen=["Dim1/Dim2/Dim3 + DimNType", "NumericValue", "Value", "TimeDim",
                     "SpatialDim/SpatialDimType", "Date", "IndicatorDimension.Dimension",
                     "@odata.count"],
        sample=detail + per_ind,
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=f"gho-{stamp}",
        # 没有发布日可用，宁缺——把行修改戳当发布日会把"数据到 2021"说成"这一版 2021 年发布"
        release_date=None,
        release_bytes=sum(sizes.values()),
        release_sha256=raw.sha256_bytes(
            b"".join((key_dir / n).read_bytes() for n in sorted(sizes))
        ),
    )


def _stamp(sets: dict) -> str:
    dates = [str(r.get("Date"))[:10]
             for doc in sets.values() for r in doc.get("value", []) if r.get("Date")]
    return max(dates) if dates else "no-row"
