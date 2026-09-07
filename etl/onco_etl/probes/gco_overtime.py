"""GCO "Cancer Over Time"（登记处年度序列）中国口径探针。

同一个 GCO 门户下的另一个产品，与 Cancer Today 是两个数据库：Today 给国家级单点估算
（2024 一个年份、无年龄组），这一路给**逐年 × 5 岁年龄组**的登记处序列。SEER 只能给
8 档宽年龄组、GBD 的数值全在登录门后，这一维的 ≥5 年 × ≥10 年龄组就看这里。

入口形状同样是 SPA 壳：`/overtime/` 只回一个 3 KB 壳，API base 连同版本号写死在前端
构建产物 `chunk-vendors.*.js` 里（`https://gco-api.iarc.fr/api/overtime/v2/22/`），
5 岁组的标签数组与指标清单写在 `index.*.js` 里。所以版本号从 bundle 读，不写死；
年龄档标签也从 bundle 读并连同响应一起归档，离线重放不必再下一遍 900 KB 的 JS。

响应里 `type` 只有 0/1（新发/死亡），逐年序列按 (type, sex, cancer) 一行一行给，
每行的 `ages` / `populations` / `age_specific_rate` 是 18 个 5 岁档 + `unk` 三个同构字典。
"""
from __future__ import annotations

import json
import re
import time

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "gco_overtime"
DATASET = "gco-overtime-series"
APP = "https://gco.iarc.who.int/overtime/"
API_ROOT = "https://gco-api.iarc.fr/api/overtime/"

# 一个正则只认这一个事实：API base 出现在 vendors 包里，带两级版本号
VENDORS_RE = re.compile(r'src="(/overtime/src/subsites/overtime/assets/js/chunk-vendors\.[0-9a-f]+\.js)"')
INDEX_RE = re.compile(r'src="(/overtime/src/subsites/overtime/assets/js/index\.[0-9a-f]+\.js)"')
API_URL_RE = re.compile(r"https?://gco-api\.iarc\.fr/api/overtime/v(\d+)/(\d+)/")
LABELS_RE = re.compile(r"ages_labels\s*[:=]\s*\[([^\]]*)\]")
INDICATORS_RE = re.compile(r"indicators\s*[:=]\s*\[(.{0,900}?)\}\]", re.S)

COUNTRY_ISO3 = "CHN"
NEED_YEARS = 5
NEED_BANDS = 10
# sex=0 是两性合计；女性专属癌要看到自己的 sex=2 那行才算分层到位
SEX_BY_DECLARED = {"both": ("0",), "female": ("2",), "male": ("1",)}

CRITERIA = (
    "中国 18 病 × ≥5 个年度 × ≥10 个 5 岁年龄组 × 声明性别的年龄别率与标化率可匿名编程取回；"
    "发病与死亡两条序列分别裁定"
)

SERIES_FILES = ("data_incidence.json", "data_mortality.json")
ARCHIVE_FILES = ("app_config.json", "meta_cancers.json", "meta_populations.json") + SERIES_FILES


def _js(url: str, ms: int) -> tuple[str, int, int, str]:
    r = fetch(url, timeout=(10, 90))
    ms += r.latency_ms
    if not r.ok or not r.body:
        raise SystemExit(f"{url} → {r.status or r.reachability}：{r.note}")
    return r.text, ms, r.status, r.reachability


def _unwrap(parsed: object) -> list:
    """meta/* 直接是列表，data/* 外面裹一层 {"dataset": [...], "error": []}。

    在线与离线两条路径都必须过这里：_get_json 曾经顺手把 dataset 拆掉，而离线读归档
    拿到的是整份响应，于是同一个探针在两种模式下看到两种形状，重放当场炸在断言上。
    """
    if isinstance(parsed, dict):
        ds = parsed.get("dataset")
        return ds if isinstance(ds, list) else []
    return parsed if isinstance(parsed, list) else []


def _get_json(url: str, ms: int) -> tuple[object, int, int, str, bytes]:
    r = fetch(url, timeout=(10, 120), max_bytes=32_000_000)
    ms += r.latency_ms
    if not r.ok or not r.body:
        raise SystemExit(f"{url} → {r.status or r.reachability}：{r.note}")
    try:
        d = json.loads(r.text)
    except json.JSONDecodeError as e:
        # 实测这个 API 出错时也给 HTTP 200 + PHP fatal error 的 HTML，状态码不足以判可用
        raise SystemExit(f"{url} → HTTP {r.status} 但响应不是 JSON（{r.content_type}）：{r.text[:160]}")
    return d, ms, r.status, r.reachability, r.body


