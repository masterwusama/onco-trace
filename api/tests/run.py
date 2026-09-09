#!/usr/bin/env python
"""服务层跑测器：拿真库把三个接口的每个数字对账一遍。

    python api/tests/run.py        # 退出码非 0 即有 FAIL（不需要 pytest）

与 etl 侧那台跑测器的分工不同：解析回归守的是"上游字节 → 行"的路径，这一台守的是
"行 → 响应"的路径，所以库里现有什么就比什么，不比一份写死的快照——装载器下周重跑，
行数会变，而"接口报的数与 SQL 直查的数不等"这件事无论哪一周都是 bug。

因此每个断言都写成两路对照：一路走 TestClient（SQLAlchemy + FastAPI），一路用 pymysql
把同一件事另问一次。两条路各自独立写 SQL，任何一边把 WHERE 条件写错就会红。
只有三样东西是写死的：18 个疾病码（P0 基准，漂了就说明有人改了 targets.py）、
docs/MVP裁定.md §二 那几处空态落在哪些病上（裁定本身，不是数据），
以及各维分组应当互斥且覆盖全表这一结构性事实。
"""
from __future__ import annotations

import json
import re
import sys
import time
import warnings
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台默认 GBK

ROOT = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(ROOT / "api"))

import pymysql  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from onco_api.app import create_app  # noqa: E402
from onco_api.config import load_settings  # noqa: E402

# §二 那六处空态在 18 病上的落点（裁定原文，不是查出来的）
CODES = ["bladder", "brain", "breast_female", "cervix", "colorectum", "esophagus", "kidney",
         "leukemia", "liver", "lung", "myeloma", "nhl", "ovary", "pancreas", "prostate",
         "stomach", "thyroid", "uterus"]
# 列表页每行该回的身份列：这一份写死在这里而不从路由 import——路由漏一列，
# 照着它写的断言会跟着一起漏，就永远不红了
LIST_COLS = ("code", "name_zh", "name_en", "category", "sex", "icd10", "icdo3",
             "mondo_id", "mondo_name", "ncit_id", "ot_node")
NO_ZH_SYMPTOM = ["bladder", "brain", "cervix", "esophagus", "kidney", "liver", "nhl",
                 "ovary", "prostate", "stomach", "thyroid"]          # §二.4：11 病
NO_STAGE_SURVIVAL = ["leukemia"]                                     # §二.6
NO_EXPOSURE = ["brain"]                                              # CRA 里整档缺席
THIN_EXPOSURE = 3                                                    # ≥3 那条判据线
HEME = ["leukemia", "myeloma", "nhl"]                                # category='heme'

# 逐病空态的把手：文案前 13 字。九条规则在这一个长度上两两不同（下面有断言），
# 所以拿它分组既能认规则又不用抄整句文案——改了字也不会红一片
G_ZH = "这一病没有现成的中文症状清"
G_FREQ = "症状频率带空缺：PDQ 的"
G_PAF = "危险因素只有清单与位点级效"
G_NO_EXPOSURE = "这一病在 GBD 的暴露清"
G_THIN = "可干预暴露只有 1–2 条"
G_CN_DEATH_AGE = "中国没有死亡年龄组：WHO"
G_NO_STAGE = "这一病没有分期别的五年生存"
G_ANATOMY_HEME = "这一病没有亚部位下钻：它是"
G_ANATOMY_OTHER = "这一病没有亚部位下钻：库内"
DISEASE_GAP_KEYS = [G_ZH, G_FREQ, G_PAF, G_NO_EXPOSURE, G_THIN, G_CN_DEATH_AGE,
                    G_NO_STAGE, G_ANATOMY_HEME, G_ANATOMY_OTHER]


class Checks:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, bool, str]] = []

    def eq(self, group: str, what: str, got, want) -> None:
        ok = got == want
        self.items.append((group, what, ok, "" if ok else f"得到 {got!r}，期望 {want!r}"))

    def ok(self, group: str, what: str, cond: bool, note: str = "") -> None:
        self.items.append((group, what, bool(cond), note))


