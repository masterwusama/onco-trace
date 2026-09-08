"""SEER Site Recode 探针：器官树骨架 + 形态学码，一个文件覆盖 anatomy 与 histology 两维。

判据分两半，缺一半都算 partial：
1. 码表可解析（行数、列名、site recode 个数）
2. 18 病 100% 能落到某个 site recode 分组——这一条才决定器官树建不建得起来

`load_payload()` 与 `scan()` 是给 `load/anatomy.py` 用的：探针判定"这维够用吗"，
装载器把同一份解析结果写成行。两边各写一套解析，迟早一边说够用一边库里没数。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
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


class _Unreachable(Exception):
    def __init__(self, verdict: str, message: str, obs: tuple) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.message = message
        self.obs = obs


@dataclass(frozen=True)
class HistCode:
    """一个形态学/行为码。同一码在文件里偶有两个描述（8815/3 在两处分别写作
    Solitary fibrous tumor 与 Hemangiopericytoma），label 按文件顺序把两个都留下——
    挑一个就是替源做主，而这一列将来要能回答"源到底怎么写的"。
    """

    code_behavior: str
    label: str
    group_code: str
    group_label: str

    @property
    def code(self) -> str:
        return self.code_behavior.partition("/")[0]

    @property
    def behavior(self) -> str:
        return self.code_behavior.partition("/")[2]


@dataclass
class IcdoScan:
    """一次 sitetype.xlsx 解析的产出。sites / site_codes 都保持文件行序。"""

    n_rows: int = 0
    n_hist_pairs: int = 0  # 探针原口径：(码, 描述) 对数，与"多少个形态学码"一致
    fields: list[str] = field(default_factory=list)  # 表头，回答"源这一版给了哪些列"
    sites: dict[str, str] = field(default_factory=dict)  # recode 原文 → Site Description
    topo: dict[str, set[int]] = field(default_factory=dict)  # recode → 三位拓扑码集
    codes: dict[str, HistCode] = field(default_factory=dict)  # code_behavior → 码
    site_codes: dict[str, list[str]] = field(default_factory=dict)  # recode → 该组下的码


def version_key(version: str) -> str | None:
    """URL 里那段 8 位数字 → 发布日。上游改写法时返回 None，别让脏值进 date 列。"""
    return (
        f"{version[:4]}-{version[4:6]}-{version[6:8]}"
        if len(version) == 8 and version.isdigit()
        else None
    )


def load_payload(offline: bool) -> tuple[bytes, str, str, Path | None, tuple]:
    """返回 (内容, 版本, 来源说明, 已存在的归档路径, 取数观测)。offline 时不联网。

    版本对离线重放取归档目录名、对联网取 URL 里那段 `d(\\d{8})`——两处都是同一个字面量，
    所以同一版发布重放出来的 release 不会被拆成两条。
    """
    if offline:
        p = raw.newest(SOURCE, FILENAME)
        if not p:
            raise SystemExit(f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/{FILENAME} 不存在")
        # 归档目录名取自 URL 里的日期，离线时目录本身就是版本，从路径上取
        return p.read_bytes(), p.parent.name, f"offline:{p.name}", p, ("offline", None, None)

    src = BY_CODE[SOURCE]
    url = src.download_url or src.home_url
    res = fetch(url, timeout=(10, 180))
    obs = (res.reachability, res.status, res.latency_ms)
    if not res.ok or not res.body:
        raise _Unreachable(
            "dead" if res.status in (404, 410) else "blocked",
            f"{url} → {res.status or res.reachability}：{res.note}",
            obs,
        )
    m = re.search(r"d(\d{8})", url)
    return res.body, (m.group(1) if m else ""), url, None, obs


def scan(body: bytes) -> IcdoScan:
    wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [c for c in next(rows, ()) if c]
    out = IcdoScan(fields=[str(c) for c in header])
    labels: dict[str, list[str]] = {}
    groups: dict[str, tuple[str, str]] = {}
    pairs: set[tuple[str, str]] = set()
    for r in rows:
        if not r or not r[0]:
            continue
        out.n_rows += 1
        recode, desc = str(r[0]).strip(), str(r[1]).strip()
        if recode not in out.sites:
            out.sites[recode] = desc
            out.topo[recode] = expand_topo(recode)
            out.site_codes[recode] = []
        if not r[4]:
            continue
        key = str(r[4]).strip()
        label = str(r[5]).strip() if r[5] else ""
        if key not in out.site_codes[recode]:
            out.site_codes[recode].append(key)
        pairs.add((key, label))
        bucket = labels.setdefault(key, [])
        if label and label not in bucket:
            bucket.append(label)
        groups.setdefault(key, (str(r[2]).strip(), str(r[3]).strip() if r[3] else ""))
    wb.close()
    out.n_hist_pairs = len(pairs)
    out.codes = {
        key: HistCode(key, "; ".join(ls), *groups[key]) for key, ls in labels.items()
    }
    return out


def probe(offline: bool = False) -> ProbeResult:
    try:
        body, version, origin, existing, (reach, http, ms) = load_payload(offline)
    except _Unreachable as e:
        return ProbeResult(
            verdict=e.verdict,
            message=e.message,
            criteria=CRITERIA,
            dataset_code=DATASET,
            reachability=e.obs[0],
            http_status=e.obs[1],
            latency_ms=e.obs[2],
        )

    scanned = scan(body)
    sites, topo = scanned.sites, scanned.topo

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

    path = existing or raw.archive(SOURCE, version or "unknown", FILENAME, body)
    rel = raw.rel(path)
    total_codes = sum(len(s) for s in topo.values())

    if uncovered:
        verdict = "partial" if covered else "empty"
        msg = (
            f"{len(sites)} 个 site recode / {total_codes} 个拓扑码 / {scanned.n_hist_pairs} 个形态学码；"
            f"18 病挂载 {len(covered)}/{len(TARGETS)}，未挂载：{', '.join(uncovered)}"
        )
    else:
        verdict = "ok"
        msg = (
            f"{len(sites)} 个 site recode / {total_codes} 个拓扑码 / {scanned.n_hist_pairs} 个形态学码；"
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
        rows_seen=scanned.n_rows,
        diseases_covered=len(covered),
        diseases_total=len(TARGETS),
        fields_seen=scanned.fields,
        sample=matched,
        raw_path=rel,
        reachability=reach,
        http_status=http,
        latency_ms=ms,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=version_key(version),
        release_bytes=len(body),
        release_sha256=raw.sha256_file(path),
    )
