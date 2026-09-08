"""GWAS Catalog 探针：判"效应量与 CI 到底能不能匿名按病取回"，顺便判 GWAS 能不能算危险因素。

B4 的 CRA 那一半量到"关联有、强度没有"（效应量 0/18）。这一支正好相反：
`OR or BETA` + `95% CI (TEXT)` 两列匿名就在文件里，填充率 84% / 83%，
但它给的是**位点 × 表型**的关联，不是这一维想要的**可干预暴露**，而且全表没有一个 PAF。
两个数分开报：效应量形式上过，暴露语义没过——混起来 B7 就会把"遗传易感位点"
当成"危险因素清单通了"，那等于用另一张表把缺口藏起来。

四件实测出来的口径事，都直接影响 P1 能不能自动落库：

  ① 疾病侧对齐只能查 ID，不能猜名字。`MAPPED_TRAIT_URI` 的癌种档写的是
    `purl.obolibrary.org/obo/MONDO_0008903`——GWAS 的 trait 词表本身以 MONDO 为键
    （24619 档里 2129 个是 MONDO_），所以 B2 那个"MONDO 的 EFO xref 只有 5/18"在这里
    不构成障碍，方向反过来即可。但按 targets 声明的**主条目** URI 精确命中，有行的只有
    16/18（breast_female、uterus 一行都没有），按判据（≥3 个带效应量与区间的独立位点）
    只剩 14/18——pancreas 主条目 12 行全无区间、esophagus 并掉无区间的只剩 1 个位点。
    关联大量挂在同级组织学档（breast carcinoma、endometrial carcinoma）上，并进来可达
    18/18，但那要人工裁定才算数：P1 已把这四病裁成 `targets.GWAS_URI` 一份声明。
    所以本探针报三个数——主条目命中（严格，14/18）、主条目+声明档（这一列落库用的口径）、
    未声明的关键词候选档（上界，逐档连行数一起进 sample）。要再放宽必须是改那份声明，
    不许探针自己并档。
  ② `OR or BETA` 是一列混装。全表值 <0 的只有 6 个，方向写在 CI 文本里
    （`[0.036] unit decrease` 这类注记 84 万行），所以 β 的符号不在这列里；
    而 OR 与 β 在区间同为正时从形态上分不开。落库想分 `effect_kind` 只能整列留"未判定"，
    不许按"值<1 就是保护性 OR"这种猜法。
  ③ CI 列非空 ≠ 有区间。非空 991,389 行、含可解析区间只有 894,029 行，差的 97,360 行
    （占全表 8.2%）只有 `unit increase` 这类文字没有数字，所以"CI 填充率"必须按
    "含可解析区间"算（75.0%），不能按非空算（83.2%）。
  ④ `MAPPED_TRAIT_URI` 是多值列，分隔符实测是逗号不是分号（96,520 行带 2~7 个 URI）。
    整格不拆开就取尾段，会把 1,339 行 `EFO_…,MONDO_0008170` 形态的 MR 研究行
    算成这个病自己的一条遗传证据——命中数与覆盖病数都会虚高。而 `MAPPED_TRAIT` 与 URI
    共用逗号且段数对不上（9,191 行），所以拆档只认 URI，label 只当文本看待。

取数姿势：整包 73.5 MB，但这条链路单条长连接会被限速并在 ~54 MB 处断流
（实测一次 GET 只回 53,767,694 字节且以 HTTP 200 正常收尾），所以按 8 MB 分段 Range 取，
段短了就续。不是洁癖——不这样跑就永远拿不到完整包。
版本号从 `releases/<年>/<月>/` 日历目录读，`release_date` 用这份包在 FTP 上的日期
（实测 2026-09-04），不用件内 `max(DATE ADDED TO CATALOG)`（实测 2026-09-01）：
后者是"最后录入的关联"，是内容新鲜度，早发布日三天，拿它当发布日会让增量判定跟着错。
"""
from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path

from .. import raw
from ..fetch import fetch
from ..sources import BY_CODE
from ..targets import GWAS_URI, TARGETS
from .result import ProbeResult

