"""MONDO 探针：ID 主干。全站跨源关联都靠它，所以这一维不达标后面全部退化。

取 .obo 而不是 .json：53 MB 对 107 MB，而且能逐行流式解析，
不必把上百 MB 的 JSON 树整个读进内存。

判据改过两次，两次都是被实测推翻的，记在这里免得下一个人再走一遍：

  第一版假定"用 ICD-10 xref 找主条目"。2026-09-01 版实测：MONDO 根本没有裸 `ICD10:`
  前缀，只有 ICD10CM(2142 行) / ICD10WHO(209 行) / ICD9(5658 行)；而挂着 ICD10CM:C34 的
  是 grouping 父类 MONDO:0000376 "respiratory system cancer"，不是 "lung cancer"。
  按 ICD-10 命中会稳定落到上一层分类词——器官树挂到"呼吸系统"而不是"肺"，
  而覆盖率报告还挺好看。这种错法比命中不了危险得多。

  第二版改按 ICD-9 命中，18/18 都命中了，但捞到的是 bronchus cancer、anal canal cancer、
  descending colon cancer 这类亚部位 term，捞不到疾病主条目本身：MONDO 把 ICD 码全挂在
  亚部位粒度上，主条目一个都没有——colorectal cancer(MONDO:0005575) 的 xref 里既无 ICD9
  也无 ICD10CM，non-Hodgkin lymphoma(MONDO:0018908) 同样。所以主条目只能在 targets.py
  里声明，本探针负责校验声明是否还成立（上游改 ID、合并 term、标 obsolete 都要能发现）。

主条目的 xref 实测（18 个）：NCIT 18/18、UMLS 17/18、MESH 7/18、EFO 5/18。
NCIT 是唯一可靠的跨源钥匙，所以判据只卡它；MESH/EFO 的缺口如实上报——
B5 的文献与试验检索、B4 的 GWAS 关联都不能指望从 MONDO 拿这两个 ID。

顺带量到两件对后续批次有用的事：disease_has_location → UBERON 全库仅 775 行、
disease_has_feature → HP 仅 819 行（63278 个 term 里约 1%），
所以 MONDO 当不了器官树或症状维的主源，只能当补充。

支持离线重放：.obo 有 51 MB，解析规则改一行就重抓一次要七分钟，而那七分钟里上游没变。

`load_payload()` 与 `scan()` 是给装载器复用的：疾病主档的 mondo_name/ncit_id/xrefs 和
亚部位节点都出自这一份解析。两边各写一套 OBO 解析，迟早会有一边改了规则。
"""
from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .. import raw
from ..fetch import fetch
from ..sources import BY_CODE
from ..targets import TARGETS
from .codes import expand_icd9, icd9_matches
from .result import ProbeResult

SOURCE = "mondo"
DATASET = "mondo.obo"
FILENAME = "mondo.obo"
CRITERIA = (
    "targets.py 声明的 18 个 MONDO 主条目全部存在、非 obsolete，且每个都带 NCIT xref"
    "（实测唯一 18/18 可靠的跨源钥匙）；MESH/EFO 缺口只上报不卡判据"
)

# 判据卡的：NCIT 是唯一 18/18 都有的跨源钥匙
REQUIRED_XREFS = ("NCIT",)
# 缺口要点名上报的三类，各自决定后面某一批能不能走通：
#   MESH → B5 Europe PMC 文献检索；EFO → B4 GWAS Catalog 与 Open Targets；UMLS → 临床术语兜底
GAP_XREFS = ("MESH", "EFO", "UMLS")


class _Unreachable(Exception):
    def __init__(self, verdict: str, message: str, obs: tuple) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.message = message
        self.obs = obs


@dataclass(frozen=True)
class MondoTerm:
    """一个 term 的解析结果。装载器要的都在这里，不必回头再扫 51 MB。"""

    id: str
    name: str
    xrefs: tuple[str, ...] = ()
    obsolete: bool = False
    uberon: tuple[str, ...] = ()
    hp: tuple[str, ...] = ()
    icd9: tuple[str, ...] = ()

    def xref(self, prefix: str) -> tuple[str, ...]:
        """按前缀取码值：`xref('NCIT')` → `('C9340', …)`，前缀与冒号都不留。"""
        up = prefix.upper() + ":"
        return tuple(x.split(":", 1)[1] for x in self.xrefs if x.upper().startswith(up))


