"""SEER Cancer Stat Facts 探针：统计层与生存率两维，18 病各一个 HTML 页。

页面里可抓的表 SEER 自己标了记号：`class="scrapeTable"`、`id="scrapeTable_NN"`。
但 NN 的编号和表在页面里的出现顺序不一致（实测 lungb 是 _01 _02 _04 _05 _03 _07 _08 _06），
所以不能按编号取表——只能按文档顺序走一遍，用最近的那个 `<strong>` 标题来认领每张表。
表的 `<h5>Males/Females</h5>` 性别标签也不能单独用：它在年龄分布表上会串到前一张表的性别。

八张表只有三种形状，行/表头必须按 thead 与 tbody 的结构切，不能靠"第一行像不像表头"猜：
  年度序列（_01）表头两行——第一行是 4 个 metric 各 colspan=2，第二行是 Observed / Modeled Trend，
    tbody 50 行。按 rows[1:] 切会把第二行表头当成一条数据，每个 metric 的观测年数都虚高 1。
    Observed 与 Modeled Trend 必须分成两列落库：后者是 SEER 的拟合线，不是观测值。
  分期（_02）与年龄分布（_03 _06）表头一行、tbody N 行。
  种族费率（_04 _05 _07 _08）压根没有 thead，6 行全是数据，按 rows[1:] 切会白扔 "All Races"。

每页的口径是分裂的，这一点必须原样带出来而不是取一个代表值：
年度序列的脚注写 "New cases come from SEER 12 ... All Races, Both Sexes"，
年龄分布表上方写 "SEER 21 2019–2023, Age-Adjusted"（发病）与 "U.S. 2020–2024"（死亡），
生存率分期表是另一个年份窗。同一页里三套 cohort，落 stat_cohort 时按 metric 各记各的。
"""
from __future__ import annotations

import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

from lxml import html as LH

from .. import raw
from ..fetch import fetch
from ..targets import BY_CODE, TARGETS
from .result import ProbeResult

SOURCE = "seer_statfacts"
DATASET = "statfacts-html"
BASE = "https://seer.cancer.gov/statfacts/html/"
CRITERIA = (
    "18 病各有 Stat Facts 页；每页年度序列 ≥5 年、≥1 个 metric；"
    "生存率 ≥3 个分期档（白血病豁免：SEER 未为其发布分期表）；"
    "费率表的性别证据与 targets.py 声明的 sex 一致；年龄组 <10 档只上报不卡判据"
)

# target.code → SEER 的页面短名。这是源自己的命名，跟 ICD 无关，只能声明；
# 18 个都实测过 h1 标题与 targets 的语义对得上（corp 的 h1 是 "Uterine Cancer"，
# SEER 的 Uterine Cancer 口径本就只含子宫体、不含宫颈，但页面标题不写这一点）
SLUG = {
    "lung": "lungb",
    "colorectum": "colorect",
    "liver": "livibd",
    "stomach": "stomach",
    "breast_female": "breast",
    "pancreas": "pancreas",
    "esophagus": "esoph",
    "prostate": "prost",
    "cervix": "cervix",
    "ovary": "ovary",
    "thyroid": "thyro",
    "bladder": "urinb",
    "kidney": "kidrp",
    "brain": "brain",
    "uterus": "corp",
    "leukemia": "leuks",
    "nhl": "nhl",
    "myeloma": "mulmy",
}

# 表标题 → 这张表填哪一维。只认白名单里的标题，别的 <strong>（表格单元格里也有
# <strong>，例如 "Non-Hispanic White"）一律忽略，否则认领关系会被单元格文本带跑
TITLES = (
    (re.compile(r"Rate of New Cases per 100,000 Persons by Race/Ethnicity & Sex"), "incidence_rate"),
    (re.compile(r"Death Rate per 100,000 Persons by Race/Ethnicity & Sex"), "mortality_rate"),
    (re.compile(r"Percent of New Cases by Age Group"), "age_incidence"),
    (re.compile(r"Percent of Deaths by Age Group"), "age_mortality"),
    (re.compile(r"5-Year Relative Survival by Stage at Diagnosis"), "stage_survival"),
)
SEX_RE = re.compile(r"^(Males|Females)$")

