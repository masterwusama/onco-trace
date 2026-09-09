"""五年存活率：头条、分期档、逐年序列三层，一台取一行都不落。

SEER 的这张页面上三层数长得像，其实是三件事：全分期头条是当期队列的一个数，分期档是
同一个年份窗里按分期切开的四到五档，逐年序列是另一套队列（SEER 8）从 1975 年起一列。
所以响应按三层分开回，而不是把 94–99 行摊平成一张表——摊平的下一句话一定是"能不能
把 2022 年的分期档画到逐年序列的末端"，而那不是一套人。

分层不按 `year = 0` 判（这张表没有一行是 0），按"同一个 (档, 年份窗) 下有几个年份"判：
一个的是当期点，多个的是序列。这条判据与 dimensions.py 里生存维度量的切法是同一份，
两边不一致就会有一边红。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy import Connection

from ..db import get_conn, rows
from ..dimensions import NOT_REJECTED
from ..serialize import PROV_COLS, Refs, to_series
from . import get_disease, shell

router = APIRouter(prefix="/api", tags=["survival"])

# 序列头：分期档与年份窗一起决定"这是不是一串可以连起来的数"。is_observed 必须在里面，
# 拟合线今天到 2023、观测线止于 2018，混一条就是把预测当观测发布。
SERIES_HEADER = ("stage", "stage_scheme", "window_label", "is_observed", "region", "dataset_code")
POINTS = ("year", "rate_pct")
NOT_STAGED = "none"

LAYERS = {
    "headline": "全分期头条：当年队列窗里这一个病的所有分期合起来的一个数",
    "by_stage": "同一 cohort 按分期切开的档；两套分期（seer_summary 与 ann_arbor）不是一套，"
                "不并成一张表，也不按同一顺序排——源表格的显示顺序没有进 schema，"
                "这里按 stage 字典序回",
    "trend": "逐年序列：另一套队列（SEER 8）的观测值与拟合值各一条，两者年份重叠但不能相减",
}


@router.get("/diseases/{code}/survival")
def disease_survival(
    code: str = Path(..., description="disease.code"),
    conn: Connection = Depends(get_conn),
) -> dict:
    dis = get_disease(conn, code)
    refs = Refs(conn)
    header = ", ".join(SERIES_HEADER)
    prov = ", ".join(PROV_COLS)
    rowset = rows(
        conn,
        f"SELECT {header}, {prov}, year, rate_pct FROM survival "
        f"WHERE disease_id = :did AND {NOT_REJECTED} "
        "ORDER BY stage_scheme, stage, window_label, is_observed DESC, year",
        {"did": dis["id"]},
    )
    series = to_series(refs, rowset, SERIES_HEADER, POINTS)
    single = [s for s in series if s["n_points"] == 1]
    trend = [s for s in series if s["n_points"] > 1]
    # 全分期头条取年份最大的那一串；实测每病恰好一串（跑测器把这条钉成 1）
    heads = sorted((s for s in single if s["stage_scheme"] == NOT_STAGED),
                   key=lambda s: -s["points"][0]["year"])
    out = shell(conn, code, dis, "survival",
                {"series_key": list(SERIES_HEADER), "layer_split": "单点=当期，多点=序列"})
    return {
        **out,
        "layers": LAYERS,
        "headline": _flatten(heads[0]) if heads else None,
        "by_stage": [_flatten(s) for s in single if s["stage_scheme"] != NOT_STAGED],
        "trend": trend,
    }


def _flatten(series: dict) -> dict:
    """只有一行的序列摊成一行：分期档表要的是"这一档多少"，不是一串点。"""
    out = {k: v for k, v in series.items() if k not in ("points", "n_points")}
    return {**out, **series["points"][0]}