@dataclass
class MondoScan:
    """一次全量扫描的产出。"""

    version: str = ""
    n_terms: int = 0
    n_uberon: int = 0
    n_hp: int = 0
    anchors: dict[str, MondoTerm] = field(default_factory=dict)  # target.code -> 主条目
    obsolete_hit: list[str] = field(default_factory=list)  # "code:MONDO:id"
    subsites: dict[str, list[MondoTerm]] = field(default_factory=dict)  # code -> 亚部位


def version_key(version: str) -> str:
    """data-version 字面量常带斜杠（'releases/2026-09-01'），拍平成目录名与发布日。"""
    return version.rsplit("/", 1)[-1] or "unknown"


def load_payload(offline: bool) -> tuple[bytes, str, Path | None, tuple]:
    """返回 (内容, 来源说明, 已存在的归档路径, 取数观测)。offline 时不联网。

    探针与装载器都从这里取字节，否则可能一个读归档一个联网而解析出两样东西。
    取数观测是 (reachability, http_status, latency_ms)：这一趟到底是直连、走代理，
    还是根本没联网。离线重放必须记成 offline——记成 direct 等于宣称"直连取到了"，
    而 scheduler 正是按这一列决定要不要配代理。
    """
    if offline:
        p = raw.newest(SOURCE, FILENAME)
        if not p:
            raise SystemExit(f"离线重放需要先有一份归档：data/raw/{SOURCE}/**/{FILENAME} 不存在")
        return p.read_bytes(), f"offline:{p.parent.name}/{p.name}", p, ("offline", None, None)

    src = BY_CODE[SOURCE]
    url = src.download_url or src.home_url
    # 53 MB 走代理时不快，读取超时给足
    res = fetch(url, timeout=(15, 600))
    obs = (res.reachability, res.status, res.latency_ms)
    if not res.ok or not res.body:
        raise _Unreachable(
            "dead" if res.status in (404, 410) else "blocked",
            f"{url} → {res.status or res.reachability}：{res.note}",
            obs,
        )
    return res.body, url, None, obs


def scan(body: bytes) -> MondoScan:
    out = MondoScan(subsites={t.code: [] for t in TARGETS})
    wanted = {t.mondo_id: t for t in TARGETS}
    prefixes = {t.code: expand_icd9(t.icd9) for t in TARGETS}

    cur: dict | None = None

    def flush() -> None:
        if not cur or not cur["id"].startswith("MONDO:"):
            return
        out.n_uberon += len(cur["loc"])
        out.n_hp += len(cur["feat"])
        term = MondoTerm(
            id=cur["id"],
            name=cur["name"],
            xrefs=tuple(cur["xref"]),
            obsolete=cur["obs"],
            uberon=tuple(cur["loc"]),
            hp=tuple(cur["feat"]),
            icd9=tuple(
                x.split(":", 1)[1] for x in cur["xref"] if x.upper().startswith("ICD9:")
            ),
        )
        t = wanted.get(term.id)
        if term.obsolete:
            # "ID 写错了"和"上游把这个 term 合并/废弃了"是两种完全不同的故障，
            # 前者要改 targets.py，后者要跟上游找替代 ID，所以必须分开记
            if t:
                out.obsolete_hit.append(f"{t.code}:{t.id}")
            return

        if t:
            out.anchors[t.code] = term
            return

        # 亚部位排除主条目自己：pancreas 的主条目就带 ICD9:157.x，
        # 不排除的话它会把自己算成一个下钻子节点
        if term.icd9:
            for code, pre in prefixes.items():
                if any(icd9_matches(x, pre) for x in term.icd9):
                    out.subsites[code].append(term)

    for line in io.BytesIO(body):
        s = line.decode("utf-8", "replace").rstrip("\n")
        if s.startswith("data-version:"):
            out.version = s.split(":", 1)[1].strip()
        elif s.startswith("["):
            flush()
            cur = {"id": "", "name": "", "xref": [], "obs": False, "loc": [], "feat": []}
            if s == "[Term]":
                out.n_terms += 1
        elif cur is None:
            continue
        elif not cur["id"] and s.startswith("id:"):
            # 不能 split(':', 1)：'id: MONDO:0005061' 会只切出 'MONDO'，
            # 切掉 3 字符前缀再 strip 才能把整个 CURIE 原样留下
            cur["id"] = s[3:].strip()
        elif s.startswith("name:"):
            cur["name"] = s[5:].strip()
        elif s.startswith("is_obsolete:") and "true" in s.lower():
            cur["obs"] = True
        elif s.startswith("xref:"):
            cur["xref"].append(_xref_value(s[5:]))
        elif s.startswith("relationship: disease_has_location UBERON:"):
            cur["loc"].append(s.split()[2])
        elif s.startswith("relationship: disease_has_feature HP:"):
            cur["feat"].append(s.split()[2])
    flush()
    return out


