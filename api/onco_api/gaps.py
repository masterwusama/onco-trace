"""空态注册表：页面上每一处"暂无可靠来源"由这条规则表算出来。

裁定在 docs/MVP裁定.md §二，那六处是 P0 实测出来的结论。这里不照抄那份名单，
而是把每一处写成"读哪个度量、等于几才算缺"，所以：

- 装载器哪天把 `paf` 或 `freq_band` 填上了，接口就不再说它空着——文档会漂，谓词不会；
- 反过来，哪个病新掉出一批零行（比如某个源改版少了一维），页面也会立刻如实说缺。

逐病判定（`gaps_for_disease`）与整维判定（`global_gaps`）分开，是因为这两类在前端
的说法不同：前者只有这一病空着、别家有数，后者是全站的坑。混在一起写，页面就会把
"整维没有"显示成"这一病查不出东西"——那是最难被读者发现的一类误导。

谓词读的是 counts 与 disease 两样东西：只看计数会说出假原因。"没有亚部位下钻"这一条
实测有六个病命中，其中三个是血液系统肿瘤（ICD-9 那一章编的是细胞类型不是部位），
另外三个是卵巢、前列腺、甲状腺——把它们一并说成血病是编的，所以按 category 分成两条。
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy import Connection

from .db import rows

EMPTY_LABEL = "暂无可靠来源"

# ctx = {"counts": {dim: {measure: n}}, "disease": {code, category, sex, ...}}
Rule = tuple[str, str, str, Callable[[dict], bool]]  # (dim, 页面文案, 判据出处, 谓词)

DISEASE_RULES: tuple[Rule, ...] = (
    (
        "symptom",
        "这一病没有现成的中文症状清单，中文名列留空；英文清单来自 NCI PDQ。",
        "三条匿名中文路实测只覆盖 7/18 病，低于裁定线；按源分行而不是做翻译列（§二.4）",
        lambda x: x["counts"]["symptom"]["zh"] == 0,
    ),
    (
        "symptom",
        "症状频率带空缺：PDQ 的症状小节里没有一条百分号。",
        "唯一给得出六档的 Orphanet 在常见上皮癌上 0 命中，所以 freq_band 建而不填（§二.3）",
        lambda x: x["counts"]["symptom"]["freq"] == 0,
    ),
    (
        "risk",
        "危险因素只有清单与位点级效应量，没有归因强度：PAF 整个在 IHME 授权门后。",
        "vizhub 数据面四个路由一律 401；账号已注册，换 token 那条取数路还没接（§一、§二.2）",
        lambda x: x["counts"]["risk"]["paf"] == 0,
    ),
    (
        "risk",
        "这一病在 GBD 的暴露清单里一行都没有，危险因素只有遗传关联那一层。",
        "CRA A2 交叉表实测 17/18 病有行，脑肿瘤整档缺席（§一 危险因素行）",
        lambda x: x["counts"]["risk"]["exposure"] == 0,
    ),
    (
        "risk",
        "可干预暴露只有 1–2 条，低于判据线的 ≥3：按实有条数显示，不补 0，"
        "也不与遗传关联排成同一张榜。",
        "CRA 按 REI 层级剔掉聚合父档后 10/18 病达到 ≥3 个独立暴露（§二.2）",
        lambda x: 0 < x["counts"]["risk"]["exposure"] < 3,
    ),
    (
        "stat",
        "中国没有死亡年龄组：WHO GHO 实测排除，这一维只剩 IHME 注册账号一条路。",
        "stat_fact 里 metric=age_death_pct 且 region=China 的行实测为 0；年龄构成只有美国的 "
        "SEER 8 档宽分组（§一 死亡年龄组行）",
        lambda x: x["counts"]["stat"]["age_death_cn"] == 0,
    ),
    (
        "survival",
        "这一病没有分期别的五年生存率：源本身不发分期表，只有全分期一个数。",
        "SEER 对白血病整页无分期表（§一 五年存活率行、§二.6）",
        lambda x: x["counts"]["survival"]["stage"] == 0 and x["counts"]["survival"]["any"] > 0,
    ),
    (
        "anatomy",
        "这一病没有亚部位下钻：它是血液系统肿瘤，而 ICD-9 的 200–208 章编的是细胞类型不是部位，"
        "三台整维跳过。",
        "C2b 的亚部位筛选规则对血病整维不适用；器官级分组（primary）仍照常挂（§五 关联器官行）",
        lambda x: x["counts"]["anatomy"]["subsite"] == 0
        and x["disease"].get("category") == "heme",
    ),
    (
        "anatomy",
        "这一病没有亚部位下钻：库内 51 个亚部位节点没有一个挂到它。",
        "亚部位只收「ICD-9 为它单开了部位档、且这一档没被同病别的 term 共用」的 MONDO term，"
        "145 个候选留下 51 个（§五 关联器官行）",
        lambda x: x["counts"]["anatomy"]["subsite"] == 0
        and x["disease"].get("category") != "heme",
    ),
)

# 整维级：读某一列的填充数，为 0 才报（填上就不报，与逐病规则同理）
GLOBAL_UNFILLED: tuple[Rule, ...] = (
    (
        "anatomy",
        "器官节点没有中文名：没有可匿名取回的公开中文器官名源，页面按英文标签加病名呈现。",
        "anatomy_node.label_zh 建而不填（§五 三条横切约定 3）",
        lambda x: x["n"] == 0,
    ),
    (
        "risk",
        "危险因素节点没有中文名：同上，位点标签与暴露名都按源原文显示。",
        "risk_factor.label_zh 建而不填（§五 三条横切约定 3）",
        lambda x: x["n"] == 0,
    ),
)
_UNFILLED_SQL = {
    "anatomy": "SELECT SUM(label_zh IS NOT NULL) AS n FROM anatomy_node",
    "risk": "SELECT SUM(label_zh IS NOT NULL) AS n FROM risk_factor",
}

# 连表都没有的维：WHO fact sheet 判据不达标，整个维缺席，不是"一列空着"
NARRATIVE_GAP = (
    "narrative",
    "疾病页不给叙述/介绍段落：全站 fact sheet 只有 73 个主题，18 病里 4 病有 ≥3 条要点清单。",
    "who_factsheet 探针判 partial，判据线是 ≥12/18（§一 叙述行）",
)


def _gap(dim: str, text: str, basis: str, scope: str, **extra) -> dict:
    return {"dim": dim, "scope": scope, "label": EMPTY_LABEL, "text": text,
            "basis": basis, **extra}


def gaps_for_disease(counts: dict[str, dict[str, int]], disease: dict) -> list[dict]:
    ctx = {"counts": counts, "disease": disease}
    return [_gap(dim, text, basis, "disease") for dim, text, basis, pred in DISEASE_RULES if pred(ctx)]


def global_gaps(conn: Connection) -> list[dict]:
    dim, text, basis = NARRATIVE_GAP
    out = [_gap(dim, text, basis, "dimension", available=False)]
    for key, kf, bf, pred in GLOBAL_UNFILLED:
        n = int(rows(conn, _UNFILLED_SQL[key])[0]["n"] or 0)
        if pred({"n": n}):
            out.append(_gap(key, kf, bf, "column"))
    return out
