#!/usr/bin/env python
"""服务层跑测器：拿真库把十五条接口的每个数字对账一遍。

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
研究层四台（试验/文献/靶点/在研药）第一次出现"一病几千行"，所以断言的重心跟着换三样：
分页切出来那一页必须是同一个全序里的那一段（逐页走完等于整表，越界 offset 回空页而不是
回整表）；分面按未过滤的整维算，所以"筛一次之后的 total_rows"必须正好等于分面那个数，
而筛完选项集合不许缩水；`page.total_rows` 与 `source_hit.value` 各说一件事，文献那一台
18/18 病必须不相等（每病截在 500 行而源命中上万）。
D4a 起了前端，于是多一道路径契约：frontend/src 里出现的每一个后端路径字面量都必须在
`client.js` 那份清单上，清单上每一条都必须是真注册过的路由，而注册了却还没画屏的那几条
（`BACKEND_ONLY`）要写明理由——三个方向都钉，接口与页面才不会各说各话。
只有三样东西是写死的：18 个疾病码（P0 基准，漂了就说明有人改了 targets.py）、
docs/MVP裁定.md §二 那几处空态落在哪些病上（裁定本身，不是数据），
以及各维分组应当互斥且覆盖全表这一结构性事实。词表四维另外钉两样：JOIN 出来的响应行该有哪些
键（多一个 parent / children 就是把库里没有的层级说成有），以及遗传关联榜的截断规则。
"""
from __future__ import annotations

import gzip
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
# 站点源码在仓库同级的 frontend/src；接口路径契约由 check_frontend 守
FRONTEND_SRC = ROOT / "frontend" / "src"
# 后端注册了、前端还没画屏的接口：加一维而不给屏，就得在这里写下一行理由，
# 否则那道门禁会红——它要的是"没人对着页面核对过的接口"不存在，不是"两边条数相等"。
# D4b 起是空的：十四条疾病页接口 + 榜那一屏把十五条全覆盖了。空不代表这条规则停了。
BACKEND_ONLY: dict[str, str] = {}
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
def check_routes(c: Checks) -> set[str]:
    paths = set(create_app().openapi()["paths"])
    c.eq("routes", "D3a+D3b+D3c+D3d 注册的十五条 API 路径", paths,
         {"/api/meta", "/api/diseases", "/api/diseases/{code}",
          "/api/diseases/{code}/stats", "/api/diseases/{code}/survival",
          "/api/stats/metrics", "/api/stats/compare",
          "/api/diseases/{code}/anatomy", "/api/diseases/{code}/histology",
          "/api/diseases/{code}/symptoms", "/api/diseases/{code}/risk-factors",
          "/api/diseases/{code}/trials", "/api/diseases/{code}/publications",
          "/api/diseases/{code}/targets", "/api/diseases/{code}/drugs"})

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
    return paths


def check_frontend(c: Checks, paths: set[str]) -> None:
    """前端 ↔ 后端的路径契约：页面敢打的接口，与后端真注册的接口，两边对得上。

    三个方向都要钉：前端写了后端没有的路径是一次 404；后端加了路径而没人写屏，
    是接口在无人核对的情况下上线；而面板绕过 `client.js` 直接 fetch 一条新路径，
    等于把这份清单变成装饰品——所以第三项看的不是清单，是源码里出现过的字面量。
    """
    src = FRONTEND_SRC
    if not src.is_dir():
        c.ok("web", "frontend/src 在（不在就是门禁静默放过）", False, f"找不到 {src}")
        return

    client_js = (src / "api" / "client.js").read_text(encoding="utf-8")
    m = re.search(r"export const API_PATHS = \[(.*?)\]", client_js, re.S)
    c.ok("web", "client.js 里 API_PATHS 那份清单读得出来", m is not None)
    listed = re.findall(r"'([^']+)'", m.group(1)) if m else []
    c.ok("web", "清单非空", bool(listed), f"{len(listed)} 条")

    unknown = [p for p in listed if "/api" + p not in paths]
    c.ok("web", "清单里每一条都是后端真注册的路径", not unknown, f"多余：{unknown}")
    c.eq("web", "清单自身不重复", sorted(set(listed)), sorted(listed))

    # 字面量扫描：只认像后端路径的那几种开头（'/disease/' 是前端路由，不算）
    used: set[str] = set()
    strays: list[str] = []
    for f in sorted(src.rglob("*")):
        if f.suffix not in (".js", ".vue") or not f.is_file():
            continue
        for lit in re.findall(r"['`](/(?:api|diseases|stats|meta)[^'`]*)['`]",
                              f.read_text(encoding="utf-8")):
            if lit == "/api":  # client.js 拼接用的前缀，不是一条路径
                continue
            used.add(lit)
            if lit not in listed:
                strays.append(f"{f.relative_to(src).as_posix()}:{lit}")
    c.ok("web", "源码里每个后端路径字面量都在清单上（没有绕过 client.js 的调用）",
         not strays, f"绕过清单：{sorted(set(strays))}")

    unreferenced = {p for p in paths if not any(p == "/api" + u for u in used)}
    c.eq("web", "后端注册了而前端还没画屏的接口，正是写好了理由的那几条",
         unreferenced, set(BACKEND_ONLY))


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
                 "(SELECT COUNT(DISTINCT risk_factor_id) FROM disease_risk_factor"
                 " WHERE role='genetic') AS `genetic_loci`, "
                 "(SELECT COUNT(DISTINCT risk_factor_id) FROM disease_risk_factor"
                 " WHERE role='exposure') AS `exposure_nodes`, "
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
                 "genetic_loci": int(_col(
                     db, "SELECT COUNT(DISTINCT risk_factor_id) FROM disease_risk_factor"
                         " WHERE disease_id=%s AND role='genetic'", (did,))),
                 "exposure_nodes": int(_col(
                     db, "SELECT COUNT(DISTINCT risk_factor_id) FROM disease_risk_factor"
                         " WHERE disease_id=%s AND role='exposure'", (did,))),
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
    r = want["risk"]
    c.ok(f"list[{code}]", "去重度量不超过行数（distinct 取错列就会倒挂）",
         r["genetic_loci"] <= r["genetic"] and r["exposure_nodes"] <= r["exposure"],
         f"genetic {r['genetic_loci']} 个位点 / {r['genetic']} 行，"
         f"exposure {r['exposure_nodes']} 个节点 / {r['exposure']} 行")


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
def check_metrics(c: Checks, cl: TestClient, db) -> None:
    """榜那个选择器的料：`/api/stats/metrics` 逐度量与直查对照。

    路由是一条 `GROUP BY metric` 扫全表，这台反过来按名字逐度量单独问——两边同序同名，
    才说明页面上那份清单真的是 `stat_fact.metric` 的取值，而不是谁抄下来的一份快照。
    """
    d = cl.get("/api/stats/metrics").json()
    want_names = [x["metric"] for x in _q(db, f"SELECT metric FROM stat_fact WHERE {NR}"
                                             " GROUP BY metric ORDER BY metric")]
    c.eq("mtr", "度量名与直查同序同名（这份清单就是唯一出处）",
         [i["metric"] for i in d["items"]], want_names)
    # 聚合行不是一行事实，挂出处就会让页面把"这一度量有 5,490 行"署给某一行
    c.eq("mtr", "每个度量项就这些键（聚合层不许挂出处）",
         {tuple(sorted(i)) for i in d["items"]},
         {("axes", "diseases", "metric", "rows", "unit",
           "year_first", "year_last", "year_zero_rows")})
    c.eq("mtr", "各度量行数相加＝该表非 rejected 行数（没有度量被漏在清单外）",
         sum(i["rows"] for i in d["items"]),
         _col(db, f"SELECT COUNT(*) FROM stat_fact WHERE {NR}"))
    for i in d["items"]:
        g = f"mtr[{i['metric']}]"
        r = _q(db, "SELECT COUNT(*) rows_, COUNT(DISTINCT disease_id) dis, SUM(year=0) yz,"
                   " MIN(NULLIF(year,0)) y0, MAX(NULLIF(year,0)) y1"
                   f" FROM stat_fact WHERE {NR} AND metric=%s", (i["metric"],))[0]
        c.eq(g, "行数与覆盖病数", (i["rows"], i["diseases"]), (int(r["rows_"]), int(r["dis"])))
        c.eq(g, "year=0 单点行数与真实年份跨度（0 不是公元 0 年）",
             (i["year_zero_rows"], i["year_first"], i["year_last"]),
             (int(r["yz"] or 0), r["y0"], r["y1"]))
        us = [x["unit"] for x in _q(db, f"SELECT unit FROM stat_fact WHERE {NR} AND metric=%s"
                                       " GROUP BY unit ORDER BY unit", (i["metric"],))]
        c.eq(g, "单位（一根轴才排得成榜；多于一个回数组）",
             i["unit"], us[0] if len(us) == 1 else us)
        c.eq(g, "四轴未过滤时各有几个取值", i["axes"],
             {k: int(_col(db, f"SELECT COUNT(DISTINCT {k}) FROM stat_fact"
                              f" WHERE {NR} AND metric=%s", (i["metric"],))) for k in AXES})


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
    c.ok("cmp", "404 指路去哪儿找合法值", "/api/stats/metrics" in r.json()["detail"])
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