def _xref_value(s: str) -> str:
    """OBO 的 xref 行带尾注：`xref: ICD9:162.3 {source="DOID:1324"}`。

    不切掉 `{...}` 的话，取出来的"码"是 `162.3 {source="DOID:1324"}`，
    拿去和 targets 的前缀比永远不等，整个探针会静默地一个都命中不了。
    """
    return s.split(" {", 1)[0].strip()


def probe(offline: bool = False) -> ProbeResult:
    try:
        body, origin, existing, obs = load_payload(offline)
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
    anchors = scanned.anchors
    subsites = {code: len(v) for code, v in scanned.subsites.items()}
    obsolete_hit = scanned.obsolete_hit
    obs_codes = {e.split(":", 1)[0] for e in obsolete_hit}

    prefix_hist: Counter = Counter()
    for term in anchors.values():
        prefix_hist.update(x.split(":", 1)[0] for x in term.xrefs)

    # 归档放在解析之后：data-version 要先读出来才能当目录名，
    # 否则同一版本重跑会攒出一堆日期目录，反而看不出上游有没有变
    key = version_key(scanned.version)
    path = existing or raw.archive(SOURCE, key, FILENAME, body)
    rel = raw.rel(path)
    sha = raw.sha256_file(path)

    # "找不到"和"被废弃"分开报：前者说明 targets.py 里的 ID 写错了，
    # 后者说明上游把这 term 合并了，要去 MONDO 的 replaced_by 里找新 ID
    missing = [
        f"{t.code}({t.mondo_id})"
        for t in TARGETS
        if t.code not in anchors and t.code not in obs_codes
    ]

    def has(t_code: str, prefix: str) -> bool:
        return bool(anchors[t_code].xref(prefix))

    missing_required = sorted(
        f"{p}:{t.code}"
        for p in REQUIRED_XREFS
        for t in TARGETS
        if t.code in anchors and not has(t.code, p)
    )
    gaps = {
        p: [t.code for t in TARGETS if t.code in anchors and not has(t.code, p)]
        for p in GAP_XREFS
    }

    sample = [
        {
            "target": t.code,
            "name_zh": t.name_zh,
            "mondo_id": t.mondo_id,
            "mondo_name": anchors[t.code].name if t.code in anchors else None,
            "resolved": t.code in anchors,
            "xrefs": sorted({x.split(":", 1)[0] for x in anchors[t.code].xrefs})
            if t.code in anchors
            else [],
            "subsite_terms": subsites[t.code],
            "uberon": len(anchors[t.code].uberon) if t.code in anchors else 0,
            "hp": len(anchors[t.code].hp) if t.code in anchors else 0,
        }
        for t in TARGETS
    ]

    resolved = len(anchors)
    msg = f"{scanned.n_terms} 个 term，主条目解析 {resolved}/{len(TARGETS)}"
    if missing:
        msg += f"；文件里找不到：{', '.join(missing)}"
    if obsolete_hit:
        msg += f"；已被上游废弃：{', '.join(obsolete_hit)}"
    if missing_required:
        msg += f"；缺必需 xref：{', '.join(missing_required)}"
    for p in GAP_XREFS:
        g = gaps[p]
        if g:
            msg += f"；{p} 缺 {len(g)}/{resolved}：{', '.join(g)}"
    msg += (
        f"；ICD-9 另捞到亚部位 term "
        f"{sum(subsites.values())} 个（器官/组织学下钻用）"
    )
    msg += (
        f"；UBERON 定位 {scanned.n_uberon} 行 / HP 症状 {scanned.n_hp} 行"
        "（有效 MONDO term 内，太稀疏，不能当主源）"
    )
    msg += f"；来源 {origin}"

    if resolved == 0:
        verdict = "empty"
    elif missing or obsolete_hit or missing_required:
        verdict = "partial"
    else:
        verdict = "ok"

    fields = sorted(prefix_hist, key=lambda k: -prefix_hist[k]) + [
        f"UBERON(disease_has_location)={scanned.n_uberon}",
        f"HP(disease_has_feature)={scanned.n_hp}",
    ] + [f"{p}缺口={len(gaps[p])}" for p in GAP_XREFS]

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        rows_seen=scanned.n_terms,
        diseases_covered=resolved,
        diseases_total=len(TARGETS),
        fields_seen=fields,
        sample=sample,
        raw_path=rel,
        reachability=obs[0],
        http_status=obs[1],
        latency_ms=obs[2],
        dataset_code=DATASET,
        upstream_version=scanned.version,
        release_date=key,
        release_bytes=len(body),
        release_sha256=sha,
    )
