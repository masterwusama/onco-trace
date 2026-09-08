"""装载器注册表与调度：`python -m onco_etl load --code disease`。

与探针的 runner 同构——一个模块一个维度，暴露 `load(ctx)`，落库、joblog 收口、
计数与打印由这里统一做。区别只在两件事：装载器写业务表（探针只写裁定），
以及装载器可以 `--dry-run`（把整批行写进去再回滚，用来验约束而不碰存量数据）。

顺序有讲究：`disease` 必须排第一，其它装载器都靠它拿 `disease_id`。
单独跑下游装载器时如果主档是空的，会直接报错而不是静默产出零行。
"""
from __future__ import annotations

import sys
import time

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from .. import joblog
from . import anatomy, base, disease

# 顺序即执行顺序，新增装载器往里加一行（`disease` 保持在最前，其余靠它拿外键）
_MODULES = (disease, anatomy)
REGISTRY = {m.__name__.rsplit(".", 1)[-1]: m for m in _MODULES}

Ctx = base.Ctx


def available() -> list[str]:
    return list(REGISTRY)


def run(codes: list[str] | None, offline: bool = False, dry_run: bool = False) -> int:
    picked = codes or available()
    unknown = [c for c in picked if c not in REGISTRY]
    if unknown:
        print(f"没有这些装载器：{unknown}；已有：{available()}")
        return 1

    job = joblog.start("load", {"loaders": len(picked), "offline": offline, "dry_run": dry_run})
    ctx = Ctx(offline=offline, dry_run=dry_run, job=job)
    total = 0
    try:
        for code in picked:
            mod = REGISTRY[code]
            print(f"== {code}" + ("（dry-run，写完回滚）" if dry_run else ""))
            t0 = time.perf_counter()
            res = mod.load(ctx)
            secs = time.perf_counter() - t0
            total += res.rows
            print(f"   {res.brief()}  {secs:.1f}s")
            job.tally(written=res.rows)
    except BaseException as e:  # noqa: BLE001 —— 半途中断也要把已装载的部分收口
        job.note(f"中断：{type(e).__name__}: {e}")
        joblog.finish(job, ok=False)
        raise
    job.note(("试运行，未提交" if dry_run else f"共写 {total} 行"))
    joblog.finish(job, ok=True)
    return 0
