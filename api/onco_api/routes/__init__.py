"""路由层的公共手续：按病码取身份。

只放这一件事。`/api/diseases/{code}` 与 D3b 起的各维度页找病必须同一道手续、
同一句 404，否则同一页上两个接口会对同一个病码给出两种说法。

一维一个路由模块，加完要在 `app.py` 里 `include_router` 一次；漏挂由
`api/tests/run.py` 的 `check_routes` 红——它把注册路径写成死集合，不靠人数。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import Connection

from ..dimensions import identities


def get_disease(conn: Connection, code: str) -> dict:
    all_of = identities(conn)
    if not all_of:  # 主档空着是装载没跑，不是这个病没有
        raise HTTPException(500, "disease 主档是空的，请先跑 C2a 的装载器")
    dis = all_of.get(code)
    if dis is None:
        raise HTTPException(404, f"没有这个疾病码：{code}。全部可用值见 /api/diseases")
    return dis
