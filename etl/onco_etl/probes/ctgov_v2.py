"""ClinicalTrials.gov v2 查询词命中率探针：判"这一病有没有在做试验"，不是判"接口通不通"。

判据写的是"每病在注册试验 >0"，但这一维给前端看的是**前沿研究**——2003 年做完的试验不是前沿。
所以达标线定在"按声明查询词能取回 ≥1 项在招试验"，同时把全库总数与状态分布一起报上去。

三处口径都是实测踩过来的，其中第一条推翻了这个探针的第一版设计：

  ① 计数有机制，只是文档没写在参数表里：`countTotal=true` 让响应顶层多出一个
    `totalCount`（实测 lung 14,501，与翻页数出来的行数完全相等）。
    本探针第一版按"顶层只有 studies 与 nextPageToken、没有计数"设计，
    用 pageSize=1000 翻页数了 18 病两趟共约 160 个请求、467 s——而 `countOnly`、
    `meta`、`totalHits` 这些猜出来的参数名一律 400，真名只有 `countTotal`。
    `pageSize=0` 也回 200 但不给 count，别当成空结果。
  ② `query.cond` 里加不加引号是两种检索。裸串走 MeSH 概念映射：`Lung Neoplasms` 与
    `lung cancer` 都回 14,501（同一个主题词，展开后比字面串多 1,238 条）；
    一旦这个词不是 MeSH 主题词，裸串就退化成词级匹配——`cervical cancer` 裸 11,331
    对加引号 2,538（4.5 倍），`nervous system cancer` 裸 8,135 对 4,075。
    所以只有声明为 MeSH 主题词的第一项能裸写，自由文本同义词一律必须加引号，
    否则"这一病有多少试验"会被别的癌种的试验灌水。这一支逐词量裸/引两种数，
    比值 ≥2 的词按"词写宽了"报警。
    混写两个数都要：18 病合计裸写主题词比整串加引号多 9,787 项（MeSH 展开），
    同义词又在主题词之上多并进来 2,823 项、13 病有增量（脑瘤一项就多 1,021），
    所以既不能整串加引号、也不能只查第一项。
    方向不总是单边——`Multiple Myeloma` 裸写比加引号少 13 条，
    说明概念映射也可能落在比字面串更窄的一档上。
  ③ 在招可以直接问，不必整页取回状态再数：`filter.overallStatus=RECRUITING,…` 与
    `query.cond` 能同时用（实测 lung 在招 3,182）。地理过滤器仍然没有——
    `filter.geo` 的三种写法与 `aggFilters=geo:CHN` 实测都被拒，
    所以"有没有中国参与的试验"只能取 `locations.country` 自己数。

版本戳仍然在另一个路由：`/api/v2/studies` 的 etag 是站点静态资源戳，
`/api/v2/version` 才给 `apiVersion` 与 `dataTimestamp`（实测 2.0.5 / 2026-09-04T09:00:06）。
"""
from __future__ import annotations

import json
import time
from urllib.parse import quote

from .. import raw
from ..clock import today
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "ctgov_v2"
DATASET = "studies-countTotal-status"
API = "https://clinicaltrials.gov/api/v2/studies"
VERSION_URL = "https://clinicaltrials.gov/api/v2/version"
HEADERS = {"Accept": "application/json"}

CRITERIA = (
    "每病按 targets.py 声明的查询词能匿名取回 ≥1 项在招试验"
    "（RECRUITING / NOT_YET_RECRUITING / ENROLLING_BY_INVITATION）；只有历史登记不算达标"
)

# 在招 = 试验还开着门。ACTIVE_NOT_RECRUITING 单独统计：它"未招募"但仍在随访，
# 对"前沿研究"这一维不算活跃，但 B7 要放宽口径时得看得到这个数
ACTIVE = "RECRUITING,NOT_YET_RECRUITING,ENROLLING_BY_INVITATION"
IDLE = "ACTIVE_NOT_RECRUITING"
DONE = "COMPLETED"

