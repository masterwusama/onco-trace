"""GBD 批量入口探针：判"不登录、不点界面，能不能把数值取回来"。

判据只有一条，但结论必须分两半报，混在一起就只剩"GBD 能用 / 不能用"这种没用的话：

  A 设计覆盖——GBD 的词表里有没有 18 病 × 中国 × ≥5 年 × ≥10 年龄组 × 双性别。
    这一半匿名可测，证据是 Results Tool 自己在界面上挂着的那份 codebook ZIP
    （`/sites/default/files/ihme_query_tool/` 下的静态文件不在登录门内）。
  B 数值入口——真正的估计值能不能匿名编程取回。实测三条路都堵着：
    GHDX 记录页把每个文件的 href 换成 `/download-access/login`（页面本身 200，
    文件名与字节数都读得到，就是不给链接）；Results Tool 的查询接口要 Azure AD B2C
    换来的 token（scope `.../data-api/data.read`）；界面前还有一层 Cloudflare 机器人校验。

所以这一维的裁定写成"词表达标、数值 0/18"。`diseases_covered` 按真正能落库的算，
词表那一半进 message 与 sample——覆盖度矩阵要的是可用数据，不是设计意图。

受保护页是这里最容易踩的坑：某些 record 返回 HTTP 200，正文却是"Protected Page —
Enter password"。按状态码判可用会把它记成"页面正常、只是没有文件"，把一个授权问题
说成源缺数据。认标题不认状态码，离线重放时也只有一个标题可认。
"""
from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from pathlib import Path

import openpyxl

from .. import raw
from ..fetch import fetch
from ..targets import TARGETS
from .result import ProbeResult

SOURCE = "gbd_results"
DATASET = "gbd21-codebook"
CODEBOOK_NAME = "IHME_GBD_2021_CODEBOOK.zip"
CODEBOOK = (
    "https://ghdx.healthdata.org/sites/default/files/ihme_query_tool/" + CODEBOOK_NAME
)
INDEX = "https://ghdx.healthdata.org/gbd-2023"
GHDX = "https://ghdx.healthdata.org"

CRITERIA = (
    "存在可匿名、可编程调用的批量入口，按 18 病 × 中国 × ≥5 年 × ≥10 年龄组 × 双性别"
    "取到 incidence/deaths 数值；词表覆盖与数值入口分别裁定"
)

# 这一维真正要落库的四个度量。GBD 词表里还有 YLLs/DALYs/HALE/人口学等 17 个，
# 不在判据内但一起数着——换一个度量就可能换到覆盖，那是 B7 裁定要用的信息。
NEED_MEASURES = ("Deaths", "Incidence", "Prevalence", "YLDs (Years Lived with Disability)")

LOGIN_HREF = "/download-access/login"

FILE_RE = re.compile(
    r'<a href="(?P<href>[^"]+)"\s+type="(?P<ctype>[^;"]+);\s*length=(?P<bytes>\d+)"\s*'
    r'title="(?P<name>[^"]+)">',
    re.I,
)
RECORD_RE = re.compile(r'href="(/record/[^"#?]+)"')
DOI_RE = re.compile(r"https?://doi\.org/(10\.[^\s\"<]+)", re.I)


def _parse_record(html: str) -> dict:
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    files = [
        {
            "name": m["name"],
            "bytes": int(m["bytes"]),
            "type": m["ctype"].rsplit("/", 1)[-1],
            # 只留"是否被换成登录链接"这一位事实：真被门挡住的行 href 全是同一个串，
            # 把 22 份一模一样的字符串存进 sample 没有信息量
            "gated": LOGIN_HREF in m["href"],
        }
        for m in FILE_RE.finditer(html)
    ]
    doi = DOI_RE.search(html)
    return {
        "protected": bool(title and "protected page" in title.group(1).lower()),
        "files": files,
        "doi": doi.group(1) if doi else "",
    }


