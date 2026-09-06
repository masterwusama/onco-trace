"""SEER Site Recode 探针：器官树骨架 + 形态学码，一个文件覆盖 anatomy 与 histology 两维。

判据分两半，缺一半都算 partial：
1. 码表可解析（行数、列名、site recode 个数）
2. 18 病 100% 能落到某个 site recode 分组——这一条才决定器官树建不建得起来
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import openpyxl

from .. import raw
from ..fetch import fetch
from ..sources import BY_CODE
from ..targets import TARGETS
from .codes import expand_topo
from .result import ProbeResult

SOURCE = "icdo3_seer"
DATASET = "sitetype-icdo3"
FILENAME = "sitetype.xlsx"
CRITERIA = "码表可解析（≥150 个拓扑码）且 18 病 100% 落到某个 site recode 分组"


def probe(offline: bool = False) -> ProbeResult:
    src = BY_CODE[SOURCE]
    url = src.download_url or src.home_url
    m = re.search(r"d(\d{8})", url)
    version = m.group(1) if m else ""

    existing: Path | None = None
    if offline:
        existing = raw.newest(SOURCE, FILENAME)
        if not existing:
            raise SystemExit(f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/{FILENAME} 不存在")
        body = existing.read_bytes()
        origin = f"offline:{existing.name}"
        # 归档目录名取自 URL 里的日期，离线时目录本身就是版本，从路径上取
        version = existing.parent.name
        reach, http, ms = "offline", None, None
    else:
        res = fetch(url, timeout=(10, 180))
        if not res.ok or not res.body:
            return ProbeResult(
                verdict="dead" if res.status in (404, 410) else "blocked",
                message=f"{url} → {res.status or res.reachability}：{res.note}",
                criteria=CRITERIA,
                dataset_code=DATASET,
                reachability=res.reachability,
                http_status=res.status,
                latency_ms=res.latency_ms,
            )
        body = res.body
        origin = url
        reach, http, ms = res.reachability, res.status, res.latency_ms

    path = existing or raw.archive(SOURCE, version or "unknown", FILENAME, body)
    rel = raw.rel(path)

    wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [c for c in next(rows) if c]

    sites: dict[str, str] = {}
    topo: dict[str, set[int]] = {}
    hbeh: set[tuple] = set()
    n = 0
    for r in rows:
        if not r or not r[0]:
            continue
        n += 1
        recode, desc = str(r[0]).strip(), str(r[1]).strip()
        if recode not in sites:
            sites[recode] = desc
            topo[recode] = expand_topo(recode)
        if r[4]:
            hbeh.add((str(r[4]).strip(), str(r[5]).strip() if r[5] else ""))
    wb.close()

    # 挂载判定：两侧都展开成 C 码后三位的整数集，相交即命中
    covered: list[str] = []
    uncovered: list[str] = []
    matched: list[dict] = []
    for t in TARGETS:
        want = expand_topo(t.icdo3)
        hit = [f"{k}={v}" for k, v in sites.items() if want & topo[k]]
        if hit:
            covered.append(t.code)
            matched.append({"target": t.code, "name_zh": t.name_zh, "sites": hit[:3], "n_sites": len(hit)})
        else:
            uncovered.append(f"{t.code}({t.icdo3})")

    total_codes = sum(len(s) for s in topo.values())
    # upstream_version 要保持和 URL 里那段 token 一模一样，release_date 才是给 date 列用的
    iso = f"{version[:4]}-{version[4:6]}-{version[6:8]}" if len(version) == 8 and version.isdigit() else None

    if uncovered:
        verdict = "partial" if covered else "empty"
        msg = (
            f"{len(sites)} 个 site recode / {total_codes} 个拓扑码 / {len(hbeh)} 个形态学码；"
            f"18 病挂载 {len(covered)}/{len(TARGETS)}，未挂载：{', '.join(uncovered)}"
        )
    else:
        verdict = "ok"
        msg = (
            f"{len(sites)} 个 site recode / {total_codes} 个拓扑码 / {len(hbeh)} 个形态学码；"
            f"18 病全部挂载到器官级分组"
        )
    if total_codes < 150:
        verdict = "partial"
        msg += f"；拓扑码 {total_codes} 个，未达 150 的判据"
    msg += f"；来源 {origin}"

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=n,
        diseases_covered=len(covered),
        diseases_total=len(TARGETS),
        fields_seen=header,
        sample=matched,
        raw_path=rel,
        reachability=reach,
        http_status=http,
        latency_ms=ms,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=iso,
        release_bytes=len(body),
        release_sha256=raw.sha256_file(path),
    )