SAMPLE = 50         # 字段完整性每病抽 50 条，只用于量填充率
SLEEP = 0.1
OVER_MATCH = 2.0    # 裸串/引号串 命中比超过这个倍数 = 这个词不是主题词、裸写会灌进别的癌种

NCT = "protocolSection.identificationModule.nctId"
STATUS = "protocolSection.statusModule.overallStatus"
COUNT_FIELDS = f"{NCT},{STATUS}"

# 落 trial 表要点名的列。全部实测过：这些路径在 `fields=` 白名单里回 200，
# 而 designAllocation / designModel / masking / stdAgeMinimum / resultFirstPosted /
# conditionsModule.meshTerm / derivedSection.* 一律 400（v2 把前几个收进 designInfo，
# 后几个根本不开放），所以只能按这份清单取数
FIELD_PATHS = {
    "nct_id": NCT,
    "title": "protocolSection.identificationModule.officialTitle",
    "brief_title": "protocolSection.identificationModule.briefTitle",
    "overall_status": STATUS,
    "study_type": "protocolSection.designModule.studyType",
    "phases": "protocolSection.designModule.phases",
    "design_info": "protocolSection.designModule.designInfo",
    "enrollment": "protocolSection.designModule.enrollmentInfo",
    "conditions": "protocolSection.conditionsModule.conditions",
    "interventions": "protocolSection.armsInterventionsModule.interventions",
    "arm_groups": "protocolSection.armsInterventionsModule.armGroups",
    "primary_outcome": "protocolSection.outcomesModule.primaryOutcomes",
    "secondary_outcome": "protocolSection.outcomesModule.secondaryOutcomes",
    "other_outcome": "protocolSection.outcomesModule.otherOutcomes",
    "eligibility_criteria": "protocolSection.eligibilityModule.eligibilityCriteria",
    "elig_sex": "protocolSection.eligibilityModule.sex",
    "healthy_volunteers": "protocolSection.eligibilityModule.healthyVolunteers",
    "lead_sponsor": "protocolSection.sponsorCollaboratorsModule.leadSponsor",
    "collaborators": "protocolSection.sponsorCollaboratorsModule.collaborators",
    "location_countries": "protocolSection.contactsLocationsModule.locations.country",
    "publications": "protocolSection.referencesModule.references",
    "fda_regulated": "protocolSection.oversightModule.isFdaRegulatedDrug",
    "why_stopped": "protocolSection.statusModule.whyStopped",
}
# 站点面向中文用户，"有没有中国参与的试验"是要展示的事实，单独数
CHINA = "China"


def _url(cond: str, fields: str, page: int, status: str | None = None) -> str:
    u = (f"{API}?format=json&pageSize={page}&countTotal=true"
         f"&query.cond={quote(cond)}&fields={quote(fields)}")
    return f"{u}&filter.overallStatus={status}" if status else u


def _get(url: str) -> tuple[dict, int, str, int]:
    """一次请求 → (JSON, 毫秒, reachability, http_status)。网络层失败直接中止：
    半趟结果写进 source_probe_log 会被下一个人当成上游结论读。"""
    r = fetch(url, headers=HEADERS, max_bytes=40_000_000, timeout=(10, 300))
    if r.status != 200 or not r.body:
        raise SystemExit(f"{url[:150]} → {r.status or r.reachability}：{r.note} {r.text[:160]}")
    try:
        return json.loads(r.text), r.latency_ms, r.reachability, r.status
    except json.JSONDecodeError:
        raise SystemExit(f"{url[:150]} → HTTP 200 但响应不是 JSON（{r.content_type}）")


def _count(cond: str, status: str | None = None) -> tuple[int, int, str]:
    """问一个命中数。`(总数, 毫秒, reachability)`——耗时与走没走代理必须一路带回去，
    某一步落代理而整行记 direct，scheduler 就会按"不需要代理"排这一源的周任务。"""
    doc, ms, reach, _ = _get(_url(cond, NCT, 1, status))
    total = doc.get("totalCount")
    if total is None:
        # totalCount 不出现说明 countTotal 这条路变了——宁可中止，也不要记成"这一病 0 项"
        raise SystemExit(f"响应没有 totalCount：{json.dumps(doc)[:200]}")
    return int(total), ms, reach