# SEER 没给白血病发布 "5-Year Relative Survival by Stage at Diagnosis"：实测 leuks.html
# 全文找不到这个标题，scrapeTable 只有 7 张（其余 17 页都是 8 张），生存率只给一条
# 1975–2018 的时间序列。白血病是血液肿瘤，本来就没有实体瘤分期，这是源的事实而非解析失败，
# 所以按页豁免。不能按 category='heme' 豁免——NHL 与骨髓瘤同为 heme，却各有 Ann Arbor
# 分期表（实测 5 档、4 档）。
# 豁免名单要反过来盯着：上游哪天补了这张表，这里就得把它捡回来，所以豁免页若真解析出
# 分期档会在消息里点名，而不是悄悄多算一页。
NO_STAGE = frozenset({"leukemia"})

# targets.py 的 sex → 页面上该出现的性别证据
SEX_OF = {"both": ("Males", "Females"), "female": ("Females",), "male": ("Males",)}


def _clean(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip()


def _row(tr) -> list[str]:
    return [_clean(c) for c in tr.xpath("./th|./td")]


def _split(table) -> tuple[list[list[str]], list[list[str]]]:
    """(表头行, 数据行)。按 thead/tbody 的结构切，不靠"第一行像不像表头"猜。

    两种错法都实测过：
      年度序列表的表头有两行（第一行 metric 名 colspan=2，第二行 Observed/Modeled Trend），
      按 rows[1:] 切会把第二行表头当成一条数据，每个 metric 的观测年数都虚高 1；
      种族费率表根本没有 thead，6 行全是数据，按 rows[1:] 切会白扔掉 "All Races" 那一行。
    """
    head = [r for r in (_row(tr) for tr in table.xpath("./thead/tr")) if r]
    body = [r for r in (_row(tr) for tr in table.xpath("./tbody/tr")) if r]
    if not head and not body:
        body = [r for r in (_row(tr) for tr in table.xpath("./tr")) if r]
    return head, body


def _obs(body_rows: list[list[str]], i: int) -> dict:
    """第 i 个数据列（body 索引 i+1）的观测年数与首末年。

    '-' 是 SEER 的"无观测值"占位符，不是 0。按年份数而不是按行数，
    才看得出各 metric 的观测窗长短不一——生存率就比发病率短一截。
    """
    yrs = [
        r[0]
        for r in body_rows
        if len(r) > i + 1 and r[0].isdigit() and r[i + 1].strip() not in ("", "-")
    ]
    return {"n": len(yrs), "y0": yrs[0] if yrs else None, "y1": yrs[-1] if yrs else None}


def parse_page(body: bytes) -> dict:
    """按文档顺序认领每张 scrapeTable，返回这一页量到的东西。"""
    doc = LH.fromstring(body.decode("utf-8", "replace"))
    page: dict = {
        "h1": "",
        "year_series": None,
        "stage_survival": None,
        "age_incidence": None,
        "age_mortality": None,
        "rate_sexes": set(),
        "race_groups": [],
        "vintages": [],
    }
    h1 = doc.xpath("//h1")
    if h1:
        page["h1"] = _clean(h1[0])

    kind = sex = ""
    for el in doc.iter():
        if el.tag in ("h2", "h3"):
            kind = sex = ""  # 换小节了，前面认领到的标题与性别都不再有效
        elif el.tag == "strong":
            t = _clean(el)
            for rx, k in TITLES:
                if rx.search(t):
                    kind, sex = k, ""
                    break
        elif el.tag == "h5":
            t = _clean(el)
            if SEX_RE.match(t):
                sex = t
        elif el.tag == "p":
            # 表上方的口径行，形如 "SEER 21 2019–2023, Age-Adjusted"
            t = _clean(el)
            if re.match(r"^(SEER|U\.S\.)\s", t) and len(t) < 80 and t not in page["vintages"]:
                page["vintages"].append(t)
        elif el.tag == "table" and str(el.get("id", "")).startswith("scrapeTable"):
            head, body_rows = _split(el)
            hdr = head[0] if head else []
            sub = head[1] if len(head) > 1 else []
            if hdr and hdr[0] == "Year":
                mets = [h for h in hdr[1:] if h]
                # 第一行表头每个 metric 是 colspan=2，第二行把它拆成 Observed / Modeled Trend。
                # Modeled Trend 是 SEER 的拟合线不是观测值，落 stat_cohort 时必须与 Observed
                # 分成两列，否则"表里有 50 行"会被当成"有 50 个观测"——最近两三年常常只有拟合值。
                # 配对只能按位置 zip：写成 for m in mets for s in sub 会得到 4×8=32 个重名组合，
                # 字典推导一折叠，四个 metric 里三个的观测年数全成 0（实测踩过）。
                cols = (
                    [f"{m} — {sub[2 * k + j]}" for k, m in enumerate(mets) for j in (0, 1)]
                    if len(sub) == 2 * len(mets)
                    else mets
                )
                ys = [r[0] for r in body_rows if r and r[0].isdigit()]
                page["year_series"] = {
                    "metrics": mets,
                    "n_years": len(ys),
                    "y0": ys[0] if ys else None,
                    "y1": ys[-1] if ys else None,
                    "observed": {c: _obs(body_rows, i) for i, c in enumerate(cols)},
                }
            elif kind == "stage_survival":
                page["stage_survival"] = {
                    "n": len(body_rows),
                    "labels": [r[0] for r in body_rows if r],
                }
            elif kind in ("age_incidence", "age_mortality"):
                page[kind] = {"n": len(body_rows), "bands": [r[0] for r in body_rows if r]}
            elif kind in ("incidence_rate", "mortality_rate"):
                if sex:
                    page["rate_sexes"].add(sex)
                # 这两张表没有表头行，6 行是种族/民族分组，性别拆成 Males/Females 两张表
                # （各自的 <h5> 标签紧邻在表前）。记下来是给 stat_cohort 映射当字段清单用。
                if kind == "incidence_rate" and not page["race_groups"]:
                    page["race_groups"] = [r[0] for r in body_rows if r]
    return page


def _last_modified(res) -> str | None:
    if not res.last_modified:
        return None
    try:
        return parsedate_to_datetime(res.last_modified).date().isoformat()
    except (TypeError, ValueError):
        return None


def sex_verdict(code: str, page: dict) -> str:
    """这一页的性别证据够不够，返回 ok / missing / conflict。

    两条证据链都要认：非性别特异癌 SEER 按 Males/Females 各出一张费率表（带 <h5> 标签），
    性别特异癌只出一张表、页面上根本没有 <h5> 性别标签，性别只写在口径行里
    （实测宫颈是 "SEER 21 2019–2023, All Races, Females"）。
    第一版判据写的是"双性别齐备"，18 页里判掉 5 页——被判掉的恰好是乳腺/宫颈/卵巢/
    子宫体/前列腺这 5 个性别特异癌，是判据错了不是数据缺。

    conflict 单独一档：口径行里出现了对立性别，说明 targets.py 的 sex 声明写错了。
    那是本仓库的 bug 而非源的缺陷，和 missing 混报会让人去查错的那一边。
    """
    want = SEX_OF[BY_CODE[code].sex]
    other = [s for s in ("Males", "Females") if s not in want]
    vintages = page["vintages"]
    if any(s in v for v in vintages for s in other):
        return "conflict"
    # "Females" 不含子串 "Males"（大小写不同），所以这个包含判断不会自己撞上自己
    if all(s in page["rate_sexes"] or any(s in v for v in vintages) for s in want):
        return "ok"
    return "missing"


def probe(offline: bool = False) -> ProbeResult:
    unknown = [t.code for t in TARGETS if t.code not in SLUG]
    if unknown:
        raise SystemExit(f"SLUG 里缺这些 target：{unknown}")

    key_dir: Path | None = None
    if offline:
        # 必须限定"目录里真的有 .html"：probe-reach 会把 seer_statfacts.bin 归档到
        # 同一个源目录下的 reach-<日期>/，它一跑就比专项归档新，纯按 mtime 挑会挑中那个空壳，
        # 18 页全找不到之后被判成 dead——给覆盖度留下一条源已死的假消息
        key_dir = raw.newest_dir(SOURCE, "*.html")
        if not key_dir:
            raise SystemExit(
                f"离线重放需要先有一份归档：data/raw/{SOURCE}/<version>/*.html 不存在。"
                "本仓库只检了 4 页代表页做解析回归（etl/tests/fixtures/seer_statfacts/，"
                "跑 `python etl/tests/run.py`，不需要归档），18 页的覆盖裁定必须实跑重取"
            )

    pages: dict[str, dict] = {}
    dead: list[str] = []
    blocked: list[str] = []
    bodies: dict[str, bytes] = {}
    lms: list[str] = []
    total_bytes = 0
    reach = "offline" if offline else "direct"
    worst_ms = 0
    statuses: set[int] = set()

    for t in TARGETS:
        slug = SLUG[t.code]
        if offline:
            p = key_dir / f"{slug}.html"
            if not p.is_file():
                dead.append(f"{t.code}(缺归档 {p.name})")
                continue
            body = p.read_bytes()
        else:
            res = fetch(BASE + slug + ".html", timeout=(10, 60))
            worst_ms = max(worst_ms, res.latency_ms)
            if res.status:
                statuses.add(res.status)
            if res.reachability == "proxy":
                reach = "proxy"
            if res.status in (404, 410):
                dead.append(f"{t.code}({slug})")
                continue
            if not res.ok or not res.body:
                blocked.append(f"{t.code}({res.status or res.reachability})")
                continue
            body = res.body
            lm = _last_modified(res)
            if lm:
                lms.append(lm)
        bodies[slug] = body
        total_bytes += len(body)
        pages[t.code] = parse_page(body)
        if not offline:
            time.sleep(0.4)

    if not pages:
        return ProbeResult(
            verdict="dead" if dead else "blocked",
            message=f"{len(TARGETS)} 页一页都没取到：dead={dead} blocked={blocked}",
            criteria=CRITERIA,
            dataset_code=DATASET,
            reachability=reach,
        )

    version = max(lms) if lms else (key_dir.name if offline else "")
    if not offline and bodies:
        d = raw.archive_dir(SOURCE, version or "unknown")
        for slug, b in sorted(bodies.items()):
            (d / f"{slug}.html").write_bytes(b)
        key_dir = d

    # ---- 逐维判定 ----
    ys_ok = [c for c, p in pages.items() if p["year_series"] and p["year_series"]["n_years"] >= 5]
    stage_pages = [c for c in pages if c not in NO_STAGE]
    st_ok = [c for c in stage_pages if pages[c]["stage_survival"] and pages[c]["stage_survival"]["n"] >= 3]
    st_unexpected = sorted(c for c in NO_STAGE if c in pages and pages[c]["stage_survival"])
    sex_stat = {c: sex_verdict(c, p) for c, p in pages.items()}
    sex_ok = [c for c, v in sex_stat.items() if v == "ok"]
    sex_missing = sorted(c for c, v in sex_stat.items() if v == "missing")
    sex_conflict = sorted(c for c, v in sex_stat.items() if v == "conflict")
    ages = {
        c: (p["age_incidence"] or {}).get("n", 0)
        for c, p in pages.items()
    }
    age_ok = [c for c, n in ages.items() if n >= 10]
    age_have = sorted({n for n in ages.values() if n})

    n_years = max((p["year_series"]["n_years"] for p in pages.values() if p["year_series"]), default=0)
    y0 = min((p["year_series"]["y0"] for p in pages.values() if p["year_series"] and p["year_series"]["y0"]), default=None)
    y1 = max((p["year_series"]["y1"] for p in pages.values() if p["year_series"] and p["year_series"]["y1"]), default=None)
    metrics = sorted({m for p in pages.values() if p["year_series"] for m in p["year_series"]["metrics"]})
    # 四个 metric 各有各的观测窗：发病率来自 SEER 8 / SEER 12，死亡率来自 U.S.，
    # 生存率来自 SEER 8 且止于 2018 前后。只报"最长 50 年"会让人以为四列都取得到 2024，
    # 实际最近几年多半只有 Modeled Trend 没有 Observed，所以逐列量、逐列报。
    obs_cols = sorted(
        {c for p in pages.values() if p["year_series"] for c in p["year_series"]["observed"]}
    )
    obs_span: dict[str, dict] = {}
    for c in obs_cols:
        vals = [
            p["year_series"]["observed"][c]
            for p in pages.values()
            if p["year_series"] and c in p["year_series"]["observed"]
        ]
        ns = [v["n"] for v in vals]
        y0s = sorted(v["y0"] for v in vals if v["y0"])
        y1s = sorted(v["y1"] for v in vals if v["y1"])
        obs_span[c] = {
            "n_min": min(ns) if ns else 0,
            "n_max": max(ns) if ns else 0,
            "y0": y0s[0] if y0s else None,
            "y1": y1s[-1] if y1s else None,
        }
    # Modeled Trend 是拟合值，报观测窗时只列 Observed；没有二级表头的表则整列都算
    obs_report = [c for c in obs_cols if c.endswith("Observed")] or obs_cols
    race_groups = next((p["race_groups"] for p in pages.values() if p["race_groups"]), [])
    # Modeled Trend 越过 Observed 的那几年就是"只有拟合值、没有观测"的区间。
    # 实测生存率 Observed 止于 2018、Modeled Trend 到 2023，最后 5 年全是模型外推；
    # 前端若把这两列画成一条线又不标注，等于把预测当观测发布。所以这个差要显式量出来。
    modeled_ahead = []
    for c in obs_report:
        if not c.endswith("Observed"):
            continue
        md = obs_span.get(c.removesuffix("Observed") + "Modeled Trend")
        o = obs_span[c]
        if md and o["y1"] and md["y1"] and md["y1"] > o["y1"]:
            modeled_ahead.append(
                f"{c.removesuffix(' — Observed')} 观测止于 {o['y1']}、拟合到 {md['y1']}"
            )
    stages = sorted({s for p in pages.values() if p["stage_survival"] for s in p["stage_survival"]["labels"]})
    bands = next(
        (p["age_incidence"]["bands"] for p in pages.values() if p["age_incidence"]), []
    )
    vintages = sorted({v for p in pages.values() for v in p["vintages"]})

    n = len(pages)
    obs_txt = "、".join(
        f"{c.removesuffix(' — Observed')} "
        f"{obs_span[c]['n_min']}–{obs_span[c]['n_max']}年"
        f"({obs_span[c]['y0']}–{obs_span[c]['y1']})"
        for c in obs_report
    )
    msg = (
        f"{n}/{len(TARGETS)} 页取到；年度序列 {len(ys_ok)}/{n} 页 ≥5 年（最长 {n_years} 年，{y0}–{y1}），"
        f"{len(metrics)} 个 metric；观测窗 {obs_txt}；"
        f"生存率分期 {len(st_ok)}/{len(stage_pages)} 页 ≥3 档"
        f"（{len(stages)} 档：{', '.join(stages)}；{', '.join(sorted(NO_STAGE))} 豁免）；"
        f"性别口径 {len(sex_ok)}/{n} 页与 targets.py 声明一致；"
        f"年龄组 {len(age_ok)}/{n} 页 ≥10 档，实测 {age_have} 档"
    )
    if bands:
        msg += f"（{', '.join(bands)}）"
    if race_groups:
        msg += f"；种族/民族 {len(race_groups)} 组：{', '.join(race_groups)}"
    if modeled_ahead:
        msg += f"；这些年份只有拟合值没有观测：{'、'.join(modeled_ahead)}"
    if sex_conflict:
        msg += (
            f"；性别与口径行矛盾（要改 targets.py 的 sex，不是源的问题）："
            f"{', '.join(sex_conflict)}"
        )
    if sex_missing:
        msg += f"；缺性别证据：{', '.join(sex_missing)}"
    if st_unexpected:
        msg += (
            f"；豁免页竟解析出分期档，上游补了表，可以把它移出 NO_STAGE："
            f"{', '.join(st_unexpected)}"
        )
    if dead:
        msg += f"；取不到：{', '.join(dead)}"
    if blocked:
        msg += f"；被挡：{', '.join(blocked)}"
    if vintages:
        msg += f"；口径行 {len(vintages)} 种：{'; '.join(vintages[:4])}"
    msg += f"；来源 {'offline:' + key_dir.name if offline else BASE}"
    if len(age_ok) < n:
        msg += (
            "。年龄组是宽分组（<20 到 >84 共 8 档），画发病/死亡年龄分布够用，"
            "但做不了 5 岁组标化率；SEER*Explorer 底层是 2000 美国标准人口 20 个 5 岁组，"
            "要更细得走它内部的 render_region_*.php JSON 接口（未公开文档，表单参数需逆向）"
        )

    if dead or blocked:
        verdict = "partial" if pages else ("dead" if dead else "blocked")
    elif len(ys_ok) == n and len(st_ok) == len(stage_pages) and len(sex_ok) == n:
        verdict = "ok"
    else:
        verdict = "partial"

    sample = [
        {
            "target": c,
            "slug": SLUG[c],
            "h1": pages[c]["h1"],
            "years": pages[c]["year_series"]["n_years"] if pages[c]["year_series"] else 0,
            "observed": pages[c]["year_series"]["observed"] if pages[c]["year_series"] else {},
            "stages": (pages[c]["stage_survival"] or {}).get("n", 0),
            "stage_exempt": c in NO_STAGE,
            "age_bands": (pages[c]["age_incidence"] or {}).get("n", 0),
            "age_bands_death": (pages[c]["age_mortality"] or {}).get("n", 0),
            "sex": BY_CODE[c].sex,
            "sex_verdict": sex_stat[c],
            "sexes": sorted(pages[c]["rate_sexes"]),
            "race_groups": pages[c]["race_groups"],
            "vintages": pages[c]["vintages"],
        }
        for c in sorted(pages)
    ]

    fields = (
        [f"metric={m}" for m in metrics]
        + [f"year_col={c}" for c in obs_cols]
        + [
            f"stage={len(stages)}",
            f"stage_exempt={','.join(sorted(NO_STAGE))}",
            f"age_band={len(bands)}",
            f"race_group={len(race_groups)}",
            f"vintage={len(vintages)}",
            f"year_span={y0}-{y1}",
        ]
    )

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=n,
        diseases_covered=n,
        diseases_total=len(TARGETS),
        fields_seen=fields,
        sample=sample,
        raw_path=raw.rel(key_dir) if key_dir else None,
        reachability=reach,
        http_status=statuses.pop() if len(statuses) == 1 else None,
        latency_ms=worst_ms or None,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=version or None,
        release_bytes=total_bytes,
        release_sha256=raw.sha256_bytes(
            b"".join(bodies[s] for s in sorted(bodies))
        ),
    )
