#!/usr/bin/env python
"""服务层跑测器：拿真库把六个接口的每个数字对账一遍。

    python api/tests/run.py        # 退出码非 0 即有 FAIL（不需要 pytest）

与 etl 侧那台跑测器的分工不同：解析回归守的是"上游字节 → 行"的路径，这一台守的是
"行 → 响应"的路径，所以库里现有什么就比什么，不比一份写死的快照——装载器下周重跑，
行数会变，而"接口报的数与 SQL 直查的数不等"这件事无论哪一周都是 bug。

因此每个断言都写成两路对照：一路走 TestClient（SQLAlchemy + FastAPI），一路用 pymysql
把同一件事另问一次。两条路各自独立写 SQL，任何一边把 WHERE 条件写错就会红——生存率
那三层尤其要这样：路由用 `EXISTS (… year <> …)` 判"这一串是不是序列"，这台改用
`COUNT(DISTINCT year) > 1` 判同一件事，两种写法今天同解，抄错条件的那一边会红。
榜（/api/stats/compare）先在全库断言"五轴+年份钉死后一行一病、只有一个版本"，再让每个
度量照自己回的 `needs`/`choices` 一路选到榜、与直查逐病对比——所以"钉死口径之后要不要再
聚合"与"前端能不能不问人就把口径选完"都不是文档里的承诺，是跑出来的。
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
NR = "review_status<>'rejected'"
# 装载器带"判非"那一步的三张表：symptom 实测 32 条 rejected，stat_fact 与 survival 今天
# 一条没有。列表写死在这里（不从 DIMS 读）：漏写基线的那个 Dim 正是要抓的那一个。
REVIEW_FILTERED = ("symptom", "stat_fact", "survival")
# 统计层的一条序列由这八列共同定义（多一列就漏判混串，少一列就会把两批人连成一条线）
SERIES_KEY = ("metric", "unit", "dataset_code", "region", "sex", "age_band",
              "estimate_basis", "cohort_note")
# 榜要钉死的四轴（metric 由调用方给，year 单独选）
AXES = ("region", "estimate_basis", "sex", "age_band")
# 生存率的"串"与路由换一种写法判：同 (病, 档, 年份窗) 里distinct 年份多于一个
SURV_SERIES = ("(SELECT COUNT(DISTINCT w.year) FROM survival w "
               "WHERE w.disease_id=survival.disease_id AND w.stage=survival.stage "
               f"AND w.window_label=survival.window_label AND w.{NR})")

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
    c.eq("routes", "D3a+D3b 注册的六条 API 路径", paths,
         {"/api/meta", "/api/diseases", "/api/diseases/{code}",
          "/api/diseases/{code}/stats", "/api/diseases/{code}/survival",
          "/api/stats/compare"})

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


# ------------------------------------------------- API 实际发给 MySQL 的语句
def check_sql_filter(c: Checks, seen: list[str]) -> None:
    """每句碰这三张表的 SELECT 都真的把 review_status 写进了 WHERE。

    这一条不比对数字，因为比不出来：stat_fact 与 survival 今天零行 rejected，
    "过滤了"与"忘了过滤"回一样的数（D3b 的变异检查实测：把维度基线改回恒真，
    1172 条断言全绿）。所以直接看引擎发出去的语句文本——装载器哪天给这两张表
    加上"判非"那一步，漏过滤的那一处今天就先红。
    """
    touches = [s for s in seen
               if any(re.search(rf"\bFROM\s+`?{t}`?\b", s, re.I) for t in REVIEW_FILTERED)]
    c.ok("sql", "监听确实看到了 API 发出的取数语句（挂不上这里会红，不会空过）",
         len(touches) > 50, f"共 {len(seen)} 句，其中 {len(touches)} 句碰 "
                            f"{'/'.join(REVIEW_FILTERED)}")
    # 放过一句的形状：/api/meta 的 table_rows 问的就是"这张表物理上多少行"，
    # rejected 的留痕行也算（症状维 270 行，非 rejected 238 行，两个数页面各说一件事）。
    # 正则钉的是整句原文，改一个字就不再被放过，所以这一处豁免不会随手变宽。
    table_rows = re.compile(r"^SELECT COUNT\(\*\) AS n FROM `\w+`$")
    exempt = [s for s in touches if "review_status" not in s and table_rows.match(s)]
    leaks = [s for s in touches if "review_status" not in s and not table_rows.match(s)]
    c.ok("sql", "唯一的豁免是表行数那一句", len(exempt) >= 3,
         f"放过 {len(exempt)} 句：{[s[:40] for s in exempt[:3]]}")
    c.ok("sql", "其余每一句碰这三张表都带 review_status 过滤", not leaks,
         f"漏了 {len(leaks)} 句：{[s[:110] for s in leaks[:2]]}")


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
                 f"SUM(estimate_basis='query_count') AS `query_count`, COUNT(*) AS `rows` "
                 f"FROM stat_fact WHERE {NR}"),
        # 生存率三层不按 year=0 切（这张表没有一行是 0）：同 (病, 档, 年份窗) 里
        # distinct 年份多于一个才算一串——与路由的 EXISTS 写法不同，两边同解才不红
        ("survival", "SELECT COUNT(*) AS `any`, SUM(stage_scheme<>'none') AS `stage`, "
                     f"SUM(stage_scheme='none' AND {SURV_SERIES} = 1) AS `all_stage_point`, "
                     f"SUM(stage_scheme='none' AND {SURV_SERIES} > 1) AS `all_stage_series`, "
                     f"SUM(is_observed=1) AS `observed`, COUNT(*) AS `rows` "
                     f"FROM survival WHERE {NR}"),
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
        "stat": {"any": _sid(db, did, "stat_fact", NR),
                 "cn_point": _sid(db, did, "stat_fact", f"region='China' AND year=0 AND {NR}"),
                 "cn_trend": _sid(db, did, "stat_fact", f"region='China' AND year>0 AND {NR}"),
                 "us_series": _sid(db, did, "stat_fact",
                                   f"metric IN ('new_case_rate','death_rate') AND {NR}"),
                 "age_case": _sid(db, did, "stat_fact", f"metric='age_case_pct' AND {NR}"),
                 "age_death": _sid(db, did, "stat_fact", f"metric='age_death_pct' AND {NR}"),
                 "age_death_cn": _sid(db, did, "stat_fact",
                                      f"metric='age_death_pct' AND region='China' AND {NR}"),
                 "query_count": _sid(db, did, "stat_fact", f"estimate_basis='query_count' AND {NR}")},
        "survival": {"any": _sid(db, did, "survival", NR),
                     "stage": _sid(db, did, "survival", f"stage_scheme<>'none' AND {NR}"),
                     "all_stage_point": _sid(db, did, "survival",
                                             f"stage_scheme='none' AND {SURV_SERIES} = 1 AND {NR}"),
                     "all_stage_series": _sid(db, did, "survival",
                                              f"stage_scheme='none' AND {SURV_SERIES} > 1 AND {NR}"),
                     "observed": _sid(db, did, "survival", f"is_observed=1 AND {NR}")},
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


# ---------------------------------------------------------------- 统计层：逐病序列
def check_stats(c: Checks, cl: TestClient, db, ids: dict) -> None:
    per_code = {i["code"]: i for i in cl.get("/api/diseases").json()["items"]}
    for code in CODES:
        did = ids[code]
        g = f"stat[{code}]"
        r = cl.get(f"/api/diseases/{code}/stats")
        c.eq(g, "HTTP 200", r.status_code, 200)
        d = r.json()
        c.eq(g, "响应块齐（口径说明与数据同请求回来）", sorted(d),
             sorted(["code", "name_zh", "table", "note", "conventions", "series",
                     "counts", "measures", "gaps"]))
        c.eq(g, "序列键就是那八列", d["conventions"]["series_key"], list(SERIES_KEY))

        want = {}
        cols = ", ".join(f"`{k}`" for k in SERIES_KEY)
        for row in _q(db, f"SELECT {cols}, COUNT(*) AS n, MIN(year) AS y0, MAX(year) AS y1,"
                          " COUNT(DISTINCT dataset_release_id) AS rel"
                          f" FROM stat_fact WHERE disease_id=%s AND {NR}"
                          " AND estimate_basis<>'query_count'"
                          f" GROUP BY {cols}", (did,)):
            want[tuple(str(row[k]) for k in SERIES_KEY)] = (
                int(row["n"]), int(row["y0"]), int(row["y1"]), int(row["rel"]))
        got = {}
        for s in d["series"]:
            yrs = [p["year"] for p in s["points"]]
            got[tuple(str(s[k]) for k in SERIES_KEY)] = (
                s["n_points"], min(yrs), max(yrs), 1 if s["provenance"].get("dataset") else 0)
        # 一条断言管住四件事：序列数、每条点数、年份跨度、一条串只落一个数据集版本
        c.eq(g, "序列与 SQL 分组一一对应（口径不混串、不跨版本）", got, want)

        dup = [tuple(str(s[k]) for k in SERIES_KEY) for s in d["series"]
               if len({p["year"] for p in s["points"]}) != s["n_points"]]
        c.eq(g, "一条序列里同一年不出现两次（键少列就会红）", dup, [])
        c.eq(g, "点数和 = 该病非 query_count 行数",
             sum(s["n_points"] for s in d["series"]),
             _sid(db, did, "stat_fact", f"estimate_basis<>'query_count' AND {NR}"))
        c.eq(g, "counts 就是 query_count 那几行（每个度量一行）",
             [(x["metric"], float(x["value"])) for x in d["counts"]],
             [(x["metric"], float(x["value"])) for x in _q(
                 db, "SELECT metric, value FROM stat_fact WHERE disease_id=%s AND"
                     f" estimate_basis='query_count' AND {NR} ORDER BY metric", (did,))])
        c.ok(g, "每条序列自带源、版本与抽取法",
             all(s["provenance"]["source"]["code"] and s["provenance"]["dataset"]["code"]
                 and s["provenance"]["extract_method"] for s in d["series"]))
        c.eq(g, "measures 与列表页同一份", d["measures"], per_code[code]["dims"]["stat"]["measures"])
        c.eq(g, "空态只收 stat 维且与列表页同一条", d["gaps"],
             [x for x in per_code[code]["gaps"] if x["dim"] == "stat"])


# ---------------------------------------------------------------- 统计层：跨病榜
def check_compare(c: Checks, cl: TestClient, db) -> None:
    bad = _q(db, "SELECT metric, region, estimate_basis, sex, age_band, year,"
                 " COUNT(*) AS n, COUNT(DISTINCT disease_id) AS d,"
                 " COUNT(DISTINCT dataset_release_id) AS rel"
                 f" FROM stat_fact WHERE {NR}"
                 " GROUP BY metric, region, estimate_basis, sex, age_band, year"
                 " HAVING n <> d OR rel > 1")
    # 榜的全部前提：五轴加年份钉死后一行一病、一个版本，所以取数不需要再聚合
    c.eq("cmp", "五轴+年份钉死后一行一病且只有一个版本", bad, ())

    def slice_sql(prefix: dict, year: int | None = None, tbl: str = ""):
        """五轴（+年份）的 WHERE 与参数。`tbl` 不是装饰：`disease` 也有 `sex`，
        榜那条 JOIN 不写限定就是 MySQL 1052（两列同名）。"""
        keys = ("metric",) + AXES
        q = f"{tbl}." if tbl else ""
        conds = " AND ".join(f"{q}{k}=%s" for k in keys)
        args = tuple(prefix[k] for k in keys)
        if year is not None:
            conds += f" AND {q}year=%s"
            args += (year,)
        return conds, args

    NR_SF = "sf." + NR

    def avail(prefix: dict, column: str) -> list[str]:
        """钉到 `prefix` 这一步，`column` 还剩哪些取值（与端点同序）。

        榜的四轴不是四个独立的枚举：`age_band` 全库 19 档，钉到
        region=China + estimate_basis=national_estimate + sex=both 只剩 1 档。
        这一台另算一遍，就是为了分得出"按前缀算"与"抄了一份全库清单"。
        """
        keys = ("metric",) + AXES
        conds = " AND ".join(f"{k}=%s" for k in keys if k in prefix)
        args = tuple(prefix[k] for k in keys if k in prefix)
        return [str(x["v"]) for x in _q(
            db, f"SELECT {column} v FROM stat_fact WHERE {NR} AND {conds}"
                f" GROUP BY {column} ORDER BY {column}", args)]

    metrics = [x["metric"] for x in _q(db, f"SELECT metric FROM stat_fact WHERE {NR}"
                                          " GROUP BY metric ORDER BY metric")]
    c.ok("cmp", "每个度量都进了榜", len(metrics) >= 10, f"实测 {len(metrics)} 个：{metrics}")
    for metric in metrics:
        g = f"cmp[{metric}]"
        params = {"metric": metric}
        first = d = cl.get("/api/stats/compare", params=params).json()
        # 只给了度量的那一次才分得清：调用方没写过的轴要么自动钉、要么进 needs
        c.eq(g, "四轴各归其位（needs + auto_pinned 不重不漏）",
             sorted([*first["auto_pinned"], *first["needs"]]), sorted(AXES))
        for _ in AXES:                      # 前端的选择器就照 needs 一路点下去
            if not d["needs"]:
                break
            col = d["needs"][0]
            c.ok(g, f"needs 里的 {col} 同时给了可取值", d["choices"].get(col))
            pre = {k: v for k, v in d["pinned"].items() if k != "year"}
            c.eq(g, f"{col} 的可取值按已钉前缀算（不是全库清单）",
                 [x["value"] for x in d["choices"][col]], avail(pre, col))
            params[col] = d["choices"][col][0]["value"]
            d = cl.get("/api/stats/compare", params=params).json()
        c.eq(g, "按 needs 走完就到榜（不再有未钉的轴）", d["needs"], [])
        c.eq(g, "走完四轴就到底：pinned 里五样齐",
             sorted(d["pinned"]), sorted(("metric", "year") + AXES))
        pinned = {k: d["pinned"][k] for k in ("metric",) + AXES}
        conds, args = slice_sql(pinned, d["year_used"], "sf")
        want = _q(db, "SELECT d.code, sf.value FROM stat_fact sf"
                      " JOIN disease d ON d.id = sf.disease_id"
                      f" WHERE {NR_SF} AND {conds}"
                      " ORDER BY sf.value DESC, d.code", args)
        c.eq(g, "榜与直查逐病同序同值",
             [(i["code"], float(i["value"])) for i in d["items"]],
             [(x["code"], float(x["value"])) for x in want])
        c.eq(g, "coverage 的分子就是榜上病数，分母是 18 病", d["coverage"], f"{len(want)}/18")
        c.eq(g, "absent 与榜上互补（缺谁说要缺谁，不零填）",
             sorted([i["code"] for i in d["items"]] + [a["code"] for a in d["absent"]]),
             sorted(CODES))
        c.eq(g, "year_used = 该口径有行的最新一年", d["year_used"],
             int(_col(db, f"SELECT MAX(year) FROM stat_fact WHERE {NR} AND {slice_sql(pinned)[0]}",
                      slice_sql(pinned)[1])))
        c.eq(g, "years_available 与直查同年", d["years_available"],
             [int(x["year"]) for x in _q(db, f"SELECT year FROM stat_fact WHERE {NR} AND "
                                  f"{slice_sql(pinned)[0]} GROUP BY year ORDER BY year",
                                  slice_sql(pinned)[1])])
        c.ok(g, "单位单一（榜只有一根轴）", isinstance(d["unit"], str), f"实测 {d['unit']}")

    d = cl.get("/api/stats/compare", params={"metric": "incidence_asr"}).json()
    c.eq("cmp", "口径没钉全时不回榜", (d["needs"], d["items"], d["coverage"]),
         (["estimate_basis", "sex"], [], None))
    # choices 问的是"钉到这一步还剩什么"：sex 那一步的前缀是 (metric, 已自动钉的 region)
    if "region" in d["auto_pinned"]:
        want_sex = {x["v"]: (int(x["n"]), int(x["c"])) for x in _q(
            db, "SELECT sex v, COUNT(DISTINCT disease_id) n, COUNT(*) c FROM stat_fact"
                f" WHERE {NR} AND metric='incidence_asr' AND region=%s GROUP BY sex",
            (d["pinned"]["region"],))}
        c.eq("cmp", "sex 候选带的覆盖病数与行数与直查一致",
             {x["value"]: (x["diseases"], x["rows_"]) for x in d["choices"]["sex"]}, want_sex)
    d = cl.get("/api/stats/compare", params={"metric": "incidence_asr", "region": "China",
                                             "estimate_basis": "national_estimate",
                                             "sex": "both"}).json()
    c.eq("cmp", "sex=both 缺的正是主档标了单一性别的病（不拿两性凑一个率）",
         sorted(a["code"] for a in d["absent"]),
         sorted(x["code"] for x in _q(db, "SELECT code FROM disease WHERE sex<>'both'")))
    c.eq("cmp", "源没有地区列的度量把 region='' 当一个真取值钉住",
         cl.get("/api/stats/compare", params={"metric": "trial_count"}).json()["pinned"]["region"], "")

    c.eq("cmp", "metric 必填（缺了是 422 不是空榜）", cl.get("/api/stats/compare").status_code, 422)
    r = cl.get("/api/stats/compare", params={"metric": "no_such_metric"})
    c.eq("cmp", "未知度量 404", r.status_code, 404)
    c.ok("cmp", "404 指路去哪儿找合法值", "/api/meta" in r.json()["detail"])
    r = cl.get("/api/stats/compare", params={"metric": "incidence_asr", "region": "Mars"})
    c.eq("cmp", "口径取值不在该切片内 404", r.status_code, 404)
    c.ok("cmp", "404 回的是该口径实际可取的值", "China" in r.json()["detail"],
         r.json()["detail"])
    r = cl.get("/api/stats/compare", params={"metric": "incidence_asr", "region": "China",
                                             "estimate_basis": "national_estimate",
                                             "sex": "both", "year": 1900})
    c.eq("cmp", "年份不在该切片内 404（不静默换成别的年）", r.status_code, 404)
    c.ok("cmp", "404 说出有数据的年份范围", "没有行" in r.json()["detail"], r.json()["detail"])


# ---------------------------------------------------------------- 生存率三层
def check_survival(c: Checks, cl: TestClient, db, ids: dict) -> None:
    key = ("stage", "stage_scheme", "window_label", "is_observed", "region", "dataset_code")
    per_code = {i["code"]: i for i in cl.get("/api/diseases").json()["items"]}
    for code in CODES:
        did = ids[code]
        g = f"surv[{code}]"
        d = cl.get(f"/api/diseases/{code}/survival").json()
        m = d["measures"]
        c.eq(g, "响应块齐", sorted(d),
             sorted(["code", "name_zh", "table", "note", "layers", "conventions",
                     "headline", "by_stage", "trend", "measures", "gaps"]))
        c.eq(g, "序列键就是那六列", d["conventions"]["series_key"], list(key))
        c.eq(g, "三层各有一句口径说明", sorted(d["layers"]),
             ["by_stage", "headline", "trend"])

        cols = ", ".join(f"`{k}`" for k in key)
        want_p: dict[tuple, tuple] = {}
        want_s: dict[tuple, tuple] = {}
        for row in _q(db, f"SELECT {cols}, COUNT(*) AS n, COUNT(DISTINCT year) AS yrs,"
                          " MIN(year) AS y0, MAX(year) AS y1,"
                          " COUNT(DISTINCT dataset_release_id) AS rel"
                          f" FROM survival WHERE disease_id=%s AND {NR} GROUP BY {cols}", (did,)):
            val = (int(row["n"]), int(row["y0"]), int(row["y1"]), int(row["rel"]))
            k = tuple(str(row[x]) for x in key)
            (want_p if row["yrs"] == 1 else want_s)[k] = val
        got_p = {}
        for x in [i for i in ([d["headline"]] + d["by_stage"]) if i]:
            got_p[tuple(str(x[kk]) for kk in key)] = (
                1, x["year"], x["year"], 1 if x["provenance"].get("dataset") else 0)
        got_s = {}
        for s in d["trend"]:
            yrs = [p["year"] for p in s["points"]]
            got_s[tuple(str(s[kk]) for kk in key)] = (
                s["n_points"], min(yrs), max(yrs), 1 if s["provenance"].get("dataset") else 0)
        c.eq(g, "单点层（头条+分期档）与 SQL 的单年分组一一对应", got_p, want_p)
        c.eq(g, "逐年层与 SQL 的多年分组一一对应", got_s, want_s)

        n_rows = (1 if d["headline"] else 0) + len(d["by_stage"]) + sum(
            s["n_points"] for s in d["trend"])
        c.eq(g, "三层点数和 = 该病非 rejected 行数（一行都不落）", n_rows, m["any"])
        c.eq(g, "头条条数 = all_stage_point 度量", 1 if d["headline"] else 0, m["all_stage_point"])
        c.eq(g, "分期档数 = stage 度量", len(d["by_stage"]), m["stage"])
        c.eq(g, "逐年点数 = all_stage_series 度量",
             sum(s["n_points"] for s in d["trend"]), m["all_stage_series"])
        c.ok(g, "分期档同一个年份窗（两套人不并一张表）",
             len({x["window_label"] for x in d["by_stage"]}) <= 1,
             f"实测 {sorted({x['window_label'] for x in d['by_stage']})}")
        c.eq(g, "头条与逐年不同年份窗（不能把当期点接到序列末端）",
             sorted(({d["headline"]["window_label"]} if d["headline"] else set())
                    & {s["window_label"] for s in d["trend"]}), [])
        c.ok(g, "分期档里没有全期档", all(x["stage_scheme"] != "none" for x in d["by_stage"]))
        c.eq(g, "measures 与列表页同一份", m, per_code[code]["dims"]["survival"]["measures"])
        c.eq(g, "空态只收 survival 维且与列表页同一条", d["gaps"],
             [x for x in per_code[code]["gaps"] if x["dim"] == "survival"])
        c.eq(g, "无分期档的空态与 stage 度量互证",
             bool(d["gaps"]) and any("分期" in x["text"] for x in d["gaps"]), m["stage"] == 0)


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
    for path in ("/api/diseases/nope/stats", "/api/diseases/nope/survival"):
        r = cl.get(path)
        c.eq("edges", f"{path} 未知疾病码 404 且指路", (r.status_code, "/api/diseases" in r.json()["detail"]), (404, True))
    c.eq("edges", "POST 没有路由可打", cl.post("/api/diseases", json={}).status_code, 405)
    c.eq("edges", "PATCH 同样没有", cl.patch("/api/diseases/lung", json={}).status_code, 405)
    for path in ("/api/diseases/lung/stats", "/api/diseases/lung/survival", "/api/stats/compare"):
        c.eq("edges", f"{path} 只读", cl.post(path, json={}).status_code, 405)


GROUPS = (("guard", check_guard), ("routes", check_routes), ("meta", check_meta),
          ("diseases", check_diseases), ("stats", check_stats), ("compare", check_compare),
          ("survival", check_survival), ("gaps", check_gaps), ("edges", check_edges))


def main() -> int:
    warnings.filterwarnings("ignore", message=".*starlette.testclient.*", append=True)
    c = Checks()
    with _db() as db:
        cl = TestClient(create_app())
        ids = {r["code"]: r["id"] for r in _q(db, "SELECT id, code FROM disease")}
        t0 = time.perf_counter()
        check_guard(c)
        # 只读护栏跑完才挂监听（它 dispose 过引擎，这里拿到的是新建的那一台）。
        # 之后每一句 API 真正发给 MySQL 的 SQL 都收进 seen，最后由 check_sql_filter 复盘。
        from onco_api import db as apidb
        from sqlalchemy import event

        seen: list[str] = []

        def _tap(_conn, _cur, statement, _params, _ctx, _em):
            seen.append(statement)

        event.listen(apidb.engine(), "before_cursor_execute", _tap)
        check_routes(c)
        check_meta(c, cl, db)
        check_diseases(c, cl, db)
        check_stats(c, cl, db, ids)
        check_compare(c, cl, db)
        check_survival(c, cl, db, ids)
        check_gaps(c, cl, db)
        check_edges(c, cl)
        event.remove(apidb.engine(), "before_cursor_execute", _tap)
        check_sql_filter(c, seen)
        ms = (time.perf_counter() - t0) * 1000
        # 逐请求耗时只报不判：机器之间差一个数量级，写死会天天红
        for path in ("/api/meta", "/api/diseases", "/api/diseases/lung",
                     "/api/diseases/lung/stats", "/api/diseases/lung/survival",
                     "/api/stats/compare?metric=incidence_total&region=China"
                     "&estimate_basis=national_estimate&sex=both&age_band="):
            t = time.perf_counter()
            cl.get(path)
            print(f"耗时  {path[:56]:56} {(time.perf_counter() - t) * 1000:7.1f} ms")

    fails = 0
    for group, what, ok, note in c.items:
        if not ok:
            fails += 1
            print(f"FAIL  [{group}] {what}  {note}")
    print(f"\n{len(c.items) - fails}/{len(c.items)} 通过，整跑 {ms:.0f} ms")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
