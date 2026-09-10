"""统计层与生存率装载器：GLOBOCAN、GCO Over Time、SEER Stat Facts、GBD Results 四台源写两张表。

一台装载器管四个源，因为它们填的是同一张长表 `stat_fact`，而"五年存活率"那一维有
自己的表 `survival`（分期档与观测/拟合两列口径是这张表存在的理由）。拆成四台要把
`num` / `clip_note` / 性别与口径行的解析写四遍，合在一台里四个源各占一个取数函数。

五条口径决定，都是这两张表的列注释逼出来的：

1. **只落声明性别那一行。** 源都同时给两性与合计三套数——GCO/GLOBOCAN 是 sex 0/1/2
   （0＝合计），GBD 是 1/2/3（3＝合计），两套码空间互不相通，映射分开声明。
   三种性别一起进同一张长表，一次不带 sex 条件的 `SUM` 就把例数算成两倍——所以按
   `targets.sex` 各取一行，页面真要分性别比时再回归档读原始响应。
2. **`estimate_basis` 分清"估算"与"实测"。** GLOBOCAN 是把 919 个亚登记处加权外推到全国
   的模型估算，走 `national_estimate`；GBD 2023 的中国死亡数同样是国家级建模估算、
   同走 `national_estimate`——差别只在它细到 20 个年龄档；GCO Over Time 的中国序列是
   5 个登记处覆盖 60% 人口的外推，走 `registry_extrapolated`；SEER 的 Observed 列与
   年龄构成是登记在册的实测，走 `registry_cohort`，Modeled Trend 列是 Joinpoint 拟合线，
   走 `model_trend`。SEER 的 "Death Rate — U.S." 那一列虽然是全美死因口径，它同样是
   登记真数而不是模型外推，所以与 GLOBOCAN 的 `national_estimate` 不是一回事——这一列
   的分工是"能不能当实测读"，不是"覆盖多大地理范围"。
3. **`year=0` 表示"这不是年度序列"**，不是"公元 0 年"：GLOBOCAN 的国家级单点估算、
   SEER 的年龄组构成占比都填 0，估算年份写在 `cohort_note` 与 `dataset_release.upstream_version`；
   GBD 的年龄组是 2021 单年，照实落 `year=2021`。
4. **五年存活率一个数只落一张表：只进 `survival`。** SEER 年度序列表的四个表头列只有两个去处——
   新发率与死亡率进 `stat_fact`，5-Year Relative Survival 那一列拆出的 Observed / Modeled Trend
   两栏进 `survival`。四个表头列里 "Rate of New Cases" 出现两次（SEER 8 与 SEER 12 两套队列），
   所以它是同一 metric 配两个 `region` 而不是两行重复值。这不是顺手分家：
   `docs/MVP裁定.md` §五 把"发病量 / 年龄组 / 趋势"派给 `stat_fact`、
   把"五年存活率"整维派给 `survival`，而 `survival.is_observed` 那列存在的理由就是分开观测值与
   拟合值。同一个数写进两张表，迟早有一边先漂——实测一次装载里逐格相同的有 1674 行。
5. **GBD 的死亡年龄构成是算出来的，不是源给的。** 年龄组 ZIP 里只有档内死亡数
   （Deaths × Number × 2021 × China × All Population，整份文件就这一个口径，探针逐行断言过），
   `age_death_pct` 的分子分母都由 `gbd_rows` 算：分母＝该病声明性别在场档的合计。ZIP 里
   没有全年龄行，但 410/Both 在场 20 档合计与单独取的全年龄单行逐位相等
   （`gbd_results.BAND_SUM_ANCHOR`），缺的档全是低龄零死亡档——求和缺它们不缺数。
   档内绝对死亡数不落这张表：同口径的国家级单点已在 GLOBOCAN 的 `mortality_total` 行上，
   GBD 这一份的价值在年龄档细分，落两份绝对数迟早有人拿去相减。

`metric` 的取值沿用列注释里那一张单子，不添新词：年龄别数值靠 `age_band` 非空来区分，
`incidence_crude_rate` 配 `age_band='45-49'` 就是那一档的年龄别率而不是全年龄粗率。
SEER 页面里的种族/民族费率表（6 组 × 两性）与分期表上的「各期占新发病例百分比」那一列
这一批不落——MVP 的维度里没有种族，而病例构成与生存率共用一张表会让 `survival.rate_pct`
有两种含义；两份原始表都在归档里，要补就在后续批次里连着判据一起补。
"""
from __future__ import annotations

