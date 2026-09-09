#!/usr/bin/env python
"""解析回归跑测器：拿仓库里的 fixture 页重跑探针的解析器，结构事实必须与判据依赖的一致。

    python etl/tests/run.py        # 跑全部；退出码非 0 即有 FAIL（不需要 pytest）

fixture 是上游原样字节（`etl/tests/fixtures/` 在 `.gitattributes` 里按二进制处理，
连换行都不改），所以这一跑兼作漂移检测：SEER 改了表结构，这里先红，
而不是等覆盖度文档里莫名少一页。`manifest.json` 记着每页的 sha256、来源版本
与"为什么是这一页"，跑测器先核字节再核解析结果。

归档该不该进仓库是按体积逐源判的：MONDO 51 MB、GWAS 整包 71 MB 不进，
SEER 四页 340 KB 进、研究层四份记录切片 48 KB 进——判据分支每支有一页在场上就够。

症状与危险因素两页都不吃 fixture。前者的三份归档（PDQ 88 KB、WHO 27 KB、维基条目）与后者的
两份归档（GWAS 整包、CRA 交叉表）都在 `data/raw` 里而不进仓库，而这两页要锁的是装载器判定
规则的**行为**（症状的归一与剔非症状、危险因素的幂等键粒度与档位/占位符口径），不是上游字节，
所以输入是合成的解析记录。真归档里的数由装载器自己在跑库时报出来核对
（`load --code symptoms` 与 `load --code risks` 的 message 就是那份对账单，
两份 EYEBALL 声明还会在数漂移时直接中止装载）。

研究层两页都要。`research_rows` 与症状、危险因素同法锁行为（值域中止、幂等键粒度、阈值口径、
归档往返），`research_fixtures` 再把四份上游原样记录喂给同一批函数，只判一件事：
字段真的在我们读的那个路径上。这一页存在的理由是 `trial.enrollment` 一度读 `enrollmentInfo.value`
而源给的形状是 `{count, type}`——整列会静默变 NULL 且不报错，而手搓的 fixture 照着同一个错猜，
于是回归跟着一起自证通过。只有原样字节能把这类错单独照出来。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Windows 控制台默认 GBK，中文断言输出会变乱码
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "etl"))

from onco_etl import raw
from onco_etl.load import anatomy, research, risks, symptoms  # noqa: E402
from onco_etl.targets import TARGETS, ot_node  # noqa: E402
from onco_etl.probes import mondo  # noqa: E402
from onco_etl.probes import gbd_cra, gwas_catalog  # noqa: E402
from onco_etl.probes import nci_pdq_html, who_factsheet, wikidata  # noqa: E402
from onco_etl.probes import seer_statfacts as seer  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 四页共享的年度序列事实。SEER 8 那两列是 49 年而不是 50 行——最后一年的 New Cases
# 是 '-'（无观测值），按行数算会把"占位符"当成"有数"。
METRICS = ["Rate of New Cases — SEER 8", "Rate of New Cases — SEER 12",
           "Death Rate — U.S.", "5-Year Relative Survival — SEER 8"]
OBS = {
    "Rate of New Cases — SEER 8 — Observed": (49, "1975", "2023"),
    "Rate of New Cases — SEER 12 — Observed": (32, "1992", "2023"),
    "Death Rate — U.S. — Observed": (50, "1975", "2024"),
    "5-Year Relative Survival — SEER 8 — Observed": (44, "1975", "2018"),
    "5-Year Relative Survival — SEER 8 — Modeled Trend": (49, "1975", "2023"),
}


class Checks:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def eq(self, what: str, got, want) -> None:
        ok = got == want
        self.items.append((what, ok, "" if ok else f"得到 {got!r}，期望 {want!r}"))

    def ok(self, what: str, cond: bool, note: str = "") -> None:
        self.items.append((what, bool(cond), note))


def check_seer(c: Checks) -> None:
    d = FIXTURES / "seer_statfacts"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    pages = {}
    for name, info in man["files"].items():
        body = (d / name).read_bytes()
        c.eq(f"seer fixture {name} 字节未改写",
             (len(body), hashlib.sha256(body).hexdigest()),
             (info["bytes"], info["sha256"]))
        pages[name[:-5]] = seer.parse_page(body)

    for slug, page in pages.items():
        ys = page["year_series"] or {}
        c.eq(f"seer {slug} 年度序列跨度", (ys.get("n_years"), ys.get("y0"), ys.get("y1")),
             (50, "1975", "2024"))
        c.eq(f"seer {slug} metric 清单", ys.get("metrics"), METRICS)
        obs = ys.get("observed") or {}
        # 8 列 = 4 个 metric × (Observed, Modeled Trend)。少一半说明二级表头没配上，
        # 拟合线就会和观测值挤成同名列
        c.eq(f"seer {slug} 观测/拟合分列", len(obs), 8)
        for col, want in OBS.items():
            got = obs.get(col) or {}
            c.eq(f"seer {slug} {col}",
                 (got.get("n"), got.get("y0"), got.get("y1")), want)

    c.eq("seer lungb 分期档", (pages["lungb"]["stage_survival"] or {}).get("n"), 4)
    c.eq("seer lungb 双性别费率表标签", pages["lungb"]["rate_sexes"], {"Males", "Females"})
    c.eq("seer lungb 种族分组", len(pages["lungb"]["race_groups"]), 6)
    c.eq("seer lungb 年龄档", (pages["lungb"]["age_incidence"] or {}).get("n"), 8)
    c.eq("seer lungb 性别口径", seer.sex_verdict("lung", pages["lungb"]), "ok")

    # 白血病这一支：SEER 没给它发分期表，解析必须真的什么也没找到（None 而不是空表），
    # 探针才走 NO_STAGE 豁免而不是"≥3 档"判据
    c.eq("seer leuks 无分期表（豁免分支）", pages["leuks"]["stage_survival"], None)
    c.ok("seer leuks 在 NO_STAGE 名单里", "leukemia" in seer.NO_STAGE)
    c.eq("seer leuks 性别口径", seer.sex_verdict("leukemia", pages["leuks"]), "ok")

    # 同为血液肿瘤，NHL 却有 Ann Arbor 五档——防的是"按 category='heme' 一律豁免"
    c.eq("seer nhl 分期档（heme 不等于无分期）",
         (pages["nhl"]["stage_survival"] or {}).get("n"), 5)

    # 性别特异癌这一支：页面上没有 <h5> 性别标签、也没有种族费率表，
    # 性别只写在口径行里，所以两条证据链都必须被认到
    c.eq("seer prost 无 <h5> 性别标签", pages["prost"]["rate_sexes"], set())
    c.eq("seer prost 无种族费率表", pages["prost"]["race_groups"], [])
    c.ok("seer prost 口径行带 Males",
         any("All Races, Males" in v for v in pages["prost"]["vintages"]))
    c.eq("seer prost 性别口径", seer.sex_verdict("prostate", pages["prost"]), "ok")
    # 拿男性页去核对女性专属病：必须判成 conflict（targets.py 声明写错了），
    # 不能混进 missing（源没给证据）——两者要去查的边不一样
    c.eq("seer prost 当作女性病核对", seer.sex_verdict("cervix", pages["prost"]), "conflict")


def check_subsites(c: Checks) -> None:
    """亚部位挑选规则。MONDO 的 ICD-9 xref 混着部位与细胞类型，这条规则决定器官树里
    会不会冒出 'small cell lung carcinoma' 这种组织学亚型——所以逐条锁住。"""
    lung = next(t for t in anatomy.TARGETS if t.code == "lung")
    pre = sorted(anatomy.expand_icd9(lung.icd9))
    # 声明里的四位前缀写成 ICD-9 的带点档位：1622 → 162.2
    def dot(p: str) -> str:
        return f"{p[:3]}.{p[3]}"

    free = [dot(p) for p in pre if p[3] not in anatomy.RESIDUAL_LAST]
    residual = [dot(p) for p in pre if p[3] in anatomy.RESIDUAL_LAST]
    assert len(free) >= 4 and len(residual) >= 2, "肺的 ICD-9 声明不够覆盖这条规则的各分支"

    def term(mid: str, codes: tuple[str, ...]) -> mondo.MondoTerm:
        return mondo.MondoTerm(id=mid, name=f"t{mid}", icd9=codes)

    scanned = mondo.MondoScan(
        subsites={
            "lung": [
                term("A", (free[0],)),  # 单开了一档 → 收
                term("B", (residual[0],)),  # .8/.9 是"其他/未特指" → 不收
                term("C", (free[1],)),  # C 与 D 抢同一档，说明那码标的是器官本身
                term("D", (free[1],)),  # 而不是亚部位，分不出谁对就都不收
                term("E", (residual[1], free[2], free[3])),  # 兜底档不牵连；多档取字典序最小
                term("F", (f"{free[0][:3]}{free[0][3]}",)),  # 没写成带点档位 → 不收
            ],
            "leukemia": [term("H", ("204.1",))],  # 合格也不收：200–208 章编的是细胞类型
        }
    )
    got = anatomy.pick_subsites(scanned)
    c.eq("subsite 肺的产出", [(t.id, slot) for t, slot in got["lung"]],
         [("A", free[0]), ("E", free[2])])
    c.eq("subsite 血病整维跳过", got["leukemia"], [])
    c.eq("subsite 其余病为空", sum(len(v) for k, v in got.items() if k not in ("lung", "leukemia")), 0)


def check_symptoms(c: Checks) -> None:
    """症状装载的两处判断。合成的解析记录，不联网也不读库——
    这两步是这一维仅有的非机械环节，改错了不会自己冒出来。"""
    lung = next(t for t in TARGETS if t.code == "lung")
    p1, p2 = lung.pdq_pages
    breast = next(t for t in TARGETS if t.code == "breast_female")

    def page(path: str, items: list[str], mode: str = "list") -> dict:
        head = "Signs and symptoms of something"
        return {"path": path, "status": 200, "lastmod": "2026-02-10", "updated": "",
                "mode": mode, "heading": head,
                "items": [{"text": t, "anchor": f"_a{i}", "heading": head}
                          for i, t in enumerate(items)]}

    def payload(pages: dict) -> nci_pdq_html.PdqPayload:
        return nci_pdq_html.PdqPayload(pages, "v", None, {}, "offline", None, 0)

    ids = {"lung": 1, "breast_female": 2}
    rows, collapsed, sentences = symptoms.pdq_rows(
        payload({"lung": [page(p1, ["Cough", "Chest pain.", "Fatigue"]),
                          page(p2, ["cough", "Weight loss"])]}), 6, 61, ids)
    # 大小写与尾标点都算同一条症状（唯一键的排序规则 utf8mb4_0900_ai_ci 就是这么判的），
    # 留下的是声明顺序第一页那一份——换了留哪一份，source_url 与 anchor 就跟着换
    c.eq("pdq 归一后留几条", ([r["name"] for r in rows], collapsed, sentences),
         (["Cough", "Chest pain.", "Fatigue", "Weight loss"], 1, 0))
    c.eq("pdq 复述留声明首册", rows[0]["source_url"], nci_pdq_html.BASE + p1)
    c.eq("pdq 行按源口径记 review_status", {r["review_status"] for r in rows}, {"spot_checked"})
    c.eq("pdq name 原样存不剥句号", "Chest pain." in [r["name"] for r in rows], True)

    _r, _cl, sentences = symptoms.pdq_rows(
        payload({breast.code: [page(breast.pdq_pages[0], ["Symptoms vary by type.", "Early cancer "
                                                          "often has no symptoms."], mode="sentence")]}),
        6, 61, ids)
    # 散文句不落：那两条讲的是"症状"这件事，不是任何一个症状项，且 anchor 只有 main-content
    c.eq("pdq 散文句不落库", sentences, 2)

    def aborts(what: str, code: str, n: int, want: str) -> None:
        try:
            got = wikidata.wiki_drops(code, n)
            c.ok(what, False, f"没中止，返回 {len(got)} 条剔除")
        except SystemExit as e:
            ok = want in str(e)
            c.ok(what, ok, "" if ok else f"中止理由不含「{want}」：{e}")

    for code, (real, note) in wikidata.WIKI_EYEBALL.items():
        dropped = {i for _why, ids_ in wikidata.WIKI_DROP.get(code, ()) for i in ids_}
        c.ok(f"wiki_drops {code} 两份声明自洽 —— {note[:18]}",
             wikidata.wiki_drops(code, real + len(dropped)).keys() == dropped)
    aborts("wiki_drops 解析条数漂移就中止", "colorectum", 18, "不符")
    aborts("wiki_drops 序号越界就中止", "pancreas", 5, "超出")
    aborts("新冒出没人读过的清单不代判", "liver", 6, "目测")

    rec = {"title": "胰臟癌", "sec": "症狀及徵象", "n": 7,
           "items": [f"項{i}" for i in range(1, 8)], "why": ""}
    wrows = symptoms.wiki_rows({"pancreas": rec}, 16, {"pancreas": 6})
    c.eq("维基 剔除序号对上行", [r["review_status"] for r in wrows],
         ["spot_checked"] * 4 + ["rejected"] * 3)
    c.eq("维基行不挂发布", {r["dataset_release_id"] for r in wrows}, {None})
    c.eq("维基行的语种与锚点", {(r["name_lang"], r["anchor"]) for r in wrows}, {("zh", "症狀及徵象")})

    c.eq("WHO 症状节标题按同一正则取",
         who_factsheet.sym_heading({"sections": ["重要事实", "症状", "治疗"]}), "症状")
    c.eq("WHO 英文版 Symptoms 也认", who_factsheet.sym_heading({"sections": ["Overview", "Symptoms"]}),
         "Symptoms")
    c.eq("WHO 没有症状节就是空", who_factsheet.sym_heading({"sections": ["重要事实", "概述"]}), "")


def check_risks(c: Checks) -> None:
    """危险因素装载的口径。输入是合成的 Assoc / Factor 与手搓的 Risk 表：这一维的坑全在
    "一行该是什么"与"哪些行算这个病"，跟上游字节无关，而两份真归档（GWAS 整包 71 MB、
    CRA 交叉表）都不进仓库。真归档里的数由装载器跑库时自己报出来核对。"""
    def a(**kw):
        base = dict(code="lung", tier="main", tail="MONDO_0005238", trait="lung cancer",
                    gene="CHRNA5", snps="rs1", risk_allele="rs1-A", chr_id="15",
                    chr_pos="78885724", freq="0.34", p_value="1E-8", mlog="8.0",
                    or_beta="1.2", ci="[1.1-1.3]", pubmed="111", study="GCST001",
                    initial="", replication="", has_eff=True, has_ci=True)
        base.update(kw)
        return gwas_catalog.Assoc(**base)

    def kept(rows):
        return risks.kept_assocs(rows)

    # 幂等键的粒度是"一条关联"：一篇论文登记两个研究是两条（实测按 PUBMEDID 构造会把
    # 496 组这样的关联折掉），同一研究按两个 p 值报同一位点也是两条
    c.eq("assoc 一论文两研究不折行", len(kept([a(study="GCST001"), a(study="GCST002")])), 2)
    c.eq("assoc 同研究两个 p 值算两条", len(kept([a(p_value="1E-8"), a(p_value="3E-7")])), 2)
    # 只有只差一个连接号写法的那一组才该折，且留哪条由 keep_rank 定，不随行序变
    dash = a(ci="[1.11–1.23]", has_ci=False, order=0)
    plain = a(ci="[1.11-1.23]", has_ci=True, order=5)
    c.eq("assoc 只差连接号折成一条", len(kept([dash, plain])), 1)
    c.eq("assoc 折行留可解析区间那条", [x.ci for x in kept([dash, plain])], ["[1.11-1.23]"])
    c.eq("assoc 折行结果与行序无关", [x.ci for x in kept([plain, dash])], ["[1.11-1.23]"])
    c.eq("assoc 主条目档优先于声明档",
         [(x.assoc_key(), x.tier) for x in kept([a(tier="declared"), a()])][0][1], "main")
    c.eq("assoc 声明档与主条目同键所以只留一条",
         len({x.assoc_key() for x in kept([a(), a(tier="declared")])}), 1)

    c.eq("label 空基因退 SNPS", risks._label(a(gene="NR", snps="chr17:43124027")),
         "chr17:43124027")
    try:
        risks._label(a(gene="-", snps="NR"))
        c.ok("label 两个都空中止", False, "没中止，落了个空 label 的节点")
    except SystemExit as e:
        c.ok("label 两个都空中止", "label" in str(e), str(e))

    multi = a(gene="", snps="rs765899; rs737387", risk_allele="rs765899-?; rs737387-?",
              chr_id="14;14", chr_pos="68497029;68500665", ci="NR", freq="-",
              p_value="1E-245", pubmed="NR", tier="declared")
    rf = {("genetic_locus", "rs765899; rs737387"): 77}
    ids = {"lung": 1}
    row = risks.gwas_links([multi], rf, ids, 6, 61, ["lung"])[0]
    c.eq("link 档位与角色", (row["role"], row["uri_tier"], row["risk_factor_id"]),
         ("genetic", "declared", 77))
    # 多 SNP 行的 CHR_ID / CHR_POS 是分号串：原先按 int / varchar(4) 拍会截断或报错
    c.eq("link 分号串坐标原样落",
         (row["snps"], row["chr_id"], row["chr_pos"]),
         ("rs765899; rs737387", "14;14", "68497029;68500665"))
    c.eq("link 占位符落成空串或 NULL",
         (row["ci95_text"], row["risk_allele_freq"], row["pubmedid"], row["p_value_text"]),
         ("", None, 0, "1E-245"))
    c.eq("link 效应量共列不判方向", row["effect_kind"], "unknown")
    c.eq("link 效应量与 mlog 落成数值而非文本", (row["or_beta"], row["pvalue_mlog"]),
         (1.2, 8.0))
    c.eq("link 目测过的病记 spot_checked", row["review_status"], "spot_checked")
    c.eq("link 没目测的病不冒领",
         risks.gwas_links([multi], rf, ids, 6, 61, [])[0]["review_status"], "unreviewed")

    # CRA：Risk 表那四列是"这个组合有数"的标记，不是度量值
    hdr = ["Cause ID", "Cause", "REI ID", "Risk", *gbd_cra.MEASURE_COLS]

    def cell(cid, name, rid, flags):
        return [cid, f"cause{cid}", rid, name, *flags]

    pairs, names, numeric = gbd_cra._risk_table([hdr,
                                                 cell(426, "Smoking", 110, ["X", "", "", "X"]),
                                                 cell(426, "Smoking", 110, ["", "X", "X", ""]),
                                                 cell(426, "High alcohol use", 201,
                                                      ["X", "", "", ""])])
    c.eq("risk 同对多行取并集", pairs[426][110], ["1", "1", "1", "1"])
    c.eq("risk REI 名按 id 收", names, {110: "Smoking", 201: "High alcohol use"})
    c.eq("risk 存在性标记不算数值列", numeric, [])
    try:
        gbd_cra._risk_table([hdr[:4], [426, "cause426", 110, "Smoking"]])
        c.ok("risk 表头没有 Deaths 就中止", False, "没中止")
    except SystemExit as e:
        c.ok("risk 表头没有 Deaths 就中止", "Deaths" in str(e), str(e))

    kids = {1: [110, 111], 110: [], 111: []}
    c.eq("REI 树 父档后代", gbd_cra._descendants(1, kids), {110, 111})
    rs = {1: [], 110: [], 111: []}
    # 装载器的 own 表达式：有后代在本病清单里的档只是聚合展示，计了会和子档重复
    c.eq("REI 树 聚合父档被剔子档留下",
         [r for r in rs if not (gbd_cra._descendants(r, kids) & set(rs))], [110, 111])

    def factor(**kw):
        base = dict(code="lung", cause_id=426, cause_name="Lung cancer", rei_id=110,
                    rei_name="Smoking", deaths=True)
        base.update(kw)
        return gbd_cra.Factor(**base)

    f = risks.cra_links([factor()], {("exposure", "smoking"): 91}, ids, 7, 71, ["lung"])[0]
    c.eq("cra 行只有清单没有强度",
         (f["role"], f["uri_tier"], f["trait_uri"], f["snps"], f["or_beta"], f["pvalue_mlog"]),
         ("exposure", None, "GBD:426", "", None, None))
    c.eq("cra 行表型名是 GBD Cause 名", f["trait_label"], "Lung cancer")
    c.eq("cra 键按病因×REI 构造",
         len({factor(rei_id=110).assoc_key(), factor(rei_id=111).assoc_key()}), 2)
    c.eq("cra 病因不同才算两条",
         len({factor(cause_id=426).assoc_key(), factor(cause_id=427).assoc_key()}), 2)

    def aborts(what: str, fn, want: str) -> None:
        try:
            fn()
            c.ok(what, False, "没中止")
        except SystemExit as e:
            ok = want in str(e)
            c.ok(what, ok, "" if ok else f"中止理由不含「{want}」：{e}")

    aborts("GWAS 逐病数漂移就中止", lambda: risks.check_gwas([a()]), "漂移")
    aborts("CRA 风险因素换名就中止",
           lambda: risks.check_cra([factor(rei_name="Tobacco")]), "不再成立")
    aborts("CRA 条数漂移就中止",
           lambda: risks.check_cra([
               factor(rei_id=i, rei_name=n)
               for i, n in enumerate(sorted(gbd_cra.REI_EYEBALL))]), "条数漂移")


def check_research(c: Checks) -> None:
    """研究层装载（C2f）的判断。四源都是活源，行级取值每周都在变，所以这一页锁的是
    **归一化与中止行为**而不是条数：值域声明、幂等键粒度、阈值口径、归档往返。
    真归档里的那批数由装载器自己在跑库时报出来核对（`load --code research` 的 message）。"""
    lung = next(t for t in TARGETS if t.code == "lung")
    colon = next(t for t in TARGETS if t.code == "colorectum")
    breast = next(t for t in TARGETS if t.code == "breast_female")
    ids = {t.code: i + 1 for i, t in enumerate(TARGETS)}
    sid = {research.CT: 6, research.PMC: 7, research.OT: 8}
    rid = {research.CT_ROWS: 61, research.PMC_ROWS: 71,
           research.OT_ASSOC_ROWS: 81, research.OT_DRUG_ROWS: 82}

    def payload(**kw) -> research.Payload:
        p = research.Payload()
        p.recs = {ds: kw.get(ds, {}) for ds in rid}
        p.meta = {ds: {"version": "v", "release_date": None,
                       "stats": {"year_from": 2021, "pages": 1}, "dir": None, "sizes": {}}
                  for ds in rid}
        return p

    def aborts(what: str, fn, want: str) -> None:
        try:
            fn()
            c.ok(what, False, "没中止")
        except SystemExit as e:
            ok = want in str(e)
            c.ok(what, ok, "" if ok else f"中止理由不含「{want}」：{e}")

    def study(nct="NCT1", status="RECRUITING", why=None, no_hv=False) -> dict:
        elig = {"eligibilityCriteria": "Inclusion Criteria:\n- 成人", "sex": "All",
                "healthyVolunteers": False}
        if no_hv:
            elig.pop("healthyVolunteers")
        return {"protocolSection": {
            "identificationModule": {"nctId": nct, "officialTitle": "Non-Small Cell Lung Carcinoma",
                                     "briefTitle": "brief"},
            "statusModule": {"overallStatus": status, **({"whyStopped": why} if why else {})},
            "designModule": {"studyType": "Interventional", "phases": ["PHASE3"],
                             "designInfo": {"masking": "NONE"},
                             # 实测形状是 {count: 整数, type: "ESTIMATED"}，不是 value/citation
                             "enrollmentInfo": {"count": 120, "type": "ESTIMATED"}},
            "conditionsModule": {"conditions": ["Lung Neoplasms"]},
            "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "X"}],
                                        "armGroups": []},
            "outcomesModule": {"primaryOutcomes": [{"measure": "OS", "timeFrame": "2 years"},
                                                   {"measure": "PFS"}]},
            "eligibilityModule": elig,
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Site"}, "collaborators": []},
            "contactsLocationsModule": {"locations": [{"country": "China"}, {"country": "USA"},
                                                      {"country": "China"}]},
            "referencesModule": {"references": []},
            "oversightModule": {"isFdaRegulatedDrug": True}}}

    cut = research.Cutter()
    p = payload(**{research.CT_ROWS: {lung.code: {"hit": 2, "studies": [
        study(), study(nct="NCT2", why="因赞助方决定提前结束")]}}})
    rows = research.trial_rows(p, ids, sid[research.CT], rid[research.CT_ROWS], cut)
    c.eq("trial overallStatus 按声明归档", [r["status_bucket"] for r in rows],
         ["active", "active"])
    # 估算/实际这个标记只存在于源的 enrollmentInfo 对象里，整块挪进 design_info 才留得住
    c.eq("trial 招募数与估算标记同处一行",
         [(r["enrollment"], r["design_info"]["enrollmentInfo"]["type"]) for r in rows],
         [(120, "ESTIMATED")] * 2)
    c.eq("trial 入排标准按源形状存（JSON 字符串不是数组）",
         json.loads(rows[0]["eligibility"]), "Inclusion Criteria:\n- 成人")
    c.eq("trial 国家数组去重排序", rows[0]["location_countries"], ["China", "USA"])
    c.eq("trial 主要结局并成一句", rows[0]["primary_outcome"], "OS [2 years]; PFS")
    # 布尔列不拿 0 冒充"源没说"：False→0、缺字段→NULL 是两种事实
    c.eq("trial 终止原因照常抄", (rows[0]["why_stopped"], rows[1]["why_stopped"]),
         (None, "因赞助方决定提前结束"))
    # 声明词之间是子串关系时两个都算命中——这一列回答的是"哪几个词把它带出来的"
    c.eq("trial matched_terms 按子串命中", rows[0]["matched_terms"],
         ["Lung Neoplasms", "non-small cell lung carcinoma", "small cell lung carcinoma"])
    blank = research.trial_rows(
        payload(**{research.CT_ROWS: {lung.code: {"hit": 1, "studies": [study(no_hv=True)]}}}),
        ids, sid[research.CT], rid[research.CT_ROWS], cut)[0]
    c.eq("trial 布尔列区分未给", (blank["healthy_volunteers"], blank["fda_regulated"]),
         (None, 1))
    aborts("trial 冒出没读过的 overallStatus 就中止",
           lambda: research.trial_rows(payload(**{research.CT_ROWS: {
               lung.code: {"hit": 1, "studies": [study(status="SOMETHING_NEW")]}}}),
               ids, sid[research.CT], rid[research.CT_ROWS], research.Cutter()), "overallStatus")

    # EPMC：业务键三档退让，NULL 进唯一键会长双份
    c.eq("ext_key pmid 优先", research.ext_key({"pmid": "123", "doi": "10/a", "title": "T"}), "123")
    c.eq("ext_key 退 doi 且大写归一", research.ext_key({"doi": "10:a/b", "title": "T"}), "10:A/B")
    h1, h2 = research.ext_key({"title": "T one"}), research.ext_key({"title": "T two"})
    c.ok("ext_key 退标题哈希（同标题同键、异标题异键、前缀 t 不与 pmid 撞）",
         h1.startswith("t") and len(h1) == 33
         and h1 == research.ext_key({"title": " t ONE "}) and h1 != h2)
    aborts("ext_key 三个都空就中止", lambda: research.ext_key({"pmid": "  "}), "业务键")

    p = payload(**{research.PMC_ROWS: {lung.code: {"hit": 95927, "records": [
        {"pmid": "1", "doi": "10.x", "title": "Lung cancer immune therapy", "source": "MED",
         "journalInfo": {"journal": {"title": "Journal of clinical oncology"}},
         "pubYear": "2024", "isOpenAccess": "Y", "inEPMC": "N", "hasPDF": "Y",
         "abstractText": "lung cancer abstract"},
        # 预印本（source=PPR）整块 journalInfo 是 null：期刊名没有，不能拿库别代码顶上
        {"pmid": "2", "title": "Preprint on pan-cancer targets", "source": "PPR",
         "journalInfo": None, "pubYear": "2025"},
        {"pmid": "NR", "title": "无键记录"}]}}})
    prows = research.pub_rows(p, ids, sid[research.PMC], rid[research.PMC_ROWS], cut)
    c.eq("pub 非数字 pmid 不落整数、键退到哈希",
         (prows[2]["pmid"], prows[2]["ext_key"].startswith("t")), (None, True))
    # 顶层 source 是 MED/PPR/PMC/AGR 这种库别代码，当期刊名存进去，页面那一列就只有四个值
    c.eq("pub 期刊名取 journalInfo.journal.title，预印本留 NULL",
         [r["journal"] for r in prows], ["Journal of clinical oncology", None, None])
    c.eq("pub 记录级 flag 按 Y 判定",
         [(r["is_oa"], r["in_epmc"], r["has_pdf"], r["has_abstract"]) for r in prows],
         [(1, 0, 1, 1), (0, 0, 0, 0), (0, 0, 0, 0)])
    c.eq("pub 抄录行记 unreviewed", {r["review_status"] for r in prows}, {"unreviewed"})

    def assoc(tid, score, novelty=None):
        return {"score": score, "novelty": novelty,
                "target": {"id": tid, "approvedSymbol": tid[-3:], "approvedName": "n" + tid},
                "datasourceScores": [{"id": "egmn", "score": score}]}

    p = payload(**{research.OT_ASSOC_ROWS: {
        lung.code: {"node": "MONDO_0008903", "count": 643,
                    "rows": [assoc("ENSG1", 0.9015, 0.0001003876489362007), assoc("ENSG2", 0.1),
                             assoc("ENSG2", 0.5), assoc("ENSG3", 0.0999)]},
        colon.code: {"node": "MONDO_0004168", "count": 2, "rows": [assoc("ENSG1", 0.2)]}}})
    kept = research.assoc_rows(p)
    c.eq("assoc 阈值边界含 0.1（等于阈值算可讨论）、阈下不进库",
         [g["score"] for _t, _b, g in kept], [0.9015, 0.1, 0.5, 0.2])
    nodes = research.target_nodes(kept, sid[research.OT], rid[research.OT_ASSOC_ROWS], cut)
    # ENSG3 在归档里但过了阈值以外：节点只从落库的关系行长出来，否则留下一批没有边的孤儿
    c.eq("target 跨病去重成节点且不带阈下靶点", [n["ot_id"] for n in nodes], ["ENSG1", "ENSG2"])
    tg = {n["ot_id"]: i + 1 for i, n in enumerate(nodes)}
    links = research.assoc_links(kept, tg, ids, sid[research.OT], rid[research.OT_ASSOC_ROWS], cut)
    # uk 只到 (病,靶点,源)：一批里同键两行留给谁必须有规则，答案是留最强那条
    dup = [l for l in links if (l["disease_id"], l["target_id"]) == (ids[lung.code], tg["ENSG2"])]
    c.eq("assoc 同病同靶点多行收成一行、留最高分",
         (len(dup), dup[0]["score"]), (1, 0.5))
    c.eq("assoc novelty 按 6 位存", links[0]["novelty"], 0.0001)
    c.eq("assoc 记查询用的节点", {l["node_used"] for l in links},
         {"MONDO_0008903", "MONDO_0004168"})
    aborts("assoc 指向没落的靶点就中止",
           lambda: research.assoc_links(kept, {"ENSG1": 1}, ids, sid[research.OT], 1, research.Cutter()),
           "节点表里没有")
    one = research.assoc_rows(payload(**{research.OT_ASSOC_ROWS: {
        lung.code: {"node": "MONDO_0008903", "count": 1,
                    "rows": [{"score": 0.5, "novelty": None, "target": {"id": "ENSG1"},
                              "datasourceScores": []}]}}}))
    lone = research.assoc_links(one, {"ENSG1": 1}, ids, sid[research.OT], 1, research.Cutter())[0]
    c.eq("assoc 空 datasourceScores 落 NULL 不落 []", lone["datasource_scores"], None)

    def drug(name, stage, moas=(), did="CHEMBL1"):
        return {"id": "h" + name + stage, "maxClinicalStage": stage,
                "drug": {"id": did, "name": name, "drugType": "approved",
                         "mechanismsOfAction": {"rows": [{"mechanismOfAction": m} for m in moas]}}}

    p = payload(**{research.OT_DRUG_ROWS: {
        lung.code: {"count": 5, "rows": [drug("X", "PHASE_1_2", ["EGFR inhibitor"]),
                                         drug("x", "PHASE_1_2", ["TKI"]),
                                         drug("X", "PHASE_3"), drug("Y", "PRECLINICAL"),
                                         drug("Z", "PHASE_2")]},
        breast.code: {"count": 1, "rows": [drug("X", "PHASE_3")]}}})
    drows = research.drug_rows(p, ids, sid[research.OT], rid[research.OT_DRUG_ROWS], cut)
    c.eq("drug 按 (病,药,阶段) 收拢且药名不分大小写",
         [(r["disease_id"], r["drug_name"], r["phase"]) for r in drows],
         [(ids[breast.code], "X", "PHASE_3"), (ids[lung.code], "X", "PHASE_1_2"),
          (ids[lung.code], "X", "PHASE_3"), (ids[lung.code], "Y", "PRECLINICAL"),
          (ids[lung.code], "Z", "PHASE_2")])
    c.eq("drug 同键多机制并成数组", drows[1]["moa"], ["EGFR inhibitor", "TKI"])
    c.eq("drug 没机制落 NULL 不落 []", drows[2]["moa"], None)
    # 实测最长是前列腺癌一行 193 字符的描述性药名；旧列宽 191 在 uk 之前截尾巴，
    # 截断就不只是少几个字，而是把两个不同的药折成同一个键
    long_name = "A" * 193
    c.eq("drug 193 字符药名整名落库",
         [len(r["drug_name"]) for r in research.drug_rows(
             payload(**{research.OT_DRUG_ROWS: {
                 lung.code: {"count": 1, "rows": [drug(long_name, "PHASE_2")]}}}),
             ids, sid[research.OT], 1, research.Cutter())], [193])
    aborts("drug 冒出没读过的阶段就中止",
           lambda: research.drug_rows(payload(**{research.OT_DRUG_ROWS: {
               lung.code: {"count": 1, "rows": [drug("X", "PHASE_9")]}}}),
               ids, sid[research.OT], 1, research.Cutter()), "maxClinicalStage")
    aborts("drug 没名字进不了 uk_drug 就中止",
           lambda: research.drug_rows(payload(**{research.OT_DRUG_ROWS: {
               lung.code: {"count": 1, "rows": [{"maxClinicalStage": "PHASE_2",
                                                 "drug": {"name": " "}}]}}}),
               ids, sid[research.OT], 1, research.Cutter()), "没名字")

    def seed(blank, over=None):
        """计数是逐病取的，所以载荷必须 18 病齐全；其余病按"取到了但命中 0"铺占位。"""
        return {t.code: (over or {}).get(t.code, blank) for t in TARGETS}

    p = payload(
        **{research.CT_ROWS: seed({"hit": 0, "studies": []},
                                  {lung.code: {"hit": 3182, "studies": [study()]}})},
        **{research.PMC_ROWS: seed({"hit": 0, "records": []},
                                   {lung.code: {"hit": 95927, "records": [{"pmid": "1"}]}})},
        **{research.OT_ASSOC_ROWS: seed({"node": "", "count": 0, "rows": []},
                                        {lung.code: {"node": "MONDO_0008903", "count": 643,
                                                     "rows": [assoc("ENSG1", 0.5),
                                                              assoc("ENSG2", 0.02)]}})},
        **{research.OT_DRUG_ROWS: seed({"count": 0, "rows": []},
                                       {lung.code: {"count": 4, "rows": [
                                           drug("X", "PHASE_3"), drug("x", "PHASE_2"),
                                           drug("Y", "PHASE_3"), drug("Y", "PHASE_3")]}})})
    krows = research.assoc_rows(p)
    facts = research.query_facts(
        p, ids, rid, sid, cut,
        research.assoc_links(krows, {"ENSG1": 1}, ids, sid[research.OT],
                             rid[research.OT_ASSOC_ROWS], cut),
        research.drug_rows(p, ids, sid[research.OT], rid[research.OT_DRUG_ROWS], cut))
    lung_facts = [f for f in facts if f["disease_id"] == ids[lung.code]]
    by = {f["metric"]: f for f in lung_facts}
    c.eq("query_count 每病四行、一病不缺", len(facts), 4 * len(TARGETS))
    # 四个数各按各的口径：命中数、命中数、进库行数、去重到药名——不是一个"研究总量"的四个拆法
    c.eq("query_count 四个数各按各的口径", {m: f["value"] for m, f in by.items()},
         {"trial_count": 3182, "publication_count": 95927, "target_count": 1, "drug_count": 2})
    c.eq("query_count 全部 year=0 且 basis=query_count",
         {(f["year"], f["estimate_basis"], f["unit"]) for f in facts},
         {(0, "query_count", "count")})
    c.eq("query_count 各挂自己那份数据集的发布",
         {f["dataset_release_id"] for f in facts}, set(rid.values()))
    c.ok("query_count 的注不超 128 列宽", all(len(f["cohort_note"]) <= 128 for f in facts))
    c.ok("drug_count 的注带着源给的三元组行数", "给 4 行" in by["drug_count"]["cohort_note"])
    aborts("计数没取到就中止，不补 0",
           lambda: research.query_facts(payload(), ids, rid, sid, cut, [], []), "没有计数")

    # 归档往返：--offline 那一趟全靠这条闭环，所以它必须在不打网络的前提下被测到
    keep, tmp = raw.DATA_RAW, Path(tempfile.mkdtemp())
    try:
        raw.DATA_RAW = tmp
        got = {lung.code: {"hit": 1, "studies": [study()]}}
        p2 = research.Payload()
        research._archive(p2, research.CT, research.CT_ROWS, "trial", got,
                          version="2026-09-04T09:00:06", key="rows-2026-09-04",
                          release_date="2026-09-04", stats={"pages": 1, "year_from": 2021})
        p3 = research.Payload()
        research._replay(p3, research.CT, research.CT_ROWS, "trial")
        c.eq("归档往返回到解析前的同一份行", p3.recs[research.CT_ROWS], got)
        c.eq("归档往返复现版本与统计",
             (p3.meta[research.CT_ROWS]["version"], p3.meta[research.CT_ROWS]["stats"]["pages"]),
             ("2026-09-04T09:00:06", 1))
        c.eq("同一目录的 sha 稳定（重放不改凭证）",
             research._dir_sha(p3.meta[research.CT_ROWS]["dir"]),
             research._dir_sha(p2.meta[research.CT_ROWS]["dir"]))
        aborts("归档目录与数据集对不上就中止",
               lambda: research._replay(research.Payload(), research.CT,
                                        "other-dataset-code", "trial"), "对不上")
    finally:
        raw.DATA_RAW = keep
        shutil.rmtree(tmp, ignore_errors=True)


def check_research_fixtures(c: Checks) -> None:
    """研究层四份 fixture：每份是一病归档的前 3 条**上游原样记录**（整份归档在 data/raw 不进仓库）。

    这一页不判条数也不判行为（那些在 `check_research`），只判一件事：字段真的在装载器读的那个
    路径上。所以每条期望值都从归档 JSON 反推，不写死——上游哪天改名或挪层，反推就会对不上。"""
    d = FIXTURES / "research"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    by_ds = {info["dataset_code"]: (name, info) for name, info in man["files"].items()}
    ids = {t.code: i + 1 for i, t in enumerate(TARGETS)}
    cut = research.Cutter()

    def slice_of(ds: str, arr: str):
        name, info = by_ds[ds]
        body = (d / name).read_bytes()
        c.eq(f"research fixture {name} 字节未改写",
             (len(body), hashlib.sha256(body).hexdigest()), (info["bytes"], info["sha256"]))
        blk = json.loads(body.decode("utf-8"))
        code = info["disease"]
        p = research.Payload()
        p.recs = {k: ({code: blk} if k == ds else {}) for k in by_ds}
        return next(t for t in TARGETS if t.code == code), blk, blk[arr], p

    # 一、CT：三档在招真的各在源里有一条，enrollment 的键名不是猜的
    t, blk, studies, p = slice_of(research.CT_ROWS, "studies")
    rows = research.trial_rows(p, ids, 6, 61, cut)
    c.eq("真归档 study 与 trial 行一对一行", len(rows), len(studies))
    active = {k for k, v in research.STATUS_BUCKET.items() if v == "active"}
    c.eq("真归档三条恰好覆盖在招三档且全落 active",
         ({r["overall_status"] for r in rows}, {r["status_bucket"] for r in rows}),
         (active, {"active"}))
    enrs = [s["protocolSection"]["designModule"]["enrollmentInfo"] for s in studies]
    c.ok("真归档 enrollmentInfo 的形状就是 {count, type}（没有 value 这个键）",
         all(set(e) == {"count", "type"} for e in enrs))
    c.eq("真归档 enrollment 取的就是 count、估算标记随行留在 design_info",
         [(r["enrollment"], r["design_info"]["enrollmentInfo"]["type"]) for r in rows],
         [(e["count"], e["type"]) for e in enrs])
    c.eq("真归档在招试验的入组数全是估算（一个 ACTUAL 都没有）",
         sorted({e["type"] for e in enrs}), ["ESTIMATED"])
    terms = set(t.search_terms)
    c.ok("真归档每条 study 都被声明词带到、matched_terms 不含声明外的词",
         all(r["matched_terms"] and set(r["matched_terms"]) <= terms for r in rows))
    c.ok("真归档有声明词是另一些词的子串，同一行因此命中多词",
         any(len(r["matched_terms"]) > 1 for r in rows))
    crit = [(s["protocolSection"].get("eligibilityModule") or {}).get("eligibilityCriteria")
            for s in studies]
    c.eq("真归档入排标准整段原样存（JSON 字符串，不切成条目数组）",
         [json.loads(r["eligibility"]) if r["eligibility"] else None for r in rows],
         [str(v or "").strip() or None for v in crit])
    locs = [sorted({l.get("country") for l in
                    (s["protocolSection"].get("contactsLocationsModule") or {}).get("locations") or []
                    if l.get("country")}) for s in studies]
    c.eq("真归档地点国家去重排序", [r["location_countries"] for r in rows], locs)
    outcomes = [((s["protocolSection"].get("outcomesModule") or {}).get("primaryOutcomes")) or []
                for s in studies]
    c.eq("真归档主要结局并句的形状（measure [timeFrame]，分号连，不裁 measure）",
         [r["primary_outcome"] for r in rows],
         ["; ".join(f"{o.get('measure')} [{o['timeFrame']}]" if o.get("timeFrame")
                    else str(o.get("measure")) for o in os) for os in outcomes])
    c.eq("真归档三条都没给衍生文献：publications 落 NULL 不落 []",
         [r["publications"] for r in rows], [None] * len(rows))
    c.eq("真归档只落在招，why_stopped 整列空", [r["why_stopped"] for r in rows],
         [None] * len(rows))

    # 二、EPMC：期刊名在 journalInfo.journal.title，顶层 source 是库别代码不是期刊
    t, blk, recs, p = slice_of(research.PMC_ROWS, "records")
    prows = research.pub_rows(p, ids, 7, 71, cut)
    c.eq("真归档 record 与 publication 行一对一行", len(prows), len(recs))

    def title_of(r):
        ji = r.get("journalInfo") or {}
        return str((ji.get("journal") or {}).get("title") or "").strip() or None

    c.eq("真归档期刊名与 journalInfo.journal.title 逐字一致",
         [r["journal"] for r in prows], [title_of(r) for r in recs])
    codes = {"MED", "PPR", "PMC", "AGR"}
    c.ok("真归档期刊名不是 EPMC 的库别代码（第一版照字段名存了顶层 source）",
         all(r["journal"] not in codes for r in prows)
         and all(r["journal"] != s.get("source") for r, s in zip(prows, recs)))
    c.ok("真归档每条记录都带 journalInfo.journal.title", all(title_of(r) for r in recs))
    c.eq("真归档三条都有数字 pmid，业务键用不到 doi 与标题哈希",
         [(r["pmid"], r["ext_key"]) for r in prows],
         [(int(r["pmid"]), str(r["pmid"])) for r in recs])
    c.eq("真归档三个全文标记按记录级 Y 落 1/0",
         [(r["is_oa"], r["in_epmc"], r["has_pdf"]) for r in prows],
         [tuple(1 if str(r.get(k, "")).upper() == "Y" else 0
                for k in ("isOpenAccess", "inEPMC", "hasPDF")) for r in recs])
    c.eq("真归档 pub_year 取 pubYear 原样",
         [r["pub_year"] for r in prows], [int(r["pubYear"]) for r in recs])
    c.eq("真归档 publication 是抄录，记 unreviewed",
         {r["review_status"] for r in prows}, {"unreviewed"})

    # 三、OT 关联：节点、分数位数与查询节点名
    t, blk, arecs, p = slice_of(research.OT_ASSOC_ROWS, "rows")
    kept = research.assoc_rows(p)
    c.eq("真归档阈值以上的行全部留下", len(kept),
         len([g for g in arecs if float(g["score"]) >= research.ASSOC_MIN_SCORE]))
    nodes = research.target_nodes(kept, 8, 81, cut)
    raw = {g["target"]["id"]: g for g in arecs}
    c.eq("真归档一个靶点一个节点", sorted(n["ot_id"] for n in nodes), sorted(raw))
    byid = {n["ot_id"]: n for n in nodes}
    c.eq("真归档 symbol/name 抄 approvedSymbol/approvedName",
         [(byid[i]["approved_symbol"], byid[i]["approved_name"]) for i in sorted(raw)],
         [(raw[i]["target"]["approvedSymbol"], raw[i]["target"]["approvedName"])
          for i in sorted(raw)])
    tg = {n["ot_id"]: i + 1 for i, n in enumerate(nodes)}
    links = {l["target_id"]: l for l in research.assoc_links(kept, tg, ids, 8, 81, cut)}
    c.eq("真归档 score 保 4 位、novelty 保 6 位",
         [(links[tg[i]]["score"], links[tg[i]]["novelty"]) for i in sorted(raw)],
         [(round(float(raw[i]["score"]), 4), round(float(raw[i]["novelty"]), 6))
          for i in sorted(raw)])
    c.eq("真归档 datasourceScores 整份留成数组，不折成计数",
         [len(links[tg[i]]["datasource_scores"]) for i in sorted(raw)],
         [len(raw[i]["datasourceScores"]) for i in sorted(raw)])
    c.eq("真归档 node_used 记的是这一病查询用的节点", {l["node_used"] for l in links.values()},
         {blk["node"]})
    # 乳腺癌是 B7c 换过的宽档：归档里那个节点必须就是 targets 声明的，否则整维查错病
    c.eq("真归档的宽档节点与 targets 声明同一个", blk["node"], ot_node(t).replace(":", "_"))

    # 四、OT 药：药名大小写、机制 null 与列宽
    t, blk, drecs, p = slice_of(research.OT_DRUG_ROWS, "rows")
    drows = research.drug_rows(p, ids, 8, 82, cut)
    c.eq("真归档三条药收成三行（(病,药,阶段) 这一份没撞键）", len(drows), len(drecs))
    c.eq("真归档药名原样存（源里全是大写，按小写收拢只用于键）",
         sorted(r["drug_name"] for r in drows), sorted(g["drug"]["name"] for g in drecs))
    c.eq("真归档 CHEMBL 码与阶段原样抄",
         sorted((r["drug_id"], r["phase"]) for r in drows),
         sorted((g["drug"]["id"], g["maxClinicalStage"]) for g in drecs))
    c.ok("真归档阶段全在 STAGES 声明内", all(r["phase"] in research.STAGES for r in drows))
    named = {g["drug"]["name"]: [m["mechanismOfAction"]
             for m in ((g["drug"].get("mechanismsOfAction") or {}).get("rows")) or []]
             for g in drecs}
    c.ok("真归档确有 mechanismsOfAction 为 null 的药（源里就是 null，不是空数组）",
         any(not v for v in named.values()))
    c.eq("真归档没机制的那行落 NULL",
         {k: v for k, v in ((r["drug_name"], r["moa"]) for r in drows) if not v},
         {k: None for k, v in named.items() if not v})
    c.eq("真归档机制数组逐条抄 mechanismOfAction 原文",
         {r["drug_name"]: r["moa"] for r in drows if r["moa"]},
         {k: v for k, v in named.items() if v})
    c.eq("四份 fixture 无一列触到列宽（触到就是列宽又拍小了）", cut.brief(), "无")


CHECKS = (("seer_statfacts", check_seer), ("anatomy_subsites", check_subsites),
          ("symptom_rows", check_symptoms), ("risk_rows", check_risks),
          ("research_rows", check_research), ("research_fixtures", check_research_fixtures))


def main() -> int:
    c = Checks()
    for _, fn in CHECKS:
        try:
            fn(c)
        except Exception as e:  # noqa: BLE001 - 解析器抛错就是回归失败，不是跑测器坏了
            c.items.append((fn.__name__, False, f"抛异常 {type(e).__name__}: {e}"))
    bad = 0
    for what, ok, note in c.items:
        if not ok:
            bad += 1
        print(("PASS  " if ok else "FAIL  ") + what + (f" —— {note}" if note else ""))
    print(f"\n{len(c.items) - bad}/{len(c.items)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