def _mixed(terms: tuple[str, ...]) -> str:
    """主查询：第一项裸写（拿 MeSH 概念展开），其余加引号（当字面短语）。
    实测两种写法能混在一条布尔串里：`Lung Neoplasms OR "lung cancer"` 回 14,501。"""
    return " OR ".join((terms[0],) + tuple('"%s"' % t for t in terms[1:]))


def _quoted(terms: tuple[str, ...]) -> str:
    return " OR ".join('"' + t + '"' for t in terms)


def dig(sec: dict, path: str):
    """按 `FIELD_PATHS` 那种点分路径从一条记录的 `protocolSection` 取值，任一段缺失回 None。

    提出来给装载器共用不是收敛代码美观：两处各写一份 walk，源改字段名时只会有一边
    悄悄取成 None，表现是"探针说这列有值，库里全是空"。

    路径要能穿过数组：`locations.country` 的 `locations` 是一组对象，只走字典的话这一路
    恒为 None——第一版就把它读成"50 条抽样里 0 条有研究地点"，而源其实给了。
    """
    cur: object = sec
    for part in path.split(".")[1:]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            cur = [x.get(part) for x in cur if isinstance(x, dict) and x.get(part) is not None]
        else:
            return None
        if cur is None:
            return None
    return cur


def _presence(v) -> bool:
    if v is None or v == "" or v == [] or v == {}:
        return False
    if isinstance(v, dict):
        return any(_presence(x) for x in v.values())
    if isinstance(v, list):
        return any(_presence(x) for x in v)
    return True


def _as_list(v) -> list:
    if v is None:
        return []
    out = []
    for x in (v if isinstance(v, list) else [v]):
        out.append(x.get("country") if isinstance(x, dict) else str(x))
    return [x for x in out if x]


def _sample(cond: str) -> tuple[dict, int, str]:
    """抽一页带全字段的记录，量每个字段有没有值（落 trial 表的可行性）与中国地点占比。"""
    doc, ms, reach, _ = _get(_url(cond, ",".join(FIELD_PATHS.values()), SAMPLE))
    rows = doc.get("studies") or []
    pres: dict[str, int] = {k: 0 for k in FIELD_PATHS}
    china = 0
    peek = []
    for s in rows:
        sec = s.get("protocolSection") or {}
        got: dict[str, object] = {}
        for key, path in FIELD_PATHS.items():
            cur = dig(sec, path)
            got[key] = cur
            if _presence(cur):
                pres[key] += 1
        countries = _as_list(got["location_countries"])
        if CHINA in countries:
            china += 1
        if len(peek) < 3:
            peek.append({"nct_id": got["nct_id"], "conditions": got["conditions"],
                         "overall_status": got["overall_status"], "phases": got["phases"],
                         "primary_outcome": _presence(got["primary_outcome"]),
                         "n_countries": len(set(countries))})
    return {"total": int(doc.get("totalCount") or 0), "records": len(rows),
            "present": pres, "china_sites": china, "peek": peek}, ms, reach