def _codebook_version(body: bytes) -> str:
    m = re.search(r"Y(\d{4})M(\d{2})D(\d{2})", " ".join(zipfile.ZipFile(io.BytesIO(body)).namelist()))
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _codebook_tables(body: bytes) -> tuple[dict, dict[str, list[dict]]]:
    """codebook ZIP 里两份文件：横向词表 CSV + 四张层级 XLSX。

    CSV 是"每一列各自一个词表、长度互不相等、短列留空"的形状，
    所以取值只能按列切、跳过空串，不能当普通表 zip()——那样所有词表都会被截到最短那列的长度。
    """
    z = zipfile.ZipFile(io.BytesIO(body))
    csv_name = next(n for n in z.namelist() if n.upper().endswith(".CSV"))
    rows = list(csv.reader(io.StringIO(z.read(csv_name).decode("utf-8-sig", "replace"))))
    hdr = [c.strip() for c in rows[0]]

    def column(header: str) -> list[str]:
        i = hdr.index(header)
        return [r[i].strip() for r in rows[2:] if len(r) > i and r[i].strip()]

    vocab = {
        "measures": column("measure_name"),
        "sexes": column("sex_label"),
        "age_groups": column("age_group_name"),
        "years": [y for y in column("year_id") if y.isdigit()],
        "locations": dict(zip(column("location_id"), column("location_name"))),
    }
    xlsx_name = next(n for n in z.namelist() if n.upper().endswith(".XLSX"))
    wb = openpyxl.load_workbook(io.BytesIO(z.read(xlsx_name)), read_only=True, data_only=True)
    trees: dict[str, list[dict]] = {}
    for sheet in ("Cause Hierarchy", "REI Hierarchy"):
        it = wb[sheet].iter_rows(values_only=True)
        h = [str(c) for c in next(it)]
        trees[sheet] = [dict(zip(h, r)) for r in it if r and r[0] not in (None, "")]
    return vocab, trees


