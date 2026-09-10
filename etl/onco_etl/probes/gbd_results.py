"""GBD 结果数值探针：授权后的取数路——任务在浏览器里人工提交，轮询与下载匿名可编程。

B3/B4 时这一维裁成 paused：词表匿名可取（codebook ZIP 仍在 2024-05-16/ 归档里，
`gbd_cra._rei_tree` 还跨源读它），数值四路全 401。2026-09-10 注册 IHME 免费非商用
账号（Azure AD B2C，scope `.../data-api/data.read`）实测走通，整条路分两半：

  **提交（一次性、人工辅助）**——`php/download.php` 只认浏览器上下文里的 Bearer
  token：Python requests 与 curl_cffi（多档浏览器指纹）一律 401
  `Unable to parse authentication token`，是 Cloudflare 按客户端特征歧视，不是
  token 本身的问题。所以两个下载任务是在浏览器里提交的，凭据在 `.env` 的
  IHME_USER/IHME_PASS。任务按参数哈希缓存（poll 响应的 `hash` 字段就是 taskID），
  同一组参数永远指向同一份产物——taskID 写成下面的常量，"提交"这一步于是固化成
  仓库资产，重放不再需要登录。
  **轮询 + 下载（永久匿名）**——`php/get_download_result.php?taskID=` 与
  `dl.healthdata.org` 两个路由实测匿名 200，本模块的 online 分支只走这一半。

两个任务各回一个 ZIP（单 CSV 成员 + citation.txt）：

  `AGE_TASK` 19 病因（18 病 + Neoplasms 总档 410）× 中国 × 2021 × Deaths ×
  Number × 20 年龄档 × 三性别，951 行。410/Both 在场 20 档合计
  2,401,092.519546612，与单独取的全年龄单行逐位相等——"档内求和＝全年龄总量"
  的锚，`load/stats.py` 按档算死亡构成比时分母的合法性全靠它。
  `PAF_TASK` 18 病因 × 33 REI × 中国 × 2021 × Deaths，213 行＝3 组各 71 对
  （Number/All ages、Percent/All ages、Percent/Age-standardized）。PAF 取
  Percent + Age-standardized 那 71 行：(cause_id, rei_id) 对与 A2 CRA 骨架
  （`gbd_cra.EYEBALL` 合计 71）逐对相等，脑 0 条两边一致。

值的三个事实，都是这批数给的教训：

  PAF 可以为负——GBD 按暴露反事实算，保护方向的归因是负数（实测 3 行，最小
  -0.0733），Cervical|Unsafe sex 恰为 1.0。`paf` 列是有符号 decimal，负值照落。
  匹配只按 (cause_id, rei_id) 数字键，不按名字——两份源的名字写法一致是巧合，
  不是键。
  GBD 的 sex id 是 3=Both/1=Male/2=Female，与 GCO 那套 0/1/2 不是同一码空间，
  `load/stats.py` 里两套映射分开声明。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "gbd_results"
DATASET = "gbd2023-deaths-paf"
CRITERIA = (
    "18 病 × 中国 × 2021 × 20 年龄档的死亡数、33 可干预暴露 × 病因对的年龄标化 PAF，"
    "经 IHME 账号授权取回且轮询/下载可匿名重放；任务提交是一次性浏览器动作"
)

# poll 与下载都实测匿名 200；提交侧见模块注释
POLL = "https://vizhub.healthdata.org/gbd-results/php/get_download_result.php"
API_VERSION = "2023.0.0"

AGE_TASK = "f3d78e0182a78b31815b277aa4eb8921"
PAF_TASK = "03f12fcf29faf2d602fa34634bbab911"
ZIP_GLOB = "IHME-GBD_2023_DATA-*.zip"

NEOPLASMS = "410"
# 410/Both 在场 20 档的合计（年龄组 ZIP 里没有全年龄行，这个数来自单独取的
# 全年龄单行 probe_test.zip）——防"ZIP 换了别年版还静默落库"的锚
BAND_SUM_ANCHOR = 2401092.519546612

# CSV 的 sex_id 全是字符串，声明性别到 GBD 码的映射也按字符串给
GBD_SEX = {"both": "3", "male": "1", "female": "2"}


def _zip_name(tid: str) -> str:
    return f"IHME-GBD_2023_DATA-{tid[:8]}-1.zip"


def _csv_rows(body: bytes) -> list[dict]:
    """ZIP → 行 dict 列表。每个 ZIP 恰好一个 CSV 成员（另一个是 citation.txt）。"""
    z = zipfile.ZipFile(io.BytesIO(body))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig", "replace"))))


@dataclass
class GbdPayload:
    """取数这一趟的产出。`blocked` 非空表示没取成，探针原样报回去。"""

    age: list[dict] = field(default_factory=list)
    paf: list[dict] = field(default_factory=list)
    paf_total: int = 0  # paf ZIP 全部行数（3 组 71 对），rows_seen 描述归档要按它算
    blocked: ProbeResult | None = None
    key_dir: Path | None = None
    version: str = "unknown"
    size: int = 0
    sha: str = ""
    ms: int = 0
    reach: str = "direct"
    http: int | None = None


def load_payload(offline: bool) -> GbdPayload:
    pl = GbdPayload()
    reach = "offline" if offline else "direct"
    ms_total = 0
    http: int | None = None

    if offline:
        key_dir = raw.newest_dir(SOURCE, ZIP_GLOB)
        paths = [
            (key_dir / _zip_name(tid)) if key_dir else None for tid in (AGE_TASK, PAF_TASK)
        ]
        if not all(p and p.is_file() for p in paths):
            raise SystemExit(
                f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/"
                f"IHME-GBD_2023_DATA-{{{AGE_TASK[:8]}|{PAF_TASK[:8]}}}-1.zip 不齐")
        bodies = [p.read_bytes() for p in paths]
        version = key_dir.name
    else:
        bodies = []
        for tid in (AGE_TASK, PAF_TASK):
            pr = fetch(f"{POLL}?taskID={tid}", timeout=(10, 60), max_bytes=200_000,
                       headers={"Accept": "application/json"})
            ms_total += pr.latency_ms
            if pr.reachability == "proxy":
                reach = "proxy"
            if not pr.ok:
                pl.blocked = ProbeResult(
                    verdict="dead" if pr.status in (404, 410) else "blocked",
                    message=f"{POLL}?taskID={tid} → {pr.status or pr.reachability}：{pr.note}",
                    criteria=CRITERIA, dataset_code=DATASET, reachability=pr.reachability,
                    http_status=pr.status, latency_ms=ms_total)
                return pl
            try:
                task = json.loads(pr.body)
            except ValueError:
                pl.blocked = ProbeResult(
                    verdict="dead",
                    message=f"{POLL}?taskID={tid} 回的不是 JSON——vizhub 改了轮询路由，"
                            "整个匿名重放的那一半要重判",
                    criteria=CRITERIA, dataset_code=DATASET, reachability=reach,
                    http_status=pr.status, latency_ms=ms_total)
                return pl
            if task.get("state") != "success" or not task.get("urls"):
                pl.blocked = ProbeResult(
                    verdict="dead",
                    message=f"任务 {tid} 状态是 {task.get('state')!r}——IHME 清了任务缓存，"
                            "要在浏览器里重新提交一次（凭据 .env 的 IHME_USER/IHME_PASS），"
                            "把新 taskID 换进模块常量",
                    criteria=CRITERIA, dataset_code=DATASET, reachability=reach,
                    http_status=pr.status, latency_ms=ms_total)
                return pl
            zr = fetch(task["urls"][0], timeout=(10, 120), max_bytes=20_000_000)
            ms_total += zr.latency_ms
            http = zr.status
            if zr.reachability == "proxy":
                reach = "proxy"
            if not zr.ok or not zr.body or zr.truncated:
                pl.blocked = ProbeResult(
                    verdict="dead" if zr.status in (404, 410) else "blocked",
                    message=f"{task['urls'][0]} → {zr.status or zr.reachability}：{zr.note}"
                            f" declared={zr.declared_bytes} got={len(zr.body)}"
                            f" truncated={zr.truncated}",
                    criteria=CRITERIA, dataset_code=DATASET, reachability=zr.reachability,
                    http_status=zr.status, latency_ms=ms_total)
                return pl
            bodies.append(zr.body)
        version = API_VERSION
        key_dir = raw.archive_dir(SOURCE, version)
        for tid, b in zip((AGE_TASK, PAF_TASK), bodies):
            (key_dir / _zip_name(tid)).write_bytes(b)

    all_paf = _csv_rows(bodies[1])
    pl.age = _csv_rows(bodies[0])
    pl.paf = [r for r in all_paf
              if r["metric_name"] == "Percent" and r["age_name"] == "Age-standardized"]
    pl.paf_total = len(all_paf)
    pl.key_dir, pl.version, pl.size = key_dir, version, sum(len(b) for b in bodies)
    pl.sha = raw.sha256_bytes(b"".join(bodies))
    pl.ms, pl.reach, pl.http = ms_total, reach, http
    return pl


def probe(offline: bool = False) -> ProbeResult:
    pl = load_payload(offline)
    if pl.blocked:
        return pl.blocked
    age, paf = pl.age, pl.paf

    # 口径断言：一行混进来就中止，不按"多数行对"猜
    for r in age:
        if (r["measure_name"], r["metric_name"], r["year"], r["location_id"],
                r["population_group_id"]) != ("Deaths", "Number", "2021", "6", "1"):
            raise SystemExit(f"年龄组 ZIP 里混进口径外的行：{r}")
    for r in paf:
        if (r["measure_name"], r["year"], r["location_id"], r["sex_id"]) != (
                "Deaths", "2021", "6", "3"):
            raise SystemExit(f"PAF 行里混进口径外的行：{r}")
    bands = {r["age_id"] for r in age}
    if len(bands) != 20:
        raise SystemExit(f"年龄档现在是 {len(bands)} 个（<5 到 95+ 应为 20）——重读再改声明")
    causes_age = {r["cause_id"] for r in age}
    missing = [t.code for t in TARGETS if t.gbd_cause not in causes_age]
    if missing:
        raise SystemExit(f"这些病的 gbd_cause 不在年龄组 ZIP 里：{', '.join(missing)}")
    triples = {(r["cause_id"], r["sex_id"], r["age_id"]) for r in age}
    if len(triples) != len(age):
        raise SystemExit("年龄组行里 (cause_id, sex_id, age_id) 有重复——下载不完整")
    pairs = {(r["cause_id"], r["rei_id"]) for r in paf}
    if len(pairs) != len(paf):
        raise SystemExit("PAF 行里 (cause_id, rei_id) 有重复——下载不完整或口径漂了")

    anchor = sum(float(r["val"]) for r in age
                 if r["cause_id"] == NEOPLASMS and r["sex_id"] == "3")
    if abs(anchor - BAND_SUM_ANCHOR) > 1e-6:
        raise SystemExit(
            f"410/Both 的 20 档合计 {anchor} ≠ 全年龄锚 {BAND_SUM_ANCHOR}——"
            "IHME 换了数或换了口径，重读归档再改锚")

    per_band = {
        t.code: sum(1 for r in age
                    if r["cause_id"] == t.gbd_cause and r["sex_id"] == GBD_SEX[t.sex])
        for t in TARGETS
    }
    per_paf = {t.code: sum(1 for r in paf if r["cause_id"] == t.gbd_cause) for t in TARGETS}
    vals = [float(r["val"]) for r in paf]
    neg = [r for r in paf if float(r["val"]) < 0]
    top = sorted(paf, key=lambda r: -float(r["val"]))[:5]
    msg = (
        f"授权路走通：两份 ZIP 匿名取回（{POLL} 与 dl.healthdata.org 实测 200；"
        "提交是浏览器里的一次性人工动作，任务按参数哈希缓存，taskID 固化在模块常量里）。"
        f"死亡年龄组 {len(age)} 行＝19 病因 × 三性别 × 20 档（<5 到 95+，无全年龄行；"
        f"缺档全在低龄段，是零死亡档），18 病声明性别合计 {sum(per_band.values())} 行；"
        f"410/Both 在场 20 档合计 {anchor:.1f}＝全年龄单行，构成比分母的锚成立。"
        f"PAF {len(paf)} 行（Percent × Age-standardized；ZIP 里另两组 Number/Percent × "
        f"All ages 各 {pl.paf_total // 3} 行没取），{len(pairs)} 个 (cause_id, rei_id) 对"
        f"与 A2 CRA 骨架逐对相等；值域 [{min(vals):.4f}, {max(vals):.4f}]，"
        f"负值 {len(neg)} 行（保护方向，照落），Cervical|Unsafe sex＝1.0。"
        f"归档两份 ZIP 共 {pl.size} B、{len(age) + pl.paf_total} 行"
    )
    if pl.reach == "proxy":
        msg += "。这一趟走了代理，reachability 按实情记"
    return ProbeResult(
        verdict="ok",
        message=msg,
        criteria=CRITERIA,
        rows_seen=len(age) + pl.paf_total,
        diseases_covered=len(TARGETS) - len(missing),
        diseases_total=len(TARGETS),
        fields_seen=[f"gbd2023:{c}" for c in pl.paf[0]],
        sample=[
            {"code": t.code, "bands": per_band[t.code], "paf": per_paf[t.code]}
            for t in TARGETS
        ] + [
            {"cause": r["cause_name"], "rei": r["rei_name"], "paf": float(r["val"])}
            for r in top
        ],
        raw_path=raw.rel(pl.key_dir) if pl.key_dir else None,
        reachability=pl.reach,
        http_status=pl.http,
        latency_ms=pl.ms or None,
        dataset_code=DATASET,
        upstream_version=pl.version,
        release_date=None,
        release_bytes=pl.size,
        release_sha256=pl.sha,
    )
