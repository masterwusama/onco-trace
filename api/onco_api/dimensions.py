"""维度 → 计数：列表页"这一病有没有这一维"与各维总量都从这一份注册表出。

一台聚合而不是逐病逐维问一百八十次：每个维度是一条 `GROUP BY disease_id` 的查询，
18 病一次跑完，所以 `/api/diseases` 一次请求是 10 条聚合查询，不是 18 × 10 条计数。

度量的切法照 docs/MVP裁定.md §一 走，每一处分开都有具体理由：
- 症状分 en / zh：中文路实测只覆盖 7/18 病，混成一个数会让另外 11 病看起来也有中文名。
  同一张 symptom 表里判为非症状的 32 条置了 `rejected`，它们参与留痕但不参与覆盖数。
- 危险因素分 genetic / exposure：两层形状不同（一边有位点与 p 值、一边只有清单），
  页面分栏不混排，所以"有没有数"也得分别问。
- 统计层按"中国国家级单点 / 中国逐年×年龄组 / 美国年度序列 / 年龄组构成 / 查询计数"
  切开：这几组之间不允许相减（GLOBOCAN 的全国估算与 GCO 的登记处外推不同源），
  所以不给一个合计的 stat 数。`age_death_cn` 就是"中国死亡年龄组零行"那条空态的
  实测来源，而不是文档里的一句断言。
- 生存率分分期档 / 全期头条 / 逐年序列：白血病只有后两层，分期档为 0 是源不给而不是解析失败。
- 试验与药各带一个去重度量（`distinct:` 前缀）：`trial` 23,705 行只有 19,254 个 NCT、
  `drug` 6,309 行只有 2,437 个药名，页面把行数说成"多少个试验/药"就是虚报。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection

from .db import rows

DISTINCT = "distinct:"
NOT_REJECTED = "review_status <> 'rejected'"

# 生存率的三层不能按 `year = 0` 切。stat_fact 用 0 当"不是年度序列"的哨兵，
# survival 却没有一行是 0（实测 0/1,762）：装载器给全期头条打的是源标的年份窗末年
# 2022，与 SEER 8 逐年序列里的 2022 撞在同一个值上。真正分开三层的是
# "同一个 (病, 档, 年份窗) 下有几个年份"——一个的是当期点，多个的是逐年序列。
# 相关子查询走 uk_survival 的前缀，全表一遍实测 18 ms。
SAME_WINDOW_OTHER_YEAR = (
    f"EXISTS (SELECT 1 FROM survival w WHERE w.disease_id = survival.disease_id"
    f" AND w.stage = survival.stage AND w.window_label = survival.window_label"
    f" AND w.year <> survival.year AND w.{NOT_REJECTED})"
)


@dataclass(frozen=True)
class Dim:
    key: str
    label: str           # 页面上的维名，措辞与 docs/MVP裁定.md §一 对齐
    table: str
    base: str            # 该维计数时的 WHERE 条件（恒真写 1=1）
    measures: tuple[tuple[str, str], ...]  # (度量名, 布尔条件 或 distinct:列名)
    count_measure: str   # 决定"这一维有没有数"的那一个度量
    note: str            # 这一维的口径要点，直接进响应，省得前端再去别处找


DIMS: tuple[Dim, ...] = (
    Dim(
        "anatomy", "关联器官", "disease_anatomy", "1=1",
        (("primary", "role='primary'"), ("subsite", "role='subsite'")),
        "primary",
        "primary 是器官级分组，subsite 只做下钻，两档不混排",
    ),
    Dim(
        "histology", "组织学", "disease_histology", "1=1",
        (("codes", "1=1"),),
        "codes",
        "这一维是自建展开（basis=via_site_recode），覆盖度矩阵里没有它的逐病格",
    ),
    Dim(
        "symptom", "症状", "symptom", NOT_REJECTED,
        (("en", "name_lang='en'"), ("zh", "name_lang='zh'"), ("freq", "freq_band IS NOT NULL")),
        "en",
        "按源分行、不做翻译列；freq 为 0 就是「建而不填」那一列的实测",
    ),
    Dim(
        "risk", "危险因素", "disease_risk_factor", "1=1",
        (
            ("any", "1=1"),
            ("genetic", "role='genetic'"),
            ("exposure", "role='exposure'"),
            ("paf", "paf IS NOT NULL"),
        ),
        "any",
        "genetic 是遗传易感性不是可干预暴露；exposure 有清单无强度；paf 建而不填",
    ),
    Dim(
        "stat", "发病与死亡统计", "stat_fact", NOT_REJECTED,
        (
            ("any", "1=1"),
            ("cn_point", "region='China' AND year=0"),
            ("cn_trend", "region='China' AND year>0"),
            ("us_series", "metric IN ('new_case_rate','death_rate')"),
            ("age_case", "metric='age_case_pct'"),
            ("age_death", "metric='age_death_pct'"),
            ("age_death_cn", "metric='age_death_pct' AND region='China'"),
            ("query_count", "estimate_basis='query_count'"),
        ),
        "any",
        "五组度量之间不可相减；query_count 是「按声明词命中多少条」，不是流行病学计数",
    ),
    Dim(
        "survival", "五年存活率", "survival", NOT_REJECTED,
        (
            ("any", "1=1"),
            ("stage", "stage_scheme <> 'none'"),
            ("all_stage_point", f"stage_scheme = 'none' AND NOT {SAME_WINDOW_OTHER_YEAR}"),
            ("all_stage_series", f"stage_scheme = 'none' AND {SAME_WINDOW_OTHER_YEAR}"),
            ("observed", "is_observed = 1"),
        ),
        "any",
        "美国 SEER 口径，页面必须写明不是中国数据；拟合值与观测值分列存；"
        "三层按 (档, 年份窗) 的跨度分，不按 year=0——这张表每行都是真实年份",
    ),
    Dim(
        "trial", "在招试验", "trial", "1=1",
        (("rows", "1=1"), ("nct", DISTINCT + "nct_id")),
        "rows",
        "一行是一个试验命中一个病，行数不等于试验数；只取在招三档",
    ),
    Dim(
        "publication", "前沿文献", "publication", "1=1",
        (("rows", "1=1"), ("oa", "is_oa = 1"), ("in_epmc", "in_epmc = 1")),
        "rows",
        "每病按相关度取前 500，是上限样本不是全量；真实命中数看 stat 的 query_count",
    ),
    Dim(
        "target", "靶点", "disease_target", "1=1",
        (("rows", "1=1"), ("novelty", "novelty IS NOT NULL")),
        "rows",
        "只收 score ≥ 0.1 的关联，阈下那近 20 万条是共现级",
    ),
    Dim(
        "drug", "在研药", "drug", "1=1",
        (("rows", "1=1"), ("names", DISTINCT + "drug_name"), ("moa", "moa IS NOT NULL")),
        "rows",
        "一行是一个 (病, 药, 阶段)，phase 是源原文不是有序档",
    ),
)

DIM_BY_KEY = {d.key: d for d in DIMS}


def _expr(cond: str) -> str:
    return f"COUNT(DISTINCT {cond[len(DISTINCT):]})" if cond.startswith(DISTINCT) else f"SUM({cond})"


def _select(d: Dim, group_by_disease: bool) -> str:
    cols = ", ".join(f"{_expr(c)} AS `{n}`" for n, c in d.measures)
    where = f" WHERE {d.base}" if d.base != "1=1" else ""
    if group_by_disease:
        return f"SELECT disease_id, {cols} FROM {d.table}{where} GROUP BY disease_id"
    # `_rows` 是表行数本身，与各维的 `rows` 度量分开命名：publication 那一维的
    # rows 就是表行数，同一句里两个同名别名会直接报错
    return f"SELECT COUNT(*) AS `_rows`, {cols} FROM {d.table}{where}"


def counts_by_disease(conn: Connection) -> dict[int, dict[str, dict[str, int]]]:
    """每个病每一维的实测条数。

    18 个病先各发一格零，再让查询结果覆盖：没出行的格子必须是 0 而不是缺席，
    否则前端分不清"这一维零行"和"这一维没查"。
    """

    def blank() -> dict[str, dict[str, int]]:
        return {d.key: {n: 0 for n, _ in d.measures} for d in DIMS}

    out = {r["id"]: blank() for r in rows(conn, "SELECT id FROM disease")}
    for d in DIMS:
        for r in rows(conn, _select(d, True)):
            m = out.get(r["disease_id"])
            if m is None:  # 表里出现了主档没有的病（装载器多写了行），不静默吞掉
                m = out.setdefault(r["disease_id"], blank())
            for n, _ in d.measures:
                m[d.key][n] = int(r[n] or 0)
    return out


def totals(conn: Connection) -> dict[str, dict[str, int]]:
    """各维全库合计，`/api/meta` 用。

    不能拿逐病计数相加：去重度量（药名数、NCT 数）跨病会重复计，全库那条走自己的
    COUNT(DISTINCT)。
    """
    out: dict[str, dict[str, int]] = {}
    for d in DIMS:
        r = rows(conn, _select(d, False))[0]
        out[d.key] = {n: int(r[n] or 0) for n, _ in d.measures}
        out[d.key]["table_rows"] = int(r["_rows"] or 0)
    return out


_IDENTITY = ("id", "code", "name_zh", "category", "sex")


def identities(conn: Connection) -> dict[str, dict]:
    """病码 → 身份几列。维度页要按同一个 404 口径找病，还要 gaps 规则读的 category。"""
    cols = ", ".join(_IDENTITY)
    return {r["code"]: r for r in rows(conn, f"SELECT {cols} FROM disease ORDER BY code")}


def counts_by_code(conn: Connection) -> dict[str, dict[str, dict[str, int]]]:
    """`counts_by_disease` 按病码重排；疾病页与各维度页共用这一份。"""
    per_id = counts_by_disease(conn)
    by_code = identities(conn)
    return {code: per_id[r["id"]] for code, r in by_code.items() if r["id"] in per_id}