SOURCE = "gwas_catalog"
DATASET = "associations-ontology-annotated-full"
CRITERIA = (
    "每病 ≥3 个独立危险因素带效应量（PAF 或 OR/RR + CI），可匿名编程取回；"
    "GWAS 给的是位点×表型关联，按 targets 声明的主条目 URI + `GWAS_URI` 声明档命中计，"
    "未声明的同级档只作上界上报"
)

ZIP_NAME = "gwas-catalog-associations_ontology-annotated-full.zip"
MEMBER_KEY = "gwas-catalog-download-associations"
META_NAME = "probe_meta.json"
CHUNK = 8 << 20
TRIES = 4


def _ftp() -> str:
    """`releases/` 根地址从登记表反推，不在探针里再抄一遍主机路径：
    两处各写一份，改了 `sources.py` 而探针仍打旧地址时，
    probe-reach 与专项探针会给出互相矛盾的可达性结论。"""
    url = BY_CODE[SOURCE].download_url or ""
    head = url.split("releases/", 1)
    if len(head) != 2:
        raise SystemExit(f"sources.py 里 {SOURCE}.download_url 不含 releases/ 段，"
                         f"探针无从推出日历目录根：{url!r}")
    return head[0] + "releases/"


EFFECT_COL = "OR or BETA"
CI_COL = "95% CI (TEXT)"
URI_COL = "MAPPED_TRAIT_URI"
TRAIT_COL = "MAPPED_TRAIT"
NULLS = {"", "NR", "NA", "N/A", "-"}
# `[1.09-1.22] unit increase` 是主流形态：区间取前缀，注记另算。fullmatch 会把这种判成解析失败
CI_HEAD = re.compile(r"^\[\s*(-?[\d.]+)\s*-\s*(-?[\d.]+)\s*\]")
MIN_FACTORS = 3

# 同级候选档的收法：单档 + 像肿瘤 + 器官关键词命中，且不是"暴露测量"或良性/亚型噪声档
ORGAN_RE = {
    "lung": r"lung|bronch|trachea",
    "colorectum": r"colorect|colon cancer|rectal|bowel cancer",
    "liver": r"liver|hepat|bile duct",
    "stomach": r"gastric|stomach",
    "breast_female": r"breast",
    "pancreas": r"pancrea",
    "esophagus": r"esophag|oesophag|barrett",
    "prostate": r"prostat",
    "cervix": r"cervix|cervical",
    "ovary": r"ovar",
    "thyroid": r"thyroid",
    "bladder": r"bladder",
    "kidney": r"kidney|renal",
    "brain": r"brain|glioma|glioblastoma|astrocyt|oligodendro|medulloblastoma|meningioma"
             r"|ependymoma|nervous system",
    "uterus": r"uter|endometri",
    "leukemia": r"leukem|myeloid|lymphoblastic",
    "nhl": r"lymphoma",
    "myeloma": r"myeloma|plasma cell",
}
CANCER_RE = re.compile(
    r"cancer|carcinom|neoplasm|lymphoma|leukem|myeloma|melanom|glioma|sarcoma|adenoma"
    r"|astrocytom|oligodendroglioma|blastoma", re.I)
# `glioma pathogenesis-related protein 1 measurement`（1777 行）是按关键词捞脑癌必踩的坑：
# 那是蛋白水平量的 QTL，不是脑癌结局。MR 的 `暴露 measurement, 癌` 同理
MEAS_RE = re.compile(r"measurement|level of | in blood| in serum|expression|activity|score", re.I)
EXCLUDE_RE = {
    "nhl": r"hodgkin",
    "uterus": r"endometriosis|leiomyoma",
    "cervix": r"dysplasia|intraepithelial",
    "prostate": r"benign|hyperplasia",
    "lung": r"copd|asthma|emphysema|nodule",
    "liver": r"fibrosis|cirrhosis|steatohepatitis|failure",
    "kidney": r"calculus|stone|cyst",
    "brain": r"malformation|dementia|epilep",
    "leukemia": r"hairy cell",
    "myeloma": r"monoclonal gammopathy",
}
ORGAN_RX = {k: re.compile(v, re.I) for k, v in ORGAN_RE.items()}
EXCLUDE_RX = {k: re.compile(v, re.I) for k, v in EXCLUDE_RE.items()}


