"""GBD 危险因素归因探针：判"效应量能不能匿名取回"，而不是判"有没有危险因素清单"。

危险度这一维的出口判据要的是每病 ≥3 个危险因素**带 PAF 或 RR/OR + CI**。实测分两半：

  A 效应量——一条都没有。vizhub GBD Compare 只有 `GET /api/config` 匿名开放（回版本号），
    /api/metadata、/api/data、/api/hierarchy、/api/data/version 四个全 401；
    对照 /api/language 回 404，说明那四个 401 是"路由在、要授权"，不是整站兜底。
    授权面是 Azure AD B2C：shell 里 `__requireAuth__ ??= true`，
    bundle 里 scope `.../data-api/data.read`。
  B 关联骨架——匿名能拿。data guide 页上挂着 A2 交叉表，`Risk` 表 2390 行 cause×REI 对
    （工作簿声明 8917 行，其余整行空白），度量列全部是 `X`（"这个组合有数"），没有任何数值。
    所以它能填 `disease_risk_factor` 的关联与 role，填不了 `paf`。

`diseases_covered` 按判据算=0（效应量一条没有），关联骨架覆盖多少个病写进 message 与 sample。
这个口径和 gbd_results 一致：覆盖度矩阵要的是能落库的数，词表和骨架另说——混在一起，
B7 就会把"有清单没强度"这一维当成通了。

聚合档必须剔：REI 是 5 层树，`Dietary` 与它下面十几个子档会同时出现在同一病的行里，
直接数档数等于把一个危险因素数成十几个。规则是"本病集合内还有后代的档不计"——
不能按 level≥3 切：`High body-mass index`、`High fasting plasma glucose` 是二档却自带
暴露与 PAF，按层数切会把达标病数从 10/18 误判成 7/18。层级表唯一的匿名来源在 gbd_results
归档的 codebook 里（2023 版挂在登录门后），所以这里跨源读它，不在本模块复制一份 URL。

取表与归档抽在 `load_payload` 里、三张表的拼接抽在 `_cra` 里，判据只读它们的产出——
`load/risks.py` 落那份可干预暴露清单时不必再解析一遍工作簿，也不必重抄一份剔聚合档的规则。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "gbd_cra"
DATASET = "cra-cause-risk-map"
CRITERIA = (
    "每病 ≥3 个独立危险因素带效应量（PAF 或 RR/OR + CI），可匿名编程取回；"
    "只有 cause×risk 关联而没有效应量不算达标"
)

GUIDE = "https://www.healthdata.org/research-analysis/about-gbd/gbd-data-and-tools-guide"
SITE = "https://www.healthdata.org"
# 版本号写在文件名里（…_Y2025M10D23.XLSX），所以 URL 必须每次从 guide 页现取，不能拼死
A2_RE = re.compile(r'href="((?:https?://[^"]*?)?/sites/default/files/[^"]*A2_RESULTS[^"]*\.XLSX)"', re.I)
A2_GLOB = "*A2_RESULTS*.XLSX"
META_NAME = "probe_meta.json"

VIZHUB = "https://vizhub.healthdata.org/gbd-compare"
CONFIG_ROUTE = "/api/config"
# 数据面逐个走一遍：结论会随 IHME 政策变，只留一句"要授权"不够复现。
# language 故意放进去当对照组——它 404，用来证明那四个 401 不是全站兜底
DATA_ROUTES = ("/api/metadata", "/api/data", "/api/hierarchy", "/api/data/version")
CONTROL_ROUTES = ("/api/language",)

CODEBOOK_NAME = "IHME_GBD_2021_CODEBOOK.zip"
REI_SHEET = "REI Hierarchy"
MEASURE_COLS = ("Deaths", "YLLs", "YLDs", "DALYs")
MIN_FACTORS = 3

# 逐条目测（2026-09-08，读的是 `data/raw/gbd_cra/2025-10-23` 那份 A2 的 Risk 表：
# 18 病各自的独立危险因素清单 + 33 个 REI 名，也就是下面这两份声明）。
# 判的是这一维唯一的规则产物——"剔掉聚合档之后剩下的确实都是可干预暴露"：33 个名里没有
# Tobacco / Dietary / Metabolic / Occupational 这类 level-2 聚合层（它们只作为父档出现，
# 计了会和自己的子档重复），也没有中间量表型（"High fasting plasma glucose" 是 GBD 自己
# 建模用的暴露，不是我们判出来的）。brain 0 条是源里这一癌确实没有独立风险因素行。
# 条数与名单都是装载器落库前的核对项：GBD 改清单就是这份结论过期，不许按老口径静默落库。
EYEBALL: dict[str, int] = {
    "lung": 16, "colorectum": 11, "liver": 5, "stomach": 3, "breast_female": 7,
    "pancreas": 4, "esophagus": 4, "prostate": 4, "cervix": 2, "ovary": 2,
    "thyroid": 1, "bladder": 2, "kidney": 3, "brain": 0, "uterus": 1,
    "leukemia": 4, "nhl": 1, "myeloma": 1,
}
REI_EYEBALL = frozenset({
    "Ambient particulate matter pollution", "Chewing tobacco",
    "Diet high in processed meat", "Diet high in red meat", "Diet high in sodium",
    "Diet low in calcium", "Diet low in fiber", "Diet low in fruits", "Diet low in milk",
    "Diet low in vegetables", "Diet low in whole grains", "Drug use", "High alcohol use",
    "High body-mass index", "High fasting plasma glucose",
    "Household air pollution from solid fuels", "Low physical activity",
    "Occupational exposure to arsenic", "Occupational exposure to asbestos",
    "Occupational exposure to benzene", "Occupational exposure to beryllium",
    "Occupational exposure to cadmium", "Occupational exposure to chromium",
    "Occupational exposure to diesel engine exhaust",
    "Occupational exposure to formaldehyde", "Occupational exposure to nickel",
    "Occupational exposure to polycyclic aromatic hydrocarbons",
    "Occupational exposure to silica", "Occupational exposure to trichloroethylene",
    "Residential radon", "Secondhand smoke", "Smoking", "Unsafe sex",
})


def _sheets(body: bytes) -> dict[str, list]:
    wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    return {ws.title: list(ws.iter_rows(values_only=True)) for ws in wb.worksheets}


def _is_number(v: str) -> bool:
    return bool(re.fullmatch(r"-?[\d.]+(?:[eE][-+]?\d+)?", v))


def _risk_table(rows: list) -> tuple[dict, dict, list]:
    """Risk 表 → ({cause_id: {rei_id: [各度量有无数]}}, {rei_id: 名字}, 数值型列名)。

    存在性标记 X 与效应量共用同一批列名，只能按值分：一列里过半是数字才算"这列有数"。
    """
    hdr = [("" if v is None else str(v)).strip() for v in rows[0]]
    ci, ri, ni = hdr.index("Cause ID"), hdr.index("REI ID"), hdr.index("Risk")
    mi = [hdr.index(m) for m in MEASURE_COLS if m in hdr]
    if "Deaths" not in hdr:
        raise SystemExit(
            f"Risk 表表头里没有 Deaths 列（只有 {hdr}）——IHME 改了列名，"
            "死亡那一半的度量覆盖度无从判断，别把这个结论落库")
    pairs: dict[int, dict[int, list]] = defaultdict(dict)
    names: dict[int, str] = {}
    for row in rows[1:]:
        try:
            cid, rei = int(row[ci]), int(row[ri])
        except (TypeError, ValueError):
            continue
        flags = ["1" if str(row[m] or "").strip() else "0" for m in mi]
        # 同一对出现多行时取并集：覆盖写法等于把"这一格有数"这一位事实丢掉
        cur = pairs[cid].get(rei)
        pairs[cid][rei] = flags if cur is None else [
            "1" if a == "1" or b == "1" else "0" for a, b in zip(cur, flags)]
        names[rei] = str(row[ni] or "").strip()
    numeric = []
    for i, h in enumerate(hdr):
        if not h or i in {ci, ri, ni}:
            continue
        vals = [str(row[i]).strip() for row in rows[1:]
                if i < len(row) and row[i] is not None and str(row[i]).strip()]
        if vals and sum(1 for v in vals if _is_number(v)) >= len(vals) / 2:
            numeric.append(h)
    return pairs, names, numeric


def _rei_tree() -> tuple[dict, dict, Path]:
    """REI 的 parent/level：唯一匿名来源是 gbd_results 归档里的 codebook。"""
    d = raw.newest_dir("gbd_results", CODEBOOK_NAME)
    if not d or not (d / CODEBOOK_NAME).is_file():
        raise SystemExit(
            f"REI 层级表唯一的匿名来源在 data/raw/gbd_results/*/{CODEBOOK_NAME}，"
            "先跑 ops\\etl.ps1 probe --code gbd_results 再跑这一支"
        )
    z = zipfile.ZipFile(d / CODEBOOK_NAME)
    xlsx = next(n for n in z.namelist() if n.upper().endswith(".XLSX"))
    rows = list(openpyxl.load_workbook(io.BytesIO(z.read(xlsx)), read_only=True,
                                       data_only=True)[REI_SHEET].iter_rows(values_only=True))
    parent, level = {}, {}
    for r in rows[1:]:
        if r[0] is None:
            continue
        rid = int(r[0])
        parent[rid] = int(r[2]) if r[2] is not None else rid
        level[rid] = int(r[4]) if r[4] is not None else -1
    return parent, level, d


def _descendants(rid: int, kids: dict) -> set:
    out, stack = set(), [rid]
    while stack:
        for k in kids.get(stack.pop(), []):
            if k not in out:
                out.add(k)
                stack.append(k)
    return out


def _config_fields(routes: dict) -> dict:
    """/api/config 是这一支唯一匿名开放的 vizhub 面——它给的是版本面，不是数据。

    解析要容错：这条 200 是判据的一部分（"匿名面只有版本号"），正文换形状时
    宁可少一句限定，也不能让整支探针挂在这条装饰性字段上。
    """
    try:
        d = json.loads(routes[CONFIG_ROUTE]["body"])["data"]
    except (KeyError, TypeError, ValueError):
        return {}
    return {k: d[k] for k in ("releaseText", "gbdYear", "copyYear") if d.get(k) is not None}


@dataclass(frozen=True)
class Factor:
    """一个"病 × 独立危险因素"对：装载器写的一行就是它。

    只收剔掉聚合档之后的独立档——`Dietary` 与它下面十几个子档会同时出现在同一病的行里，
    两个都落等于把一个危险因素数成十几个。
    """

    code: str
    cause_id: int
    cause_name: str
    rei_id: int
    rei_name: str
    deaths: bool  # Deaths 那格有 `X`。四列度量都只是"这个组合有数"的标记，不是效应量

    def assoc_key(self) -> str:
        return hashlib.sha1(f"cra|{self.cause_id}|{self.rei_id}".encode("utf-8")).hexdigest()


@dataclass
class Cra:
    """Risk / Cause / codebook 三张表拼到一起的产出：判据读 `per`，装载器读 `factors`。"""

    sheets: dict = field(default_factory=dict)
    chdr: list = field(default_factory=list)
    risk_hdr: list = field(default_factory=list)
    pairs: dict = field(default_factory=dict)
    rei_names: dict = field(default_factory=dict)
    numeric: list = field(default_factory=list)
    causes: dict = field(default_factory=dict)
    cause_ids: set = field(default_factory=set)
    risk: list = field(default_factory=list)
    cause: list = field(default_factory=list)
    risk_blank: int = 0
    unknown_rei: list = field(default_factory=list)
    hier_dir: Path | None = None
    per: dict = field(default_factory=dict)
    factors: list[Factor] = field(default_factory=list)


@dataclass
class CraPayload:
    """取表这一趟的产出。`blocked` 非空表示这份 A2 没拿成，探针原样报回去。"""

    cra: Cra | None = None
    blocked: ProbeResult | None = None
    key_dir: Path | None = None
    version: str = "unknown"
    a2_name: str = ""
    size: int = 0
    sha: str = ""
    meta: dict = field(default_factory=dict)
    ms: int = 0
    reach: str = "direct"
    http: int | None = None
    proxied: list = field(default_factory=list)


def load_payload(offline: bool) -> CraPayload:
    pl = CraPayload()
    reach = "offline" if offline else "direct"
    ms_total = 0
    key_dir: Path | None = None
    http: int | None = None
    meta: dict = {}
    via_proxy: list[str] = []

    def mark(name: str, res) -> None:
        nonlocal reach
        if res.reachability == "proxy":
            reach = "proxy"
            via_proxy.append(name)

    if offline:
        key_dir = raw.newest_dir(SOURCE, A2_GLOB)
        a2_path = next((p for p in key_dir.glob(A2_GLOB)), None) if key_dir else None
        if not a2_path:
            raise SystemExit(
                f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/{A2_GLOB} 不存在")
        a2 = a2_path.read_bytes()
        a2_name = a2_path.name
        sha = raw.sha256_file(a2_path)
        meta = json.loads((key_dir / META_NAME).read_text(encoding="utf-8"))
    else:
        g = fetch(GUIDE, timeout=(10, 60), max_bytes=6_000_000)
        ms_total += g.latency_ms
        mark("guide 页", g)
        if not g.ok:
            pl.blocked = ProbeResult(
                verdict="dead" if g.status in (404, 410) else "blocked",
                message=f"{GUIDE} → {g.status or g.reachability}：{g.note}",
                criteria=CRITERIA, dataset_code=DATASET, reachability=g.reachability,
                http_status=g.status, latency_ms=g.latency_ms)
            return pl
        m = A2_RE.search(g.text)
        if not m:
            pl.blocked = ProbeResult(
                verdict="dead",
                message="guide 页 200 但没有 A2_RESULTS 交叉表链接——IHME 改了附件命名或下架了，"
                        "这一维的匿名关联骨架也跟着没了",
                criteria=CRITERIA, dataset_code=DATASET, reachability=g.reachability,
                http_status=g.status, latency_ms=ms_total)
            return pl
        href = m.group(1)
        a2_url = href if href.lower().startswith("http") else SITE + href
        a2_name = a2_url.rsplit("/", 1)[-1]
        r = fetch(a2_url, timeout=(10, 180), max_bytes=200_000_000)
        ms_total += r.latency_ms
        http = r.status
        mark("A2 交叉表", r)
        if not r.ok or not r.body or r.truncated:
            pl.blocked = ProbeResult(
                verdict="dead" if r.status in (404, 410) else "blocked",
                message=f"{a2_url} → {r.status or r.reachability}：{r.note} "
                        f"declared={r.declared_bytes} got={len(r.body)} truncated={r.truncated}",
                criteria=CRITERIA, dataset_code=DATASET, reachability=r.reachability,
                http_status=r.status, latency_ms=ms_total)
            return pl
        a2, sha = r.body, raw.sha256_bytes(r.body)

        routes = {}
        for path in (CONFIG_ROUTE,) + DATA_ROUTES + CONTROL_ROUTES:
            rr = fetch(VIZHUB + path, timeout=(10, 45), max_bytes=400_000,
                       headers={"Accept": "application/json"})
            ms_total += rr.latency_ms
            mark(path, rr)
            routes[path] = {"status": rr.status, "reach": rr.reachability,
                            "ctype": (rr.content_type or "")[:40], "bytes": len(rr.body),
                            "body": rr.text[:700]}
            time.sleep(0.3)
        meta = {"guide": GUIDE, "a2_url": a2_url, "vizhub": VIZHUB, "routes": routes,
                "via_proxy": via_proxy, "config": _config_fields(routes)}

    m = re.search(r"Y(\d{4})M(\d{2})D(\d{2})", a2_name)
    version = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "unknown"
    if not offline:
        key_dir = raw.archive_dir(SOURCE, version)
        (key_dir / a2_name).write_bytes(a2)
        (key_dir / "guide.html").write_bytes(g.body)
        (key_dir / META_NAME).write_bytes(
            json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))

    pl.cra = _cra(a2)
    pl.key_dir, pl.version, pl.a2_name, pl.size = key_dir, version, a2_name, len(a2)
    pl.sha, pl.meta, pl.ms, pl.http = sha, meta, ms_total, http
    pl.reach, pl.proxied = reach, via_proxy
    return pl


def _cra(a2: bytes) -> Cra:
    """Risk / Cause / codebook 三张表合成一份产出：判据数的 `per` 与装载器落的 `factors` 同源。

    表间拼接若在探针里另留一份，两边会悄悄分叉，而分叉的表现形式是
    "探针说这维够用，库里却没有数"。
    """
    c = Cra()
    c.sheets = _sheets(a2)
    c.chdr = [("" if v is None else str(v)).strip() for v in c.sheets["Cause"][0]]
    c.risk_hdr = [("" if v is None else str(v)).strip() for v in c.sheets["Risk"][0]]
    c.pairs, c.rei_names, c.numeric = _risk_table(c.sheets["Risk"])
    c.cause_ids = {int(r[0]) for r in c.sheets["Cause"][1:] if r and r[0] is not None}
    # read_only 模式给的行数包含上万条空行，只有带真值的行数能进 rows_seen，
    # 否则 dataset_release 会记下一个这份文件根本没有的规模
    c.risk = [r for r in c.sheets["Risk"][1:] if any(v not in (None, "") for v in r)]
    c.cause = [r for r in c.sheets["Cause"][1:] if any(v not in (None, "") for v in r)]
    c.risk_blank = len(c.sheets["Risk"]) - 1 - len(c.risk)
    causes = {}
    for row in c.cause:
        try:
            causes[int(row[0])] = str(row[c.chdr.index("Cause")])
        except (TypeError, ValueError, IndexError):
            continue
    c.causes = causes

    parent, level, c.hier_dir = _rei_tree()
    kids: dict[int, list[int]] = defaultdict(list)
    for k, p in parent.items():
        if p != k:
            kids[p].append(k)
    c.unknown_rei = sorted({r for v in c.pairs.values() for r in v} - set(parent))

    per: dict[str, dict] = {}
    for t in TARGETS:
        rs = c.pairs.get(int(t.gbd_cause), {})
        # 集合内无后代 = 独立危险因素；有后代的那一档只是聚合展示，计了会重复
        own = sorted((r for r in rs if not (_descendants(r, kids) & set(rs))),
                     key=lambda r: (level.get(r, -1), c.rei_names.get(r, "")))
        per[t.code] = {
            "code": t.code, "gbd_cause": int(t.gbd_cause),
            "gbd_name": causes.get(int(t.gbd_cause), ""),
            "tiers": len(rs), "independent": len(own),
            "with_deaths": sum(1 for r in own if rs[r][0] == "1"),
            "factors": [c.rei_names.get(r, str(r)) for r in own],
        }
        for rid in own:
            c.factors.append(Factor(
                code=t.code, cause_id=int(t.gbd_cause),
                cause_name=causes.get(int(t.gbd_cause), ""), rei_id=rid,
                rei_name=c.rei_names.get(rid, str(rid)), deaths=rs[rid][0] == "1"))
    c.per = per
    return c


def probe(offline: bool = False) -> ProbeResult:
    pl = load_payload(offline)
    if pl.blocked:
        return pl.blocked
    c = pl.cra
    sheets, pairs, rei_names, numeric = c.sheets, c.pairs, c.rei_names, c.numeric
    causes, per, hier_dir, unknown_rei = c.causes, c.per, c.hier_dir, c.unknown_rei
    risk, cause, cause_ids = c.risk, c.cause, c.cause_ids
    chdr, risk_hdr, risk_blank = c.chdr, c.risk_hdr, c.risk_blank
    a2_name, size = pl.a2_name, pl.size
    version, key_dir, sha = pl.version, pl.key_dir, pl.sha
    meta, ms_total, reach, http = pl.meta, pl.ms, pl.reach, pl.http
    via_proxy = pl.proxied

    routes = meta.get("routes", {})
    via_proxy = meta.get("via_proxy") or via_proxy
    cfg = meta.get("config") or _config_fields(routes)
    gated = [p for p in DATA_ROUTES if (routes.get(p) or {}).get("status") in (401, 403)]
    open_data = [p for p in DATA_ROUTES if (routes.get(p) or {}).get("status") == 200]
    notfound = [p for p in CONTROL_ROUTES if (routes.get(p) or {}).get("status") == 404]
    cov = sum(1 for v in per.values() if v["independent"] >= MIN_FACTORS)
    cov_death = sum(1 for v in per.values() if v["with_deaths"] >= MIN_FACTORS)
    no_rows = [k for k, v in per.items() if not v["tiers"]]
    # 词表里没这个病因档 ≠ 有档却没有危险因素行，混在一起会指错方向（前者要改 targets.py）
    no_cause = [t.code for t in TARGETS if int(t.gbd_cause) not in cause_ids]

    pairs_n = sum(len(v) for v in pairs.values())
    risk_total = len(sheets["Risk"]) - 1
    cfg_txt = "、".join(f"{k}={v}" for k, v in cfg.items()) or "版本面（字段名已变）"
    msg = (
        f"效应量未达判据：GBD Compare 数据面 {'、'.join(gated) or '、'.join(DATA_ROUTES)} 全 401 "
        f"`Unauthorized.`（对照 {'、'.join(notfound) or '、'.join(CONTROL_ROUTES)} 回 404，"
        f"所以是真有路由且要授权，不是整站兜底）；匿名开放面只有 GET {CONFIG_ROUTE} 回 200，"
        f"给的内容只有 {cfg_txt}，不含数据。"
        "要走通只能注册 IHME 免费非商用账号（Azure AD B2C，scope data-api/data.read）。"
        f"匿名可取回的是 guide 页挂的 {a2_name}（{size} B，{len(sheets)} 张表）："
        f"Risk 表真数据 {len(risk)} 行（read_only 报 {risk_total} 行，其中 {risk_blank} 行整行空白），"
        f"去重后 {pairs_n} 个 cause×REI 对 = {len(pairs)} 个病因 × {len(rei_names)} 个 REI；"
        f"{'/'.join(MEASURE_COLS)} 四列的值只有 X（该组合有数）或空，"
        f"数值型列 {len(numeric)} 个——能建关联、建不了 paf。"
        f"Cause 表 {len(causes)} 档，targets.py 声明的 gbd_cause "
        f"{len(TARGETS) - len(no_cause)}/{len(TARGETS)} 在列{'' if not no_cause else '（缺 ' + ', '.join(no_cause) + '）'}。"
        f"关联骨架按 {raw.rel(hier_dir)} 的 {REI_SHEET} 剔掉聚合档后："
        f"{cov}/{len(TARGETS)} 病有 ≥{MIN_FACTORS} 个独立危险因素"
        f"（带 Deaths 度量的 {cov_death}/{len(TARGETS)}），"
        f"Risk 表里一行没有的 {len(no_rows)} 个：{', '.join(no_rows) or '—'}；"
        "每病档位明细与逐个危险因素名在 sample 里"
    )
    if unknown_rei:
        msg += (f"。另有 {len(unknown_rei)} 个 REI 不在 2021 层级表里（{unknown_rei[:6]}），"
                "无法判聚合关系，按叶子计入，计数偏乐观")
    if via_proxy:
        msg += (f"。这一趟 {len(via_proxy)} 步走了代理（{', '.join(via_proxy)}），"
                "reachability 按「任一步落过代理」记；probe-reach 只打登记表里那一个 URL，"
                "两趟结论可以不一致，scheduler 配代理以这一行为准")
    return ProbeResult(
        verdict="blocked" if not numeric or not open_data else (
            "ok" if cov == len(TARGETS) else "partial"),
        message=msg,
        criteria=CRITERIA,
        rows_seen=len(risk) + len(cause),
        diseases_covered=0 if not numeric or not open_data else cov,
        diseases_total=len(TARGETS),
        fields_seen=[f"sheet:{t}" for t in sheets]
        + [f"Cause:{c}" for c in chdr if c and c != "None"]
        + [f"Risk:{c}" for c in risk_hdr if c and c != "None"]
        + [f"gbd_results codebook:{REI_SHEET}"],
        sample=list(per.values()),
        raw_path=raw.rel(key_dir) if key_dir else None,
        reachability=reach,
        http_status=http,
        latency_ms=ms_total or None,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=None if version == "unknown" else version,
        release_bytes=size,
        release_sha256=sha,
    )