import re

from .. import raw
from ..probes import gbd_results, gco_overtime, globocan, seer_statfacts
from ..targets import TARGETS
from .base import Ctx, LoadResult, prov, replace_scope

TODAY = globocan.SOURCE
TODAY_DATASET = globocan.DATASET
OVERTIME = gco_overtime.SOURCE
OVERTIME_DATASET = gco_overtime.DATASET
SEER = seer_statfacts.SOURCE
SEER_DATASET = seer_statfacts.DATASET
GBD = gbd_results.SOURCE
GBD_DATASET = gbd_results.DATASET

ALL_STAGES = "All stages"

# GCO/GLOBOCAN 按 sex 0/1/2 三套都给，装载只取 targets 声明的那一套；GBD 是另一套
# 码空间（3=Both/1=Male/2=Female），映射在 gbd_results.GBD_SEX，不并进这张表
SEX_DECLARED = {"both": 0, "male": 1, "female": 2}

# 逐病只取声明性别那一行：见模块注释第 1 条
NEED_TODAY = {0: "incidence", 1: "mortality", 2: "prevalence"}
NEED_OVERTIME = (("data_incidence.json", 0, "incidence"), ("data_mortality.json", 1, "mortality"))

# 度量列 → (metric 后缀, 单位)。total 是例数，asr 是世界标化率，crude_rate 是粗率
MEASURE = (("total", "_total", "count"), ("asr", "_asr", "per_100k"),
           ("crude_rate", "_crude_rate", "per_100k"))
# GCO Over Time 的年龄档：计数走 _total，年龄别率借 _crude_rate 这个名字配 age_band 用
AGE_FIELDS = (("ages", "_total", "count"), ("age_specific_rate", "_crude_rate", "per_100k"))

# SEER 年度序列的表头单元格 → (metric, 单位)。表头里的 " — SEER 8" 是这一列的队列
SERIES_HEADER = (
    (re.compile(r"^Rate of New Cases"), "new_case_rate", "per_100k"),
    (re.compile(r"^Death Rate"), "death_rate", "per_100k"),
    (re.compile(r"^5-Year Relative Survival"), "survival_rate_5y", "percent"),
)
SERIES_SEX = re.compile(r"\b(Both Sexes|Females|Males)\b")
SEREX_TO_ENUM = {"Both Sexes": "both", "Females": "female", "Males": "male"}
# 口径行开头的队列标记，如 "SEER 21 (Excluding IL) 2016–2022" 与 "U.S. 2020–2024"
WINDOW = re.compile(r"^(SEER [^,]*?\d{4}[–-]\d{4}|U\.S\. \d{4}[–-]\d{4})")
YEARS = re.compile(r"(\d{4})[–-](\d{4})")
# 现患口径句 187 个字符塞不进 varchar(128)，从原句里取两个关键片段（都是逐字子串）
PREV_RATIOS = re.compile(r"\d+-;\d+- and \d+-year prevalence ratios")
PREV_PERIOD = re.compile(r"for the period \(\d{4}-\d{4}\)")

REVIEW = "spot_checked"
MAX_NOTE = 128


def num(v: object) -> float | None:
    """'52.0' / '11.74%' / 42.9 → float；'-'、空串与非数字 → None（这一格不落行）。

    '-' 是 SEER 表示"这一格没有观测"的占位符，不是 0。把占位符落成 0 会同时骗过两件事：
    折线图上凭空多一个谷底，以及"这一维有 50 个观测"。
    """
    s = str(v).strip() if v is not None else ""
    if not s or s == "-":
        return None
    if not re.fullmatch(r"-?\d+(?:,\d{3})*(?:\.\d+)?%?", s):
        return None
    return float(s.rstrip("%").replace(",", ""))


