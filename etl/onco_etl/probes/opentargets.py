"""Open Targets 关联体量探针：判"这一病能不能拿到带分数的靶点/药关联"，顺带量清下载体量。

判据是每病关联靶点 ≥10。这一支三件事都要出数，因为平台的"匿名可取"分两层，
而这两层给的东西完全不是一回事：

  · 点查层：GraphQL `POST /api/v4/graphql` 匿名直连，`disease(efoId:"MONDO_…")` 直接收
    MONDO 号（冒号换下划线），一次别名批量就能把 18 病的关联数、文献量、表型数、
    在研药数全拿回来。判据只靠这一层就能裁。
    父节点不能并进同一趟——实测 18 病连 `parents` 一次问完回 408 Request Timeout，
    按 6 病一批分三批才全通（单批 3.8~4.7 s）。
  · 批量层：EBI FTP 的 `platform/<版本>/output/` 是 56 个 parquet 数据集目录，
    `croissant.json` 只声明三个分发根（FTP/GCS/S3）而不给单文件大小，
    所以体量只能逐个目录列出来加。这一步量的是"要不要走批量下载"，
    与判据无关，但没有它 B7 会以为"能下载"和"能点查"是同一件事。

窄档陷阱是这一支最要紧的负向结论：targets.py 为了语义对齐 icd10 故意选了窄主条目
（female breast carcinoma、malignant pancreatic neoplasm），而 OT 的关联与文献挂在更宽的
父节点上——同一份声明在两个节点上差三个量级（female breast carcinoma 88 篇文献 /
643 个关联靶点，它的父节点 breast carcinoma 710,750 篇 / 17,963 个靶点）。
父节点的数字仍然一并取回用来对账，但换不换档不再悬着：B7c 已裁成一份声明式覆盖
`targets.OT_NODE`，探针只按它查（目前 18 病里只有乳腺癌换宽节点），
不许探针自己按名字猜一个宽档混进去。

表型数顺带量了（lung 5、colorectum 8、liver/breast/prostate 0）：这条对 B6 的症状维是
负面证据，写在这里是为了 B6 不必再打一次同样的查询。
"""
from __future__ import annotations

import json
import re
import time

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS, codes, ot_node
from .result import ProbeResult

SOURCE = "opentargets"
DATASET = "platform-release-associations"
RELEASE = "https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/"
CROISSANT = RELEASE + "latest/croissant.json"
GQL = "https://api.platform.opentargets.org/api/v4/graphql"
CRITERIA = "每病关联靶点 ≥10，且能匿名点查取回带分数的靶点清单；只有整库 parquet 可下不算达标"

MIN_ASSOC = 10
SLEEP = 0.2
# 目录清单是 Apache 索引：一行 = 图标格 + 名字格 + 日期格 + 大小格，大小写成 "68M" / "1.2K" / "0"
ROW_RE = re.compile(
    r'<td><a href="([^"]+)">[^<]*</a></td><td align="right">([^<]*)</td>'
    r'<td align="right">\s*([^<]*)\s*</td>')
UNIT = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def _bytes(s: str) -> int:
    m = re.fullmatch(r"([\d.]+)\s*([KMGT]?)", s.strip())
    return int(float(m[1]) * UNIT[m[2]]) if m else 0


def _get(url: str, mb: int = 8_000_000, tries: int = 3) -> tuple[bytes, int, str, int]:
    """FTP 目录与清单文件都算大（association 目录一页 6 KB，croissant 640 KB），
    偶发断流要重试：这一支不是"源坏了"，别把网络抖动写成 dead。"""
    last: tuple[bytes, int, str, int] = (b"", 0, "error", 0)
    for k in range(tries):
        r = fetch(url, max_bytes=mb, timeout=(10, 180))
        last = (r.body, r.latency_ms, r.reachability, r.status or 0)
        if r.status == 200 and r.body:
            return last
        time.sleep(1 + k)
    raise SystemExit(f"{url} → {last[3] or last[2]}（重试 {tries} 次仍失败）")


def _listing(url: str) -> tuple[list, int, str]:
    body, ms, reach, _ = _get(url)
    rows = []
    for name, when, size in ROW_RE.findall(body.decode("utf-8", "replace")):
        if name.startswith(("?C=", "/")):
            continue
        rows.append((name, when.strip(), _bytes(size)))
    return rows, ms, reach


