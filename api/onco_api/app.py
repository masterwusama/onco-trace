"""应用装配。

只挂 GET：这台服务对库只做读，写路径全在采集层那边（README 约定 1）。会话级
READ ONLY 在 db.py 里已经兜了一道，这里是第二道。

不写 pydantic 响应模型：响应里的字段名就是 db/schema.sql 的列名，再写一份模型
等于把同一个契约维护两遍，而改列时模型不会自己跟着改。代价是 /api/docs 里
响应体只标成 object，真正的字段清单以这份 DDL 与 docs/MVP裁定.md §五 为准。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .routes import diseases, meta, stats, survival

DESCRIPTION = (
    "癌症与高致死疾病的结构化数据站后端。所有数值随行带出处"
    "（`provenance`：源、数据集版本、抽取方式、复核状态、装载时间），"
    "空态与零行分开回：`available: false` 配 `gaps` 里那句「暂无可靠来源」的理由。"
)


def create_app() -> FastAPI:
    s = load_settings()
    app = FastAPI(
        title="onco-trace API",
        version="0.1",
        description=DESCRIPTION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(s.cors_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(meta.router)
    app.include_router(diseases.router)
    app.include_router(stats.router)
    app.include_router(survival.router)
    return app