# ---------------------------------------------------------------- 词表四维
# 四台的共同形状：一行是一个词而不是一个数，所以都不折序列，每条带两份出处
# （关系行一份、节点行一份）。断言按同一个路子写：响应那边是路由的 SQL，
# 这边用 pymysql 另问一遍、并在 Python 里把聚合重做，两边写法刻意不同。
# 三张表（disease_anatomy / disease_histology / disease_risk_factor）的维度基线是 1=1
# 而不是非 rejected：装载器对这三张表没有「判非」那一步，读侧凭空加一道过滤
# 就会把库里没有的口径编出来，所以这里也照 1=1 比。
_SHELL_KEYS = ["code", "conventions", "gaps", "measures", "name_zh", "note", "table"]
ANAT_NODE_COLS = ("id", "kind", "code", "label", "label_zh", "icdo3_range", "icd9")
ANAT_MOUNT_COLS = ("id", "role", "basis", "matched_codes")
HIST_CODE_COLS = ("id", "code", "behavior", "code_behavior", "label", "group_code", "group_label")
HIST_MOUNT_COLS = ("id", "via_recode", "basis")
SYMPTOM_COLS = ("id", "name", "name_lang", "heading", "source_url", "anchor", "extract_kind",
                "page_lastmod", "freq_band",
                "derive_marker")  # DDL 里那列就叫 provenance，换名带出才不被出处对象盖掉
RISK_ASSOC_COLS = ("id", "role", "uri_tier", "trait_label", "trait_uri", "snps", "risk_allele",
                   "chr_id", "chr_pos", "risk_allele_freq", "p_value_text", "pvalue_mlog",
                   "or_beta", "effect_kind", "ci95_text", "pubmedid", "study_accession",
                   "initial_sample", "replication_sample", "paf", "paf_basis")
RISK_NODE_COLS = ("id", "kind", "label", "label_zh")
DIM_TABLES = {"anatomy": "disease_anatomy", "histology": "disease_histology",
              "symptom": "symptom", "risk": "disease_risk_factor",
              "trial": "trial", "publication": "publication",
              "target": "disease_target", "drug": "drug"}


def _shell_ok(c: Checks, g: str, d: dict, row: dict, dim: str, extra: list[str]) -> None:
    """八台共用的头部：身份、注册表带出的表名与口径、与列表页同一份度量和空态。"""
    c.eq(g, "响应块齐", sorted(d), sorted([*_SHELL_KEYS, *extra]))
    c.eq(g, "table 就是这一维注册的那张表", d["table"], DIM_TABLES[dim])
    c.eq(g, "note 与列表页那一维同一句", d["note"], row["dims"][dim]["note"])
    c.eq(g, "measures 与列表页同一份", d["measures"], row["dims"][dim]["measures"])
    c.eq(g, "gaps 只收这一维、与列表页同一条", d["gaps"],
         [x for x in row["gaps"] if x["dim"] == dim])
    c.ok(g, "conventions 每条都给了页面一句话",
         bool(d["conventions"]) and all(len(v) > 20 for v in d["conventions"].values()),
         f"{sorted(d['conventions'])}")


def _shape_ok(c: Checks, g: str, what: str, items: list, cols: tuple, joined: tuple) -> None:
    """行的键集合只许是「DDL 里那几列 + provenance + 那一份 JOIN 出来的另一张表」。

    钉的是键而不是值：读侧凭空多出一个 parent / children / score 键，就等于把库里
    没有的层级或分数说成有。
    """
    if not items:
        return
    c.eq(g, what, {tuple(sorted(set(x) - set(joined) - {"provenance"})) for x in items},
         {tuple(sorted(cols))})


def check_vocab(c: Checks, cl: TestClient, db, ids: dict) -> None:
    per_code = {i["code"]: i for i in cl.get("/api/diseases").json()["items"]}
    # 判非的症状条目全库 32 条：任何一台把它们带进响应都是把「已排除」说成「有」
    rejected = {r["id"] for r in _q(db, f"SELECT id FROM symptom WHERE NOT ({NR})")}
    marked = int(_col(db, "SELECT COUNT(*) FROM symptom WHERE provenance<>''"))
    for code in CODES:
        did = ids[code]
        # 形状一变（少一列、多一个键、接口 500）不该把整台跑测带走：这一病这一维记成红，
        # 其余 71 台继续跑完。变异检查里五条改坏原本只留下一个 traceback，
        # 后面两千条断言一条没跑，报告上看不出红在哪一维。
        for g, fn, extra in (("anat", _vocab_anatomy, ()), ("hist", _vocab_histology, ()),
                             ("symp", _vocab_symptoms, (rejected,)), ("risk", _vocab_risk, ())):
            try:
                fn(c, cl, db, code, did, per_code[code], *extra)
            except Exception as e:  # noqa: BLE001 跑测器宁可红一片，也不许悄悄少跑一段
                c.ok(g, f"{code} 这一维的对账整台跑完（没抛异常）", False,
                     f"{type(e).__name__}: {e}")
    c.eq("vocab", "全库没有一行用过 symptom.provenance 那一列（derive_marker 只是留空位）",
         marked, 0)