def clip(s: str, n: int = MAX_NOTE) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def clip_note(s: str) -> str:
    """按整句取口径行，取到 128 字符为止。

    不按字符数硬切是因为这些句子的后半截才是要紧话：SEER 年度序列的脚注前半段说
    "Rates are Age-Adjusted."，后半段说拟合线是怎么算出来的，硬切会落在两句中间。
    """
    sents = [x.strip() for x in re.split(r"(?<=\.)\s+", s) if x.strip()]
    out = ""
    for x in sents:
        if out and len(out) + 1 + len(x) > MAX_NOTE:
            break
        out = f"{out} {x}".strip()
    return clip(out or s)


def sex_of(text: str) -> str:
    """从口径行里认性别。SEER 的三套措辞是 "Both Sexes" / "Females" / "Males"。"""
    m = SERIES_SEX.search(text)
    return SEREX_TO_ENUM[m.group(1)] if m else "both"


COHORT_TAG = re.compile(r"^(SEER \d+|U\.S\.)")


def region_of(cohort: str) -> str:
    """口径行开头的队列标记 → region 列（'SEER 8' / 'SEER 21' / 'US'），认不出留空。

    只取队列标记，年份窗不进 region——region 要与列注释里那张取值单对齐，
    年份窗有它自己的位置（`cohort_note` 与 `survival.window_label`）。
    """
    m = COHORT_TAG.match(cohort.strip())
    if not m:
        return ""
    return "US" if m.group(1).startswith("U.S.") else m.group(1)


def window_of(cohort: str) -> str:
    """'SEER 21 (Excluding IL) 2016–2022, All Races, … by SEER Combined Summary Stage' → 前半截。"""
    m = WINDOW.match(cohort)
    return clip(m.group(1) if m else cohort, 64)


def window_end(cohort: str) -> int:
    m = YEARS.search(cohort)
    return int(m.group(2)) if m else 0


def year_series_table(page: dict):
    """这一页唯一的那张年度序列表（实测 18 页各有且只有一张）。"""
    return next(
        (t for t in page["tables"] if seer_statfacts.kind_of(t) == "year_series"), None
    )


def series_note(page: dict) -> str:
    """年度序列表下方那句脚注——队列、性别、是否年龄标化都写在里面。"""
    tbl = year_series_table(page)
    return (
        seer_statfacts.para_after(page["paras"], tbl.pos, seer_statfacts.COHORT_ANY)
        if tbl
        else ""
    )


def _stat(
    did: int, sid: int, dataset: str, rid: int, metric: str, unit: str, value: float,
    basis: str, method: str, *, year: int = 0, age_band: str = "", sex: str = "both",
    region: str = "", note: str = "",
) -> dict:
    return {
        "disease_id": did,
        "source_id": sid,
        "dataset_code": dataset,
        "dataset_release_id": rid,
        "metric": metric,
        "unit": unit,
        "value": value,
        "year": year,
        "age_band": age_band,
        "sex": sex,
        "region": region,
        "estimate_basis": basis,
        "cohort_note": clip(note),
        **prov(
            source_id=sid, dataset_release_id=rid, extract_method=method, review_status=REVIEW
        ),
    }


def factsheet(pl: globocan.TodayPayload) -> dict:
    fs = pl.blobs["factsheet.json"]
    assert isinstance(fs, dict) and isinstance(fs.get("dataset"), list)
    return fs


def today_rows(pl: globocan.TodayPayload, sid: int, rid: int, ids: dict[str, int]) -> list[dict]:
    """GLOBOCAN 国家级单点估算：一个病 × 一个度量 × 一个指标 = 一行，year 恒为 0。"""
    fs = factsheet(pl)
    desc = fs.get("description") or {}
    by_key = {(r["cancer_code"], r["sex"], r["type"]): r for r in fs["dataset"]}
    rows: list[dict] = []
    for t in TARGETS:
        code, sex = int(t.gco_today), SEX_DECLARED[t.sex]
        for ty, kind in NEED_TODAY.items():
            r = by_key.get((code, sex, ty))
            if not r:
                continue
            note = today_note(desc, kind, pl.version)
            for field, suffix, unit in MEASURE:
                v = num(r.get(field))
                if v is None:
                    continue
                rows.append(
                    _stat(
                        ids[t.code], sid, TODAY_DATASET, rid, kind + suffix, unit, v,
                        "national_estimate", "l1_structured",
                        region="China", sex=t.sex, note=note,
                    )
                )
    return rows


