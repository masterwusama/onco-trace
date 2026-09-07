"""Europe PMC 文献量与全文可得率探针：判"这一病近五年有没有读得到的东西"。

判据两半：命中 >0 只说明检索到了，还得说明**匿名能读到正文**，否则 B6 的 L3 抽取没有输入。
所以这一支报三个数——近 5 年命中、其中 OPEN_ACCESS 命中、其中 IN_EPMC 命中，
再加上一次 core 抽样量摘要填充率。四个数分别是四种"能读到"的强度，混成一个就说不清了。

三处口径必须记下来，都是实测踩过来的：

  ① 编码会改变结果。同一串文本，自己 percent-encode 与按表单方式 `+` 编码给出的
    hitCount 不同（实测 20,275 对 3,474）。所以查询串一律交给 `urlencode` 生成整条 URL，
    不许手工拼 `%20`。
  ② `PUB_YEAR:2021-2025` 这种连字符写法被静默忽略——回的是不加年份过滤的 260,001，
    一个错字都不给。近 5 年只能写 `lucene=true` + `PUB_YEAR:[2021 TO *]`（lung 实测 95,927）。
    这一条最阴：不报错、结果看着也正常，只有拿全库数对比才发现过滤根本没生效。
  ③ MeSH 主题词不能当键。`MH:"Lung Neoplasms"` 只有 3,474 篇，同一词的自由文本命中
    26 万（差两个量级）；而括号式 `[MeSH Terms]` 对不同概念给出几乎相同的数
    （13,923 对 13,956），说明它根本没落到主题词字段上。所以疾病键只能靠声明的查询词，
    MeSH 那一列在这支探针里只用来做对照，不用来命中。

全文率有两个数，都要报：按 facet 算是 50.9%（`OPEN_ACCESS:Y` 命中 / 近 5 年命中，18 病合并），
按相关度排序抽 900 条读记录级 flag 只有 31.6%。差额是抽样构成造成的——命中集里 93.9% 是
MED 源（845/900），而单病实测 MED 内部 OA 只有 24.0%、PMC 源内部是 81.8%。facet 说的是
"库里有多少"，记录级说的是"你随手翻到的是多少"，前端展示用前者、判断 L3 能不能拿到正文用后者。
"""
from __future__ import annotations

import json
import time
from collections import Counter
from urllib.parse import urlencode

from .. import raw
from ..clock import today
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "europepmc"
DATASET = "search-count-availability"
REST = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
HEADERS = {"Accept": "application/json"}
RECENT = 5          # "近 5 年"，与计划里那一行的口径一致
SAMPLE = 50         # 每条查询抽 50 条读记录级 flag：够算率，又不至于把额度刷穿
SLEEP = 0.12

CRITERIA = (
    f"每病近 {RECENT} 年 TITLE_ABS 命中 >0 且其中 OPEN_ACCESS 命中 >0"
    "（只检得到、读不到正文不算达标）"
)

# 记录级可用性 flag：这些是 EPMC 逐条给的，不是查询条件，所以用来核对 facet 的数
FLAGS = ("isOpenAccess", "inEPMC", "inPMC", "hasPDF", "hasBook", "hasSuppl",
         "hasTextMinedTerms", "hasDbCrossReferences", "hasTMAccessionNumbers", "hasReferences")
# 落 literature 表要用的键与链接：逐条数非空，不只看"响应里有这个字段"
CORE_FIELDS = ("title", "pmid", "doi", "abstractText")


def _get(params: dict) -> tuple[dict, int, str, int]:
    url = f"{REST}?{urlencode(params)}"
    r = fetch(url, headers=HEADERS, max_bytes=20_000_000, timeout=(10, 180))
    if r.status != 200 or not r.body:
        raise SystemExit(f"{url[:150]} → {r.status or r.reachability}：{r.note} {r.text[:160]}")
    try:
        d = json.loads(r.text)
    except json.JSONDecodeError:
        raise SystemExit(f"{url[:150]} → HTTP 200 但不是 JSON（{r.content_type}）")
    # EPMC 出错时 HTTP 仍是 200，错误写在 body 的 errCode 里。
    # 不拦下来，一次写错的查询会被当成"这个病没有文献"记进覆盖度矩阵——假死讯
    if "errCode" in d or "hitCount" not in d:
        raise SystemExit(f"{url[:150]} → HTTP 200 但查询被拒：{json.dumps(d)[:220]}")
    return d, r.latency_ms, r.reachability, r.status


def _ta_expr(terms: tuple[str, ...]) -> str:
    return "(" + " OR ".join('TITLE_ABS:"%s"' % t for t in terms) + ")"