def _gql(query: str, mb: int = 8_000_000, tries: int = 4) -> tuple[dict, int, str]:
    """POST 一个 GraphQL 查询。HTTP 200 里也可能装着 errors，必须单独判。"""
    last = "no attempt"
    for k in range(tries):
        r = fetch(GQL, method="POST", headers={"Content-Type": "application/json"},
                  json_body={"query": query}, max_bytes=mb, timeout=(10, 180))
        last = f"{r.status or r.reachability}：{r.note}"
        if r.status and 400 <= r.status < 500 and r.status not in (408, 429):
            # 400 是查询自己写错了，重试不会变好，而原因只在这份响应体里。
            # 丢掉它就只能报"拿不到 200"，白重跑一趟四分钟的 FTP 目录清单
            raise SystemExit(f"GraphQL HTTP {r.status}：" + (r.text or "")[:500])
        if r.status == 200 and r.body:
            try:
                d = json.loads(r.text)
            except json.JSONDecodeError:
                raise SystemExit(f"GraphQL 回 200 但不是 JSON（{r.content_type}）：{r.text[:160]}")
            if d.get("errors"):
                # 字段名写错会整条查询回 errors 而 HTTP 仍是 200。
                # 放过去就是"18 病关联数全 0"的假死讯
                raise SystemExit("GraphQL 报错：" + json.dumps(d["errors"])[:400])
            return d["data"], r.latency_ms, r.reachability
        time.sleep(1 + k)
    raise SystemExit(f"GraphQL 重试 {tries} 次仍失败（{last}）")


def _efo(mondo_id: str) -> str:
    """MONDO:0008903 → MONDO_0008903：OT 的 efoId 参数收 MONDO，但要下划线形式。"""
    return mondo_id.replace(":", "_")


def _node(t) -> str:
    """这一病在 OT 上要查的那个节点：targets.OT_NODE 覆盖过的用宽档，否则用 mondo_id。"""
    return _efo(ot_node(t))


