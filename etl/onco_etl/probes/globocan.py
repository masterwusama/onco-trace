"""GCO "Cancer Today"（GLOBOCAN 估算）中国口径探针。

这个源是 SPA 外壳：`gco.iarc.who.int/today/` 返回 200 的 6 KB 页面里没有数据，真实入口是
`https://gco-api.iarc.fr/api/globocan/v3/<版本>/` 下的 JSON，匿名直连即出，不需要 token、
没有登录门（GBD 那一维就卡在这道门上，所以"有没有门"这件事本身要单独留一行证据）。

版本号不写死：`v3/[vdb]` 里的 `[vdb]` 是前端构建常量（bundle 里的 `data_version:`2024``），
从 `/today/` 引的 main-*.js 里正则读出来。IARC 换季版时 URL 形状不变、只换这个数字，
把它写死等于把探针钉死在今天这一版上。

判据只管一件事：**国家级单点估计**能不能匿名编程取回。它取不到年龄组（实测路径末段是癌种
过滤器而不是年龄档，`ages_specific=1` 加与不加都回同样行数），年度序列也只体现为
`meta/update/` 列出的历史版本——一版一个年份，不是一条曲线。年龄组与逐年那两半由同站的
Cancer Over Time 探针（源 `gco_overtime`）另行裁定，这一行不许替它说话。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "globocan"
DATASET = "gco-today-national"
APP = "https://gco.iarc.who.int/today/"
BUNDLE_RE = re.compile(r'src="(/today/assets/main-[A-Za-z0-9_-]+\.js)"')
API_PATH_RE = re.compile(r"api/globocan/v(\d+)/\[vdb\]")
VERSION_RE = re.compile(r"data_version:`(\d{4})`")
API_ROOT = "https://gco-api.iarc.fr/api/globocan/"
FS_PARAMS = "group_CRC=1&include_nmsc=1&include_nmsc_other=1"

# type 就是这个 API 里的"度量"：0 新发病例、1 死亡、2 现患
NEED_TYPES = {0: "incidence", 1: "mortality", 2: "prevalence"}

# 中国按 iso3 认，不按 country 数字认：156 今天对，换成 IARC 自定义分组码就会认错
COUNTRY_ISO3 = "CHN"

META_FILES = ("meta_update.json", "meta_cancers.json", "meta_populations.json")
DATASET_FILES = META_FILES + ("factsheet.json",)

CRITERIA = (
    "可匿名 JSON 入口按 18 病 × 中国 × 双性别取回 incidence/mortality 的例数、世界标化率"
    "与累积风险；本行只裁国家级单点估计，年龄组与逐年序列由 gco_overtime 那行裁"
)


def _iso(d: str | None) -> str | None:
    """meta/update 的 date 是 `12-12-2013`（日-月-年），而 release_date 列是 DATE。"""
    if not d:
        return None
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", d.strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat() if m else None


def _public_releases(updates: list) -> tuple[list, list]:
    """meta/update 拆成（公开发布，内部预发布）。

    `intern` 不为 0 的那几条 data_version 是 "ps" / "2024dev" / "2024ssa"，是 IARC 自己的
    占位与预发布；把它们当公开数据集登记进 dataset_release，等于给一个取不到数的版本建档案。
    """
    pub, pre = [], []
    for u in updates:
        (pre if int(u.get("intern") or 0) else pub).append(u)
    return pub, pre


def _resolve_version() -> tuple[str, str, int, int, str]:
    """从 /today/ 的 bundle 里读出版本号，返回 (major, version, http, ms, reachability)。"""
    res = fetch(APP, timeout=(10, 60), max_bytes=200_000)
    ms, reach = res.latency_ms, res.reachability
    if not res.ok or not res.body:
        raise SystemExit(f"{APP} → {res.status or res.reachability}：{res.note}")
    bundle = BUNDLE_RE.search(res.text)
    if not bundle:
        raise SystemExit(f"{APP} 的壳里没有 /today/assets/main-*.js，入口形状变了")
    js = fetch("https://gco.iarc.who.int" + bundle.group(1), timeout=(10, 90))
    ms += js.latency_ms
    reach = "proxy" if "proxy" in (reach, js.reachability) else "direct"
    if not js.ok:
        raise SystemExit(f"{bundle.group(1)} → {js.status or js.reachability}：{js.note}")
    api, ver = API_PATH_RE.search(js.text), VERSION_RE.search(js.text)
    if not api or not ver:
        raise SystemExit("bundle 里找不到 api/globocan/v[n]/[vdb] 与 data_version 常量，"
                         "入口解析规则要改")
    return api.group(1), ver.group(1), js.status, ms, reach


def _get_json(url: str, ms: int) -> tuple[object, int, int, str, bytes]:
    r = fetch(url, timeout=(10, 90), max_bytes=8_000_000)
    ms += r.latency_ms
    if not r.ok or not r.body:
        raise SystemExit(f"{url} → {r.status or r.reachability}：{r.note}")
    try:
        parsed = json.loads(r.text)
    except json.JSONDecodeError as e:
        # 这个 API 出错时会给 200 + PHP fatal error 的 HTML（实测过），所以状态码不够
        raise SystemExit(f"{url} → HTTP {r.status} 但响应不是 JSON（{r.content_type}）：{r.text[:160]}")
    return parsed, ms, r.status, r.reachability, r.body


def probe(offline: bool = False) -> ProbeResult:
    http: int | None = None
    ms = 0
    if offline:
        key_dir = raw.newest_dir(SOURCE, "factsheet.json")
        if not key_dir:
            raise SystemExit(f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/factsheet.json 不存在")
        major, version, reach = "3", key_dir.name, "offline"
        bodies = {n: (key_dir / n).read_bytes() for n in DATASET_FILES}
        blobs = {n: json.loads(b.decode("utf-8")) for n, b in bodies.items()}
    else:
        major, version, http, ms, reach = _resolve_version()
        base = f"{API_ROOT}v{major}/{version}/"
        bodies: dict[str, bytes] = {}
        blobs: dict[str, object] = {}
        for name, path in zip(
            META_FILES, ("meta/update/", "meta/cancers/all/", "meta/populations/all/")
        ):
            blobs[name], ms, http, reach, bodies[name] = _get_json(base + path, ms)
            time.sleep(0.2)
        pops = blobs["meta_populations.json"]
        assert isinstance(pops, list)
        cn = [p for p in pops if p.get("country_iso3") == COUNTRY_ISO3]
        if not cn:
            return ProbeResult(
                verdict="blocked",
                message=f"{base}meta/populations/all/ 的 {len(pops)} 个地点里没有 iso3={COUNTRY_ISO3}"
                "——中国这一档被上游挪走或改码了，本探针不猜新码",
                criteria=CRITERIA,
                reachability=reach,
                http_status=http,
                latency_ms=ms or None,
            )
        name = "factsheet.json"
        blobs[name], ms, http, reach, bodies[name] = _get_json(
            f"{base}factsheet/population/{cn[0]['country']}/?{FS_PARAMS}", ms
        )
        key_dir = raw.archive_dir(SOURCE, version)
        for n, b in bodies.items():
            (key_dir / n).write_bytes(b)

    updates, cancers, pops, fs = (
        blobs["meta_update.json"],
        blobs["meta_cancers.json"],
        blobs["meta_populations.json"],
        blobs["factsheet.json"],
    )
    assert isinstance(updates, list) and isinstance(cancers, list)
    assert isinstance(pops, list) and isinstance(fs, dict)
    rows = fs["dataset"]

    cn = next(p for p in pops if p.get("country_iso3") == COUNTRY_ISO3)
    by_key = {(r["cancer_code"], r["sex"], r["type"]): r for r in rows}
    codes = {int(c["id"]) for c in cancers}

    sample, covered, missing = [], 0, []
    for t in TARGETS:
        code = int(t.gco_today)
        meta = next((c for c in cancers if int(c["id"]) == code), None)
        got = {}
        for ty, nm in NEED_TYPES.items():
            # 取 sex=0（两性合计）那行来判"这个病有没有数"；分性别的 1/2 行同一次响应里也带着，
            # 落库时再按 targets.sex 取
            r = by_key.get((code, 0, ty))
            if r:
                got[nm] = {"total": r["total"], "asr": r["asr"], "crude_rate": r["crude_rate"]}
        if code in codes and {"incidence", "mortality"} <= set(got):
            covered += 1
        else:
            missing.append(f"{t.code}(gco_today={code})")
        sample.append(
            {
                "code": t.code,
                "gco_today": code,
                "gco_label": meta["label"] if meta else "",
                "gco_icd": meta.get("ICD") if meta else "",
                "target_icd10": t.icd10,
                "declared_sex": t.sex,
                "sexes_present": sorted({r["sex"] for r in rows if r["cancer_code"] == code}),
                **got,
            }
        )

    # 年龄维"是不是真的没有"按字段名判，不按"我没找到端点"判：上游哪天加了 ages 字段，
    # 这里会自动变成 partial，而不是继续宣称 ok
    age_fields = sorted({k for r in rows for k in r if "age" in k.lower()})
    public, pre = _public_releases(updates)
    releases = sorted({str(u.get("data_version")) for u in public})
    mine = [u for u in public if str(u.get("data_version")).split(" ")[0] == version]
    this_rel = max(mine, key=lambda u: _iso(u.get("date")) or "") if mine else None

    if covered == 0:
        verdict = "blocked"
    elif covered < len(TARGETS) or age_fields:
        verdict = "partial"
    else:
        verdict = "ok"

    msg = (
        f"国家级单点估计 {covered}/{len(TARGETS)} 病达标：一次请求回 {len(rows)} 行，形状是 "
        f"sex 0/1/2 × type {sorted({r['type'] for r in rows})}（0 新发 / 1 死亡 / 2 现患）× "
        f"{len({r['cancer_code'] for r in rows})} 个癌种码，每行带 total / asr（世界标化）/ "
        f"crude_rate / cum_risk_74。中国 country={cn['country']}（iso3 {COUNTRY_ISO3}），"
        f"估算方法码 incidence={cn.get('method_incidence')} / mortality={cn.get('method_mortality')}"
        f"（{len(cancers)} 个癌种码 × {len(pops)} 个地点码表随包取回归档）。"
        f"注意现患(type 2)在一个 (sex,cancer) 上有 3 行而不带期间标签——1/3/5 年现患混在一起，"
        f"落库前必须回 description 里读口径句，不能默认取第一行。"
    )
    if missing:
        msg += f"这些病在本版码表或响应里没配对上：{', '.join(missing)}。"
    if age_fields:
        msg += f"响应里出现了按年龄拆分的字段 {age_fields}，判据需要重写，先记 partial。"
    msg += (
        f"年龄组未达判据：{len(rows)} 行里没有任何年龄维度字段，路径末段实测是癌种过滤器而非"
        f"年龄档，`ages_specific=1` 加与不加都回同样的行数。逐年序列也不在这条路上：一版一个年份，"
        f"meta/update 的公开发布只有 {len(releases)} 档（{'、'.join(releases)}），"
        f"另有 {len(pre)} 条 intern=1 的内部预发布记录（data_version 是 ps / 2024dev / 2024ssa "
        f"一类）不算数据集；跨版本拼出来的序列每一步都换了估算口径，不能当趋势线画。"
        f"年龄别与逐年两半由 gco_overtime 那行裁定。"
    )
    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        # rows_seen 同时进 source_probe_log 与 dataset_release，只能描述归档的这份数据集本身
        rows_seen=len(rows),
        diseases_covered=covered,
        diseases_total=len(TARGETS),
        fields_seen=sorted({k for r in rows for k in r})
        + ["meta_update.data_version", "meta_cancers.ICD", "meta_populations.method_incidence"],
        sample=sample
        + [
            {
                "release": this_rel or {},
                "china_meta": {k: cn.get(k) for k in ("country", "label", "method_incidence",
                                                      "method_mortality", "income_label",
                                                      "who_region", "hdi_label")},
            }
        ],
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=_iso((this_rel or {}).get("date")),
        release_bytes=sum(len(b) for b in bodies.values()),
        release_sha256=raw.sha256_bytes(b"".join(bodies[n] for n in DATASET_FILES)),
    )
