"""研究层装载器：试验 / 文献 / 靶点与药，一台写 `trial`、`publication`、`target`、
`disease_target`、`drug`，外加 `stat_fact` 里那批 `query_count` 行。

四源合一台的理由与 risks.py 同一条：这几张表讲的是同一件事（"这一病现在在研究什么"），
拆成四台会让人以为四张表的行数能加成一个"研究总量"——而它们的行数口径根本不同：
`trial` 一行是一个 NCT 命中一个病，`publication` 一行是每病相关度前 500 里的一条。
`stat_fact` 那四行 query_count 必须与这四张表同一趟写——它们是同一批查询给出来的数，
分两趟就会有一边先漂。

**这一台与前面五台唯一的差别是探针没取过行。** B5 那三支判的是"这一维能不能拿到"，
归档里只有计数与填充率，没有可重放的行。所以行级取数由装载器自己做，并按 releases.py
那句话办——真的取回载荷就补登记一版：

  · 逐病取回行 → 原始载荷 gzip 进 `data/raw/<源>/rows-<版本>/`，下一次 `--offline` 从这里重放；
  · 以**新的 dataset_code**（`studies-active-rows` / `search-core-rows` / `assoc-rows` /
    `drug-rows`）登记 `dataset_release`，与探针那版 counts 并存——同一源同一版本本就有
    两份数据集，`stat_fact.dataset_code` 的注释就是为这个留的；
  · 查询串一律复用探针那批函数（`ct._mixed` / `ct._url` / `pmc._ta_expr` / `ot._gql` /
    `ot._efo` / `ct.dig`），装载器不重写一遍查询构造，否则"这一病有多少试验"就会有两个口径。

五条口径：

1. **trial 只落在招**（`filter.overallStatus` 那三档）。这一维给前端看的是前沿研究，
   2003 年做完的试验不是前沿；代价是 `why_stopped` 整列空（只有终止的试验才有那句），
   以及"这一病历史上做过多少试验"这个数只能另问一次全状态查询。
2. **publication 是上限样本不是全量**：每病按 EPMC 默认相关度序取前 500 条
   （9,000 行 / 18 个请求）。全量取满要七百多页、约 5 GB（pageSize 硬上限 1000），
   而库内真实命中数逐轮漂，所以它只进 `stat_fact` 的 `publication_count`、不写在这里；
   页面文案必须写"库内共 N 篇，这里取相关度前 500"。
3. **disease_target 按分数阈值取并整页早停**（`score ≥ 0.1`）。OT 的 `associatedTargets`
   按分数降序给，所以末页最小值已低于阈值就不必再翻——lung 实测前 3000 行里只有 2,185 行
   ≥0.1，一个请求就够。阈值而不是"每病前 N 个"是判据意义上的选择：这一维要回答
   "有多少可讨论的靶点"，N 是个任意数。
4. **drug 的业务键是 (病, 药, 阶段)**，源给的一行是 (药, 阶段, 来源关联) 的三元组哈希，
   同一药同一阶段会有好几行，所以按 `uk_drug` 收拢并把机制并成数组。`phase` 存
   `maxClinicalStage` 原文（`PHASE_1_2` 这种），不排成有序档——源自己没排过序。
5. **目测的是值域，不是条数。** 活源每周都变，把条数写成 EYEBALL 等于给自己埋一个
   每周必炸的中止。所以 `status_bucket` 与 `phase` 各带一份取值域声明（`STATUS_BUCKET` /
   `STAGES`），冒出没读过的值就中止让人重读；条数只报不判。`spot_checked` 因此只给
   这两张按值域判的表，`publication`/`disease_target` 是抄录与阈值，如实记 `unreviewed`。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .. import db, raw
from ..clock import today
from ..probes import ctgov_v2 as ct
from ..probes import europepmc as pmc
from ..probes import opentargets as ot
from ..targets import TARGETS, ot_node
from .base import Ctx, LoadResult, prov, replace_scope, upsert

CT, PMC, OT = ct.SOURCE, pmc.SOURCE, ot.SOURCE
# 探针那三份是计数与填充率，这四份是行；同名源下两套 dataset_code 各登记各的发布
CT_ROWS = "studies-active-rows"
PMC_ROWS = "search-core-rows"
OT_ASSOC_ROWS = "assoc-rows"
OT_DRUG_ROWS = "drug-rows"
OT_PROBE_DATASET = ot.DATASET

# 四份数据集一份一个归档目录：(源, dataset_code, 归档文件名前缀, 载荷里记录数组的键)。
# OT 的关联与药分两个目录而不是一份——一份目录两行 release 会互相声称覆盖对方的字节
DATASETS = (
    (CT, CT_ROWS, "trial", "studies"),
    (PMC, PMC_ROWS, "pub", "records"),
    (OT, OT_ASSOC_ROWS, "assoc", "rows"),
    (OT, OT_DRUG_ROWS, "drug", "rows"),
)

TRIAL_PAGE = 1000       # 实测硬上限；再大静默截到 1000
TRIAL_MAX_PAGES = 25    # 单病 25 千行——超出就是查询词写宽了，宁停不猜
PUB_CAP = 500           # 见口径 2
ASSOC_PAGE = 3000       # 实测 GraphQL 允许的 size 上限
ASSOC_MAX_PAGES = 8     # 单病 24 千行封顶；取不满阈值就停，见 stopped 字段
ASSOC_MIN_SCORE = 0.1   # 口径 3
SLEEP = 0.15

# CT 的 overallStatus → status_bucket。官方列表的全部取值逐条读过：
# 前三档是"还开着门"（= 探针的 ACTIVE 过滤器），ACTIVE_NOT_RECRUITING 在随访但不收人，
# COMPLETED 已收尾，SUSPENDED/TERMINATED/WITHDRAWN 是没做完的，UNKNOWN 与 OFFLINE 是源
# 自己标不知道。出现这份声明之外的值就中止——那说明源添了新状态，
# 而这一列是页面上"在研/已结/中止"的分栏依据，猜一个档会把计数带偏
STATUS_BUCKET = {
    "RECRUITING": "active", "NOT_YET_RECRUITING": "active",
    "ENROLLING_BY_INVITATION": "active",
    "ACTIVE_NOT_RECRUITING": "idle",
    "COMPLETED": "completed",
    "TERMINATED": "other", "WITHDRAWN": "other", "SUSPENDED": "other",
    "UNKNOWN": "other", "OFFLINE": "other",
}
# OT 的 maxClinicalStage 取值域。本轮 18 病 6,323 行归档（data/raw/opentargets/rows-26.6.3-drug）
# 逐行数出来的 11 个值，括号里是行数 / 出现的病数：PHASE_2 2,668/18、PHASE_1 1,266/18、
# PHASE_3 921/17、PHASE_1_2 827/17、APPROVAL 270/15、UNKNOWN 170/15、PHASE_2_3 110/14、
# EARLY_PHASE_1 71/15、IND 12/5、PRECLINICAL 7/4、PREAPPROVAL 1/1。
# 注意是 APPROVAL 不是 APPROVED——探针第一版只抽过 lung 一病，那份样本里没有"已上市"这一档。
# phase 原样存这些字面量，不排成有序档：源自己就没排过序。
# 冒出声明之外的值就中止——前端那一列是按这几个字符串分组排序的，得先读一遍源再加分档。
STAGES = {"PRECLINICAL", "EARLY_PHASE_1", "PHASE_1", "PHASE_1_2", "PHASE_2", "PHASE_2_3",
          "PHASE_3", "APPROVAL", "PREAPPROVAL", "IND", "UNKNOWN"}


@dataclass
class Payload:
    """一次取数的原始载荷。两份映射都按 dataset_code 索引：`recs` 是病 → 源给的记录，
    `meta` 是这一份数据集的归档元数据。离线重放填同一个形状。"""

    recs: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def one(self, ds: str, code: str) -> dict:
        return (self.recs.get(ds) or {}).get(code) or {}


def _hash16(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:32]


class Cutter:
    """按列宽截断并记账。

    MySQL 严格模式下超长直接报错，一条 400 行的批量插入里哪一行超长它不说，
    而源给的标题与机构名长度根本不可控——所以装载器自己截，并把"截了几处"报出来。
    """

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}

    def cut(self, col: str, v, width: int) -> str:
        s = "" if v is None else str(v).strip()
        if len(s) > width:
            self.hits[col] = self.hits.get(col, 0) + 1
            s = s[:width]
        return s

    def brief(self) -> str:
        return "、".join(f"{k} {v}" for k, v in sorted(self.hits.items())) or "无"


# ---------------------------------------------------------------- 归档与重放
def _dump(dir_: Path, name: str, obj) -> int:
    """一行 JSON 落一份 gzip。整页原样存而不重排字段：归一化规则改了要能拿同一份字节重放。"""
    body = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    (dir_ / name).write_bytes(body)
    return len(body)


def _dir_sha(dir_: Path) -> str:
    """整目录（含 meta.json）按文件名排序串起来哈希。

    哈希的是"这一版取回的字节"而不是解析结果，所以归一化规则改一行、重放同一份归档，
    `dataset_release.sha256` 不该变——它就充当那份对账凭据。
    """
    h = hashlib.sha256()
    for f in sorted(p for p in dir_.iterdir() if p.is_file()):
        h.update(f.name.encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()


def _archive(p: Payload, src: str, ds: str, prefix: str, got: dict, *,
             version: str, key: str, release_date: str | None, stats: dict) -> None:
    d = raw.archive_dir(src, key)
    sizes = {name: _dump(d, name, rec) for name, rec in
             ((f"{prefix}-{code}.json.gz", rec) for code, rec in got.items())}
    man = {"dataset_code": ds, "source": src, "version": version,
           "release_date": release_date, "stats": stats, "archived_on": today()}
    body = json.dumps(man, ensure_ascii=False).encode("utf-8")
    (d / "meta.json").write_bytes(body)
    sizes["meta.json"] = len(body)
    p.recs[ds] = got
    p.meta[ds] = {"version": version, "release_date": release_date, "stats": stats,
                  "dir": d, "sizes": sizes}


def _replay(p: Payload, src: str, ds: str, prefix: str) -> None:
    """从最近一份归档把这一份数据集读回 Payload，形状与在线取回那一份完全一致。"""
    d = raw.newest_dir(src, f"{prefix}-*.json.gz")
    if not d:
        raise SystemExit(f"离线重放要先有归档：data/raw/{src}/*/{prefix}-*.json.gz 不存在")
    man = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    if man.get("dataset_code") != ds:
        # 同一源下四份数据集的归档形状一样，挑错目录不会报错、只会安静少装一维
        raise SystemExit(f"{raw.rel(d)} 的 meta.json 写的是 {man.get('dataset_code')}，"
                         f"不是 {ds}——归档目录与数据集对不上")
    store = p.recs[ds] = {}
    for t in TARGETS:
        f = d / f"{prefix}-{t.code}.json.gz"
        if f.exists():
            store[t.code] = json.loads(gzip.decompress(f.read_bytes()))
    p.meta[ds] = {"version": man["version"], "release_date": man.get("release_date"),
                  "stats": man.get("stats", {}), "dir": d,
                  "sizes": {f.name: f.stat().st_size for f in sorted(d.iterdir())
                            if f.is_file()}}


def _reach(reaches: list[str]) -> str:
    return "offline" if "offline" in reaches else ("proxy" if "proxy" in reaches else "direct")


# ---------------------------------------------------------------- 取数
def fetch_trials(p: Payload, offline: bool) -> None:
    """逐病按 pageToken 链取完在招试验。`pageNumber` 实测回 400，只能顺 token 走。"""
    if offline:
        _replay(p, CT, CT_ROWS, "trial")
        return

    doc, ms, reach, _ = ct._get(ct.VERSION_URL)
    stamp = str(doc.get("dataTimestamp") or today())
    reaches = [reach]
    got, req, pages, over = {}, 1, 0, []
    fields = ",".join(ct.FIELD_PATHS.values())
    for t in TARGETS:
        cond = ct._mixed(t.search_terms)
        studies, hit, token, dpages = [], None, None, 0
        while True:
            u = ct._url(cond, fields, TRIAL_PAGE, ct.ACTIVE)
            if token:
                u += f"&pageToken={quote(str(token))}"
            d, m, reach, _ = ct._get(u)
            ms += m
            reaches.append(reach)
            req += 1
            if hit is None:
                hit = int(d.get("totalCount") or 0)
            batch = d.get("studies") or []
            studies += batch
            # 上限是"单病不许超过 25 页"，所以判的是这一病自己翻了第几页——
            # 拿跨病的总页数判，第二病起就会误报超页（brain 取回 1000/1265 那次）
            pages += 1
            dpages += 1
            token = d.get("nextPageToken")
            if not batch or len(studies) >= hit or not token:
                break
            if dpages >= TRIAL_MAX_PAGES:
                over.append(f"{t.code} {len(studies)}/{hit}")
                break
            time.sleep(SLEEP)
        if hit is not None and len(studies) != hit:
            # 翻页翻不齐，多半是 token 语义变了或源在翻页期间改了数据。
            # 不中止的话，库里就会攒下一份"看着完整"的缺页样本
            raise SystemExit(f"CT {t.code} 在招取回 {len(studies)} 行 ≠ totalCount {hit}"
                             + (f"（超页上限的病：{', '.join(over)}）" if over else ""))
        got[t.code] = {"hit": hit or 0, "studies": studies}
    _archive(p, CT, CT_ROWS, "trial", got, version=stamp, key=f"rows-{stamp[:10]}"[:40],
             release_date=stamp[:10] or None,
             stats={"requests": req, "pages": pages, "ms": ms, "over_cap": over,
                    "reach": _reach(reaches)})


def fetch_pubs(p: Payload, offline: bool) -> None:
    if offline:
        _replay(p, PMC, PMC_ROWS, "pub")
        return

    year_from = int(today()[:4]) - pmc.RECENT
    got, ms, reaches = {}, 0, []
    for t in TARGETS:
        params = {"query": f'{pmc._ta_expr(t.search_terms)} AND (PUB_YEAR:[{year_from} TO *])',
                  "format": "json", "lucene": "true", "pageSize": PUB_CAP,
                  "resultType": "core"}
        d, m, reach, _ = pmc._get(params)
        ms += m
        reaches.append(reach)
        rows = (d.get("resultList") or {}).get("result") or []
        hit = int(d.get("hitCount") or 0)
        if hit and not rows:
            raise SystemExit(f"EPMC {t.code} 命中 {hit} 篇却一行没回：分页参数或 resultType 变了")
        got[t.code] = {"hit": hit, "version": d.get("version"), "records": rows}
        time.sleep(pmc.SLEEP)
    version = f"v{got[TARGETS[0].code]['version']}-{year_from}+"
    _archive(p, PMC, PMC_ROWS, "pub", got, version=version, key=f"rows-{today()}",
             # EPMC 只给搜索版本号，不给数据发布日（B5 实测），所以这一版留空
             release_date=None,
             stats={"requests": len(TARGETS), "ms": ms, "year_from": year_from,
                    "reach": _reach(reaches)})


def fetch_assoc(p: Payload, offline: bool) -> None:
    if offline:
        _replay(p, OT, OT_ASSOC_ROWS, "assoc")
        return

    ms, reaches = 0, []
    got, req, apages, over, stopped = {}, 0, 0, [], {}
    sel = ("{score novelty datasourceScores{id score}"
           " target{id approvedSymbol approvedName}}")
    for t in TARGETS:
        node = ot._efo(ot_node(t))
        rows, count, why = [], 0, ""
        for page in range(ASSOC_MAX_PAGES):
            # `page.index` 是 0 起的**页号**不是行偏移：实测 breast_female 第 0 页
            # 0.9161→0.1041、第 1 页 0.1041→0.0476 且与前页靶点零重叠，
            # 而 index=3000 是一个不存在的页、回零行。写成 page * size 只会翻到空页
            d, m, reach = ot._gql(
                '{d:disease(efoId:"%s"){associatedTargets(page:{index:%d,size:%d}){count rows%s}}}'
                % (node, page, ASSOC_PAGE, sel), mb=60_000_000)
            ms += m
            reaches.append(reach)
            req += 1
            apages += 1
            blk = (d.get("d") or {}).get("associatedTargets") or {}
            count = int(blk.get("count") or 0)
            page_rows = blk.get("rows") or []
            if not count:
                why = "源给 count=0"
                break
            if not page_rows:
                # count>0 而整页空行是"源说有线上却没给"，翻下去也不会有——不中止就会静默少一维
                raise SystemExit(f"OT {t.code} associatedTargets count={count}，"
                                 f"第 {page + 1} 页却零行：分页参数或字段形状变了")
            rows += page_rows
            if len(rows) >= count:
                why = "取完"
                break
            if min((g.get("score") or 0) for g in page_rows) < ASSOC_MIN_SCORE:
                why = "翻到阈值以下"
                break           # 按分数降序给，已经低于阈值了，不必再翻
            time.sleep(SLEEP)
        else:
            # for 走完没 break = 阈值以上还剩着没取回，这一维就只能报"没取全"
            over.append(f"{t.code} {len(rows)}/{count}")
            why = "超页上限"
        stopped[t.code] = why
        got[t.code] = {"node": node, "count": count, "rows": rows}
    version = _api_version()
    _archive(p, OT, OT_ASSOC_ROWS, "assoc", got, version=version, key=f"rows-{version}",
             release_date=ot_release_date(OT, OT_PROBE_DATASET),
             stats={"requests": req, "pages": apages, "ms": ms, "over_cap": over,
                    "min_score": ASSOC_MIN_SCORE, "stopped": sorted(set(stopped.values())),
                    "reach": _reach(reaches)})


def fetch_drugs(p: Payload, offline: bool) -> None:
    """`drugAndClinicalCandidates` 没有 page 参数——实测整表返回（1036/1036、263/263）。"""
    if offline:
        _replay(p, OT, OT_DRUG_ROWS, "drug")
        return

    ms, reaches, req = 0, [], 0
    got: dict = {}
    version = _api_version()
    for t in TARGETS:
        node = ot._efo(ot_node(t))
        d, m, reach = ot._gql(
            '{d:disease(efoId:"%s"){drugAndClinicalCandidates{count rows{id maxClinicalStage'
            ' drug{id name drugType mechanismsOfAction{rows{mechanismOfAction actionType'
            ' targetName}}}}}}}' % node, mb=200_000_000)
        ms += m
        reaches.append(reach)
        req += 1
        blk = (d.get("d") or {}).get("drugAndClinicalCandidates") or {}
        cnt = int(blk.get("count") or 0)
        rows = blk.get("rows") or []
        if cnt and len(rows) != cnt:
            # "整表返回"是实测前提。哪天源加了截断，这里就得先知道，
            # 而不是安静少装几百个药
            raise SystemExit(f"OT {t.code} 药物行回 {len(rows)} 条 ≠ count {cnt}"
                             "——这个字段大概改成截断式返回了，重测")
        got[t.code] = {"node": node, "count": cnt, "rows": rows}
    _archive(p, OT, OT_DRUG_ROWS, "drug", got, version=version, key=f"rows-{version}-drug",
             release_date=ot_release_date(OT, OT_PROBE_DATASET),
             stats={"requests": req, "ms": ms, "reach": _reach(reaches)})


def load_payload(offline: bool) -> Payload:
    p = Payload()
    fetch_trials(p, offline)
    fetch_pubs(p, offline)
    fetch_assoc(p, offline)
    fetch_drugs(p, offline)
    return p


def _api_version() -> str:
    meta, _ms, _reach = ot._gql("{meta{apiVersion{x y z suffix}}}")
    av = (meta.get("meta") or {}).get("apiVersion") or {}
    return ".".join(str(av[k]) for k in ("x", "y", "z") if av.get(k)) or "unknown"


def ot_release_date(src: str, dataset: str) -> str | None:
    """行级两份数据集没有自己的发布日——OT 平台版本日期只写在探针那版 croissant 里，抄过来。

    取的是探针那一行而不是本次的 GraphQL apiVersion：`release_date` 这一列答的是
    "这一版数据是哪天的"，平台数据日期只有 croissant 给过。
    """
    with db.ro() as conn:
        sid = db.one(conn, "SELECT `id` FROM `source` WHERE `code`=:c", {"c": src})
        if not sid:
            return None
        row = db.one(
            conn,
            "SELECT `release_date` FROM `dataset_release`"
            " WHERE `source_id`=:s AND `dataset_code`=:d ORDER BY `id` DESC LIMIT 1",
            {"s": int(sid[0]), "d": dataset})
    # 空串会被 MySQL 严格模式当非法日期拒掉，没有就是 NULL
    return str(row[0]) if row and row[0] else None


# ---------------------------------------------------------------- trial
def bucket(status) -> str:
    v = str(status or "").strip()
    if v not in STATUS_BUCKET:
        raise SystemExit(
            f"CT 冒出没读过的 overallStatus＝{v!r}。status_bucket 是页面分栏依据，"
            "不猜档——读过官方列表后把它加进 STATUS_BUCKET")
    return STATUS_BUCKET[v]


def _matched(terms, hay: str) -> list:
    low = hay.lower()
    return [t for t in terms if t.lower() in low]


def _outcomes(v) -> str:
    """primaryOutcomes 是一组 {measure,timeFrame}，列只有一条：并成一句。

    实测最长一句 14,429 字符（有试验把几十项都标成主要结局），所以列是 text 不是 varchar。
    """
    if not isinstance(v, list):
        return ""
    return "; ".join(
        "{} [{}]".format(o.get("measure") or "", o.get("timeFrame") or "").replace(" []", "")
        for o in v if isinstance(o, dict) and o.get("measure"))


def trial_rows(p: Payload, ids, sid, rid, cut: Cutter) -> list[dict]:
    out = []
    for t in TARGETS:
        for s in (p.one(CT_ROWS, t.code)).get("studies") or []:
            sec = s.get("protocolSection") or {}
            g = {k: ct.dig(sec, path) for k, path in ct.FIELD_PATHS.items()}
            status = str(g["overall_status"] or "")
            enr = g["enrollment"] if isinstance(g["enrollment"], dict) else {}
            design = dict(g["design_info"]) if isinstance(g["design_info"], dict) else {}
            if enr:
                # 源给的是 {count, type}（实测 23,704/23,705 行有这个对象）：数字落 enrollment，
                # "估算还是实际"整块留在 design_info——只留数字的话估算与实际就混成一列
                design["enrollmentInfo"] = enr
            hay = " ".join([str(g["title"] or ""), str(g["brief_title"] or "")]
                           + [str(x) for x in ct._as_list(g["conditions"])])
            hv, fda = g["healthy_volunteers"], g["fda_regulated"]
            out.append({
                "disease_id": ids[t.code],
                "dataset_code": CT_ROWS,
                "dataset_release_id": rid,
                "nct_id": cut.cut("nct_id", g["nct_id"], 20),
                "title": str(g["title"] or "").strip(),
                "brief_title": cut.cut("brief_title", g["brief_title"], 512),
                "overall_status": cut.cut("overall_status", status, 48),
                "status_bucket": bucket(status),
                "study_type": cut.cut("study_type", g["study_type"], 24),
                "phases": g["phases"] or None,
                "design_info": design or None,
                "enrollment": int(enr["count"]) if str(enr.get("count") or "").isdigit() else None,
                "conditions": g["conditions"] or None,
                "interventions": g["interventions"] or None,
                "arm_groups": g["arm_groups"] or None,
                "primary_outcome": cut.cut("primary_outcome", _outcomes(g["primary_outcome"]), 16000),
                # 源给的是整段自由文本（含换行与项目符号），不是条目数组——原样存成 JSON 字符串
                "eligibility": (json.dumps(str(g["eligibility_criteria"]).strip(),
                                           ensure_ascii=False)
                                if g["eligibility_criteria"] else None),
                "elig_sex": cut.cut("elig_sex", g["elig_sex"], 8),
                "healthy_volunteers": None if hv is None else int(bool(hv)),
                "lead_sponsor": cut.cut(
                    "lead_sponsor",
                    (g["lead_sponsor"] or {}).get("name") if isinstance(g["lead_sponsor"], dict)
                    else g["lead_sponsor"], 191),
                "collaborators": g["collaborators"] or None,
                "location_countries": sorted(set(ct._as_list(g["location_countries"]))) or None,
                "publications": g["publications"] or None,
                "fda_regulated": None if fda is None else int(bool(fda)),
                "why_stopped": str(g["why_stopped"] or "").strip() or None,
                "matched_terms": _matched(t.search_terms, hay) or None,
                **prov(source_id=sid, dataset_release_id=rid,
                       extract_method="l1_structured", review_status="spot_checked"),
            })
    return out


# ---------------------------------------------------------------- publication
def ext_key(r: dict) -> str:
    """UPSERT 的业务键：pmid → doi → 标题哈希。NULL 进唯一键会长双份，所以三档必有产出。"""
    pmid = str(r.get("pmid") or "").strip()
    if pmid.isdigit():
        return pmid
    doi = str(r.get("doi") or "").strip().upper()
    if doi:
        return doi
    title = str(r.get("title") or "").strip().lower()
    if not title:
        raise SystemExit("EPMC 一条记录 pmid/doi/标题全空，业务键没法定")
    return "t" + _hash16(title)


def _journal(r: dict) -> str:
    """EPMC 的期刊名只在 journalInfo.journal.title 这一层，预印本（source=PPR）整块 journalInfo 是 null。"""
    ji = r.get("journalInfo")
    j = ji.get("journal") if isinstance(ji, dict) else None
    return str((j or {}).get("title") or "").strip()


def pub_rows(p: Payload, ids, sid, rid, cut: Cutter) -> list[dict]:
    out = []
    for t in TARGETS:
        for r in (p.one(PMC_ROWS, t.code)).get("records") or []:
            year = str(r.get("pubYear") or "").strip()
            out.append({
                "disease_id": ids[t.code],
                "dataset_code": PMC_ROWS,
                "dataset_release_id": rid,
                "ext_key": cut.cut("ext_key", ext_key(r), 64),
                "pmid": int(r["pmid"]) if str(r.get("pmid") or "").isdigit() else None,
                "doi": cut.cut("doi", r.get("doi"), 96),
                "title": cut.cut("title", r.get("title"), 1024),
                # 期刊名在 journalInfo.journal.title 里；顶层那个 source 是库别代码
                # （MED/PPR/PMC/AGR 四个值），第一版照字段名把它当期刊存了。预印本没期刊，留 NULL
                "journal": cut.cut("journal", _journal(r), 255) or None,
                "pub_year": int(year) if year.isdigit() else None,
                "is_oa": 1 if str(r.get("isOpenAccess", "")).upper() == "Y" else 0,
                "in_epmc": 1 if str(r.get("inEPMC", "")).upper() == "Y" else 0,
                "has_pdf": 1 if str(r.get("hasPDF", "")).upper() == "Y" else 0,
                "has_abstract": 1 if str(r.get("abstractText") or "").strip() else 0,
                "matched_terms": _matched(
                    t.search_terms, " ".join([str(r.get("title") or ""),
                                              str(r.get("abstractText") or "")])) or None,
                # 抄录 + 相关度前 500，没人逐条读过标题；阈值与上限是声明，不是目测
                **prov(source_id=sid, dataset_release_id=rid,
                       extract_method="l1_structured", review_status="unreviewed"),
            })
    return out


# ---------------------------------------------------------------- target / drug
def target_nodes(kept, sid, rid, cut: Cutter) -> list[dict]:
    """阈值以上那批关联指向的靶点，去重成节点。

    节点只从真正要落库的关系行里长出来，而不是从归档的全部行：末页总带着阈值以下的
    共现级行，把它们也落成节点就是一批没有边的孤儿，而 `target` 走 upsert 不走整批替换，
    孤儿会一直留在库里，节点总数从此对不上 `disease_target`。
    """
    seen: dict[str, tuple] = {}
    for _t, _blk, g in kept:
        tg = g.get("target") or {}
        if tg.get("id") and tg["id"] not in seen:
            seen[tg["id"]] = (tg.get("approvedSymbol") or "", tg.get("approvedName") or "")
    return [{"ot_id": cut.cut("ot_id", k, 32),
             "approved_symbol": cut.cut("approved_symbol", v[0], 64),
             "approved_name": cut.cut("approved_name", v[1], 191),
             **prov(source_id=sid, dataset_release_id=rid,
                    extract_method="l1_structured", review_status="unreviewed")}
            for k, v in sorted(seen.items())]


def assoc_rows(p: Payload) -> list[dict]:
    """阈值以上的关联行，一次过筛——`disease_target` 与 `target_count` 必须数同一批行。"""
    out = []
    for t in TARGETS:
        blk = p.one(OT_ASSOC_ROWS, t.code)
        for g in blk.get("rows") or []:
            if (g.get("score") or 0) >= ASSOC_MIN_SCORE:
                out.append((t, blk, g))
    return out


def assoc_links(kept, tg, ids, sid, rid, cut: Cutter) -> list[dict]:
    """(病, 靶点) 收成一行，留分数最高的那条。

    `uk_disease_target` 只到 (病, 靶点, 源)，可翻页取回的行里同一靶点可能出现两次
    （OT 按分数降序给，边翻边漂就会重）。不自己收拢的话一批内撞键就是"后写的赢"，
    最强的那条证据反倒没了——所以这里按最高分收，装载器再把折掉的行数报出来。
    """
    best: dict[tuple, dict] = {}
    for t, blk, g in kept:
        tid = (g.get("target") or {}).get("id")
        if not tid or tid not in tg:
            raise SystemExit(f"OT {t.code} 关联行指向靶点 {tid!r}，节点表里没有——"
                             "节点与关系两趟读的不是一份数据")
        score = round(float(g.get("score") or 0), 4)
        novelty = g.get("novelty")
        row = {
            "disease_id": ids[t.code],
            "target_id": tg[tid],
            "dataset_code": OT_ASSOC_ROWS,
            "dataset_release_id": rid,
            "score": score,
            # 实测 novelty 是 0.0001 量级的小数、要 6 位才分得开，所以列是 decimal 不是 varchar
            "novelty": None if novelty is None else round(float(novelty), 6),
            "datasource_scores": [{"id": s.get("id"), "score": s.get("score")}
                                  for s in (g.get("datasourceScores") or [])] or None,
            "node_used": cut.cut("node_used", blk.get("node"), 24),
            **prov(source_id=sid, dataset_release_id=rid,
                   extract_method="l1_structured", review_status="unreviewed"),
        }
        key = (row["disease_id"], row["target_id"])
        if key not in best or score > best[key]["score"]:
            best[key] = row
    return [best[k] for k in sorted(best)]


def drug_rows(p: Payload, ids, sid, rid, cut: Cutter) -> list[dict]:
    """(病, 药, 阶段) 收拢成一行，机制并成数组。源给的是三元组哈希，同药同阶段会有几行。"""
    kept: dict[tuple, dict] = {}
    for t in TARGETS:
        for g in (p.one(OT_DRUG_ROWS, t.code)).get("rows") or []:
            stage = str(g.get("maxClinicalStage") or "").strip()
            if stage not in STAGES:
                raise SystemExit(
                    f"OT {t.code} 冒出没读过的 maxClinicalStage＝{stage!r}。"
                    "phase 整列按这几个值分组，读过源后再加进 STAGES")
            d = g.get("drug") or {}
            name = str(d.get("name") or "").strip()
            if not name:
                raise SystemExit(f"OT {t.code} 一行药物没名字，落不进 uk_drug")
            moa = [m.get("mechanismOfAction")
                   for m in ((d.get("mechanismsOfAction") or {}).get("rows") or [])
                   if isinstance(m, dict) and m.get("mechanismOfAction")]
            row = kept.setdefault((t.code, name.lower(), stage), {
                "disease_id": ids[t.code],
                "dataset_code": OT_DRUG_ROWS,
                "dataset_release_id": rid,
                "drug_id": cut.cut("drug_id", d.get("id"), 32),
                "drug_name": cut.cut("drug_name", name, 255),
                "phase": cut.cut("phase", stage, 32),
                "moa": [],
                **prov(source_id=sid, dataset_release_id=rid,
                       extract_method="l1_structured", review_status="spot_checked"),
            })
            for m in moa:
                if m not in row["moa"]:
                    row["moa"].append(m)
    for row in kept.values():
        if not row["moa"]:
            row["moa"] = None       # 18 病 6,323 行里 4,128 行有机制，空数组与"没有这一项"该分开
    return [kept[k] for k in sorted(kept)]


# ---------------------------------------------------------------- stat_fact
def query_facts(p: Payload, ids, rids, sids, cut: Cutter, links, drugs) -> list[dict]:
    """`stat_fact` 的 query_count 行。数字全部来自这一趟的源侧计数，不再打网络。

    这一档与统计层那些流行量纲不同：它是"按声明词命中多少条"，不是流行病学计数，
    所以 `estimate_basis` 必须是 query_count、`year` 必须是 0（不是年度序列）。
    四个数各自说源的一件事，所以不合成一个"研究总量"：trial 与 publication 是**库里命中数**，
    target 是**进了库的行数**（阈值以上、同病同靶点已收拢），drug 是**去重到药名的个数**（源给的三元组行数写在注里）。
    """
    year_from = p.meta[PMC_ROWS]["stats"].get("year_from")
    out = []
    for t in TARGETS:
        named = len({r["drug_name"].lower() for r in drugs if r["disease_id"] == ids[t.code]})
        for src, ds, metric, value, note in (
            (CT, CT_ROWS, "trial_count", p.one(CT_ROWS, t.code).get("hit"),
             "ClinicalTrials.gov 按声明词在招命中（RECRUITING/NOT_YET_RECRUITING/"
             "ENROLLING_BY_INVITATION）"),
            (PMC, PMC_ROWS, "publication_count", p.one(PMC_ROWS, t.code).get("hit"),
             f"EPMC TITLE_ABS 近 {year_from} 年起命中；库里另有全年限，"
             f"本表按相关度取前 {PUB_CAP} 条"),
            (OT, OT_ASSOC_ROWS, "target_count",
             # 数的是进了 `disease_target` 的行，不是过完阈值的原始行：这个数要能跟表对上
             sum(1 for l in links if l["disease_id"] == ids[t.code]),
             f"OT associatedTargets 且 score≥{ASSOC_MIN_SCORE}（阈下是共现级，不进库）"),
            (OT, OT_DRUG_ROWS, "drug_count", named,
             f"OT 在研药去重到药名；源按 (药,阶段,关联) 给 "
             f"{p.one(OT_DRUG_ROWS, t.code).get('count')} 行"),
        ):
            if value is None:
                raise SystemExit(f"{t.code} 的 {metric} 没有计数：这一趟没取到载荷，不补 0")
            out.append({
                "disease_id": ids[t.code],
                "source_id": sids[src],
                "dataset_code": ds,
                "dataset_release_id": rids[ds],
                "metric": metric, "unit": "count", "value": int(value), "year": 0,
                "age_band": "", "sex": "both", "region": "",
                "estimate_basis": "query_count",
                "cohort_note": cut.cut("cohort_note", note, 128),
                **prov(source_id=sids[src], dataset_release_id=rids[ds],
                       extract_method="l1_structured", review_status="unreviewed"),
            })
    return out


# ---------------------------------------------------------------- 落库
def _rows_seen(ds: str, p: Payload, key: str) -> int:
    return sum(len((rec or {}).get(key) or []) for rec in (p.recs.get(ds) or {}).values())


def load(ctx: Ctx) -> LoadResult:
    p = load_payload(ctx.offline)
    cut = Cutter()
    with ctx.tx() as conn:
        sids = {}
        rids = {}
        for src, ds, prefix, key in DATASETS:
            sids[src] = ctx.source_id(src)
            if ctx.offline:
                # 离线重放不许再造一版发布：库里那一版就是当初取回的字节的凭据
                rids[ds] = ctx.latest_release(conn, src, ds)
                continue
            m = p.meta[ds]
            rids[ds] = ctx.register(
                conn, src, ds, upstream_version=m["version"], release_date=m["release_date"],
                body_bytes=sum(m["sizes"].values()), sha256=_dir_sha(m["dir"]),
                rows_seen=_rows_seen(ds, p, key), raw_path=raw.rel(m["dir"]))
        ids = ctx.disease_ids(conn)
        rows_trial = trial_rows(p, ids, sids[CT], rids[CT_ROWS], cut)
        rows_pub = pub_rows(p, ids, sids[PMC], rids[PMC_ROWS], cut)
        n = {"trial": replace_scope(conn, "trial", {"source_id": sids[CT]}, rows_trial),
             "publication": replace_scope(conn, "publication", {"source_id": sids[PMC]}, rows_pub)}
        kept = assoc_rows(p)
        n["target"] = upsert(conn, "target",
                             target_nodes(kept, sids[OT], rids[OT_ASSOC_ROWS], cut))
        tg = {str(o): int(i) for o, i in
              db.rows(conn, "SELECT `ot_id`,`id` FROM `target` WHERE `source_id`=:s",
                      {"s": sids[OT]})}
        links = assoc_links(kept, tg, ids, sids[OT], rids[OT_ASSOC_ROWS], cut)
        n["disease_target"] = replace_scope(conn, "disease_target",
                                           {"source_id": sids[OT]}, links)
        drugs = drug_rows(p, ids, sids[OT], rids[OT_DRUG_ROWS], cut)
        n["drug"] = replace_scope(conn, "drug", {"source_id": sids[OT]}, drugs)
        facts = query_facts(p, ids, rids, sids, cut, links, drugs)
        by_src: dict[int, list] = {}
        for r in facts:
            by_src.setdefault(r["source_id"], []).append(r)
        # 只 Own 自己那批 query_count 行：同一源将来还可能落别的度量，全源删会连它一起清掉
        n["stat_fact"] = sum(
            replace_scope(conn, "stat_fact",
                          {"source_id": sid, "estimate_basis": "query_count"}, rows)
            for sid, rows in by_src.items())

    hits = sum(p.one(CT_ROWS, t.code).get("hit") or 0 for t in TARGETS)
    pub_hits = sum(p.one(PMC_ROWS, t.code).get("hit") or 0 for t in TARGETS)
    assoc_all = sum(p.one(OT_ASSOC_ROWS, t.code).get("count") or 0 for t in TARGETS)
    drug_all = sum(p.one(OT_DRUG_ROWS, t.code).get("count") or 0 for t in TARGETS)
    zero_trial = [t.code for t in TARGETS if not (p.one(CT_ROWS, t.code).get("studies") or [])]
    statuses = {r["overall_status"] for r in rows_trial}
    phases = {r["phase"] for r in drugs}
    covered = len({r["disease_id"] for r in rows_trial + rows_pub + links + drugs})
    ctx.job.set(written=sum(n.values()))
    msg = (
        f"trial {n['trial']} 行 = {len({r['nct_id'] for r in rows_trial})} 个唯一 NCT"
        f"（18 病在招命中合计 {hits:,}，同一试验跨病出现算多行；逐病取齐 totalCount，"
        f"翻页 {p.meta[CT_ROWS]['stats'].get('pages')} 页，请求 "
        f"{p.meta[CT_ROWS]['stats'].get('requests')}）"
        + (f"；在招 0 项的病：{', '.join(zero_trial)}——查询词表或源的状态口径要重读"
           if zero_trial else "") + " / "
        f"publication {n['publication']} 行（每病相关度前 {PUB_CAP} 条；库里近 "
        f"{p.meta[PMC_ROWS]['stats'].get('year_from')} 年共 {pub_hits:,} 篇，"
        "所以这张表是上限样本不是全量） / "
        f"target {n['target']} 个节点 + disease_target {n['disease_target']} 行"
        f"（18 病关联总数 {assoc_all:,}，按 score≥{ASSOC_MIN_SCORE} 留下 {len(kept)} 行、"
        f"同病同靶点按最高分收拢掉 {len(kept) - n['disease_target']} 行，"
        f"翻 {p.meta[OT_ASSOC_ROWS]['stats'].get('pages')} 页，其余是共现级；"
        "节点只从落库的关系行里长出来，所以这个数对得上表） / "
        f"drug {n['drug']} 行（源给 {drug_all:,} 行三元组，按 (病,药,阶段) 收拢掉 "
        f"{drug_all - n['drug']} 行） / stat_fact query_count {n['stat_fact']} 行"
        "（trial/publication/target/drug 四个计数各一行，与这四张表同一趟写）。\n"
        f"取值域核对：overallStatus 实到 {len(statuses)} 个值、maxClinicalStage 实到 "
        f"{len(phases)} 个值，两份声明（STATUS_BUCKET {len(STATUS_BUCKET)} 值 / "
        f"STAGES {len(STAGES)} 值）之外的值一律中止。spot_checked 只给 trial 与 drug"
        "（判的是值域），publication 与 disease_target 是抄录与阈值，记 unreviewed。\n"
        f"按列宽截断的列：{cut.brief()}。\n"
        "整列空：`trial.why_stopped`（只落在招，源只给终止试验这一句，实测 23,705 行全 NULL）。\n"
        "不入库：secondary/other outcomes（载荷在归档里，表没这两列）。\n"
        + "、".join(f"{ds}={p.meta[ds]['version']}" for _s, ds, _p, _k in DATASETS)
        + f"；归档 " + "、".join(f"{ds}={raw.rel(p.meta[ds]['dir'])}"
                                for _s, ds, _p, _k in DATASETS) + "。"
        "EPMC 侧不给数据发布日（只有搜索版本号），OT 两份行数据集的发布日抄探针那版 croissant。"
    )
    return LoadResult(written=n, covered=covered, total=len(TARGETS), message=msg)