def _vocab_anatomy(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"anat[{code}]"
    d = cl.get(f"/api/diseases/{code}/anatomy").json()
    _shell_ok(c, g, d, row, "anatomy", ["primary", "subsite"])
    # 一份挂载行 + 一份节点行各有各的出处，所以两边各自的 source/review 都要单独对上
    want = {int(r["mid"]): r for r in _q(
        db, "SELECT m.id mid, n.id nid, m.role, m.basis, m.matched_codes, n.code ncode,"
            " n.kind, n.label, n.icd9, n.icdo3_range, ms.code msrc, ns.code nsrc,"
            " m.review_status mrev, n.review_status nrev"
            " FROM disease_anatomy m JOIN anatomy_node n ON n.id=m.anatomy_node_id"
            " JOIN source ms ON ms.id=m.source_id JOIN source ns ON ns.id=n.source_id"
            " WHERE m.disease_id=%s", (did,))}
    got = {}
    for tier in ("primary", "subsite"):
        for x in d[tier]:
            m = x["mounted"]
            got[int(m["id"])] = (
                tier, int(x["id"]), x["kind"], x["label"], m["role"], m["basis"],
                m["matched_codes"], x["code"], m["provenance"]["source"]["code"],
                x["provenance"]["source"]["code"], m["provenance"]["review_status"],
                x["provenance"]["review_status"])
    c.eq(g, "响应行集合 = 该病的挂载行（一台不多一台不少）", sorted(got), sorted(want))
    for mid, v in got.items():
        w = want.get(mid)
        if w is not None:
            c.eq(g, f"挂载行 {mid} 逐列对照（含两档归位与两份出处）", v,
                 (w["role"], int(w["nid"]), str(w["kind"]), str(w["label"]), str(w["role"]),
                  str(w["basis"]), str(w["matched_codes"]), str(w["ncode"]), str(w["msrc"]),
                  str(w["nsrc"]), str(w["mrev"]), str(w["nrev"])))
    c.eq(g, "primary 与 subsite 的节点互斥（同一个节点不挂两档）",
         sorted({int(x["id"]) for x in d["primary"]} & {int(x["id"]) for x in d["subsite"]}), [])
    for tier in ("primary", "subsite"):
        c.eq(g, f"{tier} 条数跟着度量走", len(d[tier]), d["measures"][tier])
        seq = [(x["kind"], x["label"]) for x in d[tier]]
        c.eq(g, f"{tier} 内按 kind 再 label 排", seq, sorted(seq))
    _shape_ok(c, g, "节点行没有冒出层级键（库里没有父子边）",
              [*d["primary"], *d["subsite"]], ANAT_NODE_COLS, ("mounted",))
    _shape_ok(c, g, "挂载行就是那四列加出处",
              [x["mounted"] for t in ("primary", "subsite") for x in d[t]],
              ANAT_MOUNT_COLS, ())
    c.eq(g, "器官中文名整列为空（与 /api/meta 那条整维空态同一件事）",
         {x["label_zh"] for t in ("primary", "subsite") for x in d[t]}, {None})


def _vocab_histology(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"hist[{code}]"
    d = cl.get(f"/api/diseases/{code}/histology").json()
    _shell_ok(c, g, d, row, "histology", ["groups", "n_groups"])
    # 聚合在 Python 里重做一遍：路由那一句用的是 SQL 的 COUNT(DISTINCT …)
    agg: dict[str, dict] = {}
    for r in _q(db, "SELECT h.group_code gc, h.group_label gl, d.via_recode vr,"
                    " d.dataset_release_id mrel, h.dataset_release_id hrel, d.basis"
                    " FROM disease_histology d JOIN histology_code h ON h.id=d.histology_code_id"
                    " WHERE d.disease_id=%s", (did,)):
        a = agg.setdefault(str(r["gc"]), {"c": 0, "gl": set(), "vr": set(),
                                          "mr": set(), "hr": set(), "b": set()})
        a["c"] += 1
        a["gl"].add(r["gl"])
        a["vr"].add(r["vr"])
        a["mr"].add(r["mrel"])
        a["hr"].add(r["hrel"])
        a["b"].add(r["basis"])
    want = {gc: (a["c"], len(a["gl"]), len(a["vr"]), len(a["mr"]), len(a["hr"]), len(a["b"]))
            for gc, a in agg.items()}
    got = {str(x["group_code"]): (x["codes"], x["label_variants"], x["via_recodes"],
                                  x["mount_releases"], x["code_releases"], x["bases"])
           for x in d["groups"]}
    c.eq(g, "组档六列与 Python 侧重算一致（含档集合本身）", got, want)
    c.eq(g, "n_groups = groups 长度", d["n_groups"], len(d["groups"]))
    c.eq(g, "各档 codes 相加 = codes 度量（一档不落、也不重计）",
         sum(x["codes"] for x in d["groups"]), d["measures"]["codes"])
    c.eq(g, "组码升序回", [str(x["group_code"]) for x in d["groups"]], sorted(want))
    c.eq(g, "没有一档的出处列混进聚合层",
         {k for x in d["groups"] for k in x if "provenance" in k or k == "source_id"}, set())
    multi = [gc for gc, a in agg.items() if len(a["gl"]) > 1]
    samples = list(dict.fromkeys([*([d["groups"][0]["group_code"], d["groups"][-1]["group_code"]]
                                    if d["groups"] else []), *multi[:2]]))
    for gc in samples:
        r = cl.get(f"/api/diseases/{code}/histology", params={"group": str(gc)}).json()
        c.eq(g, f"?group={gc} 状态 200", r.get("group"), str(gc))
        c.eq(g, f"?group={gc} 码行数与档上 codes 一致", r["n_codes"], got[str(gc)][0])
        wset = sorted(str(x["cb"]) for x in _q(
            db, "SELECT h.code_behavior cb FROM disease_histology d"
                " JOIN histology_code h ON h.id=d.histology_code_id"
                " WHERE d.disease_id=%s AND h.group_code=%s", (did, str(gc))))
        c.eq(g, f"?group={gc} 码行集合与直查一致",
             sorted(str(x["code_behavior"]) for x in r["codes"]), wset)
        c.ok(g, f"?group={gc} 每行两份出处（码表一份、逐病展开一份）",
             all(x.get("provenance") and x["mounted"].get("provenance") for x in r["codes"]),
             f"{r['n_codes']} 行")
        _shape_ok(c, g, f"?group={gc} 码行就是 histology_code 那几列",
                  r["codes"], HIST_CODE_COLS, ("mounted",))
        _shape_ok(c, g, f"?group={gc} 展开行就是 disease_histology 那三列",
                  [x["mounted"] for x in r["codes"]], HIST_MOUNT_COLS, ())
        c.eq(g, f"?group={gc} 只回这一档",
             {str(x["group_code"]) for x in r["codes"]}, {str(gc)})
        if str(gc) in multi:  # 一档两个组名：两个名字都必须在码行上，不能被 MIN() 藏掉
            c.eq(g, f"?group={gc} 的组名变体都在行上",
                 {x["group_label"] for x in r["codes"]}, agg[str(gc)]["gl"])
    # 404 有两种要分开钉：码表里有、这一病没有的组码（这一条才抓得住「忘了按病筛」），
    # 和形态上就不可能存在的组码。999 是真实存在的三位组码，用它只会撞对。
    other = _q(db, "SELECT h.group_code gc FROM histology_code h"
                   " WHERE h.group_code NOT IN ("
                   "  SELECT h2.group_code FROM disease_histology d2"
                   "  JOIN histology_code h2 ON h2.id=d2.histology_code_id"
                   "  WHERE d2.disease_id=%s) GROUP BY h.group_code ORDER BY h.group_code"
                   " LIMIT 1", (did,))
    for gc, why in ((str(other[0]["gc"]), "别病有这一档"), ("99z", "不可能的写法")):
        e = cl.get(f"/api/diseases/{code}/histology", params={"group": gc})
        c.eq(g, f"这一病没有的组码 {gc} 404 且报出本病档数（{why}）",
             (e.status_code, f"{d['n_groups']} 档" in e.json().get("detail", "")), (404, True))


def _vocab_symptoms(c: Checks, cl: TestClient, db, code: str, did: int, row: dict,
                    rejected: set) -> None:
    g = f"symp[{code}]"
    d = cl.get(f"/api/diseases/{code}/symptoms").json()
    _shell_ok(c, g, d, row, "symptom", ["sources", "n_sources"])
    dbrows = _q(db, "SELECT s.id, s.name_lang, s.review_status, s.extract_method,"
                    " src.code scode"
                    " FROM symptom s JOIN source src ON src.id=s.source_id"
                    " WHERE s.disease_id=%s", (did,))
    # 非 rejected 在 Python 里筛：路由把条件写进 WHERE，这里换一个写法判同一件事
    keep = {int(r["id"]) for r in dbrows if str(r["review_status"]) != "rejected"}
    got = {int(x["id"]) for b in d["sources"] for x in b["items"]}
    c.eq(g, "响应条目 = 该病非 rejected 症状行（判非的不进、好行不落）", got, keep)
    c.eq(g, "库里这一病的判非行数与 /api/meta 的留痕同一件事",
         len(dbrows) - len(keep), len([r for r in dbrows if int(r["id"]) in rejected]))
    c.eq(g, "同一条症状只出现在一块（跨块不重计）",
         sum(b["n_items"] for b in d["sources"]), len(got))
    c.eq(g, "n_sources = 块数", d["n_sources"], len(d["sources"]))
    c.eq(g, "块数 = 该病症状实际涉及几个源",
         d["n_sources"], len({r["scode"] for r in dbrows if int(r["id"]) in keep}))
    c.eq(g, "判非条目一个都没混进来", sorted(got & rejected), [])
    per_src: dict[str, list] = {}
    for r in dbrows:
        if int(r["id"]) in keep:
            per_src.setdefault(str(r["scode"]), []).append(r)
    for b in d["sources"]:
        sc = str(b["source"]["code"])
        want = per_src.get(sc, [])
        c.eq(g, f"源 {sc} 条数与直查一致", b["n_items"], len(want))
        c.eq(g, f"源 {sc} 一块只有一种语言（块按源分，语言不能混进同一块）",
             {x["name_lang"] for x in b["items"]}, {b["name_lang"]})
        c.eq(g, f"源 {sc} 每条的出处就写着这个源",
             {x["provenance"]["source"]["code"] for x in b["items"]}, {sc})
        c.eq(g, f"源 {sc} 的块头 dataset 与块内条目同一个版本",
             {(x.get("provenance") or {}).get("dataset", {}).get("code")
              for x in b["items"] if (x.get("provenance") or {}).get("dataset")},
             {(b.get("dataset") or {}).get("code")} if b.get("dataset") else set())
        c.eq(g, f"源 {sc} 的抽取方式与直查一致",
             {x["provenance"]["extract_method"] for x in b["items"]},
             {str(r["extract_method"]) for r in want})
        c.ok(g, f"源 {sc} 的 dataset 块头与条目同源（不张冠李戴）",
             all(x["provenance"].get("dataset", {}).get("source_code") == sc
                 for x in b["items"] if x["provenance"].get("dataset")))
    c.eq(g, "en+zh 两块度量与响应分布一致",
         (sum(1 for x in per_src.values() for r in x if r["name_lang"] == "en"),
          sum(1 for x in per_src.values() for r in x if r["name_lang"] == "zh")),
         (d["measures"]["en"], d["measures"]["zh"]))
    c.eq(g, "频率带整列为空（§二.3 建而不填，页面按空态显示）",
         {x["freq_band"] for b in d["sources"] for x in b["items"]}, {None})
    _shape_ok(c, g, "症状行键集合就是 DDL 那几列", [x for b in d["sources"] for x in b["items"]],
              SYMPTOM_COLS, ())
    c.eq(g, "库里那列 provenance 换名带出、不被出处对象盖掉",
         {str(x.get("derive_marker", "<缺键>")) for b in d["sources"] for x in b["items"]}, {""})


def _vocab_risk(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"risk[{code}]"
    d = cl.get(f"/api/diseases/{code}/risk-factors").json()
    _shell_ok(c, g, d, row, "risk", ["limit", "genetic", "exposure"])
    dbrows = _q(db, "SELECT d.id, d.role, d.uri_tier, d.risk_factor_id rf, d.study_accession,"
                    " d.dataset_release_id, d.paf, d.pvalue_mlog, d.trait_label,"
                    " d.trait_uri, d.snps, d.effect_kind, a.kind fkind, a.label flabel,"
                    " fs.code fsrc, ds.code dsrc, a.review_status frev, d.review_status drev"
                    " FROM disease_risk_factor d JOIN risk_factor a ON a.id=d.risk_factor_id"
                    " JOIN source fs ON fs.id=a.source_id JOIN source ds ON ds.id=d.source_id"
                    " WHERE d.disease_id=%s", (did,))
    gen = [r for r in dbrows if r["role"] == "genetic"]
    exp = [r for r in dbrows if r["role"] == "exposure"]
    tiers: dict = {}
    for r in gen:
        t = tiers.setdefault(str(r["uri_tier"]), {"n": 0, "loci": set(), "st": set(), "rel": set()})
        t["n"] += 1
        t["loci"].add(int(r["rf"]))
        t["st"].add(r["study_accession"])
        t["rel"].add(r["dataset_release_id"])
    c.eq(g, "tiers 与 Python 侧分组一致（档集合本身也在内）",
         {str(t["uri_tier"]): (t["rows_"], t["loci"], t["studies"], t["releases"])
          for t in d["genetic"]["tiers"]},
         {k: (v["n"], len(v["loci"]), len(v["st"]), len(v["rel"])) for k, v in tiers.items()})
    total = int(d["genetic"]["total_rows"])
    c.eq(g, "各档 rows_ 相加 = total_rows = genetic 度量",
         (sum(t["rows_"] for t in d["genetic"]["tiers"]), total, d["measures"]["genetic"]),
         (total, len(gen), len(gen)))
    c.eq(g, "去重度点数与直查的 COUNT(DISTINCT) 一致", d["measures"]["genetic_loci"],
         len({int(r["rf"]) for r in gen}))
    c.eq(g, "exposure 节点去重度量与直查一致", d["measures"]["exposure_nodes"],
         len({int(r["rf"]) for r in exp}))
    lim = int(d["limit"])
    c.eq(g, "returned = items 长度", d["genetic"]["returned"], len(d["genetic"]["items"]))
    c.eq(g, "returned = min(limit, total_rows)", d["genetic"]["returned"], min(lim, total))
    c.eq(g, "truncated 只在真的截了时才真", d["genetic"]["truncated"], total > d["genetic"]["returned"])
    got = {int(x["id"]): x for x in d["genetic"]["items"]}
    c.eq(g, "榜内每一条都是这一病的 genetic 行",
         sorted(set(got) - {int(r["id"]) for r in gen}), [])
    wmap = {int(r["id"]): r for r in dbrows}
    for i, x in got.items():
        w = wmap[i]
        c.eq(g, f"关联行 {i} 的因子就是直查那一行的节点",
             (int(x["factor"]["id"]), x["factor"]["kind"], x["factor"]["label"],
              x["factor"]["provenance"]["source"]["code"],
              x["provenance"]["source"]["code"], x["factor"]["provenance"]["review_status"],
              x["provenance"]["review_status"], x["trait_label"], x["snps"], x["effect_kind"]),
             (int(w["rf"]), str(w["fkind"]), str(w["flabel"]), str(w["fsrc"]), str(w["dsrc"]),
              str(w["frev"]), str(w["drev"]), str(w["trait_label"]), str(w["snps"]),
              str(w["effect_kind"])))
    pl = [float(x["pvalue_mlog"]) for x in d["genetic"]["items"]]
    c.eq(g, "榜内 pvalue 单调不增", pl, sorted(pl, reverse=True))
    if d["genetic"]["truncated"]:
        # 落榜的第一名（同一排序下的第 returned+1 行）不得强于在榜的最后一名：
        # 截断只可能截掉尾巴，不可能把 p=1e-300 留在门外
        nxt = _q(db, "SELECT pvalue_mlog m FROM disease_risk_factor"
                     " WHERE disease_id=%s AND role='genetic'"
                     " ORDER BY pvalue_mlog DESC LIMIT 1 OFFSET %s", (did, len(pl)))
        c.ok(g, "截断没把好结果留在门后（落榜最高分不高于在榜最低分）",
             float(nxt[0]["m"]) <= pl[-1], f"榜尾 {pl[-1]}，门后 {float(nxt[0]['m'])}")
    ex = {int(x["id"]): x for x in d["exposure"]["items"]}
    c.eq(g, "exposure 全量回且不截断",
         (d["exposure"]["returned"], len(ex), d["measures"]["exposure"]),
         (len(exp), len(exp), len(exp)))
    c.eq(g, "两层行集合不相交（一行不会既进榜又进清单）", sorted(set(got) & set(ex)), [])
    c.eq(g, "榜里每条 role=genetic 且节点是位点（不靠 label 猜层）",
         {(x["role"], x["factor"]["kind"]) for x in d["genetic"]["items"]},
         {("genetic", "genetic_locus")} if gen else set())
    c.eq(g, "清单里每条 role=exposure 且节点是暴露",
         {(x["role"], x["factor"]["kind"]) for x in d["exposure"]["items"]},
         {("exposure", "exposure")} if exp else set())
    c.eq(g, "两层的节点 id 互斥（同一个节点不同时是位点和暴露）",
         len({int(x["factor"]["id"]) for x in d["genetic"]["items"]}
             & {int(x["factor"]["id"]) for x in d["exposure"]["items"]}), 0)
    c.eq(g, "paf 一列在榜与清单上都空（§二.2 整维级的坑）",
         {x["paf"] for x in [*d["genetic"]["items"], *d["exposure"]["items"]]}, {None})
    _shape_ok(c, g, "关联行没有冒出归因分数键",
              [*d["genetic"]["items"], *d["exposure"]["items"]], RISK_ASSOC_COLS, ("factor",))
    _shape_ok(c, g, "节点行就是 risk_factor 那四列",
              [x["factor"] for x in [*d["genetic"]["items"], *d["exposure"]["items"]]],
              RISK_NODE_COLS, ())
    r5 = cl.get(f"/api/diseases/{code}/risk-factors", params={"limit": 5}).json()
    c.eq(g, "limit=5 只收窄榜、不改度量与清单",
         (r5["genetic"]["returned"], r5["measures"], r5["exposure"]["returned"]),
         (min(5, total), d["measures"], d["exposure"]["returned"]))


# ------------------------------------------------------------- 研究层四维
# 这一层的四台与前面三批最不一样：行多到必须分页。所以断言的重心从"数字对不对"挪到
# 分页特有的三件事上——(1) 这一页确实是那个序切出来的那一段，(2) 分面说的"还能筛什么、
# 各剩几行"跟真筛一次的结果一致，(3) 源命中数与落库行数分开回，且 publication 那一台
# 必须不相等。两路对照照旧：路由发一条 SQL，这边把整维的键取回 Python 里排序、切片、
# 数分面，再对返回那一页逐列比。
PHASE_NONE = "(none)"                                    # 与路由同一个哨兵，两边各写一份
TRIAL_COLS = ("id", "disease_id", "dataset_code", "nct_id", "brief_title", "title",
              "overall_status", "status_bucket", "study_type", "phases", "enrollment",
              "design_info", "conditions", "interventions", "arm_groups", "primary_outcome",
              "elig_sex", "healthy_volunteers", "lead_sponsor", "collaborators",
              "location_countries", "fda_regulated", "why_stopped", "matched_terms")
TRIAL_JSON = ("phases", "design_info", "conditions", "interventions", "arm_groups",
              "collaborators", "location_countries", "matched_terms")
TRIAL_INT = ("id", "disease_id", "enrollment", "healthy_volunteers", "fda_regulated")
PUB_COLS = ("id", "disease_id", "dataset_code", "ext_key", "pmid", "doi", "title", "journal",
            "pub_year", "is_oa", "in_epmc", "has_pdf", "has_abstract", "matched_terms")
PUB_INT = ("id", "disease_id", "pmid", "pub_year", "is_oa", "in_epmc", "has_pdf", "has_abstract")
DRUG_COLS = ("id", "disease_id", "dataset_code", "drug_id", "drug_name", "phase", "moa")
TARGET_NODE_COLS = ("id", "ot_id", "approved_symbol", "approved_name", "dataset_code")
TARGET_REL_COLS = ("id", "disease_id", "target_id", "dataset_code", "score", "novelty",
                   "datasource_scores", "node_used")
RESEARCH_PAGE_KEYS = ["facets", "filters", "items", "page", "source_hit"]


def _typed(db, sql, params, cols, ints=(), decs=(), jsons=()):
    """直查行 → 与接口同一套出参类型（整数、Decimal 转数、JSON 解码），按业务键建索引。

    这边不 import 路由的转换函数：类型转换写错一遍，两路就会一起错，对照就白做了。
    """
    out = {}
    for r in _q(db, sql, params):
        w = {k: r[k] for k in cols}
        for k in ints:
            w[k] = _num(r[k])
        for k in decs:
            w[k] = _dec(r[k])
        for k in jsons:
            w[k] = _jload(r[k])
        out[r["__key"]] = (w, str(r["__src"]), str(r["__rev"]))
    return out


def _num(v):
    return None if v is None else int(v)


def _dec(v):
    return None if v is None else float(v)


def _jload(v):
    return None if v is None else json.loads(v)


def check_research(c: Checks, cl: TestClient, db, ids: dict) -> None:
    per_code = {i["code"]: i for i in cl.get("/api/diseases").json()["items"]}
    for code in CODES:
        did = ids[code]
        # 一台的形状（少一个键、多一列、接口 500）只该红它自己那一格，其余 71 台继续跑完
        for g, fn in (("resT", _research_trials), ("resP", _research_publications),
                      ("resG", _research_targets), ("resD", _research_drugs)):
            try:
                fn(c, cl, db, code, did, per_code[code])
            except Exception as e:  # noqa: BLE001 跑测器宁可红一片，也不许悄悄少跑一段
                c.ok(g, f"{code} 这一台的对账整台跑完（没抛异常）", False,
                     f"{type(e).__name__}: {e}")


def _page_ok(c: Checks, g: str, d: dict, ordered: list, limit: int, offset: int) -> dict:
    """分页三件事：总数、这一页是那一段、后面还有没有。`ordered` 是 Python 侧排好的全序。"""
    pg = d["page"]
    c.eq(g, "page 五键齐", sorted(pg), ["has_more", "limit", "offset", "returned", "total_rows"])
    c.eq(g, "page.total_rows = 整维行数", pg["total_rows"], len(ordered))
    c.eq(g, "page 回显的 limit/offset 就是发过去的那两个", (pg["limit"], pg["offset"]),
         (limit, offset))
    c.eq(g, "returned = items 长度", pg["returned"], len(d["items"]))
    c.eq(g, "has_more 说的是这一段后面还有行", pg["has_more"], offset + len(d["items"]) < len(ordered))
    c.eq(g, "items 的条数不超过 limit", len(d["items"]) <= limit, True)
    return pg


def _facets_ok(c: Checks, g: str, d: dict, axes: list[str]) -> None:
    """分面是聚合不是行：只有 value 与 rows 两键，且回的轴就是声明的那些。

    出处一旦挂进聚合项，页面就会把"这一档有 1,200 行"当成某一行事实来署名——而它是
    这个病这一维数出来的，没有哪一行是它的出处。
    """
    c.eq(g, "facets 就回这些轴", sorted(d["facets"]), sorted(axes))
    # 分面轴的名字就是客户端要发的那个参数名。对不上等于一排点不动的选项：前端按参数名
    # 过白名单，认不出的键被直接丢掉——筛子看着在，其实什么都没筛。
    c.ok(g, "每一个分面轴都是 filters 回显的那个参数（分面键必须可请求）",
         set(axes) <= set(d["filters"]),
         f"分面 {sorted(axes)} 不在 filters {sorted(d['filters'])} 里")
    c.eq(g, "每个分面项只有 value 与 rows（聚合层不许挂出处）",
         {tuple(sorted(f)) for k in axes for f in d["facets"][k]}, {("rows", "value")})
    c.ok(g, "分面每档行数都是正整数（0 行的档不该占一个选项）",
         all(isinstance(f["rows"], int) and f["rows"] > 0
             for k in axes for f in d["facets"][k]),
         f"{ {k: len(d['facets'][k]) for k in axes} }")


def _hit_ok(c: Checks, g: str, db, did: int, d: dict, metric: str, captured) -> None:
    """源命中数与落库行数分开回；captured 传 True/False 是把这一维的实测结论钉住。"""
    w = _one(db, "SELECT value, f.extract_method, f.review_status, s.code AS src"
                 " FROM stat_fact f JOIN source s ON s.id = f.source_id"
                 " WHERE disease_id=%s AND metric=%s AND estimate_basis='query_count'"
                 " AND f.review_status<>'rejected'", (did, metric))
    h = d["source_hit"]
    c.eq(g, "source_hit.value = stat_fact 里那一行 query_count", h["value"], int(w["value"]))
    c.eq(g, "source_hit.stored_rows = measures.rows", h["stored_rows"], d["measures"]["rows"])
    c.eq(g, "captured 是现算的相等判断", h["captured"], h["value"] == h["stored_rows"])
    c.eq(g, f"captured 与实测一致（{metric}）", h["captured"], captured)
    c.eq(g, "not_captured 是那笔差额", h["not_captured"], max(h["value"] - h["stored_rows"], 0))
    p = h.get("provenance") or {}
    c.eq(g, "命中数带的是 stat_fact 那一行自己的出处（不是研究表的）",
         (p.get("source", {}).get("code"), p.get("extract_method"), p.get("review_status")),
         (w["src"], w["extract_method"], w["review_status"]))
    c.ok(g, "命中数指向的源带具名许可（页面要说这个数是哪个源报的）",
         bool(p.get("source", {}).get("license")), str(p.get("source"))[:70])


def _research_trials(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"resT[{code}]"
    d = cl.get(f"/api/diseases/{code}/trials").json()
    _shell_ok(c, g, d, row, "trial", RESEARCH_PAGE_KEYS)
    keys = _q(db, "SELECT id, nct_id, status_bucket, phases FROM trial WHERE disease_id=%s",
              (did,))
    ordered = sorted(keys, key=lambda r: r["nct_id"])       # 路由用 SQL 排序，这里用 Python
    pg = _page_ok(c, g, d, ordered, 50, 0)
    _facets_ok(c, g, d, ["status_bucket", "phase"])
    _hit_ok(c, g, db, did, d, "trial_count", True)
    want_nct = [str(r["nct_id"]) for r in ordered[:pg["limit"]]]
    c.eq(g, "这一页就是 nct_id 序切出来的那一段（含顺序）",
         [x["nct_id"] for x in d["items"]], want_nct)
    c.eq(g, "一病之内 nct_id 不重复（这个序是全序，翻页不会重行）",
         len({r["nct_id"] for r in keys}), len(keys))
    c.eq(g, "measures.nct = 去重 NCT 数（一病之内与行数相等）",
         d["measures"]["nct"], len({r["nct_id"] for r in keys}))
    fb = {}
    for r in keys:
        fb[r["status_bucket"]] = fb.get(r["status_bucket"], 0) + 1
    c.eq(g, "status_bucket 分面 = Python 计数",
         {x["value"]: x["rows"] for x in d["facets"]["status_bucket"]}, fb)
    fp = {}
    for r in keys:
        ph = _jload(r["phases"])
        for v in (set(ph) if ph else {PHASE_NONE}):
            fp[v] = fp.get(v, 0) + 1
    c.eq(g, "phase 分面 = Python 计数（数组每档各计一次、无档归 (none)）",
         {x["value"]: x["rows"] for x in d["facets"]["phase"]}, fp)
    c.ok(g, "phase 分面合计 ≥ 行数（一档多行的研究重复计）",
         sum(fp.values()) >= len(keys), f"{sum(fp.values())} vs {len(keys)}")
    top = max(d["facets"]["phase"], key=lambda x: (x["rows"], str(x["value"])))
    f = cl.get(f"/api/diseases/{code}/trials",
               params={"phase": top["value"], "limit": 200}).json()
    c.eq(g, f"?phase={top['value']} 的 total_rows 就是分面那个数",
         f["page"]["total_rows"], fp[top["value"]])
    if top["value"] == PHASE_NONE:
        c.ok(g, "?phase=(none) 回的行真的没有 phases",
             all(x["phases"] is None for x in f["items"]), f"{len(f['items'])} 行")
    else:
        c.ok(g, f"?phase={top['value']} 回的行数组里真的含它",
             all(top["value"] in (x["phases"] or []) for x in f["items"]), f"{len(f['items'])} 行")
    c.eq(g, "分面不受过滤影响（选项、顺序与各档行数都不许跟着筛变）",
         [(x["value"], x["rows"]) for x in f["facets"]["phase"]],
         [(x["value"], x["rows"]) for x in d["facets"]["phase"]])
    c.eq(g, "筛完 measures 不变（它说的是整维，不是这一页）", f["measures"], d["measures"])
    c.eq(g, "未请求时 filters 回显每一个轴都是未设",
         d["filters"], {"status_bucket": None, "phase": None, "include": []})
    c.eq(g, "筛过的轴在 filters 里回显出来（页面据此标出当前筛）",
         (f["filters"]["phase"], f["filters"]["status_bucket"]), (top["value"], None))
    want = _typed(
        db,
        "SELECT nct_id AS __key, t.id, t.disease_id, t.dataset_code, t.nct_id, t.brief_title,"
        " t.title, t.overall_status, t.status_bucket, t.study_type, t.phases, t.enrollment,"
        " t.design_info, t.conditions, t.interventions, t.arm_groups, t.primary_outcome,"
        " t.elig_sex, t.healthy_volunteers, t.lead_sponsor, t.collaborators,"
        " t.location_countries, t.fda_regulated, t.why_stopped, t.matched_terms,"
        " s.code AS __src, t.review_status AS __rev"
        " FROM trial t JOIN source s ON s.id = t.source_id"
        f" WHERE disease_id=%s AND nct_id IN ({','.join(['%s'] * len(want_nct))})",
        (did, *want_nct), TRIAL_COLS, ints=TRIAL_INT, jsons=TRIAL_JSON)
    c.eq(g, "返回那一页在库里都能找到（不漏一行不多一行）", sorted(want), sorted(set(want_nct)))
    for it in d["items"]:
        w, src, rev = want[str(it["nct_id"])]
        c.eq(g, f"试验 {it['nct_id']} 全列对照（含 JSON 解码）",
             {**{k: it[k] for k in TRIAL_COLS}, "provenance": None},
             {**w, "provenance": None})
        c.eq(g, f"试验 {it['nct_id']} 出处指向 trial 那一行的源与复核状态",
             (it["provenance"]["source"]["code"], it["provenance"]["review_status"]), (src, rev))
    _shape_ok(c, g, "行就是 trial 的业务列，没有多出来的评分键", d["items"], TRIAL_COLS, ())
    c.ok(g, "默认不带两段大字段（一页 ~120 KB 而不是 ~290 KB）",
         all("eligibility" not in x and "publications" not in x for x in d["items"]))
    c.eq(g, "整页都没有 disease_id 以外的病（按病筛真的生效）",
         {x["disease_id"] for x in d["items"]}, {did})


def _research_publications(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"resP[{code}]"
    d = cl.get(f"/api/diseases/{code}/publications").json()
    _shell_ok(c, g, d, row, "publication", RESEARCH_PAGE_KEYS)
    keys = _q(db, "SELECT id, pub_year, is_oa, in_epmc FROM publication WHERE disease_id=%s",
              (did,))
    ordered = sorted(keys, key=lambda r: int(r["id"]))      # 装载序＝EPMC 相关度序
    pg = _page_ok(c, g, d, ordered, 50, 0)
    _facets_ok(c, g, d, ["year", "is_oa"])
    _hit_ok(c, g, db, did, d, "publication_count", False)
    c.eq(g, "每病固定 500 行（上限样本，与命中数不等）", d["measures"]["rows"], 500)
    c.ok(g, "命中数严格大于落库行数（这一维没取满）",
         d["source_hit"]["value"] > d["source_hit"]["stored_rows"],
         f"{d['source_hit']['value']} vs {d['source_hit']['stored_rows']}")
    c.eq(g, "这一页就是 id（装载序＝源相关度序）切出来的那一段",
         [x["id"] for x in d["items"]], [int(r["id"]) for r in ordered[:pg["limit"]]])
    c.eq(g, "id 严格升序（全序，翻页不重行）",
         all(a["id"] < b["id"] for a, b in zip(d["items"], d["items"][1:])), True)
    yrs, oas = {}, {}
    for r in keys:
        yrs[r["pub_year"]] = yrs.get(r["pub_year"], 0) + 1
        oas[r["is_oa"]] = oas.get(r["is_oa"], 0) + 1
    c.eq(g, "year 分面（数的是 pub_year 列）= Python 计数",
         {x["value"]: x["rows"] for x in d["facets"]["year"]}, yrs)
    c.eq(g, "is_oa 分面 = Python 计数（0 与 1 两档，没有 NULL 档）",
         {x["value"]: x["rows"] for x in d["facets"]["is_oa"]}, oas)
    c.eq(g, "is_oa=1 的分面数与 oa 度量同一份", oas.get(1, 0), d["measures"]["oa"])
    c.eq(g, "in_epmc 度量 = 这一病 in_epmc=1 的行数（独立另问一次）",
         d["measures"]["in_epmc"], sum(1 for r in keys if r["in_epmc"] == 1))
    top_year = max(d["facets"]["year"], key=lambda x: (x["rows"], x["value"]))
    fy = cl.get(f"/api/diseases/{code}/publications",
                params={"year": top_year["value"], "limit": 200}).json()
    c.eq(g, f"?year={top_year['value']} 的 total_rows 就是分面那个数",
         fy["page"]["total_rows"], yrs[top_year["value"]])
    c.ok(g, f"?year={top_year['value']} 回的行 pub_year 全是它",
         all(x["pub_year"] == top_year["value"] for x in fy["items"]), f"{len(fy['items'])} 行")
    fo = cl.get(f"/api/diseases/{code}/publications", params={"is_oa": 1, "limit": 200}).json()
    c.eq(g, "?is_oa=1 的 total_rows 就是分面那个数", fo["page"]["total_rows"], oas.get(1, 0))
    c.ok(g, "?is_oa=1 回的行没有一条 is_oa 不是 1",
         all(x["is_oa"] == 1 for x in fo["items"]), f"{len(fo['items'])} 行")
    for fd in (fy, fo):
        c.eq(g, "两个轴的分面都不跟着筛变",
             {k: [(x["value"], x["rows"]) for x in fd["facets"][k]] for k in ("year", "is_oa")},
             {k: [(x["value"], x["rows"]) for x in d["facets"][k]] for k in ("year", "is_oa")})
    c.eq(g, "未请求时 filters 回显两个轴都是未设", fy["filters"]["is_oa"], None)
    c.eq(g, "筛过的轴在 filters 里回显出来",
         (fy["filters"]["year"], fo["filters"]["is_oa"]), (top_year["value"], 1))
    want = _typed(
        db, "SELECT p.id AS __key, p.id, p.disease_id, p.dataset_code, p.ext_key, p.pmid,"
            " p.doi, p.title, p.journal, p.pub_year, p.is_oa, p.in_epmc, p.has_pdf,"
            " p.has_abstract, p.matched_terms, s.code AS __src, p.review_status AS __rev"
            " FROM publication p JOIN source s ON s.id = p.source_id"
            " WHERE p.disease_id=%s ORDER BY p.id LIMIT 50", (did,),
        PUB_COLS, ints=PUB_INT, jsons=("matched_terms",))
    c.eq(g, "返回那一页在库里都能找到（不漏一行不多一行）", sorted(want),
         sorted(int(r["id"]) for r in ordered[:pg["limit"]]))
    for it in d["items"]:
        w, src, rev = want[it["id"]]
        c.eq(g, f"文献 {it['ext_key'][:24]} 全列对照",
             {**{k: it[k] for k in PUB_COLS}, "provenance": None},
             {**w, "provenance": None})
        c.eq(g, f"文献 {it['ext_key'][:24]} 出处指向 publication 那一行",
             (it["provenance"]["source"]["code"], it["provenance"]["review_status"]),
             (src, rev))
    _shape_ok(c, g, "行就是 publication 的业务列", d["items"], PUB_COLS, ())
    c.eq(g, "整页都是这一病（按病筛真的生效）", {x["disease_id"] for x in d["items"]}, {did})
    c.eq(g, "ext_key 在一病之内唯一（它是业务键）",
         len({x["ext_key"] for x in d["items"]}), len(d["items"]))


def _research_targets(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"resG[{code}]"
    d = cl.get(f"/api/diseases/{code}/targets").json()
    _shell_ok(c, g, d, row, "target", ["items", "page", "source_hit"])
    keys = _q(db, "SELECT dt.id, dt.score, t.ot_id FROM disease_target dt"
                  " JOIN target t ON t.id=dt.target_id WHERE dt.disease_id=%s", (did,))
    ordered = sorted(keys, key=lambda r: (-float(r["score"]), r["ot_id"]))
    pg = _page_ok(c, g, d, ordered, 50, 0)
    _hit_ok(c, g, db, did, d, "target_count", True)
    c.eq(g, "这一页就是 score 降序 + ot_id 兜底切出来的那一段",
         [x["mounted"]["id"] for x in d["items"]], [int(r["id"]) for r in ordered[:pg["limit"]]])
    sc = [x["mounted"]["score"] for x in d["items"]]
    c.eq(g, "榜按 score 不升", all(a >= b for a, b in zip(sc, sc[1:])), True)
    c.eq(g, "并列段内 ot_id 升序（不兜底就会重行漏行）",
         [(x["mounted"]["score"], x["ot_id"]) for x in d["items"]],
         sorted([(x["mounted"]["score"], x["ot_id"]) for x in d["items"]],
                key=lambda p: (-p[0], p[1])))
    c.eq(g, "整维没有同 (score, ot_id) 的两行（所以这个序是全序，兜底那一列够用）",
         len({(float(r["score"]), r["ot_id"]) for r in keys}), len(keys))
    c.eq(g, "一病之内 target_id 不重复（重复就是 JOIN 散列了）",
         len({int(r["id"]) for r in keys}), len(keys))
    page_ids = [int(r["id"]) for r in ordered[:pg["limit"]]]
    want = {}
    for r in _q(db, "SELECT dt.id AS __key, dt.id, dt.disease_id, dt.target_id,"
                    " dt.dataset_code, dt.score, dt.novelty, dt.datasource_scores, dt.node_used,"
                    " t.id AS nid, t.ot_id, t.approved_symbol, t.approved_name,"
                    " t.dataset_code AS tdc, ms.code AS msrc, ns.code AS nsrc,"
                    " dt.review_status AS mrev, t.review_status AS nrev"
                    " FROM disease_target dt JOIN target t ON t.id=dt.target_id"
                    " JOIN source ms ON ms.id=dt.source_id JOIN source ns ON ns.id=t.source_id"
                    " WHERE dt.disease_id=%s AND dt.id IN ("
                    + ", ".join(["%s"] * len(page_ids)) + ")", (did, *page_ids)):
        rel = {k: r[k] for k in TARGET_REL_COLS if k not in ("id", "disease_id", "target_id",
                                                             "score", "novelty")}
        rel.update({"id": int(r["id"]), "disease_id": int(r["disease_id"]),
                    "target_id": int(r["target_id"]), "score": _dec(r["score"]),
                    "novelty": _dec(r["novelty"]),
                    "datasource_scores": _jload(r["datasource_scores"])})
        node = {"id": int(r["nid"]), "ot_id": r["ot_id"],
                "approved_symbol": r["approved_symbol"],
                "approved_name": r["approved_name"], "dataset_code": r["tdc"]}
        want[int(r["__key"])] = (rel, node, str(r["msrc"]), str(r["nsrc"]),
                                 str(r["mrev"]), str(r["nrev"]))
    c.eq(g, "这一页每一行在库里都取到恰好一份（JOIN 不丢行也不散行）", len(want), len(page_ids))
    for it in d["items"]:
        rel, node, msrc, nsrc, mrev, nrev = want[it["mounted"]["id"]]
        c.eq(g, f"靶点 {it['ot_id']} 关联行全列对照",
             {**{k: it["mounted"][k] for k in TARGET_REL_COLS}, "provenance": None},
             {**rel, "provenance": None})
        c.eq(g, f"靶点 {it['ot_id']} 节点行全列对照",
             {**{k: it[k] for k in TARGET_NODE_COLS}, "provenance": None},
             {**node, "provenance": None})
        c.eq(g, f"靶点 {it['ot_id']} 两份出处分开（关联行与节点行各有各的源与复核）",
             (it["mounted"]["provenance"]["source"]["code"], it["provenance"]["source"]["code"],
              it["mounted"]["provenance"]["review_status"], it["provenance"]["review_status"]),
             (msrc, nsrc, mrev, nrev))
        c.eq(g, f"靶点 {it['ot_id']} 关联行的 target_id 指向节点行",
             it["mounted"]["target_id"], it["id"])
    _shape_ok(c, g, "节点行没有冒出分数键（分数在关联行上）", d["items"], TARGET_NODE_COLS,
              ("mounted",))
    _shape_ok(c, g, "关联行就是 disease_target 那八列",
              [x["mounted"] for x in d["items"]], TARGET_REL_COLS, ())
    c.eq(g, "整页都是这一病", {x["mounted"]["disease_id"] for x in d["items"]}, {did})
    c.eq(g, "一病之内 ot_id 不重复（唯一键保证）",
         len({x["ot_id"] for x in d["items"]}), len(d["items"]))
    c.eq(g, "measures.rows = 这一病的关联行数（一个靶点挂几个病是另一件事）",
         d["measures"]["rows"], len(keys))


def _research_drugs(c: Checks, cl: TestClient, db, code: str, did: int, row: dict) -> None:
    g = f"resD[{code}]"
    d = cl.get(f"/api/diseases/{code}/drugs").json()
    _shell_ok(c, g, d, row, "drug", RESEARCH_PAGE_KEYS)
    # 期望序另问一次 SQL 的 ORDER BY drug_name，不在 Python 里 sorted()：
    # utf8mb4_0900_ai_ci 把 '.' 排在 '(' 前，码位序相反（resX 里钉着这条实测），
    # 拿码位序当期望序只会红在无关的地方。逐列内容对照与分面计数仍是两边各算一遍。
    ordered = _q(db, "SELECT id, drug_name, phase FROM drug WHERE disease_id=%s"
                     " ORDER BY drug_name", (did,))
    pg = _page_ok(c, g, d, ordered, 50, 0)
    _facets_ok(c, g, d, ["phase"])
    _hit_ok(c, g, db, did, d, "drug_count", True)
    c.eq(g, "这一页就是药名升序切出来的那一段",
         [x["drug_name"] for x in d["items"]], [r["drug_name"] for r in ordered[:pg["limit"]]])
    c.eq(g, "一病之内药名唯一（所以 rows 与去重药名数相等）",
         (d["measures"]["rows"], d["measures"]["names"]),
         (len(ordered), len({r["drug_name"] for r in ordered})))
    fp = {}
    for r in ordered:
        fp[r["phase"]] = fp.get(r["phase"], 0) + 1
    c.eq(g, "phase 分面 = Python 计数", {x["value"]: x["rows"] for x in d["facets"]["phase"]}, fp)
    top = max(d["facets"]["phase"], key=lambda x: (x["rows"], x["value"]))
    f = cl.get(f"/api/diseases/{code}/drugs", params={"phase": top["value"]}).json()
    c.eq(g, f"?phase={top['value']} 的 total_rows 就是分面那个数", f["page"]["total_rows"],
         fp[top["value"]])
    c.ok(g, f"?phase={top['value']} 回的行 phase 全是它",
         all(x["phase"] == top["value"] for x in f["items"]), f"{len(f['items'])} 行")
    c.eq(g, "筛完度量不变（measures 说的是整维，不是这一页）", f["measures"], d["measures"])
    c.eq(g, "分面不受过滤影响（选项、顺序与各档行数都不许跟着筛变）",
         [(x["value"], x["rows"]) for x in f["facets"]["phase"]],
         [(x["value"], x["rows"]) for x in d["facets"]["phase"]])
    c.eq(g, "filters 回显：默认未筛、筛过就带出那个值",
         (d["filters"]["phase"], f["filters"]["phase"]), (None, top["value"]))
    want = _typed(
        db, "SELECT dg.drug_name AS __key, dg.id, dg.disease_id, dg.dataset_code, dg.drug_id,"
            " dg.drug_name, dg.phase, dg.moa, s.code AS __src, dg.review_status AS __rev"
            " FROM drug dg JOIN source s ON s.id = dg.source_id"
            " WHERE dg.disease_id=%s ORDER BY dg.drug_name LIMIT 50", (did,),
        DRUG_COLS, ints=("id", "disease_id"), jsons=("moa",))
    c.eq(g, "返回那一页在库里都能找到（不漏一行不多一行）", sorted(want),
         sorted(ordered[i]["drug_name"] for i in range(pg["returned"])))
    for it in d["items"]:
        w, src, rev = want[it["drug_name"]]
        c.eq(g, f"药 {it['drug_name'][:24]} 全列对照",
             {**{k: it[k] for k in DRUG_COLS}, "provenance": None},
             {**w, "provenance": None})
        c.eq(g, f"药 {it['drug_name'][:24]} 出处指向 drug 那一行",
             (it["provenance"]["source"]["code"], it["provenance"]["review_status"]),
             (src, rev))
    _shape_ok(c, g, "行就是 drug 的业务列（没有靶点键）", d["items"], DRUG_COLS, ())
    c.eq(g, "整页都是这一病", {x["disease_id"] for x in d["items"]}, {did})
    c.eq(g, "moa 有值的是数组、没值是 NULL 而不是空数组",
         {type(x["moa"]).__name__ for x in d["items"]} - {type(None).__name__}, {"list"})


def _bad(cl: TestClient, path: str) -> tuple[int, str]:
    """打一条注定要回错误的请求。路由抛出来也算这一条红——白名单一旦被绕过，
    非法列名会直接进 SQL，而 TestClient 默认把服务端异常再抛一遍，那会带走整跑。"""
    try:
        r = cl.get(path)
        body = r.json()
        return r.status_code, str(body.get("detail", body))
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def _research_edges(c: Checks, cl: TestClient, db, ids: dict) -> None:
    """分页特有的五件：走完全表、越界、参数上限、过滤值合法性的说法、以及那个"id 序
    就是源相关度序"的承诺是不是真的。"""
    did = ids["bladder"]
    n = int(_col(db, "SELECT COUNT(*) FROM drug WHERE disease_id=%s", (did,)))
    seen, off = [], 0
    while True:
        b = cl.get("/api/diseases/bladder/drugs", params={"limit": 7, "offset": off}).json()
        if not b["items"]:
            break
        seen += [x["drug_name"] for x in b["items"]]
        c.eq("resX", f"bladder drugs 第 {off // 7 + 1} 页 returned",
             b["page"]["returned"], min(7, n - off))
        off += 7
        if off > n + 7:
            c.ok("resX", "翻页不会走不完（有 bug 就跳出去）", False, f"{n} 行翻了 {off}")
            break
    want = [r["drug_name"] for r in _q(
        db, "SELECT drug_name FROM drug WHERE disease_id=%s ORDER BY drug_name", (did,))]
    c.eq("resX", "逐页取完 = 整表且顺序一致（分页不重行、不漏行）", seen, want)
    c.eq("resX", "页数 = ceil(行数 / limit)", (n + 6) // 7, off // 7)
    # 接口序是引擎的字典序不是码位序：这两个药名一个以 '.' 开头一个以 '(' 开头，
    # MySQL 把 '.' 排在前面，Python 的 sorted() 相反。前端若自己 sort() 一遍，
    # 与接口分页序就对不上，看着就像翻页跳行——所以这条既是路由 conventions 的依据，
    # 也是 _research_drugs 拿 SQL 取期望序（而不是 sorted）的理由。
    probe = [".ALPHA.-TOCOPHERYLOXYACETIC ACID", "(R)-PFI-2"]
    got = [r["drug_name"] for r in _q(
        db, "SELECT drug_name FROM drug WHERE drug_name IN (%s, %s)"
            " GROUP BY drug_name ORDER BY drug_name", tuple(probe))]
    c.eq("resX", "这两个药名库里都在（否则下一条没有对照物）", sorted(got), sorted(probe))
    c.eq("resX", "引擎序与 Python 码位序相反（分页序不可在前端复现）", got, sorted(probe, reverse=True))
    # 兜底那一列不是摆设：同分组实测存在，且按 (病, 分数) 数——翻页会撞上的并列发生在同一病的
    # 一页里；按全库 score 数是另一组数（实测 3,822 组 / 最大 384 行），与接口那句不是一个分母
    tie = _one(db, "SELECT COUNT(*) AS g, IFNULL(MAX(c), 0) AS mx FROM (SELECT COUNT(*) c"
                   " FROM disease_target GROUP BY disease_id, score HAVING c > 1) t")
    c.ok("resX", "同一病内靶点同分组真的存在（所以 score 后面要跟 ot_id）", tie["g"] > 0,
         f"{tie['g']} 组同分，最大一组 {tie['mx']} 行")
    for code, p, off2 in (("lung", "trials", 3), ("lung", "publications", 7),
                          ("breast_female", "targets", 5)):
        b = cl.get(f"/api/diseases/{code}/{p}", params={"limit": 5, "offset": off2}).json()
        full = cl.get(f"/api/diseases/{code}/{p}", params={"limit": 50}).json()
        key = "nct_id" if p == "trials" else ("id" if p == "publications" else None)
        got = [x["mounted"]["id"] if key is None else x[key] for x in b["items"]]
        exp = [x["mounted"]["id"] if key is None else x[key]
               for x in full["items"][off2:off2 + 5]]
        c.eq("resX", f"{code} {p} offset={off2} = 同一序里切的那五段", got, exp)
    b = cl.get("/api/diseases/lung/trials", params={"offset": 999999}).json()
    c.eq("resX", "越界 offset 回空页而不是回整表",
         (len(b["items"]), b["page"]["returned"], b["page"]["has_more"]), (0, 0, False))
    c.ok("resX", "越界时 total_rows 仍然如实报整维行数", b["page"]["total_rows"] > 0,
         str(b["page"]["total_rows"]))
    for bad, path in (("limit=0", "lung/trials"), ("limit=201", "lung/trials"),
                      ("offset=-1", "lung/drugs")):
        code, _ = _bad(cl, f"/api/diseases/{path}?{bad}")
        c.eq("resX", f"{path}?{bad} 是 422（越界参数不静默夹到边界）", code, 422)
    for bad in ("phase=NOPE", "status_bucket=completed", "include=bogus"):
        code, detail = _bad(cl, f"/api/diseases/lung/trials?{bad}")
        c.eq("resX", f"trials?{bad} 回 404（不是静默空集，也不是把非法值带进 SQL）", code, 404)
        c.ok("resX", f"trials?{bad} 的错误里列出了可取的值",
             "可取" in detail or "只接受" in detail, detail[:80])
    for bad in ("year=1999", "is_oa=1&year=2019"):
        code, _ = _bad(cl, f"/api/diseases/lung/publications?{bad}")
        c.eq("resX", f"publications?{bad} 回 404", code, 404)
    r = cl.get("/api/diseases/lung/trials?include=eligibility,publications")
    c.eq("resX", "include 两个大字段都回来了", r.status_code, 200)
    it = r.json()["items"][0]
    c.ok("resX", "include 之后 eligibility 是解码后的字符串、publications 是数组",
         isinstance(it.get("eligibility"), str) and isinstance(it.get("publications"), list),
         f"{type(it.get('eligibility')).__name__}/{type(it.get('publications')).__name__}")
    # 一个 NCT 挂在多个病上：同一行内容在两台接口必须一字不差（跨病不是复制两份事实）
    top = _one(db, "SELECT nct_id, COUNT(*) k FROM trial GROUP BY nct_id ORDER BY k DESC, nct_id"
                   " LIMIT 1")
    codes = [r["code"] for r in _q(
        db, "SELECT d.code FROM trial t JOIN disease d ON d.id=t.disease_id"
            " WHERE t.nct_id=%s ORDER BY d.code", (top["nct_id"],))]
    got = []
    for cd in codes[:2]:
        # 按 nct_id 在这个病内的名次定位，不指望它落在第一页（跨病最多的那个未必字号靠前）
        rank = int(_col(db, "SELECT COUNT(*) FROM trial WHERE disease_id=%s AND nct_id<%s",
                        (ids[cd], top["nct_id"])))
        p = cl.get(f"/api/diseases/{cd}/trials",
                   params={"limit": 1, "offset": rank}).json()["items"]
        got.append(p[0] if p else None)
    c.ok("resX", f"跨病最多的试验 {top['nct_id']}（{top['k']} 病）两台都按名次取得到",
         all(got), f"{codes[:2]} 的名次定位")
    if all(got):
        keys_cmp = ("title", "brief_title", "lead_sponsor", "status_bucket",
                    "enrollment", "phases", "overall_status")
        c.eq("resX", f"{top['nct_id']} 在两个病上试验本身的内容一字不差",
             {k: got[0][k] for k in keys_cmp}, {k: got[1][k] for k in keys_cmp})
        c.eq("resX", "但两行的 disease_id 不同（一行是一个 (试验, 病) 命中）",
             (got[0]["disease_id"], got[1]["disease_id"]), (ids[codes[0]], ids[codes[1]]))
        # matched_terms 不在这份"一字不差"里：它记的是"这一病的哪几个声明词命中了它"，
        # 同一试验在两个病上必然不同（实测膀胱=bladder cancer / 脑=nervous system cancer）
        terms = {r["code"]: set(json.loads(r["st"] or "[]"))
                 for r in _q(db, "SELECT code, search_terms AS st FROM disease"
                                 " WHERE code IN (%s, %s)", tuple(codes[:2]))}
        for i, cd in enumerate(codes[:2]):
            mt = got[i]["matched_terms"]
            # NULL 是一个合法值（全库 4,872/23,705 行）：那一批只经 CT 的 MeSH 展开命中，
            # 逐行验过它们的 conditions 与标题里没有一个声明词字面出现，所以不是解析失败。
            c.ok("resX", f"{cd} 那一行的 matched_terms 是 NULL 或非空数组（两值之外没有第三种）",
                 mt is None or (isinstance(mt, list) and mt), str(mt)[:80])
            c.ok("resX", f"{cd} 那一行的 matched_terms 全是它自己声明的词",
                 set(mt or []) <= terms[cd], str(mt)[:80])
        c.ok("resX", "matched_terms 随病变（不是把一份词表抄到每个病上）",
             got[0]["matched_terms"] != got[1]["matched_terms"],
             f"{got[0]['matched_terms']} vs {got[1]['matched_terms']}")
    # 文献的 id 序是不是源的相关度序：拿归档逐位对一遍（这条只在有归档时说得成）
    arc = sorted((ROOT / "data" / "raw" / "europepmc").glob("rows-*/pub-bladder.json.gz"))
    c.ok("resX", "EPMC 归档在仓库里（这条对照靠它，缺了要红不要跳）", bool(arc), str(arc[:1]))
    if arc:
        recs = json.loads(gzip.decompress(arc[-1].read_bytes()).decode("utf-8"))["records"]
        api = []
        for o in range(0, 500, 200):
            api += cl.get("/api/diseases/bladder/publications",
                          params={"limit": 200, "offset": o}).json()["items"]
        c.eq("resX", f"整维 {len(api)} 行分三页取完（limit 上限 200，500 行没有一页的路）",
             len(api), min(len(recs), 500))
        c.eq("resX", "接口按 id 序取完 = 归档记录序（id 序就是 EPMC 返回的相关度序）",
             [(str(x["pmid"] or ""), x["doi"]) for x in api],
             [(str(r.get("pmid") or ""), r.get("doi", "")) for r in recs[:len(api)]])


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
    DIMPATHS = ("anatomy", "histology", "symptoms", "risk-factors",
                "trials", "publications", "targets", "drugs")
    for path in ("/api/diseases/nope/stats", "/api/diseases/nope/survival",
                 *[f"/api/diseases/nope/{v}" for v in DIMPATHS]):
        r = cl.get(path)
        c.eq("edges", f"{path} 未知疾病码 404 且指路", (r.status_code, "/api/diseases" in r.json()["detail"]), (404, True))
    c.eq("edges", "POST 没有路由可打", cl.post("/api/diseases", json={}).status_code, 405)
    c.eq("edges", "PATCH 同样没有", cl.patch("/api/diseases/lung", json={}).status_code, 405)
    for path in ("/api/diseases/lung/stats", "/api/diseases/lung/survival", "/api/stats/compare",
                 *[f"/api/diseases/lung/{v}" for v in DIMPATHS]):
        c.eq("edges", f"{path} 只读", cl.post(path, json={}).status_code, 405)
    rf = "/api/diseases/lung/risk-factors"
    c.eq("edges", "榜的 limit=0 被拒（0 行不是一种截断）", cl.get(rf, params={"limit": 0}).status_code, 422)
    c.eq("edges", "榜的 limit 超上限被拒", cl.get(rf, params={"limit": 501}).status_code, 422)
    c.eq("edges", "榜的 limit 上限本身可取", cl.get(rf, params={"limit": 500}).status_code, 200)
    c.eq("edges", "榜的 limit 非数字被拒", cl.get(rf, params={"limit": "abc"}).status_code, 422)


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
        paths = check_routes(c)
        try:
            check_frontend(c, paths)
        except Exception as e:  # noqa: BLE001 它只读文件；抛出来也不该带走下面那份报告
            c.ok("web", "前端契约那一台整台跑完（没抛异常）", False, f"{type(e).__name__}: {e}")
        check_meta(c, cl, db)
        check_diseases(c, cl, db)
        check_stats(c, cl, db, ids)
        check_metrics(c, cl, db)
        check_compare(c, cl, db)
        check_survival(c, cl, db, ids)
        check_vocab(c, cl, db, ids)
        check_research(c, cl, db, ids)
        try:
            _research_edges(c, cl, db, ids)
        except Exception as e:  # noqa: BLE001 结果攒到最后才打印，这里不兜住就等于整份报告没了
            c.ok("resX", "分页边界那一台整台跑完（没抛异常）", False, f"{type(e).__name__}: {e}")
        check_gaps(c, cl, db)
        check_edges(c, cl)
        event.remove(apidb.engine(), "before_cursor_execute", _tap)
        check_sql_filter(c, seen)
        ms = (time.perf_counter() - t0) * 1000
        # 逐请求耗时只报不判：机器之间差一个数量级，写死会天天红
        for path in ("/api/meta", "/api/diseases", "/api/diseases/lung",
                     "/api/diseases/lung/stats", "/api/diseases/lung/survival",
                     "/api/diseases/lung/anatomy", "/api/diseases/lung/histology",
                     "/api/diseases/lung/symptoms", "/api/diseases/lung/risk-factors",
                     "/api/diseases/breast_female/risk-factors",
                     "/api/diseases/lung/trials", "/api/diseases/lung/targets?limit=200",
                     "/api/diseases/bladder/publications?limit=200",
                     "/api/diseases/breast_female/drugs",
                     "/api/stats/compare?metric=incidence_total&region=China"
                     "&estimate_basis=national_estimate&sex=both&age_band="):
            t = time.perf_counter()
            try:
                cl.get(path)
                took = f"{(time.perf_counter() - t) * 1000:7.1f} ms"
            except Exception as e:  # noqa: BLE001 这一段不判定；它抛出来不该带走上面那份 FAIL 报告
                took = f"!! {type(e).__name__}"
            print(f"耗时  {path[:56]:56} {took}")

    fails = 0
    for group, what, ok, note in c.items:
        if not ok:
            fails += 1
            print(f"FAIL  [{group}] {what}  {note}")
    print(f"\n{len(c.items) - fails}/{len(c.items)} 通过，整跑 {ms:.0f} ms")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