def _autoindex(text: str) -> list[tuple[str, str]]:
    """Apache autoindex → [(名字, 日期)]。排序链接与 Parent Directory 由调用方按名字筛掉。"""
    return [(m.group(1), m.group(2).split()[0]) for m in re.finditer(
        r'<a href="([^"?][^"]*)">[^<]*</a></td><td align="right">\s*([\d-]+ [\d:]+)', text)
        if not m.group(1).startswith("/")]


def _release() -> tuple[dict, int, list[str]]:
    """releases/<年>/<月>/ 才是版本，latest/ 只是它的镜像——版本号从日历目录读，不拼死。"""
    ms = 0
    proxied: list[str] = []
    info: dict = {"version": "unknown"}
    ftp = _ftp()

    years = fetch(ftp, timeout=(10, 60), max_bytes=400_000)
    ms += years.latency_ms
    if years.reachability == "proxy":
        proxied.append("releases/")
    ys = sorted(n.rstrip("/") for n, _ in _autoindex(years.text) if n.rstrip("/").isdigit())
    if ys:
        y = ys[-1]
        r2 = fetch(f"{ftp}{y}/", timeout=(10, 60), max_bytes=400_000)
        ms += r2.latency_ms
        if r2.reachability == "proxy":
            proxied.append(f"releases/{y}/")
        ms_list = sorted(n.rstrip("/") for n, _ in _autoindex(r2.text) if n.rstrip("/").isdigit())
        if ms_list:
            info = {"version": f"{y}-{ms_list[-1]}", "year": y, "month": ms_list[-1]}
    r3 = fetch(ftp + "latest/", timeout=(10, 60), max_bytes=400_000)
    ms += r3.latency_ms
    if r3.reachability == "proxy":
        proxied.append("releases/latest/")
    if r3.ok:
        entries = dict(_autoindex(r3.text))
        info["latest_zip_date"] = entries.get(ZIP_NAME)
        info["latest_entries"] = len(entries)
        info["zip_url"] = ftp + "latest/" + ZIP_NAME
    return info, ms, proxied


def _get_zip(url: str, dest: Path, declared: int) -> tuple[int, int, bool]:
    """分段 Range 取回整包，返回 (字节数, 延迟, 有没有落过代理)。

    大小已经对得上就直接复用：重跑探针不该把 73 MB 再下一遍。
    段取不满就重来（最多 TRIES 次），Range 被忽略回了 200 就整包重写。
    """
    if dest.is_file() and dest.stat().st_size == declared:
        return declared, 0, False
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    ms = 0
    via = False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with part.open("ab" if have else "wb") as fh:
        while have < declared:
            end = min(have + CHUNK - 1, declared - 1)
            want = end - have + 1
            got = 0
            for attempt in range(TRIES):
                r = fetch(url, timeout=(15, 240), max_bytes=want + 4096,
                          headers={"Range": f"bytes={have}-{end}"})
                ms += r.latency_ms
                via = via or r.reachability == "proxy"
                if r.status == 200:  # Range 没生效：这一发就是整包，直接落地
                    fh.seek(0)
                    fh.truncate()
                    fh.write(r.body[:want])
                    fh.flush()
                    have = got = len(r.body[:want])
                    break
                if r.ok and r.body:
                    n = min(len(r.body), want)
                    fh.write(r.body[:n])
                    fh.flush()
                    have += n
                    got += n
                    break
                time.sleep(1.5 * (attempt + 1))
            if got == 0:
                break
    if have == declared:
        part.replace(dest)
    return have, ms, via


def _member(z: zipfile.ZipFile) -> zipfile.ZipInfo:
    hits = [i for i in z.infolist() if MEMBER_KEY in i.filename and not i.is_dir()]
    if not hits:
        raise SystemExit(f"ZIP 里没有 {MEMBER_KEY}*.tsv（实际成员 {[i.filename for i in z.infolist()]}）"
                         "——EBI 改了产物名，字段清单与填充率都无从判断，别把这个结论落库")
    return hits[0]


