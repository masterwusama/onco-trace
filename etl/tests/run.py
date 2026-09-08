#!/usr/bin/env python
"""解析回归跑测器：拿仓库里的 fixture 页重跑探针的解析器，结构事实必须与判据依赖的一致。

    python etl/tests/run.py        # 跑全部；退出码非 0 即有 FAIL（不需要 pytest）

fixture 是上游原样字节（`etl/tests/fixtures/` 在 `.gitattributes` 里按二进制处理，
连换行都不改），所以这一跑兼作漂移检测：SEER 改了表结构，这里先红，
而不是等覆盖度文档里莫名少一页。`manifest.json` 记着每页的 sha256、来源版本
与"为什么是这一页"，跑测器先核字节再核解析结果。

归档该不该进仓库是按体积逐源判的：MONDO 51 MB、GWAS 整包 71 MB 不进，
SEER 四页 340 KB 进——判据分支每支有一页在场上就够。

症状那一页是唯一的例外，它不吃 fixture：三源的归档（PDQ 88 KB、WHO 27 KB、维基条目）都在
`data/raw` 里而不进仓库，而它要锁的是装载器那两条判定规则的**行为**，不是上游字节，
所以输入是合成的解析记录。真归档里的条数由装载器自己在跑库时报出来核对
（`load --code symptoms` 的 message 就是那份对账单）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，中文断言输出会变乱码
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "etl"))

from onco_etl.load import anatomy, symptoms  # noqa: E402
from onco_etl.targets import TARGETS  # noqa: E402
from onco_etl.probes import mondo  # noqa: E402
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


CHECKS = (("seer_statfacts", check_seer), ("anatomy_subsites", check_subsites),
          ("symptom_rows", check_symptoms))


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