def today_note(desc: dict, kind: str, version: str) -> str:
    """这一行凭什么是这个数——把源自己的估算句带出来，不重述。

    incidence / mortality 的 description 是结构化的一段（方法码在另一个键里，这里只取
    country_specific_method 那句），prevalence 是一整句而且行上没有期间标签：1 年 / 3 年 /
    5 年现患比混算在同一个数里，所以只取原句里的「1-;3- and 5-year prevalence ratios」
    与「for the period (…)」两个逐字片段。整句在归档的 factsheet.json 里。
    """
    d = desc.get(kind)
    if isinstance(d, str):
        bits = [m.group(0) for rx in (PREV_RATIOS, PREV_PERIOD) if (m := rx.search(d))]
        return f"prevalence {version}: " + ("; ".join(bits) if bits else clip(d, 100))
    method = (d or {}).get("country_specific_method") or ""
    return f"{kind} {version}: {method}".strip()


def overtime_rows(
    pl: gco_overtime.OvertimePayload, sid: int, rid: int, ids: dict[str, int]
) -> list[dict]:
    """GCO Over Time 的逐年 × 5 岁档序列。死亡那一半当前 0 行——源不给数，不是解析失败。"""
    labels = pl.cfg["ages_labels"]
    note = (
        f"inc_cov={pl.cn.get('inc_cov')} inc_period={pl.cn.get('inc_period')} "
        f"{pl.cn.get('inc_source') or ''}".strip()
    )
    rows: list[dict] = []
    for name, ty, kind in NEED_OVERTIME:
        for t in TARGETS:
            sex = SEX_DECLARED[t.sex]
            for r in pl.series(name):
                if (
                    int(r["cancer"]) != int(t.gco_time)
                    or int(r["sex"]) != sex
                    or int(r["type"]) != ty
                ):
                    continue
                did, year = ids[t.code], int(r["year"])
                for field, suffix, unit in MEASURE:
                    v = num(r.get(field))
                    if v is None:
                        continue
                    rows.append(
                        _stat(
                            did, sid, OVERTIME_DATASET, rid, kind + suffix, unit, v,
                            "registry_extrapolated", "l1_structured",
                            year=year, region="China", sex=t.sex, note=note,
                        )
                    )
                # 年龄档按位置对齐 app_config 里那个有序数组；"unk" 没有标签，整档丢掉
                for field, suffix, unit in AGE_FIELDS:
                    for code, value in (r.get(field) or {}).items():
                        if not code.isdigit() or not 1 <= int(code) <= len(labels):
                            continue
                        v = num(value)
                        if v is None:
                            continue
                        rows.append(
                            _stat(
                                did, sid, OVERTIME_DATASET, rid, kind + suffix, unit, v,
                                "registry_extrapolated", "l1_structured",
                                year=year, age_band=labels[int(code) - 1],
                                region="China", sex=t.sex, note=note,
                            )
                        )
    return rows


def gbd_rows(pl: gbd_results.GbdPayload, sid: int, rid: int, ids: dict[str, int]) -> list[dict]:
    """GBD 2023 的中国死亡年龄构成：源只给档内死亡数，构成比由这里算（模块注释第 5 条）。

    取行只按 (cause_id, 声明性别) 过滤——Deaths/Number/2021/China/All Population 这半个
    口径对整份文件成立，探针逐行断言过，装载器不再重判一遍。
    """
    rows: list[dict] = []
    for t in TARGETS:
        bands = [
            r for r in pl.age
            if r["cause_id"] == t.gbd_cause and r["sex_id"] == gbd_results.GBD_SEX[t.sex]
        ]
        if not bands:
            continue
        year = int(bands[0]["year"])
        total = sum(float(r["val"]) for r in bands)
        for r in sorted(bands, key=lambda r: int(r["age_id"])):
            rows.append(
                _stat(
                    ids[t.code], sid, GBD_DATASET, rid, "age_death_pct", "percent",
                    100.0 * float(r["val"]) / total,
                    "national_estimate", "l2_rule",
                    year=year, age_band=r["age_name"], sex=t.sex, region="China",
                    note=f"GBD 2023 Deaths Number {year} China All Population；"
                         "构成比＝该档死亡数/在场档合计",
                )
            )
    return rows