def probe(offline: bool = False) -> ProbeResult:
    reach = "offline" if offline else "direct"
    meta: dict = {}
    ms_total = 0
    proxied: list[str] = []

    if offline:
        key_dir = raw.newest_dir(SOURCE, "*.zip")
        zp = next(iter(sorted(key_dir.glob("*.zip"))), None) if key_dir else None
        if not zp:
            raise SystemExit(f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/{ZIP_NAME} 不存在")
        size = zp.stat().st_size
        sha = raw.sha256_file(zp)
        # 元数据缺了不许静默落成 version='unknown'：它是 dataset_release 唯一键的一列，
        # 写错就会登记出一条上游不存在的版本行
        mp = key_dir / META_NAME
        if not mp.is_file():
            raise SystemExit(f"归档目录 {raw.rel(key_dir)} 里没有 {META_NAME}"
                             "——离线重放需要它提供版本号，请联网跑一次或手工补一份")
        meta = json.loads(mp.read_text(encoding="utf-8"))
    else:
        info, ms, steps = _release()
        ms_total += ms
        proxied += steps
        url = info.get("zip_url") or (_ftp() + "latest/" + ZIP_NAME)
        head = fetch(url, method="HEAD", timeout=(10, 60))
        ms_total += head.latency_ms
        if head.reachability == "proxy":
            proxied.append("HEAD")
        if not head.ok or not head.declared_bytes:
            return ProbeResult(
                verdict="dead" if head.status in (404, 410) else "blocked",
                message=f"{url} → {head.status or head.reachability}：{head.note}"
                        "（连声明大小都拿不到，分段续传无从判断是否完整）",
                criteria=CRITERIA, dataset_code=DATASET, reachability=head.reachability,
                http_status=head.status, latency_ms=ms_total)
        declared = head.declared_bytes
        key_dir = raw.archive_dir(SOURCE, info["version"])
        zp = key_dir / ZIP_NAME
        size, ms2, via = _get_zip(url, zp, declared)
        ms_total += ms2
        if via:
            proxied.append("zip")
        if size != declared:
            return ProbeResult(
                verdict="blocked",
                message=f"{url} 分段取到 {size:,}/{declared:,} B 仍不完整——Range 语义或本机链路有问题，"
                        f"字段之外的结论全部作废（残包留在 {raw.rel(zp.with_name(zp.name + '.part'))}）",
                criteria=CRITERIA, dataset_code=DATASET,
                reachability="proxy" if proxied else "direct",
                http_status=head.status, latency_ms=ms_total)
        sha = raw.sha256_file(zp)
        info["declared_bytes"] = declared
        meta = {"release": info, "zip_url": url, "via_proxy": proxied}

    rel_info = meta.get("release") or {}
    version = rel_info.get("version") or "unknown"
    proxied = meta.get("via_proxy") or proxied
    if proxied:
        reach = "proxy"

    z = zipfile.ZipFile(zp)
    mi = _member(z)

    rows = 0
    n_eff = n_ci = n_ci_interval = n_both = n_unit = n_neg = 0
    n_multi_uri = n_lab_mismatch = 0
    dates: list[str] = []
    # 疾病侧对齐表：主条目 URI 尾段 → 病码，再加 targets.GWAS_URI 声明的同级档。
    # 声明里写冒号形式（与 mondo_id / OT_NODE 一致），尾段变换留在这里——只有这一列
    # 的取值形态是 `…/obo/MONDO_0008903`，不该把这个知识漏进 targets.py。
    main_of = {t.code: t.mondo_id.replace(":", "_") for t in TARGETS}
    unknown = sorted(set(GWAS_URI) - set(main_of))
    if unknown:
        raise SystemExit(f"targets.GWAS_URI 声明了基准外的病码 {unknown}——"
                         "这份声明是覆盖判据的一部分，路径写错的病会永远显示成「没覆盖」")
    want = {m: c for c, m in main_of.items()}
    for code, uris in GWAS_URI.items():
        for u in (x.replace(":", "_") for x in uris):
            if u == main_of[code]:
                continue
            if want.get(u, code) != code:
                raise SystemExit(f"targets.GWAS_URI 把 {u} 同时声明给 {want[u]} 与 {code}"
                                 "——一个档只能属于一个病，否则这条关联会被两个病各数一遍")
            want[u] = code
    per: dict[str, dict] = {
        t.code: {"main_rows": 0, "main_effci": 0, "multi_rows": 0,
                 "main_loci": set(), "main_studies": set(), "main_traits": {},
                 "decl_rows": 0, "decl_effci": 0,
                 "decl_loci": set(), "decl_studies": set(), "decl_traits": {},
                 "sib_rows": 0, "sib_effci": 0, "sib_loci": set(),
                 "sib_studies": set(), "sib_traits": {}}
        for t in TARGETS}

    with io.TextIOWrapper(z.open(mi.filename), encoding="utf-8", errors="replace",
                          newline="\n") as fh:
        hdr = [h.strip() for h in fh.readline().rstrip("\n").split("\t")]
        idx = {h: i for i, h in enumerate(hdr)}
        missing = [c for c in (EFFECT_COL, CI_COL, URI_COL, TRAIT_COL, "STUDY ACCESSION")
                   if c not in idx]
        if missing:
            raise SystemExit(f"关联表表头里没有 {missing}（实际 {hdr}）——EBI 改了列名，"
                             "效应量与 CI 的填充率无从判断，别把这个结论落库")

        def col(r: list, name: str) -> str:
            i = idx.get(name)
            return r[i].strip() if i is not None and i < len(r) else ""

        for line in fh:
            r = line.rstrip("\n").split("\t")
            if len(r) != len(hdr):
                continue
            rows += 1
            eff, ci = col(r, EFFECT_COL), col(r, CI_COL)
            m = CI_HEAD.match(ci) if ci not in NULLS else None
            if m:
                try:
                    float(m.group(1))
                    float(m.group(2))
                except ValueError:
                    m = None  # `[1.80-.5.00]` 这种畸形串：宁可不计数，也不猜它是 1.80–5.00
            if eff not in NULLS:
                n_eff += 1
                if eff.startswith("-"):
                    n_neg += 1
            if ci not in NULLS:
                n_ci += 1
            if m:
                n_ci_interval += 1
            if eff not in NULLS and m:
                n_both += 1
            if "unit increase" in ci or "unit decrease" in ci:
                n_unit += 1
            d = col(r, "DATE ADDED TO CATALOG")
            if d:
                dates.append(d)

            # 多值分隔符实测是逗号（96,520 行带 2~7 个 URI），不是分号。
            # 按 ";" 切会整串落成一个 token，`rsplit("/")` 取到的尾档会把
            # `EFO_xxx, MONDO_0008170` 这种 MR 混档行当成"这一行唯一的档"
            uris = [x.strip() for x in re.split(r"[;,]", col(r, URI_COL)) if x.strip()]
            single = len(uris) == 1
            if not single:
                n_multi_uri += 1
            joined = col(r, TRAIT_COL)
            # label 与 URI 共用逗号：两列拆出来的档数对不上，说明有的 EFO 名字自己就带逗号。
            # 所以拆分只认 URI，label 当整串用（它只进 sample 给人看）
            if len([x for x in joined.split(",") if x.strip()]) != len(uris):
                n_lab_mismatch += 1
            short = [u.rsplit("/", 1)[-1] for u in uris]
            hit = [want[s] for s in short if s in want]
            # 位点独立性：优先坐标，缺坐标的（ovary 那 63 行全无 CHR_ID）退回最强 SNP-等位，
            # 否则会把"有数没坐标"的病误判成"一个位点都没有"
            locus = (col(r, "CHR_ID"), col(r, "CHR_POS")) if col(r, "CHR_ID") \
                else col(r, "STRONGEST SNP-RISK ALLELE")
            effci = bool(m) and eff not in NULLS
            study = col(r, "STUDY ACCESSION")

            if hit:
                # 命中声明档的行到此为止：再走下面那段，声明档会出现在自己的候选档清单里
                for code in hit:
                    p = per[code]
                    if not single:
                        # 目标档与别的档并在一行：那是 MR 研究的暴露×结局，不能算这一病自己的证据
                        p["multi_rows"] += 1
                        continue
                    # 严格口径（只算主条目）与落库口径（主条目 + 声明档）分开累加：
                    # 换声明不该把"主条目本身有没有数"这件事抹掉，那两个数都要能看见
                    is_main = short[0] == main_of[code]
                    label = joined[:60]
                    p["decl_rows"] += 1
                    p["decl_traits"][label] = p["decl_traits"].get(label, 0) + 1
                    if is_main:
                        p["main_rows"] += 1
                        p["main_traits"][label] = p["main_traits"].get(label, 0) + 1
                    if effci:
                        p["decl_effci"] += 1
                        p["decl_loci"].add(locus)
                        p["decl_studies"].add(study)
                        if is_main:
                            p["main_effci"] += 1
                            p["main_loci"].add(locus)
                            p["main_studies"].add(study)
                continue
            if not single or not CANCER_RE.search(joined):
                continue
            for code, rx in ORGAN_RX.items():
                if not rx.search(joined) or MEAS_RE.search(joined) \
                        or (code in EXCLUDE_RX and EXCLUDE_RX[code].search(joined)):
                    continue
                p = per[code]
                p["sib_rows"] += 1
                if effci:
                    p["sib_effci"] += 1
                    p["sib_loci"].add(locus)
                    p["sib_studies"].add(study)
                key = f"{short[0]}|{joined[:50]}"
                p["sib_traits"][key] = p["sib_traits"].get(key, 0) + 1

    out = []
    for t in TARGETS:
        p = per[t.code]
        both = p["decl_loci"] | p["sib_loci"]
        out.append({
            "code": t.code, "name_en": t.name_en, "mondo_uri": t.mondo_id.replace(":", "_"),
            "declared_uris": GWAS_URI.get(t.code, ()),
            "main_rows": p["main_rows"], "main_effci_rows": p["main_effci"],
            "multi_uri_rows": p["multi_rows"],
            "main_loci": len(p["main_loci"]), "main_studies": len(p["main_studies"]),
            "decl_rows": p["decl_rows"], "decl_effci_rows": p["decl_effci"],
            "decl_loci": len(p["decl_loci"]), "decl_studies": len(p["decl_studies"]),
            "sib_rows": p["sib_rows"], "sib_loci": len(p["sib_loci"]),
            "sib_studies": len(p["sib_studies"]), "loci_all": len(both),
            "pass_main": len(p["main_loci"]) >= MIN_FACTORS,
            "pass_declared": len(p["decl_loci"]) >= MIN_FACTORS,
            "pass_all": len(both) >= MIN_FACTORS,
            "main_labels": sorted(p["main_traits"].items(), key=lambda kv: -kv[1])[:3],
            "declared_labels": sorted(p["decl_traits"].items(), key=lambda kv: -kv[1])[:6],
            "sibling_candidates": sorted(p["sib_traits"].items(), key=lambda kv: -kv[1])[:8],
        })
    cov = sum(1 for v in out if v["pass_main"])
    cov_decl = sum(1 for v in out if v["pass_declared"])
    cov_all = sum(1 for v in out if v["pass_all"])
    zero = [v["code"] for v in out if not v["main_rows"]]
    declared_only = [v["code"] for v in out if v["pass_declared"] and not v["pass_main"]]
    missing = [v["code"] for v in out if not v["pass_declared"]]
    multi_hit = sum(p["multi_rows"] for p in per.values())

    if not offline:
        (key_dir / META_NAME).write_text(
            json.dumps({**meta, "rows": rows, "cols": hdr}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    msg = (
        f"效应量这一半通了、可干预暴露那一半没通：匿名整包（{size:,} B / {rows:,} 行 / {len(hdr)} 列）里 "
        f"`{EFFECT_COL}` 非空 {n_eff:,}（{n_eff/rows*100:.1f}%）、`{CI_COL}` 非空 {n_ci:,}"
        f"（{n_ci/rows*100:.1f}%），但 CI 只能按「含可解析区间」算：真含区间 {n_ci_interval:,}"
        f"（{n_ci_interval/rows*100:.1f}%），效应量与区间同时有 {n_both:,}"
        f"（{n_both/rows*100:.1f}%）；带 `unit increase/decrease` 注记的 {n_unit:,} 行"
        f"（与含区间那批有重叠，不是另一批）说明方向写在文本里，"
        f"而值本身为负的只有 {n_neg} 行——β 的方向不在数值列里，OR 与 β 在区间同为正时形态分不开，"
        f"`effect_kind` 只能整列留未判定。全表没有任何 PAF/归因分数列。"
        f"疾病侧：`{URI_COL}` 整列是混装的（EFO/OBA/MONDO/HP 都在），"
        f"但癌种档正好落在 MONDO URI 上，按 targets 声明的 URI 精确命中就行"
        f"（B2 那个 EFO xref 5/18 的缺口在这里不构成障碍）；"
        f"多值分隔符实测是逗号不是分号——{n_multi_uri:,} 行一行带 2~7 个档"
        f"（MR 研究把暴露档与疾病档并在一行），按 `;` 切会整串落成一个 token、"
        f"尾档被当成该行唯一的档，那些 MR 行就伪装成这一病自己的证据，"
        f"所以只有单档行算进命中，混档行按病进 sample 的 `multi_uri_rows`"
        f"（18 病合计 {multi_hit:,} 行）。"
        f"覆盖分三个口径报，不许混：①只认 `mondo_id` 主条目，≥{MIN_FACTORS} 个带效应量位点的 "
        f"{cov}/{len(TARGETS)}（主条目一行没有的：{', '.join(zero) or '—'}）；"
        f"②主条目 + `targets.GWAS_URI` 声明档——①不达的 {len(GWAS_URI)} 病已在 P1 裁成声明"
        f"（{', '.join(GWAS_URI)}，逐病代价写在那份注释里），按这一口径是 {cov_decl}/{len(TARGETS)}"
        f"（靠声明档才达判据的：{', '.join(declared_only) or '—'}；仍未达的：{', '.join(missing) or '—'}）"
        f"——落库按 ②，`diseases_covered` 与矩阵那一列都读它，①原样留在 sample 的 `pass_main`；"
        f"③再并上没声明的关键词候选档是 {cov_all}/{len(TARGETS)}，那是上界不是结论，"
        f"逐病候选档与行数在 sample 里，要并进来必须先改那份声明，探针不自己放宽档。"
        f"`{TRAIT_COL}` 与 URI 列共用逗号做连接符，两列拆出来的档数对不上的有 {n_lab_mismatch:,} 行"
        f"（有的档名自己就带逗号）——拆档只认 URI，label 只能整串用，"
        f"反过来按 label 拆会把一个档数成两个"
    )
    # 发布日取这份包在 FTP 上的日期，不取 max(DATE ADDED TO CATALOG)：
    # 后者是"最后录入的关联"，是内容新鲜度，比发布日早三天，当 release_date 会误导增量判定
    zip_date = rel_info.get("latest_zip_date") or (max(dates)[:10] if dates else None)
    if version != "unknown":
        msg = (f"版本 {version}（releases/<年>/<月>/ 日历目录，latest/ 只是镜像，"
               f"latest/ 里这份的日期={rel_info.get('latest_zip_date')}，"
               f"件内最新关联日期 {max(dates)[:10] if dates else '—'}）：" + msg)
    if proxied:
        msg += "。这一趟有步骤走了代理，reachability 按「任一步落过代理」记"
    return ProbeResult(
        verdict="partial" if cov_decl and n_both else "empty",
        message=msg,
        criteria=CRITERIA,
        rows_seen=rows,
        # 判据要的是可干预暴露的效应量，位点不算；这里记的是「主条目 + GWAS_URI 声明档」
        # 那一口径，也就是矩阵这一列与落库用的口径。严格口径（只认主条目）在 message 与
        # sample 的 pass_main 里，两个数各自可见，不让换声明把主条目的空缺抹掉。
        diseases_covered=cov_decl,
        diseases_total=len(TARGETS),
        fields_seen=hdr,
        sample=out,
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=200,
        latency_ms=ms_total or None,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=zip_date,
        release_bytes=size,
        release_sha256=sha,
    )