def probe(offline: bool = False) -> ProbeResult:
    ms = 0
    http: int | None = None
    reaches: list[str] = []
    sizes: dict[str, int] = {}

    if offline:
        key_dir = raw.newest_dir(SOURCE, "diseases.json")
        if not key_dir:
            raise SystemExit(f"离线重放要先有归档：data/raw/{SOURCE}/*/diseases.json 不存在")
        man = json.loads((key_dir / "manifest.json").read_text(encoding="utf-8"))
        dirs = json.loads((key_dir / "dirs.json").read_text(encoding="utf-8"))
        out = json.loads((key_dir / "diseases.json").read_text(encoding="utf-8"))
        reaches = ["offline"]
        sizes = {n: (key_dir / n).stat().st_size
                 for n in ("manifest.json", "dirs.json", "diseases.json")}
    else:
        body, m, reach, http = _get(CROISSANT, mb=40_000_000)
        ms += m
        reaches.append(reach)
        try:
            raw_manifest = json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            raise SystemExit("croissant.json 不是 JSON：入口换了，别把这一维判成通")
        ver = str(raw_manifest.get("version") or "")
        date_pub = str(raw_manifest.get("datePublished") or "")[:10] or None
        # 只留判定要用的字段：整份 croissant 640 KB，其中 recordSet 里全是逐列描述
        dist = raw_manifest.get("distribution") or []
        man = {"version": ver, "datePublished": date_pub,
               "license": raw_manifest.get("license"),
               "citeAs": (str(raw_manifest.get("citeAs") or "")[:120] or None),
               "recordSets": len(raw_manifest.get("recordSet") or []),
               "distN": len(dist),
               "distWithLicense": len([o for o in dist if o.get("license") or o.get("licenses")]),
               "roots": [o.get("contentUrl") for o in dist if o.get("contentUrl")],
               "fileSets": len([o for o in dist if not o.get("contentUrl")]),
               # FileSet 只有 includes（`<dataset>/*.parquet`），目录名从这里解出来跟 FTP 对账
               "declared_dirs": sorted({str(o.get("includes") or "").split("/")[0]
                                        for o in dist
                                        if not o.get("contentUrl") and o.get("includes")})}
        dirs = []
        rows, m, reach = _listing(RELEASE + "latest/output/")
        ms += m
        reaches.append(reach)
        for name, _when, _size in rows:
            if not name.endswith("/"):
                continue
            sub, m, reach = _listing(RELEASE + "latest/output/" + name)
            ms += m
            reaches.append(reach)
            files = [x for x in sub if x[0].endswith(".parquet")]
            dirs.append({"dataset": name.rstrip("/"), "files": len(files),
                         "bytes": sum(x[2] for x in files),
                         "newest": max((x[1] for x in sub if x[1]), default="")})
            time.sleep(SLEEP)
        sel = ("{id name description dbXRefs associatedTargets{count} literatureOcurrences{count}"
               " phenotypes{count} drugAndClinicalCandidates{count}}")
        data, m, reach = _gql("{" + " ".join(
            'd%s:disease(efoId:"%s")%s' % (t.code, _node(t), sel) for t in TARGETS) + "}")
        ms += m
        reaches.append(reach)
        # parents 与 counts 不能并成一趟：实测 18 病一次问 parents 回 408 Request Timeout，
        # 6 病一批三批全通（单批 3.8~4.7 s）
        psel = ("{id name associatedTargets{count} literatureOcurrences{count}"
                " parents{id name associatedTargets{count} literatureOcurrences{count}}}")
        pdata: dict = {}
        for i in range(0, len(TARGETS), 6):
            chunk = TARGETS[i:i + 6]
            d, m, reach = _gql("{" + " ".join(
                'd%s:disease(efoId:"%s")%s' % (t.code, _node(t), psel) for t in chunk) + "}")
            ms += m
            reaches.append(reach)
            pdata.update(d)
        # 被覆盖掉的那一档一并取回：不取的话"当初为什么换"只剩 targets.py 里一句注释，
        # 重跑探针报不出换档前后的差值。只查覆盖病，一趟几秒。
        over = [t for t in TARGETS if ot_node(t) != t.mondo_id]
        odata: dict = {}
        if over:
            d, m, reach = _gql("{" + " ".join(
                'd%s:disease(efoId:"%s")%s' % (t.code, _efo(t.mondo_id), sel) for t in over) + "}")
            ms += m
            reaches.append(reach)
            odata = d
        # 抽样看"带分数的靶点清单"到底给不给：判据要的是 rows，不是 count。
        # 分数按数据源拆开（datasourceScores），Target 上的名字字段是 approvedSymbol/approvedName
        sdata, m, reach = _gql(
            '{d:disease(efoId:"%s"){associatedTargets(page:{index:0,size:3}){count'
            ' rows{score novelty target{id approvedSymbol approvedName}'
            ' datasourceScores{id score}}}}}' % _node(TARGETS[0]))
        ms += m
        reaches.append(reach)
        # 药物那一支的行级字段：`drug` 表建表时只量过 drugAndClinicalCandidates.count，
        # 列宽与幂等键要的是行里的形状，所以这里补一次抽样。这个字段没有 page 参数，
        # 抽到的就是全量，顺带能验"整表返回"这个前提还在不在
        ddata, m, reach = _gql(
            '{d:disease(efoId:"%s"){drugAndClinicalCandidates{count rows{id maxClinicalStage'
            ' drug{id name drugType mechanismsOfAction{rows{mechanismOfAction actionType'
            ' targetName}}}}}}}' % _node(TARGETS[0]), mb=200_000_000)
        ms += m
        reaches.append(reach)
        meta, m, reach = _gql("{meta{apiVersion{x y z suffix}}}")
        ms += m
        reaches.append(reach)
        out = {"diseases": data, "with_parents": pdata, "overridden": odata,
               "assoc_sample": sdata, "drug_sample": ddata, "meta": meta}
        key_dir = raw.archive_dir(SOURCE, ver or "no-version")
        blobs = {"manifest.json": man, "dirs.json": dirs, "diseases.json": out,
                 "croissant.json": raw_manifest}
        for n, b in blobs.items():
            payload = (body if n == "croissant.json"
                       else json.dumps(b, ensure_ascii=False).encode("utf-8"))
            (key_dir / n).write_bytes(payload)
            sizes[n] = len(payload)

    reach = "offline" if "offline" in reaches else ("proxy" if "proxy" in reaches else "direct")
    data = out["diseases"]
    # 对账：清单声明的 FileSet 目录名与 FTP 实际目录必须一致，不一致就没法"照清单下载"
    listed = {x["dataset"] for x in dirs}
    declared = set(man.get("declared_dirs") or [])
    missing_dirs = sorted(declared - listed)
    undeclared_dirs = sorted(listed - declared)
    pdata = out.get("with_parents") or {}
    per = []
    covered = 0
    for t in TARGETS:
        d = data.get("d" + t.code) or {}
        at = (d.get("associatedTargets") or {}).get("count") or 0
        lit = (d.get("literatureOcurrences") or {}).get("count") or 0
        pheno = (d.get("phenotypes") or {}).get("count") or 0
        drugs = (d.get("drugAndClinicalCandidates") or {}).get("count") or 0
        pd = (pdata.get("d" + t.code) or {}).get("parents") or []
        # 父节点取"文献最多的那一个"：并多个父节点会重复计数，取 max 才是这一病的最宽口径
        widest = max(pd, key=lambda x: (x.get("literatureOcurrences") or {}).get("count") or 0,
                     default=None)
        ok = at >= MIN_ASSOC
        covered += 1 if ok else 0
        per.append({
            "code": t.code, "ot_id": d.get("id"), "name": d.get("name"),
            "node": _node(t), "node_name": d.get("name"),
            "node_declared": t.mondo_id, "swapped": _node(t) != _efo(t.mondo_id),
            "assoc": at, "lit": lit, "pheno": pheno, "drugs": drugs,
            "desc": bool(d.get("description")), "xrefs": len(d.get("dbXRefs") or []),
            "parent": (widest or {}).get("name") or "",
            "parent_assoc": ((widest or {}).get("associatedTargets") or {}).get("count") or 0,
            "parent_lit": ((widest or {}).get("literatureOcurrences") or {}).get("count") or 0,
            "pass": ok,
        })
    by_code = {p["code"]: p for p in per}
    missing = [c for c in codes() if not by_code[c]["ot_id"]]
    if missing:
        # 200 回了但 disease 字段是 null：不是源坏了，是声明的节点号在 OT 不存在
        # （或 efoId 这个参数换了）。让它过下去只会得到"这些病关联靶点 0"的假结论。
        raise SystemExit(
            "OT 按声明节点查不到 disease(...)："
            + ", ".join(f"{c}→{by_code[c]['node']}" for c in missing)
            + "——检查 targets.mondo_id / targets.OT_NODE")
    thin_lit = [f"{p['code']} {p['lit']:,}（父节点 {p['parent']} {p['parent_lit']:,}）"
                for p in per if p["lit"] < 1000 and p["parent_lit"] >= 1000]
    fails = [p["code"] for p in per if not p["pass"]]
    total_bytes = sum(x["bytes"] for x in dirs)
    biggest = sorted(dirs, key=lambda x: -x["bytes"])[:5]
    shards = sum(x["files"] for x in dirs)
    assoc_all = sum(p["assoc"] for p in per)
    lo = min(per, key=lambda p: p["assoc"])
    hi = max(per, key=lambda p: p["assoc"])
    sample = (out.get("assoc_sample") or {}).get("d", {}).get("associatedTargets", {}).get("rows") or []
    def _peek(r: dict) -> str:
        top = max((r.get("datasourceScores") or []), key=lambda x: x.get("score") or 0,
                  default={})
        return "{} {}←{} {}".format(
            (r.get("target") or {}).get("approvedSymbol") or "?", round(r.get("score") or 0, 3),
            top.get("id") or "?", round(top.get("score") or 0, 3))
    peek = "、".join(_peek(r) for r in sample)
    # 药物行级字段：`drug` 表建表时只量过 count，phase / moa / uk_drug 三处形状都靠这一趟补
    dblk = ((out.get("drug_sample") or {}).get("d") or {}).get("drugAndClinicalCandidates") or {}
    drows = dblk.get("rows") or []
    dcnt = int(dblk.get("count") or 0)

    def _d(r, *path):
        cur = r
        for k in path:
            cur = (cur or {}).get(k)
        return cur

    dstages = sorted({str(r.get("maxClinicalStage") or "") for r in drows})
    chembl = sum(1 for r in drows if str(_d(r, "drug", "id") or "").startswith("CHEMBL"))
    named = sum(1 for r in drows if str(_d(r, "drug", "name") or "").strip())
    moa = sum(1 for r in drows if _d(r, "drug", "mechanismsOfAction", "rows"))
    dtypes = sorted({str(_d(r, "drug", "drugType") or "") for r in drows})
    # 源给的一行是 (药, 阶段, 来源关联) 的三元组：去重比就是装载器要收拢的幅度
    pairs = len({(str(_d(r, "drug", "name") or "").strip().lower(),
                  str(r.get("maxClinicalStage") or "")) for r in drows})
    name_max = max((len(str(_d(r, "drug", "name") or "")) for r in drows), default=0)
    moa_max = max((len(str(m.get("mechanismOfAction") or ""))
                   for r in drows for m in (_d(r, "drug", "mechanismsOfAction", "rows") or [])),
                  default=0)
    av = ((out.get("meta") or {}).get("meta") or {}).get("apiVersion") or {}
    api_v = ".".join(str(av[k]) for k in ("x", "y", "z") if av.get(k)) or "?"
    pheno_zero = [p["code"] for p in per if not p["pheno"]]
    # 换档前那一档的数字来自本趟另查的 overridden；旧归档里没这一项时文案写"未取回"
    orig = {}
    for k, v in (out.get("overridden") or {}).items():
        dd = v or {}
        orig[k[1:] if k.startswith("d") else k] = "assoc {:,}/lit {:,}/drugs {:,}".format(
            (dd.get("associatedTargets") or {}).get("count") or 0,
            (dd.get("literatureOcurrences") or {}).get("count") or 0,
            (dd.get("drugAndClinicalCandidates") or {}).get("count") or 0)
    swap_rows = [p for p in per if p["swapped"]]
    swap_txt = "".join(
        " 节点覆盖已生效（B7c 裁定）：{code} 从声明主条目 {dec} 换查 {node}（{name}），"
        "换后 assoc {a:,}/lit {l:,}/drugs {d:,}，换前 {orig}。".format(
            code=p["code"], dec=p["node_declared"], node=p["node"], name=p["node_name"],
            a=p["assoc"], l=p["lit"], d=p["drugs"],
            orig=orig.get(p["code"], "本趟未取回"))
        for p in swap_rows)

    if covered == len(TARGETS) and not missing and sample:
        verdict = "ok"
    elif covered:
        verdict = "partial"
    else:
        verdict = "empty"

    msg = (
        f"达标 {covered}/{len(TARGETS)}（判据＝关联靶点 ≥{MIN_ASSOC}）。"
        f"点查这一层全中：18 病的查询节点全部命中，"
        f"关联靶点最少的是 {lo['code']} {lo['assoc']:,} 个、最多 {hi['code']} {hi['assoc']:,} 个，"
        f"18 病合计 {assoc_all:,} 条；在研药（drugAndClinicalCandidates）合计 "
        f"{sum(p['drugs'] for p in per):,}。"
        f"带分数的靶点清单实测可取（{TARGETS[0].code} 前 {len(sample)} 条：{peek}）。"
        + ("" if out.get("drug_sample") else " 药物行级字段：本份归档早于该抽样，未取回。")
        + (f"药物行级字段本轮补测（`drug` 表建表时只量过 count）：{TARGETS[0].code} 一趟整表返回 "
           f"count={dcnt}、实到 {len(drows)} 行"
           + ("，行数与 count 不等——这字段大概加分页了，装载器要先重测"
              if dcnt and len(drows) != dcnt else
              f"（说明这个字段确实没有分页参数）。maxClinicalStage 见到 {dstages}；"
              f"drug.id {chembl}/{len(drows)} 是 CHEMBL 形、name 非空 {named}、"
              f"机制数组非空 {moa}；drugType 取值 {dtypes}。"
              f"按 (药名,阶段) 去重只剩 {pairs} 组，说明源给的一行是 (药,阶段,来源关联) 的三元组、"
              f"装载器必须按 uk_drug 收拢；实测最长药名 {name_max} 字符、"
              f"最长机制描述 {moa_max} 字符。") if out.get("drug_sample") else "")
        + swap_txt
        + f"其余 {len(TARGETS) - len(swap_rows)} 病仍按声明主条目查，"
        "探针不自己按名字换档（换了就把 icd10 语义对齐破坏了），只读 targets.OT_NODE 这一份声明。"
        "不随覆盖一起放宽是实测逼出来的：胰腺的宽档父节点比本节点更空"
        "（drugAndClinicalCandidates 30 vs 本节点 463），层级上量不单调。"
        f"换档后仍偏窄的：{len(thin_lit)} 病节点文献量不足千而父节点是主战场——"
        + ("；".join(thin_lit) if thin_lit else "无") + "。"
        f"批量层实测：平台 {man.get('version')}（发布 {man.get('datePublished')}）的 "
        f"output/ 有 {len(dirs)} 个数据集、{shards} 个 parquet 分片、合计 {total_bytes / 2**30:.1f} GiB，"
        "最大五个 " + "、".join(f"{b['dataset']} {b['bytes'] / 2**20:.0f} MB" for b in biggest) + "。"
        f"清单与 FTP 对账：distribution 里 {man.get('fileSets')} 个 FileSet 的 includes 解出 "
        f"{len(declared)} 个目录名，与 FTP 实到的 {len(listed)} 个相比"
        + (f"完全一致。" if not missing_dirs and not undeclared_dirs else
           f"——清单有而 FTP 没有 {missing_dirs or '无'}，FTP 有而清单没声明 {undeclared_dirs or '无'}。")
        + f"croissant 只给三个分发根（FTP/GCS/S3）不给单文件大小与 sha256 实值（根条目里 sha256 字段的"
        "值是字面量 'sha256' 占位），所以体量只能逐目录列，"
        f"这一趟 {len(dirs) + 1} 个 LIST 请求。整库不是'顺手就能下'的量级，"
        "站点按点查用就够，落库不需要整包。"
        f"许可按清单原文报：croissant 顶层 license 是一个 URL 字符串（{man.get('license')}，"
        f"即 CC0），且 {man.get('distN')} 个 distribution 没有一个自带许可字段——"
        "整份清单只在顶层说了一次话。B7c 已裁：登记表保留更严的那句'平台 Apache 2.0，数据随上游'，"
        "并把这份 CC0 记成第二个冲突声明（落在 source.legal_note 里），站点署名两句一起带，"
        f"哪一层哪天改了以本字段对账。citeAs 给了 BibTeX（{str(man.get('citeAs'))[:60]}…）。"
        f"给 B6 顺带的负向证据：{len(pheno_zero)} 病的 phenotypes.count 为 0"
        f"（{', '.join(pheno_zero)}），其余病也只在 2~8 条——OT 的表型注释当不了症状维的源。"
        f"平台版本号来自 croissant 的 version（{man.get('version')}），"
        f"GraphQL 的 meta 另给 apiVersion={api_v}（是个 x/y/z 对象，不是字符串）；"
        "整库没有可匿名取的'数据发布日'以外字段，release_date 用 croissant 的 datePublished。"
    )
    if fails:
        msg += f" 关联靶点不足 {MIN_ASSOC} 的病：{', '.join(fails)}。"

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=assoc_all,
        diseases_covered=covered,
        diseases_total=len(TARGETS),
        fields_seen=["associatedTargets.count", "literatureOcurrences.count", "phenotypes.count",
                     "drugAndClinicalCandidates.count",
                     "drugAndClinicalCandidates.rows[].id/maxClinicalStage",
                     "drugAndClinicalCandidates.rows[].drug{id,name,drugType}",
                     "drugAndClinicalCandidates.rows[].drug.mechanismsOfAction"
                     ".rows[]{mechanismOfAction,actionType,targetName}",
                     "parents", "dbXRefs", "description",
                     "associatedTargets.rows[].target.approvedSymbol/approvedName",
                     "associatedTargets.rows[].datasourceScores{id,score}",
                     "meta.apiVersion{x,y,z,suffix}", "croissant.distribution[]",
                     "croissant.recordSet[]", "croissant.version", "croissant.datePublished",
                     "croissant.license（顶层单点，distribution 逐条目无）",
                     "OT_NODE 覆盖后的 node/node_name/node_declared/swapped",
                     "output/<dataset>/*.parquet"],
        sample=per,
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=str(man.get("version") or ""),
        release_date=man.get("datePublished"),
        release_bytes=sum(sizes.values()),
        release_sha256=raw.sha256_bytes(
            b"".join((key_dir / n).read_bytes() for n in sorted(sizes))
        ),
    )