def series_col(header: str) -> tuple[str, str, str]:
    """'Rate of New Cases — SEER 8' → (metric, region, 单位)；认不出的表头返回全空。

    表头里没有 " — " 就说明 SEER 换了写法，region 留空而不是拿度量名顶上——装载消息里
    会因为 region 空点名，不会出现"region 列里写着度量名"这种脏值。
    """
    name, sep, tail = header.partition(" — ")
    for rx, metric, unit in SERIES_HEADER:
        if rx.search(name):
            return metric, region_of(tail) if sep else "", unit
    return "", "", ""


def seer_stat_rows(
    pl: seer_statfacts.StatPayload, sid: int, rid: int, ids: dict[str, int]
) -> list[dict]:
    """SEER 的两类实测：逐年序列的新发率与死亡率（Observed 与 Modeled 分列）与 8 档宽年龄组构成。

    同一张年度序列表里的 5-Year Relative Survival 两列不在这里——那张表的分期档、
    全期头条与逐年序列一起由 `seer_survival_rows` 写 `survival`（见模块注释第 4 条）。
    """
    rows: list[dict] = []
    for code, page in pl.pages.items():
        did = ids[code]
        tbl = year_series_table(page)
        raw_note = series_note(page)
        sex = sex_of(raw_note)
        footnote = clip_note(raw_note)
        ys = page["year_series"]
        if tbl and ys:
            for i, meta in enumerate(ys["cols_meta"]):
                metric, region, unit = series_col(meta["metric"])
                if not metric or metric == "survival_rate_5y":
                    continue
                modeled = meta["sub"] == "Modeled Trend"
                for r in tbl.rows:
                    if len(r) <= i + 1 or not r[0].isdigit():
                        continue
                    v = num(r[i + 1])
                    if v is None:
                        continue
                    rows.append(
                        _stat(
                            did, sid, SEER_DATASET, rid, metric, unit, v,
                            "model_trend" if modeled else "registry_cohort", "l2_rule",
                            year=int(r[0]), region=region, sex=sex, note=footnote,
                        )
                    )
        for t in page["tables"]:
            if t.kind not in ("age_incidence", "age_mortality"):
                continue
            # 年龄构成的两张表：上方那句写队列（"SEER 21 2019–2023, Age-Adjusted"），
            # 下方那句才写性别，两句话各取各的
            before = seer_statfacts.para_before(page["paras"], t.pos, seer_statfacts.COHORT)
            after = seer_statfacts.para_after(page["paras"], t.pos, seer_statfacts.COHORT)
            metric = "age_case_pct" if t.kind == "age_incidence" else "age_death_pct"
            for r in t.rows:
                if len(r) < 2 or not r[0]:
                    continue
                v = num(r[1])
                if v is None:
                    continue
                rows.append(
                    _stat(
                        did, sid, SEER_DATASET, rid, metric, "percent", v,
                        "registry_cohort", "l2_rule",
                        age_band=r[0], region=region_of(before), sex=sex_of(after), note=before,
                    )
                )
    return rows


def scheme_of(cohort: str, labels: list[str]) -> tuple[str, bool]:
    """分期体系按源自己写的措辞认（"by Ann Arbor Stage" / "by SEER Combined Summary Stage"）。

    标签形状只当一致性检查用：Stage I… 与 Localized… 两套一眼能分开，但让它决定 scheme
    就等于用我们的印象代替源的说法。两者对不上的病会进装载消息，不静默取一个。
    """
    word = "ann_arbor" if "Ann Arbor" in cohort else "seer_summary"
    shape = "ann_arbor" if any(l.startswith("Stage ") for l in labels) else "seer_summary"
    return word, word == shape


