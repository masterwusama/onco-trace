"""统计层读侧：一个病的序列清单 + 跨病的同口径榜。

一张 `stat_fact` 有两种问法。疾病页问"这一病的发病率这些年怎么走"，答案是一串按口径
分开的序列；榜页问"同一口径下十八病谁高谁低"，答案必须先把口径钉死才排得成。同一批
行，两种形状，所以两个接口。

榜这边最要紧的一条：没钉死的口径不替调用方猜。`sex=both` 的中国国家估算只有 13 病有
行（另五病的源只按性别发），把 `female`/`male` 的行并进来凑成 18 就是拿两批不同性别的
人凑一个率。所以还差哪个口径就回 `needs`，并把可取的值连同各自的覆盖病数回在 `choices`
里——前端的选择器直接读它，不必把 43 个口径组合抄进代码。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Connection

from ..db import get_conn, rows
from ..dimensions import DIM_BY_KEY, NOT_REJECTED, identities
from ..serialize import PROV_COLS, Refs, hydrate, to_series
from . import get_disease, shell

router = APIRouter(prefix="/api", tags=["stat"])

# 序列头：这几列一起决定"这一串数能不能连成一条线"。`estimate_basis` 是其中最不能省的
# 一列——GCO 的国家级估算与登记处外推同病、同度量、同名，只有这一列分开两批人。
SERIES_HEADER = ("metric", "unit", "dataset_code", "region", "sex", "age_band",
                 "estimate_basis", "cohort_note")
POINTS = ("year", "value")
# 榜的口径轴：这四列都钉死才成榜，year 可以代取最新一年
COMPARE_DIMS = ("region", "estimate_basis", "sex", "age_band")

YEAR_ZERO = (
    "year=0 不是公元 0 年：这一串的数是国家级单点估算、年龄组构成或按声明词命中的条数，"
    "源没有给年份（db/schema.sql 里 stat_fact.year 的注释）。"
)


@router.get("/diseases/{code}/stats")
def disease_stats(
    code: str = Path(..., description="disease.code，同时是前端 slug"),
    conn: Connection = Depends(get_conn),
) -> dict:
    dis = get_disease(conn, code)
    refs = Refs(conn)
    header = ", ".join(SERIES_HEADER)
    prov = ", ".join(PROV_COLS)
    series_rows = rows(
        conn,
        f"SELECT {header}, {prov}, year, value FROM stat_fact "
        f"WHERE disease_id = :did AND {NOT_REJECTED} AND estimate_basis <> 'query_count' "
        "ORDER BY metric, estimate_basis, region, sex, age_band, dataset_code, cohort_note, year",
        {"did": dis["id"]},
    )
    count_rows = rows(
        conn,
        f"SELECT metric, unit, dataset_code, cohort_note, year, value, {prov} FROM stat_fact "
        f"WHERE disease_id = :did AND {NOT_REJECTED} AND estimate_basis = 'query_count' "
        "ORDER BY metric",
        {"did": dis["id"]},
    )
    out = shell(conn, code, dis, "stat", {"year_zero": YEAR_ZERO, "series_key": list(SERIES_HEADER)})
    return {
        **out,
        "series": to_series(refs, series_rows, SERIES_HEADER, POINTS),
        # 研究层那四个数不折序列：每个只有一行，折出来是四条单点线，反而像是有序列
        "counts": [hydrate(refs, "stat_fact", r) for r in count_rows],
    }


@router.get("/stats/metrics")
def metrics(conn: Connection = Depends(get_conn)) -> dict:
    """可以排榜的度量清单：一行一个 `stat_fact.metric`，带单位、覆盖病数与年份跨度。

    榜要求调用方先给 `metric`，而这一列的取值只在库里——所以这一份就是那 17 个名字的唯一出处。
    前端抄一份进代码，装载多一维就没人知道；`/api/meta` 的 `dims.stat` 是照口径切的
    （cn_point / cn_trend / us_series / age_case / age_death / query_count），不是照 metric 切的，
    两者不能互相顶替。这里是聚合，一行不是一行事实，所以不带出处。
    """
    item_rows = rows(
        conn,
        "SELECT metric, COUNT(*) AS rows_, COUNT(DISTINCT disease_id) AS diseases,"
        "       COUNT(DISTINCT unit) AS units, MIN(unit) AS unit,"
        "       SUM(year = 0) AS year_zero_rows,"
        "       MIN(NULLIF(year, 0)) AS year_first, MAX(NULLIF(year, 0)) AS year_last,"
        "       COUNT(DISTINCT region) AS region_n,"
        "       COUNT(DISTINCT estimate_basis) AS estimate_basis_n,"
        "       COUNT(DISTINCT sex) AS sex_n, COUNT(DISTINCT age_band) AS age_band_n"
        f" FROM stat_fact WHERE {NOT_REJECTED}"
        " GROUP BY metric ORDER BY metric",
    )
    unit_rows = rows(conn, f"SELECT DISTINCT metric, unit FROM stat_fact WHERE {NOT_REJECTED}"
                           " ORDER BY metric, unit")
    by_metric: dict[str, list[str]] = {}
    for r in unit_rows:
        by_metric.setdefault(r["metric"], []).append(r["unit"])
    return {
        "table": "stat_fact",
        "note": DIM_BY_KEY["stat"].note,
        "order": "按 metric 升序（MySQL utf8mb4_0900_ai_ci 的序），不是重要度序",
        "conventions": {
            "year_zero": "year_zero_rows 是这一度量里 year=0 的行数——源没给年份的单点估算或"
                         "查询计数，它们与 year_first..year_last 那批年不在同一件事上",
            "axes": "region_n / estimate_basis_n / sex_n / age_band_n 是未过滤时这一度量各有"
                    "几个取值。榜要全部钉成才排得成，但真数要照 /api/stats/compare 当下回的"
                    " needs 走：钉完一轴，另一轴还剩几个取值会变",
        },
        "items": [
            {
                "metric": r["metric"],
                "unit": r["unit"] if r["units"] == 1 else by_metric[r["metric"]],
                "rows": int(r["rows_"]),
                "diseases": int(r["diseases"]),
                "year_zero_rows": int(r["year_zero_rows"] or 0),
                "year_first": r["year_first"],
                "year_last": r["year_last"],
                "axes": {k: int(r[f"{k}_n"]) for k in COMPARE_DIMS},
            }
            for r in item_rows
        ],
    }


@router.get("/stats/compare")
def compare(
    conn: Connection = Depends(get_conn),
    metric: str = Query(..., description="stat_fact.metric，如 incidence_asr"),
    region: str = Query(None, description="源没有地区列的按 '' 存，所以要写 region="),
    estimate_basis: str = Query(None),
    sex: str = Query(None),
    age_band: str = Query(None, description="不传＝全年龄；该切片没有全年龄行时必须显式指定"),
    year: int = Query(None, description="不传则取该口径下有行的最新一年"),
) -> dict:
    """按 (度量, 地区, 估算依据, 性别, 年龄组, 年份) 钉死后的跨病榜。"""
    pinned: dict[str, object] = {"metric": metric, "year": year}
    auto: list[str] = []
    needs: list[str] = []
    choices: dict[str, list[dict]] = {}
    given = {"region": region, "estimate_basis": estimate_basis, "sex": sex, "age_band": age_band}

    for col in COMPARE_DIMS:
        cands = _candidates(conn, col, pinned)
        if not cands and col == COMPARE_DIMS[0]:
            raise HTTPException(
                404, f"metric={metric} 在 stat_fact 里没有行。可排榜的度量清单见 /api/stats/metrics"
            )
        want = given[col]
        if want is not None:
            if want not in [c["value"] for c in cands]:
                raise HTTPException(
                    404, f"{col}={want!r} 不在 metric={metric} 已有的取值里。"
                    f"该口径下 {col} 可取：{[c['value'] for c in cands]}"
                )
            pinned[col] = want
        elif len(cands) == 1:
            pinned[col] = cands[0]["value"]  # 只有一个取值，钉它不改变任何数
            auto.append(col)
        else:
            needs.append(col)
            choices[col] = cands

    out: dict = {
        "pinned": pinned,
        "auto_pinned": auto,
        "needs": needs,
        "choices": choices,
        "unit": None,
        "note": DIM_BY_KEY["stat"].note,
        "items": [],
        "absent": [],
        "coverage": None,
        "year_used": None,
        "years_available": [],
    }
    if needs:
        return out

    where = _where(pinned)
    # 一次取回 (年份, 单位)：单位决定榜的轴，年份决定能不能画在同一条时间线上
    ry = rows(conn, f"SELECT year, unit FROM stat_fact WHERE {where} "
                    "GROUP BY year, unit ORDER BY year", _bind(pinned))
    units = {r["unit"] for r in ry}
    out["unit"] = units.pop() if len(units) == 1 else sorted(units)
    years = [int(r["year"]) for r in ry]
    out["years_available"] = years
    if year is None:
        if not years:  # 各口径都钉死了却一行没有：只有装载跑到一半才可能这样
            raise HTTPException(500, f"口径 {_slice_text(pinned)} 下没有行")
        out["year_used"] = pinned["year"] = years[-1]
    elif year not in years:
        out["year_used"] = year
        raise HTTPException(
            404, f"{_slice_text(pinned)} 在 year={year} 没有行。"
            f"该口径有数据的是 {years[0]}..{years[-1]}（{len(years)} 年）"
        )
    else:
        out["year_used"] = pinned["year"] = year

    cols = ", ".join(f"sf.{p}" for p in PROV_COLS)
    item_rows = rows(
        conn,
        f"SELECT d.code, d.name_zh, sf.value, sf.cohort_note, {cols} "
        f"FROM stat_fact sf JOIN disease d ON d.id = sf.disease_id "
        f"WHERE {_where(pinned, 'sf')} AND sf.year = :year ORDER BY sf.value DESC, d.code",
        {**_bind(pinned), "year": pinned["year"]},
    )
    refs = Refs(conn)
    out["items"] = [hydrate(refs, "stat_fact", r) for r in item_rows]
    everyone = identities(conn)
    present = {r["code"] for r in item_rows}
    out["absent"] = [{"code": c, "name_zh": r["name_zh"]} for c, r in everyone.items() if c not in present]
    out["coverage"] = f"{len(item_rows)}/{len(everyone)}"
    return out


def _bind(pinned: dict) -> dict:
    return {k: v for k, v in pinned.items() if k != "year"}


def _where(pinned: dict, prefix: str = "") -> str:
    """钉死的五轴 + 非 rejected 那条过滤，列名按 `prefix` 限定。

    限定不是风格问题：`disease` 自己也有 `sex` 与 `review_status`，榜那条 JOIN 语句
    不写 `sf.` 就是 MySQL 1052（两列同名，不知道问哪张表）。
    """
    p = f"{prefix}." if prefix else ""
    conds = [NOT_REJECTED] + [f"{c} = :{c}" for c in ("metric",) + COMPARE_DIMS]
    return " AND ".join(f"{p}{c}" for c in conds)


def _candidates(conn: Connection, col: str, pinned: dict) -> list[dict]:
    """一列口径在当前切片下还剩几个取值，各自覆盖几个病。已钉的轴都进 WHERE，
    所以这是"接着往下选还能选什么"，不是全库清单。

    `year` 不进 WHERE：它是榜取哪一刀的年份，不是切片的轴，而且这一句要报的正是
    "这一刀跨几年"——按年份过滤完，`year_first` 与 `year_last` 就成同一个值了。
    """
    conds = [NOT_REJECTED] + [f"{k} = :{k}" for k in _bind(pinned)]
    return [
        {k: ("" if v is None else v) for k, v in r.items()}
        for r in rows(
            conn,
            f"SELECT `{col}` AS value, COUNT(DISTINCT disease_id) AS diseases, "
            f"COUNT(*) AS rows_, COUNT(DISTINCT dataset_release_id) AS releases, "
            f"MIN(year) AS year_first, MAX(year) AS year_last "
            f"FROM stat_fact WHERE {' AND '.join(conds)} GROUP BY `{col}` ORDER BY `{col}`",
            _bind(pinned),
        )
    ]


def _slice_text(pinned: dict) -> str:
    return " / ".join(f"{k}={v!r}" for k, v in pinned.items())
