"""路由层的公共手续：按病码取身份，与各维度页共用的那一份头部。

只放这两件事。`/api/diseases/{code}` 与 D3b 起的各维度页找病必须同一道手续、
同一句 404，否则同一页上两个接口会对同一个病码给出两种说法；`shell` 同理——
十个维度页的头部（表名、口径要点、度量、空态）必须来自同一份 `counts_by_code`，
列表页说"这一维有 500 行"而维度页说别家数，是页面自己打自己。

一维一个路由模块，加完要在 `app.py` 里 `include_router` 一次；漏挂由
`api/tests/run.py` 的 `check_routes` 红——它把注册路径写成死集合，不靠人数。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import Connection

from .. import gaps as G
from ..dimensions import DIM_BY_KEY, counts_by_code
from ..dimensions import identities


def get_disease(conn: Connection, code: str) -> dict:
    all_of = identities(conn)
    if not all_of:  # 主档空着是装载没跑，不是这个病没有
        raise HTTPException(500, "disease 主档是空的，请先跑 C2a 的装载器")
    dis = all_of.get(code)
    if dis is None:
        raise HTTPException(404, f"没有这个疾病码：{code}。全部可用值见 /api/diseases")
    return dis


def shell(conn: Connection, code: str, dis: dict, dim: str, extra: dict) -> dict:
    """维度页头部：病身份、这一维的表名与口径要点、与列表页同一份度量和空态。

    `measures` 与 `gaps` 一律从 `counts_by_code` 现算，所以维度页与列表页说的是同一组数——
    这条不是约定，跑测器逐病逐维比过。
    """
    counts = counts_by_code(conn)[code]
    return {
        "code": dis["code"],
        "name_zh": dis["name_zh"],
        "table": DIM_BY_KEY[dim].table,
        "note": DIM_BY_KEY[dim].note,
        "conventions": extra,
        "measures": counts[dim],
        "gaps": [g for g in G.gaps_for_disease(counts, dis) if g["dim"] == dim],
    }