def seer_survival_rows(
    pl: seer_statfacts.StatPayload, sid: int, rid: int, ids: dict[str, int]
) -> tuple[list[dict], list[str]]:
    """生存率三层：分期档、At a Glance 的全期头条、以及逐年序列（观测与拟合分开）。

    白血病整页没有分期表（源不提供，不是解析失败），它只有全期头条与逐年序列，
    分期那一档在页面上按空态处理。
    """
    rows: list[dict] = []
    warn: list[str] = []

    def emit(did: int, stage: str, label: str, year: int, rate: float, observed: int,
             scheme: str) -> None:
        rows.append(
            {
                "disease_id": did,
                "source_id": sid,
                "dataset_code": SEER_DATASET,
                "dataset_release_id": rid,
                "stage": stage,
                "stage_scheme": scheme,
                "window_label": clip(label, 64),
                "year": year,
                "rate_pct": rate,
                "is_observed": observed,
                "region": region_of(label),
                **prov(
                    source_id=sid, dataset_release_id=rid,
                    extract_method="l2_rule", review_status=REVIEW,
                ),
            }
        )

    for code, page in pl.pages.items():
        did = ids[code]
        stage_tbl = next((t for t in page["tables"] if t.kind == "stage_survival"), None)
        cohort = (
            seer_statfacts.para_after(page["paras"], stage_tbl.pos, seer_statfacts.COHORT_ANY)
            if stage_tbl
            else ""
        )
        # 分期行的 stage_scheme 只描述"这一行是分期表里的哪一套档"；
        # 全期头条与逐年序列根本不带分期维度，一律 'none'
        scheme, consistent = scheme_of(cohort, [r[0] for r in stage_tbl.rows]) if stage_tbl \
            else ("none", True)
        if not consistent:
            warn.append(code)
        window = window_of(cohort) if cohort else page["headline"]["window"]

        if stage_tbl:
            for r in stage_tbl.rows:
                if len(r) < 3 or not r[0]:
                    continue
                v = num(r[2])
                if v is not None:
                    emit(did, r[0], window, window_end(window), v, 1, scheme)
        head = num(page["headline"]["rate"])
        hw = page["headline"]["window"]
        if head is not None and hw:
            emit(did, ALL_STAGES, hw, window_end(hw), head, 1, "none")

        tbl, ys = year_series_table(page), page["year_series"]
        if tbl and ys:
            for i, meta in enumerate(ys["cols_meta"]):
                metric, region, _unit = series_col(meta["metric"])
                if metric != "survival_rate_5y":
                    continue
                obs = ys["observed"][ys["cols"][i]]
                # 三段都是源自己的文字：一级表头给队列、二级表头给 Observed / Modeled Trend、
                # 该列非空格子的跨度给年份窗。源写在这一列下方的年份窗句子只说 "from 1975–2018"，
                # 观测行与拟合行共用它就会撞 uk_survival（同病同分期同年），差异只能由这个标签担
                label = f"{region} {meta['sub'] or 'Observed'} {obs['y0']}–{obs['y1']}"
                for r in tbl.rows:
                    if len(r) <= i + 1 or not r[0].isdigit():
                        continue
                    v = num(r[i + 1])
                    if v is not None:
                        emit(
                            did, ALL_STAGES, label, int(r[0]), v,
                            0 if meta["sub"] == "Modeled Trend" else 1, "none",
                        )
    return rows, warn


