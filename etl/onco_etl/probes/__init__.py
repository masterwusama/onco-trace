"""专项探针注册表与调度。

新增一个源的探针只要三步：建 `probes/<code>.py`，暴露 SOURCE / CRITERIA / probe()，
在下面的 _MODULES 里加一行。落库、归档、joblog 收口都由 runner 统一做，
各探针只负责回答"取回来的东西够不够填这一维"。
"""
from __future__ import annotations

import sys
import time

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from .. import joblog
from . import (ctgov_v2, europepmc, gbd_cra, gbd_results, globocan, gco_overtime, gwas_catalog,
               icdo3_seer, mondo, opentargets, seer_statfacts, who_gho)
from .result import ProbeResult, record

_MODULES = (icdo3_seer, mondo, seer_statfacts, gbd_results, gbd_cra, gwas_catalog, globocan,
            gco_overtime, who_gho, ctgov_v2, europepmc, opentargets)
REGISTRY = {m.SOURCE: m for m in _MODULES}


def available() -> list[str]:
    return sorted(REGISTRY)


def run(codes: list[str] | None, sleep: float = 1.0, offline: bool = False) -> int:
    picked = codes or available()
    unknown = [c for c in picked if c not in REGISTRY]
    if unknown:
        print(f"没有这些探针：{unknown}；已有：{available()}")
        return 1

    job = joblog.start("probe", {"sources": len(picked), "offline": offline})
    bad: list[str] = []
    try:
        for code in picked:
            mod = REGISTRY[code]
            print(f"== {code}：{mod.CRITERIA}")
            t0 = time.perf_counter()
            res = mod.probe(offline=offline)
            record(code, res)
            secs = time.perf_counter() - t0
            cov = (
                f"{res.diseases_covered}/{res.diseases_total}"
                if res.diseases_covered is not None
                else "-"
            )
            print(
                f"   {res.verdict:<8} 覆盖 {cov:<7} 行数 {res.rows_seen if res.rows_seen is not None else '-':<7}"
                f" {secs:.1f}s"
            )
            print(f"   {res.message}")
            job.tally(probed=1)
            if res.verdict in ("blocked", "dead", "empty", "unlicensed"):
                job.tally(blocked=1)
                bad.append(f"{code}={res.verdict}")
            elif res.verdict == "partial":
                bad.append(f"{code}=partial")
            time.sleep(sleep)
    except BaseException as e:  # noqa: BLE001 —— 半途中断也要把已跑的探针收口
        job.note(f"中断：{type(e).__name__}: {e}")
        joblog.finish(job, ok=False)
        raise
    job.note("未达判据：" + ", ".join(bad) if bad else "全部达判据")
    joblog.finish(job, ok=True)
    return 1 if bad else 0