def probe(offline: bool = False) -> ProbeResult:
    reach = "offline" if offline else "direct"
    http: int | None = None
    ms_total = 0
    key_dir: Path | None = None

    if offline:
        key_dir = raw.newest_dir(SOURCE, CODEBOOK_NAME)
        cb_path = key_dir / CODEBOOK_NAME if key_dir else None
        if not cb_path or not cb_path.is_file():
            raise SystemExit(
                f"离线重放需要先有一份归档：data/raw/{SOURCE}/*/{CODEBOOK_NAME} 不存在"
            )
        cb = cb_path.read_bytes()
        version = key_dir.name
        sha = raw.sha256_file(cb_path)
    else:
        res = fetch(CODEBOOK, timeout=(10, 120))
        ms_total += res.latency_ms
        http = res.status
        if res.reachability == "proxy":
            reach = "proxy"
        if not res.ok or not res.body:
            return ProbeResult(
                verdict="dead" if res.status in (404, 410) else "blocked",
                message=f"{CODEBOOK} → {res.status or res.reachability}：{res.note}",
                criteria=CRITERIA,
                dataset_code=DATASET,
                reachability=res.reachability,
                http_status=res.status,
                latency_ms=res.latency_ms,
            )
        cb = res.body
        # 版本取自 ZIP 里成员文件名的日期戳，不取自 URL：URL 里压根没有日期，
        # 而 IHME 会在同一个 URL 上换季版，Y2024M05D16 才是"这一版词表"的唯一标识
        version = _codebook_version(cb) or "unknown"
        sha = raw.sha256_bytes(cb)
    release_date = None if version == "unknown" else version

    vocab, trees = _codebook_tables(cb)
    causes = {
        str(int(r["Cause ID"])): str(r["Cause Name"]) for r in trees["Cause Hierarchy"]
    }
    l4: dict[str, list[str]] = {}
    for r in trees["Cause Hierarchy"]:
        if r.get("Level") == 4 and r.get("Parent ID") is not None:
            l4.setdefault(str(int(r["Parent ID"])), []).append(str(r["Cause Name"]))

    # 反向核对：声明在 targets.py 的 gbd_cause 必须真的存在于这一版词表。
    # 病因 ID 被上游回收时，落库会写出挂在空节点上的疾病——那比少一个病严重得多。
    missing = [t.code for t in TARGETS if t.gbd_cause not in causes]
    n_sub = sum(len(l4.get(t.gbd_cause, [])) for t in TARGETS)

    years = sorted(int(y) for y in vocab["years"])
    china = [i for i, n in vocab["locations"].items() if n == "China"]
    have_measures = [m_ for m_ in NEED_MEASURES if m_ in vocab["measures"]]
    ages = set(vocab["age_groups"])
    one_yr = sum(1 for a in ages if a.isdigit())

    # ---- 数值入口：整轮 GBD 2023 的 record 全走一遍，不抽样 ----
    bodies: dict[str, bytes] = {}
    records: dict[str, dict] = {}
    if offline:
        for p in sorted(key_dir.glob("record__*.html")):
            records[p.stem[len("record__"):]] = _parse_record(
                p.read_text(encoding="utf-8", errors="replace")
            )
    else:
        ir = fetch(INDEX, timeout=(10, 60))
        ms_total += ir.latency_ms
        if ir.reachability == "proxy":
            reach = "proxy"
        if not ir.ok:
            return ProbeResult(
                verdict="dead" if ir.status in (404, 410) else "blocked",
                message=f"词表已取回但 record 索引 {INDEX} → {ir.status or ir.reachability}",
                criteria=CRITERIA,
                dataset_code=DATASET,
                reachability=ir.reachability,
                http_status=ir.status,
                latency_ms=ms_total,
                raw_path=None,
                upstream_version=version,
                release_date=release_date,
                release_bytes=len(cb),
                release_sha256=sha,
            )
        for slug in sorted(set(RECORD_RE.findall(ir.text))):
            rr = fetch(GHDX + slug, timeout=(10, 60))
            ms_total += rr.latency_ms
            if rr.reachability == "proxy":
                reach = "proxy"
            if not rr.ok:
                continue
            records[slug] = _parse_record(rr.text)
            bodies[slug] = rr.body
            time.sleep(0.3)

    files = [f for rec in records.values() for f in rec["files"]]
    gated = [f for f in files if f["gated"]]
    open_files = [f for f in files if not f["gated"]]
    protected = [s for s, rec in records.items() if rec["protected"]]
    empty = [s for s, rec in records.items() if not rec["protected"] and not rec["files"]]
    gated_bytes = sum(f["bytes"] for f in gated)

    if not offline:
        key_dir = raw.archive_dir(SOURCE, version)
        (key_dir / CODEBOOK_NAME).write_bytes(cb)
        for slug, b in bodies.items():
            name = "record__" + slug.removeprefix("/record/").replace("/", "__") + ".html"
            (key_dir / name).write_bytes(b)

    usable = len(open_files)
    if not usable:
        # 数值匿名一条都取不到，这一维就还没通；词表缺哪几个病已经在 message 里点名了
        verdict = "blocked"
    else:
        verdict = "ok" if not missing else "partial"
    span = f"{years[0]}–{years[-1]}（{len(years)} 个值）" if years else "无"
    msg = (
        f"词表达标：{len(TARGETS) - len(missing)}/{len(TARGETS)} 病有对应病因档"
        f"（{len(TARGETS)} 档下面合计另带 {n_sub} 个 L4 亚档，逐个列在 sample 里）；"
        f"中国=location_id {china[0] if china else '?'}，词表含 {len(vocab['locations'])} 个地点；"
        f"年度 {span}；性别 {'/'.join(vocab['sexes'])}；"
        f"年龄组词表 {len(ages)} 档（其中 1 岁一档 {one_yr} 个）；"
        f"需要的度量 {len(have_measures)}/{len(NEED_MEASURES)} 个在列"
    )
    if missing:
        msg += f"；targets.py 声明的这些 gbd_cause 在本版词表里找不到：{', '.join(missing)}"
    if usable:
        msg += (
            f"。数值入口：GBD 2023 的 {len(records)} 个 record 共 {len(files)} 个文件，"
            f"{usable} 个可匿名取回，{len(gated)} 个仍指向 {LOGIN_HREF}"
            f"（{gated_bytes / 2**30:.1f} GiB）"
        )
    else:
        msg += (
            f"。数值入口未达判据：GBD 2023 的 {len(records)} 个 record 里 {len(protected)} 个整页要密码"
            f"（HTTP 200 的 Protected Page，不是空页）、{len(empty)} 个页面没有文件行，"
            f"{len(gated)} 个文件的链接一律被换成 {LOGIN_HREF}"
            f"（合计 {gated_bytes / 2**30:.1f} GiB，最大的四个连同大小列在 sample 里）。"
            f"数值可用覆盖 0/{len(TARGETS)}；匿名可取回的只有这份 {len(cb)} 字节的词表"
            f"（{version}）。要走通只能注册 IHME 免费非商用账号，CC BY-NC 4.0 不可商用"
        )
    return ProbeResult(
        verdict=verdict,
        message=msg,
        criteria=CRITERIA,
        # rows_seen 会同时进 source_probe_log 和 dataset_release，所以它只能描述
        # 归档的那份数据集本身（词表 578 行），不能填 record 扫出来的 118 个文件行——
        # 后者会让 dataset_release 记下一个 codebook 根本没有的行数
        rows_seen=sum(len(v) for v in trees.values()),
        diseases_covered=0 if usable == 0 else len(TARGETS) - len(missing),
        diseases_total=len(TARGETS),
        fields_seen=[
            "measure_name", "sex_label", "age_group_name", "year_id", "location_id",
            "Cause Hierarchy", "REI Hierarchy",
        ],
        sample=[
            {
                "code": t.code,
                "gbd_cause": t.gbd_cause,
                "gbd_name": causes.get(t.gbd_cause, ""),
                "l4_children": l4.get(t.gbd_cause, []),
            }
            for t in TARGETS
        ]
        + [
            {"gated_file": f["name"], "bytes": f["bytes"]}
            for f in sorted(gated, key=lambda x: -x["bytes"])[:4]
        ],
        raw_path=raw.rel(key_dir) if key_dir else None,
        reachability=reach,
        http_status=http,
        latency_ms=ms_total or None,
        dataset_code=DATASET,
        upstream_version=version,
        release_date=release_date,
        release_bytes=len(cb),
        release_sha256=sha,
    )