def probe(offline: bool = False) -> ProbeResult:
    ms = 0
    http: int | None = None
    reaches: list[str] = []
    sizes: dict[str, int] = {}
    if offline:
        key_dir = raw.newest_dir(SOURCE, "counts.json")
        if not key_dir:
            raise SystemExit(f"离线重放要先有归档：data/raw/{SOURCE}/*/counts.json 不存在")
        blobs = {n: json.loads((key_dir / n).read_text(encoding="utf-8"))
                 for n in ("version.json", "counts.json", "fields.json")}
        ver = blobs["version.json"]
        counts = blobs["counts.json"]
        fields = blobs["fields.json"]
        reaches = ["offline"]
        sizes = {n: (key_dir / n).stat().st_size for n in blobs}
    else:
        doc, m, reach, http = _get(VERSION_URL)
        ms += m
        reaches.append(reach)
        ver = doc
        counts = {}
        for t in TARGETS:
            terms = t.search_terms

            def ask(cond: str, status: str | None = None) -> int:
                nonlocal ms
                v, m_, reach_ = _count(cond, status)
                ms += m_
                reaches.append(reach_)
                time.sleep(SLEEP)
                return v

            mixed = _mixed(terms)
            per_term = []
            for term in terms:
                b, q = ask(term), ask('"%s"' % term)
                per_term.append({"term": term, "bare": b, "quoted": q,
                                 "ratio": round(b / q, 2) if q else None})
            counts[t.code] = {
                "terms": list(terms),
                "mesh_bare": ask(terms[0]),
                "all_quoted": ask(_quoted(terms)),
                "mixed": ask(mixed),
                "active": ask(mixed, ACTIVE),
                "idle": ask(mixed, IDLE),
                "completed": ask(mixed, DONE),
                "per_term": per_term,
            }
        fields = {}
        for t in TARGETS:
            res, m_, reach_ = _sample(_mixed(t.search_terms))
            ms += m_
            reaches.append(reach_)
            fields[t.code] = res
            time.sleep(SLEEP)
        # 归档目录名不能用 dataTimestamp 原串——它带冒号，Windows 上建不出目录
        key_dir = raw.archive_dir(SOURCE, str(ver.get("dataTimestamp") or today())[:10])
        blobs = {"version.json": ver, "counts.json": counts, "fields.json": fields}
        for n, b in blobs.items():
            body = json.dumps(b, ensure_ascii=False).encode("utf-8")
            (key_dir / n).write_bytes(body)
            sizes[n] = len(body)

    reach = "offline" if "offline" in reaches else ("proxy" if "proxy" in reaches else "direct")

    per = []
    covered = 0
    over = []
    for t in TARGETS:
        c = counts[t.code]
        ok = c["active"] > 0
        covered += 1 if ok else 0
        for p in c["per_term"]:
            if p["ratio"] and p["ratio"] >= OVER_MATCH:
                over.append({"code": t.code, **p})
        # 展开增益只比同口径的两个数：第一项裸写 对 第一项加引号。
        # 拿它去减 all_quoted（全部词加引号的并集）会把"同义词有贡献"误读成"主题词没映射上"
        expansion = c["mesh_bare"] - c["per_term"][0]["quoted"]
        per.append({
            "code": t.code, "terms": len(c["terms"]), "mesh_bare": c["mesh_bare"],
            "all_quoted": c["all_quoted"], "mixed": c["mixed"], "active": c["active"],
            "idle": c["idle"], "completed": c["completed"],
            "expansion_gain": expansion, "synonym_gain": c["mixed"] - c["mesh_bare"],
            "pass": ok,
        })

    n_rec = sum(f["records"] for f in fields.values())
    pres: dict[str, int] = {}
    for f in fields.values():
        for k, v in f["present"].items():
            pres[k] = pres.get(k, 0) + v
    fill = {k: round(pres.get(k, 0) / n_rec * 100, 1) if n_rec else 0.0 for k in FIELD_PATHS}
    china = sum(f["china_sites"] for f in fields.values())
    # 只进 message，不进裁定：why_stopped 只有终止的试验才有、collaborators 本来就稀疏，
    # 把它们算成"未达判据"会在覆盖度矩阵上造出一个假缺口
    low = sorted(k for k, v in fill.items() if v < 50)
    no_active = [p["code"] for p in per if not p["pass"]]
    # 声明的第一个词必须是 MeSH 主题词：裸写不比加引号多说明没映射上概念
    no_expansion = [p["code"] for p in per if p["expansion_gain"] <= 0]
    gain = sum(p["expansion_gain"] for p in per)
    syn_total = sum(p["synonym_gain"] for p in per)
    n_syn = sum(1 for p in per if p["synonym_gain"] > 0)

    if covered == len(TARGETS):
        verdict = "ok"
    elif covered:
        verdict = "partial"
    else:
        verdict = "empty"

    msg = (
        f"达标 {covered}/{len(TARGETS)}（判据口径＝在招试验 ≥1）。"
        f"按主查询（主题词裸写 OR 同义词加引号）命中 {sum(p['mixed'] for p in per):,} 项，"
        f"其中在招 {sum(p['active'] for p in per):,}、未招募但随访 "
        f"{sum(p['idle'] for p in per):,}、已完成 {sum(p['completed'] for p in per):,}。"
        f"同一批词整串加引号是 {sum(p['all_quoted'] for p in per):,} 项。按同口径逐病比："
        f"主题词裸写带来的 MeSH 展开多出 {gain:,} 项，声明的同义词又在主题词之上并进来"
        f" {syn_total:,} 项（{n_syn} 病有增量）——这一维要报对试验数，既不能整串加引号，"
        "也不能只查主题词那一项。"
        f"词宽窄按逐词裸/引对比量：{len(over)} 个词裸写命中超过引号写的 {OVER_MATCH} 倍"
        + ("（" + "；".join(f"{o['code']} {o['term']!r} 裸 {o['bare']:,} 引 {o['quoted']:,}"
                            for o in over[:4]) + "）" if over else "")
        + "——这些一律是自由文本被当成词级匹配，混进主查询会把别的癌种灌进来，"
          "所以只有声明的第一项允许裸写。"
        "落 trial 表要用的字段在抽样里过半有值的有 "
        f"{len(FIELD_PATHS) - len(low)}/{len(FIELD_PATHS)}；不足五成的是：{', '.join(low) or '无'}"
        "——这几列稀疏是试验本身的性质，只决定落库时允许为空，不构成这一维未达标。"
        f"抽样 {n_rec} 条里 {china} 条有中国大陆研究地点（占 {china / n_rec * 100:.1f}%），"
        "地点国家是取 `locations.country` 数出来的——`filter.geo` 的三种写法与 "
        "`aggFilters=geo:CHN` 都被拒，这一路没有匿名地理过滤器。"
        "入口三条实测：计数用 `countTotal=true`（响应顶层多一个 `totalCount`，实测与逐页"
        "数出来的行数完全相等，本探针第一版不知道这个参数、翻页数了约 160 个请求 467 s；"
        "`countOnly`/`meta`/`totalHits` 这些猜的名字一律 400，`pageSize=0` 回 200 但不给计数）；"
        "在招口径用 `filter.overallStatus` 与 `query.cond` 同时生效；版本戳只在 "
        f"`/api/v2/version`（实测 {ver.get('apiVersion')} / {ver.get('dataTimestamp')}），"
        "`/api/v2/studies` 的 etag 是站点静态资源戳不是数据版本。"
    )
    if no_active:
        msg += f" 没有一项在招试验的病：{', '.join(no_active)}。"
    if no_expansion:
        msg += (f" 裸写不比加引号多的病：{', '.join(no_expansion)}。"
                "两项都是 MeSH 主题词，相等（Leukemia）说明它在这条路上没有更窄的下位概念可展开，"
                "只能靠声明的同义词补；裸写反而更少（Multiple Myeloma，少 13 条）说明它被映射到了"
                "比字面串更窄的概念上——两种都不是查询词写错，但都意味着这一病的总数对词表很敏感。")

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=sum(p["mixed"] for p in per),
        diseases_covered=covered,
        diseases_total=len(TARGETS),
        fields_seen=list(FIELD_PATHS),
        sample=per + [{"field_fill_pct": fill},
                      {"over_matching_terms": over},
                      {"first_page_peek": fields.get("lung", {}).get("peek", [])}],
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=str(ver.get("dataTimestamp") or ""),
        release_date=str(ver.get("dataTimestamp") or "")[:10] or None,
        release_bytes=sum(sizes.values()),
        release_sha256=raw.sha256_bytes(
            b"".join((key_dir / n).read_bytes() for n in sorted(sizes))
        ),
    )