def probe(offline: bool = False) -> ProbeResult:
    http: int | None = None
    ms = 0
    if offline:
        key_dir = raw.newest_dir(SOURCE, "data_incidence.json")
        if not key_dir:
            raise SystemExit(
                f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/data_incidence.json 不存在"
            )
        cfg = json.loads((key_dir / "app_config.json").read_text(encoding="utf-8"))
        major, release, reach = cfg["api_major"], cfg["api_release"], "offline"
        blobs = {n: json.loads((key_dir / n).read_text(encoding="utf-8")) for n in ARCHIVE_FILES}
        sizes = {n: (key_dir / n).stat().st_size for n in ARCHIVE_FILES}
    else:
        html, ms, http, reach0 = _js(APP, ms)
        vend, idx = VENDORS_RE.search(html), INDEX_RE.search(html)
        if not vend or not idx:
            raise SystemExit(f"{APP} 的壳里找不到 chunk-vendors/index 脚本引用，入口解析规则要改")
        vsrc, ms, http, reach1 = _js("https://gco.iarc.who.int" + vend.group(1), ms)
        api = API_URL_RE.search(vsrc)
        if not api:
            raise SystemExit(f"{vend.group(1)} 里没有 api/overtime/v[n]/[m]/ 常量")
        isrc, ms, http, reach2 = _js("https://gco.iarc.who.int" + idx.group(1), ms)
        reach = "proxy" if "proxy" in (reach0, reach1, reach2) else "direct"
        major, release = api.group(1), api.group(2)
        base = f"{API_ROOT}v{major}/{release}/"

        labels = [s.strip().strip('"') for s in LABELS_RE.findall(isrc)[:1]]
        band_labels = [s for s in re.split(r'","', labels[0]) if s] if labels else []
        ind = INDICATORS_RE.search(isrc)
        indicators = re.findall(r'key:"([a-z0-9_]+)"', ind.group(1)) if ind else []
        cfg = {
            "api_major": major,
            "api_release": release,
            "api_base": base,
            "ages_labels": band_labels,
            "indicators": indicators,
            # 版本来自 app 构建产物本身，不是我们拼的 URL——记录来源，下次换版好核对
            "version_source": vend.group(1),
        }
        blobs: dict[str, object] = {"app_config.json": cfg}
        bodies: dict[str, bytes] = {
            "app_config.json": json.dumps(cfg, ensure_ascii=False).encode("utf-8")
        }
        for name, path in (
            ("meta_cancers.json", "meta/cancers/all/"),
            ("meta_populations.json", "meta/populations/all/"),
        ):
            blobs[name], ms, http, reach_, bodies[name] = _get_json(base + path, ms)
            reach = "proxy" if "proxy" in (reach, reach_) else reach
            time.sleep(0.2)
        pops = _unwrap(blobs["meta_populations.json"])
        cn = [p for p in pops if p.get("country_iso3") == COUNTRY_ISO3]
        if not cn:
            return ProbeResult(
                verdict="blocked",
                message=f"{base}meta/populations/all/ 的 {len(pops)} 个地点里没有 iso3={COUNTRY_ISO3}"
                "——中国这一档不在这一版覆盖里（或被改码），本探针不猜新码",
                criteria=CRITERIA,
                reachability=reach,
                http_status=http,
                latency_ms=ms or None,
            )
        cn = cn[0]
        # 路径是 data/{indicator}/{type}/{sex}/{country}/{cancer}/，type 0 新发、1 死亡
        for name, ty in zip(SERIES_FILES, ("0", "1")):
            blobs[name], ms, http, reach_, bodies[name] = _get_json(
                f"{base}data/rate/{ty}/0_1_2/{cn['country']}/all/", ms
            )
            reach = "proxy" if "proxy" in (reach, reach_) else reach
            time.sleep(0.2)
        key_dir = raw.archive_dir(SOURCE, f"r{release}")
        for n, b in bodies.items():
            (key_dir / n).write_bytes(b)
        sizes = {n: len(b) for n, b in bodies.items()}

    cancers = _unwrap(blobs["meta_cancers.json"])
    pops = _unwrap(blobs["meta_populations.json"])
    inc = _unwrap(blobs["data_incidence.json"])
    mort = _unwrap(blobs["data_mortality.json"])
    if offline:
        cn = next(p for p in pops if p.get("country_iso3") == COUNTRY_ISO3)
        cfg = blobs["app_config.json"]
    band_labels = cfg["ages_labels"] if isinstance(cfg, dict) else []

    def measure(rows: list, kind: str) -> tuple[int, dict, list]:
        """按 targets 逐病核对：码在不在、年份够不够、年龄档够不够、声明的性别有没有。"""
        codes = {int(c["id"]): c for c in cancers}
        present = {int(r["cancer"]) for r in rows}
        cov, detail = 0, []
        for t in TARGETS:
            code = int(t.gco_time)
            sub = [r for r in rows if r["cancer"] == code]
            years = sorted({int(r["year"]) for r in sub})
            bands = sorted({k for r in sub for k in r["ages"]} - {"unk"})
            sexes = sorted({str(r["sex"]) for r in sub})
            need_sex = SEX_BY_DECLARED[t.sex]
            ok = (
                code in codes
                and len(years) >= NEED_YEARS
                and len(bands) >= NEED_BANDS
                and set(need_sex) <= set(sexes)
            )
            cov += ok
            detail.append(
                {
                    "code": t.code,
                    "gco_time": code,
                    "gco_label": codes[code]["label"] if code in codes else "",
                    "gco_icd": codes[code].get("ICD") if code in codes else "",
                    "target_icd10": t.icd10,
                    "declared_sex": t.sex,
                    "sexes_present": sexes,
                    "years": f"{years[0]}–{years[-1]}" if years else "无",
                    "n_years": len(years),
                    "n_bands": len(bands),
                    "pass": ok,
                    "metric": kind,
                }
            )
        stats = {
            "rows": len(rows),
            "n_cancers": len(present),
            "n_years": len({int(r["year"]) for r in rows}),
            "years": sorted({int(r["year"]) for r in rows}),
            "n_bands": len({k for r in rows for k in r["ages"]} - {"unk"}),
            "sexes": sorted({r["sex"] for r in rows}),
        }
        return cov, stats, detail

    inc_cov, inc_st, inc_detail = measure(inc, "incidence")
    mort_cov, mort_st, _ = measure(mort, "mortality")

    if inc_cov == len(TARGETS) and mort_cov == len(TARGETS):
        verdict = "ok"
    elif inc_cov or mort_cov:
        verdict = "partial"
    else:
        verdict = "blocked"

    def span(st: dict) -> str:
        ys = st["years"]
        return f"{ys[0]}–{ys[-1]}（{len(ys)} 年）" if ys else "无一行"

    national = cn.get("bool_national") or (inc[0]["national"] if inc else None)
    msg = (
        f"发病序列达标 {inc_cov}/{len(TARGETS)}：中国 {span(inc_st)}，"
        f"{inc_st['n_cancers']} 个癌种码，每行带 {inc_st['n_bands']} 个 5 岁档"
        f"（`ages` 计数 / `populations` 分母 / `age_specific_rate` 率三个同构字典 + `unk`），"
        f"另有 asr / asr_e / asr_e2013 / asr_n / crude_rate / cum_risk_74 / cum_risk_79；"
        f"sex {inc_st['sexes']} 齐全。"
        f"死亡序列达标 {mort_cov}/{len(TARGETS)}（{span(mort_st)}）"
        f"——meta/populations 里中国 mortality={cn.get('mortality')}，"
        f"data/rate/1/… 实测回 0 行，这一路的死亡年龄段中国没有数据。"
        f"两条限定必须随数值一起落库：一是口径，中国 national={national}，"
        f"inc_source 明写「{cn.get('inc_source')}」，inc_cov={cn.get('inc_cov')}，"
        f"inc_period={cn.get('inc_period')}——这是登记处抽样外推，不是全国序列，"
        f"与同门 Cancer Today 的国家级估算不能并成一条曲线；"
        f"二是时效，这条序列最新一年登记到 {inc_st['years'][-1] if inc_st['years'] else '?'}，"
        f"登记处数据本身有数年报送滞后，与同门 Cancer Today 那个单点年份不同源、不可相减成趋势。"
        f"5 岁档标签取自 app 构建产物（有序数组，码 1–{len(band_labels)} 按位置对齐）："
        f"{'、'.join(band_labels[:3] + band_labels[-2:]) if band_labels else '没解析出来，只报档数'}。"
    )
    failed = [d["code"] for d in inc_detail if not d["pass"]]
    if failed:
        msg += f"发病这半没过判据的病：{', '.join(failed)}。"

    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        # 归档的数据集本身：两条序列的行数之和（死亡那条为 0 行，也是事实的一部分）
        rows_seen=inc_st["rows"] + mort_st["rows"],
        diseases_covered=inc_cov,
        diseases_total=len(TARGETS),
        fields_seen=sorted({k for r in inc for k in r})
        + ["meta_cancers.ICD", "meta_populations.inc_cov", "app_config.ages_labels"],
        sample=inc_detail
        + [
            {
                "mortality_rows": mort_st["rows"],
                "china_meta": {k: cn.get(k) for k in ("country", "label", "incidence", "mortality",
                                                      "bool_national", "inc_cov", "inc_period",
                                                      "inc_source")},
                "app_config": {k: cfg.get(k) for k in ("api_base", "version_source", "indicators")},
            }
        ],
        raw_path=raw.rel(key_dir),
        reachability=reach,
        http_status=http,
        latency_ms=ms or None,
        dataset_code=DATASET,
        upstream_version=f"r{release}",
        # 这一版 API 不带发布日，只有登记年；用最新登记年当 release_date 会把"数据到 2017"
        # 说成"这一版 2017 年发布"，宁缺
        release_date=None,
        release_bytes=sum(sizes.values()),
        release_sha256=raw.sha256_bytes(
            b"".join(
                (key_dir / n).read_bytes() if offline else bodies[n] for n in ARCHIVE_FILES
            )
        ),
    )