def _db():
    s = load_settings()
    return pymysql.connect(host=s.host, port=s.port, user=s.user, password=s.password,
                           database=s.database, charset="utf8mb4",
                           cursorclass=pymysql.cursors.DictCursor)


def _q(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _one(db, sql, params=()):
    r = _q(db, sql, params)
    return r[0] if r else None


def _col(db, sql, params=()):
    r = _one(db, sql, params)
    return list(r.values())[0] if r else None


# ---------------------------------------------------------------- 只读护栏
def check_guard(c: Checks) -> None:
    from onco_api import db as apidb

    with apidb.ro() as conn:
        from sqlalchemy import text

        ro = conn.execute(text("SELECT @@session.transaction_read_only AS v")).scalar()
        c.eq("guard", "会话是 READ ONLY（后端改数会被 MySQL 直接拒）", int(ro), 1)
        try:
            conn.execute(text("UPDATE disease SET name_en = name_en WHERE code = 'lung'"))
            c.ok("guard", "写语句真的抛错", False, "UPDATE 居然执行成功了")
        except Exception as e:  # noqa: BLE001
            c.ok("guard", "写语句真的抛错", "readonly" in str(e).lower() or "1792" in str(e),
                 f"抛的是：{type(e).__name__}: {str(e)[:90]}")
    apidb.dispose()


# ---------------------------------------------------------------- 路由清单
def check_routes(c: Checks) -> None:
    paths = set(create_app().openapi()["paths"])
    c.eq("routes", "D3a 只注册这三条 API 路径", paths,
         {"/api/meta", "/api/diseases", "/api/diseases/{code}"})

    # 解码表按 db/schema.sql 数，不靠人记：漏一列，那一列就以 "[…]" 字符串回到前端
    from onco_api.serialize import JSON_COLS

    want: dict[str, list[str]] = {}
    table = None
    for line in (ROOT / "db" / "schema.sql").read_text(encoding="utf-8").splitlines():
        if (m := re.match(r"CREATE TABLE IF NOT EXISTS `(\w+)`", line)):
            table = m.group(1)
        elif (m := re.match(r"\s+`(\w+)`\s+json\b", line)) and table:
            want.setdefault(table, []).append(m.group(1))
        elif line.startswith(") ENGINE"):
            table = None
    c.eq("routes", "schema.sql 里每个 json 列都在解码表里",
         {k: sorted(v) for k, v in want.items()}, {k: sorted(v) for k, v in JSON_COLS.items()})


# ---------------------------------------------------------------- /api/meta
def check_meta(c: Checks, cl: TestClient, db) -> None:
    m = cl.get("/api/meta")
    c.eq("meta", "HTTP 200", m.status_code, 200)
    d = m.json()

    c.eq("meta", "站点基准是 18 病", d["site"]["diseases"], 18)
    c.eq("meta", "空态文案走 §二 那一条约定", d["site"]["empty_state_label"], "暂无可靠来源")

    want = {t: int(_col(db, f"SELECT COUNT(*) FROM `{t}`"))
            for t in d["tables"]}
    c.eq("meta", "每张表行数与直查一致", d["tables"], want)
    c.eq("meta", "业务表 15 张 + 探针侧 4 张都在", len(d["tables"]), 19)

    dims = {x["key"]: x for x in d["dims"]}
    c.eq("meta", "维度清单与 §一 的十个有表维一致", sorted(dims),
         sorted(["anatomy", "histology", "symptom", "risk", "stat", "survival",
                 "trial", "publication", "target", "drug"]))
    # 每一列都按度量名取别名，所以对照不依赖列序；少列或多列都会红（见下条断言）
    for key, sql in (
        ("anatomy", "SELECT SUM(role='primary') AS `primary`, SUM(role='subsite') AS `subsite`, "
                    "COUNT(*) AS `rows` FROM disease_anatomy"),
        ("histology", "SELECT COUNT(*) AS `codes`, COUNT(*) AS `rows` FROM disease_histology"),
        ("symptom", "SELECT SUM(name_lang='en') AS `en`, SUM(name_lang='zh') AS `zh`, "
                    "SUM(freq_band IS NOT NULL) AS `freq`, COUNT(*) AS `rows` "
                    "FROM symptom WHERE review_status<>'rejected'"),
        ("risk", "SELECT COUNT(*) AS `any`, SUM(role='genetic') AS `genetic`, "
                 "SUM(role='exposure') AS `exposure`, SUM(paf IS NOT NULL) AS `paf`, "
                 "COUNT(*) AS `rows` FROM disease_risk_factor"),
        ("stat", "SELECT COUNT(*) AS `any`, SUM(region='China' AND year=0) AS `cn_point`, "
                 "SUM(region='China' AND year>0) AS `cn_trend`, "
                 "SUM(metric IN ('new_case_rate','death_rate')) AS `us_series`, "
                 "SUM(metric='age_case_pct') AS `age_case`, SUM(metric='age_death_pct') AS `age_death`, "
                 "SUM(metric='age_death_pct' AND region='China') AS `age_death_cn`, "
                 "SUM(estimate_basis='query_count') AS `query_count`, COUNT(*) AS `rows` FROM stat_fact"),
        ("survival", "SELECT COUNT(*) AS `any`, SUM(stage_scheme<>'none') AS `stage`, "
                     "SUM(stage_scheme='none' AND year=0) AS `all_stage_point`, "
                     "SUM(stage_scheme='none' AND year>0) AS `all_stage_series`, "
                     "SUM(is_observed=1) AS `observed`, COUNT(*) AS `rows` FROM survival"),
        ("trial", "SELECT COUNT(*) AS `rows`, COUNT(DISTINCT nct_id) AS `nct` FROM trial"),
        ("publication", "SELECT COUNT(*) AS `rows`, SUM(is_oa=1) AS `oa`, "
                        "SUM(in_epmc=1) AS `in_epmc` FROM publication"),
        ("target", "SELECT COUNT(*) AS `rows`, SUM(novelty IS NOT NULL) AS `novelty` "
                   "FROM disease_target"),
        ("drug", "SELECT COUNT(*) AS `rows`, COUNT(DISTINCT drug_name) AS `names`, "
                 "SUM(moa IS NOT NULL) AS `moa` FROM drug"),
    ):
        want = {k: int(v or 0) for k, v in _one(db, sql).items()}
        block = dims[key]
        c.eq("meta", f"{key} 合计与直查逐度量一致", {k: block[k] for k in want}, want)
        # 注册表里新加一个度量而这一台没跟着加，就会在这一行红：响应字段减去
        # 四个说明性字段，剩下的必须正好被直查覆盖
        c.eq("meta", f"{key} 的每个响应字段都对到了直查",
             sorted(set(block) - {"key", "label", "note", "table"}), sorted(want))

    c.eq("meta", "中国死亡年龄组实测零行（§二.1 的空态依据）", dims["stat"]["age_death_cn"], 0)
    c.eq("meta", "PAF 实测零行（§二.2）", dims["risk"]["paf"], 0)
    c.eq("meta", "症状频率带实测零行（§二.3）", dims["symptom"]["freq"], 0)
    c.eq("meta", "试验行数不等于试验数", (dims["trial"]["rows"], dims["trial"]["nct"]),
         (int(_col(db, "SELECT COUNT(*) FROM trial")), int(_col(db, "SELECT COUNT(DISTINCT nct_id) FROM trial"))))
    c.ok("meta", "药名去重数不大于行数", dims["drug"]["names"] <= dims["drug"]["rows"])

    c.eq("meta", "源登记 21 行", len(d["sources"]), int(_col(db, "SELECT COUNT(*) FROM source")))
    c.ok("meta", "每个源都带许可与署名口径",
         all(s["license"] and s["home_url"] for s in d["sources"]))
    c.eq("meta", "源的三种状态都在", sorted({s["status"] for s in d["sources"]}),
         ["active", "paused", "rejected"])
    c.eq("meta", "数据集版本按 (源, 数据集) 取最近一次", len(d["releases"]),
         int(_col(db, "SELECT COUNT(*) FROM (SELECT source_id, dataset_code "
                      "FROM dataset_release GROUP BY 1,2) t")))
    c.ok("meta", "每份 release 都回得出版本与取回时间",
         all(r["fetched_at"] for r in d["releases"]))
    c.eq("meta", "装载器跑过的任务各留最近一次", len(d["loads"]),
         int(_col(db, "SELECT COUNT(DISTINCT job_name) FROM etl_job_log")))

    g = {x["dim"] + x["scope"] for x in d["gaps"]}
    c.ok("meta", "整维级空态含叙述段（§二.5）", "narrativedimension" in g)
    c.ok("meta", "整维级空态含两处中文名（§五.3）",
         {"anatomycolumn", "riskcolumn"} <= g)


# ---------------------------------------------------------------- 列表与详情
def check_diseases(c: Checks, cl: TestClient, db) -> None:
    lst = cl.get("/api/diseases")
    c.eq("list", "HTTP 200", lst.status_code, 200)
    body = lst.json()
    c.eq("list", "18 病全在且按 code 排", [i["code"] for i in body["items"]], CODES)
    c.eq("list", "envelope 四件套", sorted(body), ["items", "limit", "offset", "total"])
    c.eq("list", "total 与主档行数一致", body["total"], int(_col(db, "SELECT COUNT(*) FROM disease")))

    ids = {r["code"]: r["id"] for r in _q(db, "SELECT id, code FROM disease")}
    per_code = {i["code"]: i for i in body["items"]}
    ident = ", ".join(f"`{c}`" for c in LIST_COLS)
    for code in CODES:
        item = per_code[code]
        c.eq(f"list[{code}]", "身份 11 列逐列与主档一致",
             {k: item[k] for k in LIST_COLS},
             _one(db, f"SELECT {ident} FROM disease WHERE id=%s", (ids[code],)))
        # 列表行不该有第二样东西：出处列一旦漏进来，前端就会拿它当业务字段显示
        c.eq(f"list[{code}]", "列表行 = 身份 11 列 + dims + gaps",
             sorted(item), sorted([*LIST_COLS, "dims", "gaps"]))
        _check_measures(c, code, item["dims"], ids[code], db)

    d = cl.get("/api/diseases/lung").json()
    c.eq("detail", "lung 主条目在库里的形态", (d["code"], d["mondo_id"], d["category"]),
         tuple(_one(db, "SELECT code, mondo_id, category FROM disease WHERE code='lung'").values()))
    c.ok("detail", "出处五列收进了 provenance",
         all(k in d["provenance"] for k in ("source", "dataset", "extract_method",
                                            "review_status", "loaded_at")))
    c.ok("detail", "行里不再散落裸的 source_id / loaded_at",
         not ({"source_id", "dataset_release_id", "loaded_at"} & set(d)))
    c.eq("detail", "JSON 列解码成了对象", (type(d["xrefs"]).__name__, type(d["search_terms"]).__name__),
         ("dict", "list"))
    c.eq("detail", "查询词表第一项是 MeSH 主题词", d["search_terms"][0],
         _col(db, "SELECT JSON_UNQUOTE(JSON_EXTRACT(search_terms,'$[0]')) FROM disease WHERE code='lung'"))
    c.ok("detail", "研究层用到的节点写在 ot_node 而不是隐含", bool(d["ot_node"]))
    c.eq("detail", "详情的逐维计数与列表同一份（同一台聚合）",
         {k: v["measures"] for k, v in d["dims"].items()},
         {k: v["measures"] for k, v in per_code["lung"]["dims"].items()})
    try:
        json.dumps(d, ensure_ascii=False, allow_nan=False)
        json.dumps(body, ensure_ascii=False, allow_nan=False)
        c.ok("edges", "两份响应都能按严格 JSON 序列化（无 NaN/Infinity）", True)
    except ValueError as e:
        c.ok("edges", "两份响应都能按严格 JSON 序列化（无 NaN/Infinity）", False, str(e))


def _check_measures(c: Checks, code: str, dims: dict, did: int, db) -> None:
    """每个度量各问一次直查，与接口那份逐维 measures 比。"""
    want = {
        "anatomy": {"primary": _sid(db, did, "disease_anatomy", "role='primary'"),
                    "subsite": _sid(db, did, "disease_anatomy", "role='subsite'")},
        "histology": {"codes": _sid(db, did, "disease_histology", "1=1")},
        "symptom": {"en": _sid(db, did, "symptom", "name_lang='en' AND review_status<>'rejected'"),
                    "zh": _sid(db, did, "symptom", "name_lang='zh' AND review_status<>'rejected'"),
                    "freq": _sid(db, did, "symptom", "freq_band IS NOT NULL AND review_status<>'rejected'")},
        "risk": {"any": _sid(db, did, "disease_risk_factor", "1=1"),
                 "genetic": _sid(db, did, "disease_risk_factor", "role='genetic'"),
                 "exposure": _sid(db, did, "disease_risk_factor", "role='exposure'"),
                 "paf": _sid(db, did, "disease_risk_factor", "paf IS NOT NULL")},
        "stat": {"any": _sid(db, did, "stat_fact", "1=1"),
                 "cn_point": _sid(db, did, "stat_fact", "region='China' AND year=0"),
                 "cn_trend": _sid(db, did, "stat_fact", "region='China' AND year>0"),
                 "us_series": _sid(db, did, "stat_fact", "metric IN ('new_case_rate','death_rate')"),
                 "age_case": _sid(db, did, "stat_fact", "metric='age_case_pct'"),
                 "age_death": _sid(db, did, "stat_fact", "metric='age_death_pct'"),
                 "age_death_cn": _sid(db, did, "stat_fact", "metric='age_death_pct' AND region='China'"),
                 "query_count": _sid(db, did, "stat_fact", "estimate_basis='query_count'")},
        "survival": {"any": _sid(db, did, "survival", "1=1"),
                     "stage": _sid(db, did, "survival", "stage_scheme<>'none'"),
                     "all_stage_point": _sid(db, did, "survival", "stage_scheme='none' AND year=0"),
                     "all_stage_series": _sid(db, did, "survival", "stage_scheme='none' AND year>0"),
                     "observed": _sid(db, did, "survival", "is_observed=1")},
        "trial": {"rows": _sid(db, did, "trial", "1=1"),
                  "nct": _col(db, "SELECT COUNT(DISTINCT nct_id) FROM trial WHERE disease_id=%s", (did,))},
        "publication": {"rows": _sid(db, did, "publication", "1=1"),
                        "oa": _sid(db, did, "publication", "is_oa=1"),
                        "in_epmc": _sid(db, did, "publication", "in_epmc=1")},
        "target": {"rows": _sid(db, did, "disease_target", "1=1"),
                   "novelty": _sid(db, did, "disease_target", "novelty IS NOT NULL")},
        "drug": {"rows": _sid(db, did, "drug", "1=1"),
                 "names": _col(db, "SELECT COUNT(DISTINCT drug_name) FROM drug WHERE disease_id=%s", (did,)),
                 "moa": _sid(db, did, "drug", "moa IS NOT NULL")},
    }
    for key, m in want.items():
        c.eq(f"list[{code}]", f"{key} 度量与直查一致", dims[key]["measures"], m)
        c.eq(f"list[{code}]", f"{key} 的 available 跟着条数量走",
             dims[key]["available"], dims[key]["count"] > 0)
    # 分组必须互斥且覆盖全表：不等就说明某个度量的条件写漏或写叠了
    s = want["stat"]
    c.eq(f"list[{code}]", "统计层五组互斥且覆盖全表",
         s["cn_point"] + s["cn_trend"] + s["us_series"] + s["age_case"] + s["age_death"] + s["query_count"],
         s["any"])
    c.eq(f"list[{code}]", "症状 en+zh 覆盖非 rejected 行",
         want["symptom"]["en"] + want["symptom"]["zh"],
         _sid(db, did, "symptom", "review_status<>'rejected'"))
    c.eq(f"list[{code}]", "危险因素两层覆盖全表",
         want["risk"]["genetic"] + want["risk"]["exposure"], want["risk"]["any"])


def _sid(db, did: int, table: str, cond: str) -> int:
    return int(_col(db, f"SELECT COUNT(*) FROM {table} WHERE disease_id=%s AND ({cond})", (did,)))


# ---------------------------------------------------------------- 空态
def check_gaps(c: Checks, cl: TestClient, db) -> None:
    per_code = {i["code"]: i for i in cl.get("/api/diseases").json()["items"]}
    by_rule = {}
    for code, item in per_code.items():
        for g in item["gaps"]:
            by_rule.setdefault(g["text"][:13], []).append(code)

    c.eq("gaps", "九条规则的把手互不相同（分组不会把两条并成一条）",
         len({*DISEASE_GAP_KEYS}), len(DISEASE_GAP_KEYS))
    # 冒出一条没列出的文案 = 有人往规则表里加了判据而这一台不知道
    c.eq("gaps", "18 病收到的空态全在列出的九条里", set(by_rule) - set(DISEASE_GAP_KEYS), set())

    c.eq("gaps", "无中文症状清单的病 = §二.4 那 11 个", sorted(by_rule.get(G_ZH, [])),
         sorted(NO_ZH_SYMPTOM))
    c.eq("gaps", "无分期别生存率的病 = §二.6 那一个", sorted(by_rule.get(G_NO_STAGE, [])),
         sorted(NO_STAGE_SURVIVAL))
    c.eq("gaps", "CRA 暴露清单里零行的病", sorted(by_rule.get(G_NO_EXPOSURE, [])),
         sorted(NO_EXPOSURE))
    c.eq("gaps", "PAF 空的病 = 全部 18 病（§二.2 是整维级的坑）", len(by_rule.get(G_PAF, [])), 18)
    c.eq("gaps", "频率带空的病 = 全部 18 病（§二.3）", len(by_rule.get(G_FREQ, [])), 18)
    c.eq("gaps", "中国死亡年龄组零行的病 = 全部 18 病（§一、§二.1）",
         len(by_rule.get(G_CN_DEATH_AGE, [])), 18)

    thin = sorted(by_rule.get(G_THIN, []))
    want_thin = sorted(r["code"] for r in _q(
        db, "SELECT d.code FROM disease d JOIN disease_risk_factor r ON r.disease_id=d.id "
            "WHERE r.role='exposure' GROUP BY d.code HAVING COUNT(*) < %s", (THIN_EXPOSURE,)))
    c.eq("gaps", "低于 ≥3 判据线的病（17 病有行 − 10 病达线）", thin, want_thin)
    c.eq("gaps", "达到 ≥3 判据线的病数与 §二.2 一致",
         18 - len(set(thin) | set(NO_EXPOSURE)), 10)

    heme_gap = sorted(by_rule.get(G_ANATOMY_HEME, []))
    other_gap = sorted(by_rule.get(G_ANATOMY_OTHER, []))
    c.eq("gaps", "血病的亚部位空态落在 category=heme 三台", heme_gap, sorted(HEME))
    c.ok("gaps", "非血病的亚部位缺口单独成条，不混进血病那句",
         bool(other_gap) and not (set(other_gap) & set(HEME)), f"实测 {other_gap}")
    c.eq("gaps", "两句话互斥（一个病不会同时收到两条同维空态）",
         len({*heme_gap, *other_gap}), len(heme_gap) + len(other_gap))
    c.ok("gaps", "提到血液的那句只发给血液肿瘤",
         all("血液" not in g["text"] or per_code[code]["category"] == "heme"
             for code in CODES for g in per_code[code]["gaps"]))

    # 零行与"整维没有"必须分得开：leukemia 的生存率维有行，只是没有分期档
    lev = per_code["leukemia"]["dims"]
    c.ok("gaps", "零行的度量与整维缺席分得开（白血病生存率有行、stage 为 0）",
         lev["survival"]["available"] and lev["survival"]["measures"]["stage"] == 0)
    c.ok("gaps", "脑肿瘤的危险因素只剩遗传一层",
         per_code["brain"]["dims"]["risk"]["measures"]["exposure"] == 0
         and per_code["brain"]["dims"]["risk"]["measures"]["genetic"] > 0)
    c.eq("gaps", "每条空态都带 label/text/basis/scope/dim 五件",
         sorted({tuple(sorted(g)) for i in per_code.values() for g in i["gaps"]}),
         [("basis", "dim", "label", "scope", "text")])


# ---------------------------------------------------------------- 边界
def check_edges(c: Checks, cl: TestClient) -> None:
    c.eq("edges", "limit=0 被拒", cl.get("/api/diseases", params={"limit": 0}).status_code, 422)
    c.eq("edges", "limit 超上限被拒", cl.get("/api/diseases", params={"limit": 500}).status_code, 422)
    p = cl.get("/api/diseases", params={"limit": 5, "offset": 15}).json()
    c.eq("edges", "分页只回窗口内三条", [i["code"] for i in p["items"]], CODES[15:18])
    c.eq("edges", "分页不改 total", p["total"], 18)
    e = cl.get("/api/diseases/nope")
    c.eq("edges", "未知疾病码 404", e.status_code, 404)
    c.ok("edges", "404 说清了去哪儿找合法值", "/api/diseases" in e.json()["detail"])
    c.eq("edges", "POST 没有路由可打", cl.post("/api/diseases", json={}).status_code, 405)
    c.eq("edges", "PATCH 同样没有", cl.patch("/api/diseases/lung", json={}).status_code, 405)


GROUPS = (("guard", check_guard), ("routes", check_routes), ("meta", check_meta),
          ("diseases", check_diseases), ("gaps", check_gaps), ("edges", check_edges))


def main() -> int:
    warnings.filterwarnings("ignore", message=".*starlette.testclient.*", append=True)
    c = Checks()
    with _db() as db:
        cl = TestClient(create_app())
        t0 = time.perf_counter()
        check_guard(c)
        check_routes(c)
        check_meta(c, cl, db)
        check_diseases(c, cl, db)
        check_gaps(c, cl, db)
        check_edges(c, cl)
        ms = (time.perf_counter() - t0) * 1000
        # 逐请求耗时只报不判：机器之间差一个数量级，写死会天天红
        for path in ("/api/meta", "/api/diseases", "/api/diseases/lung"):
            t = time.perf_counter()
            cl.get(path)
            print(f"耗时  {path:24} {(time.perf_counter() - t) * 1000:7.1f} ms")

    fails = 0
    for group, what, ok, note in c.items:
        if not ok:
            fails += 1
            print(f"FAIL  [{group}] {what}  {note}")
    print(f"\n{len(c.items) - fails}/{len(c.items)} 通过，整跑 {ms:.0f} ms")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