def load(ctx: Ctx) -> LoadResult:
    tp = globocan.load_payload(ctx.offline)
    op = gco_overtime.load_payload(ctx.offline)
    sp = seer_statfacts.load_payload(ctx.offline)
    gp = gbd_results.load_payload(ctx.offline)
    if tp.cn is None or op.cn is None:
        raise SystemExit("响应里没有 iso3=CHN 那一档，统计层的中国两路没法装载（探针会记 blocked）")
    if not sp.pages:
        raise SystemExit("SEER 一页都没解析出来，统计层的美国那一路没法装载")
    if gp.blocked:
        raise SystemExit(f"GBD 的两份 ZIP 没取回：{gp.blocked.message}")
    if not gp.age or not gp.paf:
        raise SystemExit("GBD 的 ZIP 解析出了空表——口径漂了，先跑探针看判据再装载")

    with ctx.tx() as conn:
        sid_today, sid_over, sid_seer, sid_gbd = (
            ctx.source_id(TODAY), ctx.source_id(OVERTIME), ctx.source_id(SEER),
            ctx.source_id(GBD)
        )
        rid_today = ctx.register(
            conn, TODAY, TODAY_DATASET,
            upstream_version=tp.version,
            release_date=globocan.release_date(tp.blobs["meta_update.json"], tp.version),
            body_bytes=sum(len(b) for b in tp.bodies.values()),
            sha256=raw.sha256_bytes(b"".join(tp.bodies[n] for n in globocan.DATASET_FILES)),
            rows_seen=len(factsheet(tp)["dataset"]),
            raw_path=raw.rel(tp.key_dir) if tp.key_dir else None,
        )
        rid_over = ctx.register(
            conn, OVERTIME, OVERTIME_DATASET,
            upstream_version=f"r{op.release}",
            release_date=None,
            body_bytes=sum(len(b) for b in op.bodies.values()),
            sha256=raw.sha256_bytes(b"".join(op.bodies[n] for n in gco_overtime.ARCHIVE_FILES)),
            rows_seen=sum(len(op.series(n)) for n in gco_overtime.SERIES_FILES),
            raw_path=raw.rel(op.key_dir) if op.key_dir else None,
        )
        rid_seer = ctx.register(
            conn, SEER, SEER_DATASET,
            upstream_version=sp.version,
            release_date=sp.version or None,
            body_bytes=sp.total_bytes,
            sha256=raw.sha256_bytes(b"".join(sp.bodies[s] for s in sorted(sp.bodies))),
            rows_seen=len(sp.pages),
            raw_path=raw.rel(sp.key_dir) if sp.key_dir else None,
        )
        rid_gbd = ctx.register(
            conn, GBD, GBD_DATASET,
            upstream_version=gp.version,
            release_date=None,
            body_bytes=gp.size,
            sha256=gp.sha,
            rows_seen=len(gp.age) + gp.paf_total,
            raw_path=raw.rel(gp.key_dir) if gp.key_dir else None,
        )
        ids = ctx.disease_ids(conn)
        stats = {
            sid_today: today_rows(tp, sid_today, rid_today, ids),
            sid_over: overtime_rows(op, sid_over, rid_over, ids),
            sid_seer: seer_stat_rows(sp, sid_seer, rid_seer, ids),
            sid_gbd: gbd_rows(gp, sid_gbd, rid_gbd, ids),
        }
        survs, warn = seer_survival_rows(sp, sid_seer, rid_seer, ids)
        n_stat = sum(
            replace_scope(conn, "stat_fact", {"source_id": sid}, rows)
            for sid, rows in stats.items()
        )
        n_surv = replace_scope(conn, "survival", {"source_id": sid_seer}, survs)

    covered = len(
        {r["disease_id"] for rs in stats.values() for r in rs} | {r["disease_id"] for r in survs}
    )
    staged = len({r["disease_id"] for r in survs if r["stage"] != ALL_STAGES})
    fitted = sum(1 for r in survs if not r["is_observed"])
    no_region = sorted({r["metric"] for rs in stats.values() for r in rs if not r["region"]})
    msg = (
        f"stat_fact GLOBOCAN {len(stats[sid_today])} / GCO Over Time {len(stats[sid_over])} / "
        f"SEER {len(stats[sid_seer])} / GBD 2023 中国死亡年龄组 {len(stats[sid_gbd])} 行"
        "（构成比＝该档死亡数/在场档合计，缺的档全是低龄零死亡档；410/Both 在场 20 档合计"
        "与全年龄单行逐位相等，分母的锚成立）；"
        f"survival {n_surv}（分期档 {staged} 病 + 全期头条 {len(TARGETS)} 病 + 逐年序列，"
        f"其中拟合值 {fitted} 行，五年存活率一律不落 stat_fact）；"
        f"GCO Over Time 的死亡序列 {len(op.series('data_mortality.json'))} 行——中国死亡"
        "年龄组这一维由 GBD 2023 补上，不再按空态处理"
    )
    if warn:
        msg += f"；分期措辞与标签形状不一致，要人看：{', '.join(warn)}"
    if no_region:
        msg += f"；这些度量没认出队列，region 留空：{', '.join(no_region)}"
    ctx.job.set(written=n_stat + n_surv)
    return LoadResult(
        written={"stat_fact": n_stat, "survival": n_surv},
        covered=covered,
        total=len(TARGETS),
        message=msg,
    )