def _count(params: dict, add: str | None = None) -> tuple[int, int, str]:
    """问一次命中数，返回 (hitCount, 毫秒, reachability)。`add` 传了就用 AND 追加条件。

    追加的是 `*:*` 这类"看着像空条件"的东西不行：lucene 下它未必是恒真，
    与其赌语义，不如直接不发那个 AND。
    耗时与 reachability 必须一路带回去：某一步落代理而整行记 direct，
    scheduler 就会按"不需要代理"去排这一源的周任务。
    """
    p = dict(params)
    if add:
        p["query"] = p["query"] + " AND " + add
    p["pageSize"] = 1
    d, ms, reach, _ = _get(p)
    return int(d["hitCount"]), ms, reach


def _rate(num: int, den: int) -> float:
    return round(num / den * 100, 1) if den else 0.0


def probe(offline: bool = False) -> ProbeResult:
    ms = 0
    http: int | None = None
    reaches: list[str] = []
    sizes: dict[str, int] = {}
    year_from = int(today()[:4]) - RECENT

    if offline:
        key_dir = raw.newest_dir(SOURCE, "per_disease.json")
        if not key_dir:
            raise SystemExit(f"离线重放要先有归档：data/raw/{SOURCE}/*/per_disease.json 不存在")
        meta = json.loads((key_dir / "meta.json").read_text(encoding="utf-8"))
        per = json.loads((key_dir / "per_disease.json").read_text(encoding="utf-8"))
        year_from = int(meta["year_from"])
        reaches = ["offline"]
        sizes = {n: (key_dir / n).stat().st_size for n in ("meta.json", "per_disease.json")}
    else:
        base = {
            "query": f'{_ta_expr(TARGETS[0].search_terms)} AND (PUB_YEAR:[{year_from} TO *])',
            "format": "json", "lucene": "true", "pageSize": 1, "resultType": "lite",
        }
        doc, m, reach, http = _get(base)
        ms += m
        reaches.append(reach)
        meta = {"api_version": doc.get("version"), "year_from": year_from,
                "year_to": "至今", "probed_on": today()}

        def ask(params: dict, add: str | None = None) -> int:
            """_count 的记账版：耗时与 reachability 每次都要并回主账。"""
            nonlocal ms
            v, m, reach_ = _count(params, add)
            ms += m
            reaches.append(reach_)
            return v

        per = {}
        for t in TARGETS:
            q = {"query": f"{_ta_expr(t.search_terms)} AND (PUB_YEAR:[{year_from} TO *])",
                 "format": "json", "lucene": "true", "pageSize": 1, "resultType": "lite"}
            d5, m, reach, _ = _get(q)
            ms += m
            reaches.append(reach)
            five = int(d5["hitCount"])
            oa = ask(q, "OPEN_ACCESS:Y")
            epmc = ask(q, "IN_EPMC:Y")
            allm = ask({**q, "query": _ta_expr(t.search_terms)})
            mesh = ask({**q, "query": 'MH:"%s"' % t.search_terms[0]})
            core = {**q, "pageSize": SAMPLE, "resultType": "core"}
            cd, m, reach, _ = _get(core)
            ms += m
            reaches.append(reach)
            rows = (cd.get("resultList") or {}).get("result") or []
            flags = Counter()
            srcs = Counter()
            core_fill = Counter()
            for r in rows:
                for f in FLAGS:
                    if str(r.get(f)).upper() == "Y":
                        flags[f] += 1
                for c in CORE_FIELDS:
                    if str(r.get(c) or "").strip():
                        core_fill[c] += 1
                srcs[r.get("source") or "?"] += 1
            per[t.code] = {
                "term_count": len(t.search_terms),
                "five_y": five, "oa_five_y": oa, "in_epmc_five_y": epmc,
                "all_years": allm, "mesh_heading": t.search_terms[0], "mesh_all": mesh,
                "sample": len(rows), "core_fill": dict(core_fill),
                "flags": dict(flags), "sources": dict(srcs),
                "sample_pub_years": sorted({str(r.get("pubYear")) for r in rows})[-3:],
            }
            time.sleep(SLEEP)
        key_dir = raw.archive_dir(SOURCE, f"epmc-{meta['api_version']}-{year_from}")
        blobs = {"meta.json": meta, "per_disease.json": per}
        for n, b in blobs.items():
            body = json.dumps(b, ensure_ascii=False).encode("utf-8")
            (key_dir / n).write_bytes(body)
            sizes[n] = len(body)

    reach = "offline" if "offline" in reaches else ("proxy" if "proxy" in reaches else "direct")
    total5 = sum(p["five_y"] for p in per.values())
    total_oa = sum(p["oa_five_y"] for p in per.values())
    total_epmc = sum(p["in_epmc_five_y"] for p in per.values())
    total_all = sum(p["all_years"] for p in per.values())
    total_mesh = sum(p["mesh_all"] for p in per.values())
    sampled = sum(p["sample"] for p in per.values())
    fill = Counter()
    for p in per.values():
        fill.update(p["core_fill"])
    abst = fill.get("abstractText", 0)
    oa_rec = sum(p["flags"].get("isOpenAccess", 0) for p in per.values())
    med = sum(p["sources"].get("MED", 0) for p in per.values())
    pp = sum(p["sources"].get("PPR", 0) for p in per.values())
    pmc = sum(p["sources"].get("PMC", 0) for p in per.values())

    detail = []
    covered = 0
    for t in TARGETS:
        p = per[t.code]
        ok = p["five_y"] > 0 and p["oa_five_y"] > 0
        covered += 1 if ok else 0
        detail.append({
            "code": t.code, "five_y": p["five_y"], "oa": p["oa_five_y"],
            "oa_pct": _rate(p["oa_five_y"], p["five_y"]),
            "in_epmc": p["in_epmc_five_y"], "all_years": p["all_years"],
            "mesh_all": p["mesh_all"], "mesh_pct": _rate(p["mesh_all"], p["all_years"]),
            "abstract_pct": _rate(p["core_fill"].get("abstractText", 0), p["sample"]),
            "pmid_pct": _rate(p["core_fill"].get("pmid", 0), p["sample"]),
            "doi_pct": _rate(p["core_fill"].get("doi", 0), p["sample"]),
            "record_oa_pct": _rate(p["flags"].get("isOpenAccess", 0), p["sample"]),
            "pass": ok,
        })
    zero5 = [x["code"] for x in detail if not x["five_y"]]
    no_oa = [x["code"] for x in detail if x["five_y"] and not x["oa"]]
    mesh_zero = [x["code"] for x in detail if not x["mesh_all"]]

    if covered == len(TARGETS) and not mesh_zero:
        verdict = "ok"
    elif covered:
        verdict = "partial"
    else:
        verdict = "empty"

    msg = (
        f"达标 {covered}/{len(TARGETS)}（近 {RECENT} 年命中 >0 且有 OA 命中）。"
        f"声明词表并起来近 {year_from} 年起命中 {total5:,} 篇，全库 {total_all:,} 篇，"
        f"其中 {total_oa:,} 篇标 OPEN_ACCESS（{_rate(total_oa, total5)}%）、"
        f"{total_epmc:,} 篇在 EPMC 库内可取全文（{_rate(total_epmc, total5)}%）。"
        f"同一批查询按 facet 之外的记录级 flag 抽样 {sampled} 条只有 {oa_rec} 条 isOpenAccess"
        f"（{_rate(oa_rec, sampled)}%）——差额是抽样构成造成的：抽到的源里 MED {med} 条、"
        f"PPR 预印本 {pp} 条、PMC {pmc} 条，而 MED 内部 OA 率本来就低。"
        "两个数各说各的话：facet 是库里有多少，记录级是你随手翻到的是多少，"
        "B6 判断 L3 能不能拿到正文要看后者。"
        f"core 抽样 {sampled} 条的落库字段填充率：title {fill.get('title', 0)}（{_rate(fill.get('title', 0), sampled)}%）、"
        f"pmid {fill.get('pmid', 0)}（{_rate(fill.get('pmid', 0), sampled)}%）、"
        f"doi {fill.get('doi', 0)}（{_rate(fill.get('doi', 0), sampled)}%）、"
        f"abstractText {abst}（{_rate(abst, sampled)}%）——DOI 不满是常态，"
        "落库要按 pmid 建键、DOI 只当可选链接。摘要同时是 L3 症状抽取的第二输入，"
        "这个率决定那条退路有多宽。"
        f"对照口径：同一批病按 MeSH 主题词字段（MH:）只有 {total_mesh:,} 篇"
        f"（占全库 {_rate(total_mesh, total_all)}%），所以 MeSH 在这里只能当校验，不能当疾病键。"
        f"查询串一律用 urlencode 生成（自己 percent-encode 会量出不同的命中数），"
        f"年份过滤必须配 lucene=true（PUB_YEAR:2021-2025 那种连字符写法被静默忽略）。"
        f"EPMC 只在响应里给搜索版本号，不给数据发布日：version={meta.get('api_version')}，"
        "所以 release_date 留空。"
    )
    if zero5:
        msg += f" 近 {RECENT} 年零命中的病：{', '.join(zero5)}——查询词表要改，不是源没数据。"
    if no_oa:
        msg += f" 检得到但一篇 OA 都没有的病：{', '.join(no_oa)}。"
    if mesh_zero:
        msg += (f" 声明的第一项在 MH: 字段零命中的病：{', '.join(mesh_zero)}——"
                "第一项按约定应是 MeSH 主题词，写错了。")

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=total5,
        diseases_covered=covered,
        diseases_total=len(TARGETS),
        fields_seen=["hitCount", "version", *FLAGS, *CORE_FIELDS, "source", "pubYear"],
        sample=detail,
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=f"v{meta.get('api_version')}-{year_from}+",
        release_date=None,
        release_bytes=sum(sizes.values()),
        release_sha256=raw.sha256_bytes(
            b"".join((key_dir / n).read_bytes() for n in sorted(sizes))
        ),
    )
