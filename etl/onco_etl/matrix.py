"""覆盖度矩阵：把 `source_probe_log` 的逐病裁定摊成一张 18 病 × 维度列的表，写进 docs/。

矩阵回答的是「这一维这个病有没有源真的给过可达标的数」，而它唯一的输入是探针落库的
`sample`——每病一行、带该探针自己按判据算出的布尔。三条规矩：

1. **没有逐病布尔就没有格子**。留空并说明原因，绝不拿聚合覆盖率（`diseases_covered`）
   反推逐病：反推出来的矩阵看着齐，其实是编的，而它下一环要拿去裁定建哪些业务表。
2. **列判据取自 log 的 `criteria` 原文**，不在这里重写一遍——重写就会和探针各说各话。
3. **逐病数出来的达标数要和探针自报的对得上**；对不上就在文档里点名。聚合数与它自己
   给的明细不一致，正是矩阵该暴露而不是该悄悄抹平的东西。

生成物是文档不是数据：业务表还没建，建起来之后这份矩阵的读者会变成建表的人。
"""
from __future__ import annotations

import json
import time

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import db
from .config import ROOT
from .sources import BY_CODE
from .targets import TARGETS

DOC_PATH = ROOT / "docs" / "数据源覆盖度.md"

# 维度显示顺序（dict 字面量的插入序就是列分组顺序）。
# 词表与 sources.py 的 dimensions 同一套，源在这里不许被改判到别的维
DIMS = {
    "identity": "ID 主干", "anatomy": "器官树", "histology": "组织学",
    "stat": "统计层", "survival": "生存率", "risk": "危险度",
    "symptom": "症状", "narrative": "叙述", "trial": "在招试验",
    "literature": "文献/靶点"}


@dataclass(frozen=True)
class Column:
    """矩阵的一列 = 一份专项探针数据集对 18 病的逐病裁定。

    `rule` 给字段名就直接取那个布尔（探针已经判过）；给函数就是按该探针 CRITERIA
    原文重算一遍——只有 sample 里没留布尔的列才需要，函数里的判据必须能在
    log.criteria 里找到原话，否则这一列就是探针没判过的东西。
    """
    dim: str
    source: str
    dataset: str
    label: str
    rule: str | Callable[[dict], bool] = "pass"


COLUMNS: tuple[Column, ...] = (
    Column("identity", "mondo", "mondo.obo", "MONDO 主条目", "resolved"),
    # sample 只有 n_sites；判据原话是「18 病 100% 落到某个 site recode 分组」
    Column("anatomy", "icdo3_seer", "sitetype-icdo3", "ICD-O-3 拓扑挂载",
           lambda r: (r.get("n_sites") or 0) >= 1),
    # sample 里的 pass 只覆盖发病序列；同一判据下死亡序列 0/18，别在列名里替它遮上
    Column("stat", "gco_overtime", "gco-overtime-series", "GCO 中国 年龄组（发病）"),
    # 判据原话是「incidence 与 mortality 两个量都取回」，sample 里是各自的对象
    Column("stat", "globocan", "gco-today-national", "GLOBOCAN 国家量",
           lambda r: bool((r.get("incidence") or {}).get("total"))
           and bool((r.get("mortality") or {}).get("total"))),
    Column("stat", "who_gho", "gho-odata-cause-age", "WHO GHO 死因×年龄"),
    # 白血病那页源本身不给分期表，探针按 targets.py 的性别/口径声明记 stage_exempt
    Column("survival", "seer_statfacts", "statfacts-html", "SEER 分期档",
           lambda r: (r.get("stages") or 0) >= 3 or bool(r.get("stage_exempt"))),
    # 口径是「主条目 + targets.GWAS_URI 声明档」，P1 把四病的同级档裁成了声明；
    # 只认主条目的严格数留在 sample 的 pass_main 与探针 message 里
    Column("risk", "gwas_catalog", "associations-ontology-annotated-full",
           "GWAS 位点（声明档）", "pass_declared"),
    Column("symptom", "nci_pdq_html", "symptom-items-html", "PDQ 症状清单（英）"),
    Column("symptom", "wikidata", "zh-label-symptom", "现成中文症状清单"),
    Column("narrative", "who_factsheet", "narrative-sections", "WHO 癌种专页",
           lambda r: bool(r.get("sheets"))),
    Column("trial", "ctgov_v2", "studies-countTotal-status", "CT 在招试验"),
    Column("literature", "europepmc", "search-count-availability", "EPMC 近5年文献"),
    # OT 的 associations 是「疾病↔靶点」这一眼，与 EPMC 的文献量同属研究层但不是同一列
    Column("literature", "opentargets", "platform-release-associations", "OT 关联靶点"),
)

# 有专项探针记录但刻意不进矩阵的数据集，原因写在这里而不是让读者对着空列猜
NOT_IN_MATRIX = (
    ("gbd_results", "gbd21-codebook",
     "sample 只有「词表里有这一档」，判据要的数值面 118 个文件全在 IHME 登录门后，18 病零行可判"),
    ("gbd_cra", "cra-cause-risk-map",
     "判据原话要求「≥3 个独立危险因素带效应量（PAF 或 RR/OR + CI）」，匿名侧效应量 0/18，"
     "所以探针给的就是 0/18；关联骨架 10/18 是另一件事，只在 message 里，不占一列"),
    ("ctgov_v2", "studies-query-cond",
     "同一判据的旧口径（不带 countTotal 的翻页计数），再占一列会看着像两个独立的源"),
)

# 一列都没有的维也要在文档里出现，否则读者以为矩阵没有这一维而不是这一维没有逐病证据
DIM_GAPS = {
    "histology": "SEER 的 806 个形态学码与 ICD-O-3.2 码表都是全局码表，探针没给逐病明细；"
                 "18 病的组织学分类要按 ICD-O-3 形态学段自建，不在本矩阵的证据范围内",
}


def _per_disease(sample) -> dict[str, dict]:
    arr = json.loads(sample) if isinstance(sample, str) else (sample or [])
    out: dict[str, dict] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        code = item.get("code") or item.get("target")
        if code:
            out[code] = item
    return out


def latest_probes() -> dict[tuple[str, str], dict]:
    """每 (源, 数据集) 最近一次专项探针裁定；`reach` 行排除——它只答连不连得上。"""
    with db.ro() as conn:
        rows = db.rows(
            conn,
            "SELECT s.`code`, p.`dataset_code`, p.`verdict`, p.`criteria`, p.`message`,"
            "       p.`diseases_covered`, p.`diseases_total`, p.`rows_seen`,"
            "       p.`reachability`, p.`probed_at`, p.`raw_path`, p.`sample`"
            " FROM `source_probe_log` p JOIN `source` s ON s.`id` = p.`source_id`"
            " WHERE p.`dataset_code` <> 'reach'"
            "   AND p.`id` = (SELECT MAX(p2.`id`) FROM `source_probe_log` p2"
            "                  WHERE p2.`source_id` = p.`source_id`"
            "                    AND p2.`dataset_code` = p.`dataset_code`)"
            " ORDER BY s.`code`, p.`dataset_code`",
        )
    cols = ("source", "dataset", "verdict", "criteria", "message", "covered", "total",
            "rows_seen", "reachability", "probed_at", "raw_path", "per")
    out: dict[tuple[str, str], dict] = {}
    for raw in rows:
        d = dict(zip(cols, list(raw[:-1]) + [_per_disease(raw[-1])]))
        out[(d["source"], d["dataset"])] = d
    return out


def cell(col: Column, probe: dict | None, disease: str) -> bool | None:
    """一格：True 达判据 / False 未达 / None 这一列对这个病没有可判的明细。"""
    if not probe:
        return None
    row = probe["per"].get(disease)
    if not row:
        return None
    if callable(col.rule):
        return bool(col.rule(row))
    val = row.get(col.rule)
    return val if isinstance(val, bool) else None


def build() -> str:
    probes = latest_probes()
    codes = [t.code for t in TARGETS]
    zh = {t.code: t.name_zh for t in TARGETS}

    grid: dict[Column, dict[str, bool | None]] = {}
    stat: dict[Column, dict] = {}
    for col in COLUMNS:
        probe = probes.get((col.source, col.dataset))
        g = {c: cell(col, probe, c) for c in codes}
        passed = sum(1 for v in g.values() if v)
        grid[col] = g
        stat[col] = {
            "probe": probe, "passed": passed,
            "missing": [c for c, v in g.items() if v is None],
            "mismatch": bool(probe) and probe["covered"] is not None
            and int(probe["covered"]) != passed,
        }

    L: list[str] = [
        "# 数据源覆盖度矩阵",
        "",
        "自动生成，勿手工编辑：`cd etl && python -m onco_etl matrix`"
        f"（本次生成 {time.strftime('%Y-%m-%d %H:%M')}）。",
        "",
        "唯一输入是 `source_probe_log` 里每 (源, 数据集) **最近一次**专项探针的 `sample`——"
        "每病一行、带该探针按自己判据算出的布尔。没有逐病布尔的数据集不进矩阵，"
        "聚合覆盖率不反推逐病格（理由见 `etl/onco_etl/matrix.py` 顶部三条规矩）。",
        "",
        "图例：`✓` 该病这一列达判据｜`✗` 未达｜`–` 探针没给这一病的明细。",
        "",
        f"## 18 病 × {len({c.dim for c in COLUMNS})} 维",
        "",
    ]
    head = ["病种"] + [c.label for c in COLUMNS]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for code in codes:
        row = [f"`{code}` {zh[code]}"]
        for col in COLUMNS:
            v = grid[col][code]
            row.append("✓" if v else ("✗" if v is False else "–"))
        L.append("| " + " | ".join(row) + " |")

    L += ["", "### 列说明", "",
          "| 列 | 维 | 源 | 数据集 | 裁定 | 逐病达标 | 探针自报 | 判据 | 许可（可否商用） | 最近 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for col in COLUMNS:
        p = stat[col]["probe"] or {}
        src = BY_CODE.get(col.source)
        lic = f"{src.license}（{'可' if src.commercial_use else '禁'}）" if src else "?"
        L.append(f"| {col.label} | {DIMS[col.dim]} | `{col.source}` | `{col.dataset}` | "
                 f"{p.get('verdict', '—')} | {stat[col]['passed']}/{len(codes)} | "
                 f"{p.get('covered', '—')} | {p.get('criteria') or '（无专项探针）'} | {lic} | "
                 f"{str(p.get('probed_at'))[:16]} |")

    L += ["", "### 对账", ""]
    bad = [c for c in COLUMNS if stat[c]["mismatch"]]
    if bad:
        for col in bad:
            p = stat[col]["probe"]
            L.append(f"- ⚠️ `{col.source}/{col.dataset}` 逐病数到 {stat[col]['passed']}，"
                     f"探针自报 `diseases_covered={p['covered']}`——明细与聚合有一个要修。")
    else:
        L.append("- 每列逐病数出的达标数与探针自报的 `diseases_covered` 全对得上。")
    for col in COLUMNS:
        if stat[col]["missing"]:
            L.append(f"- `{col.label}` 有 {len(stat[col]['missing'])} 病缺明细，格子里是 `–`："
                     f"{', '.join(stat[col]['missing'])}")

    L += ["", "## 每维裁定", ""]
    for dim, label in DIMS.items():
        cols = [c for c in COLUMNS if c.dim == dim]
        if not cols:
            if dim in DIM_GAPS:
                L.append(f"### {label}（`{dim}`）\n- 没有逐病证据列：{DIM_GAPS[dim]}\n")
            continue
        L.append(f"### {label}（`{dim}`）")
        for col in cols:
            p = stat[col]["probe"]
            if not p:
                L.append(f"- **{col.label}**：库里没有这个数据集的专项探针记录。")
                continue
            fails = [c for c in codes if grid[col][c] is False]
            L.append(f"- **{col.label}** `{col.source}` → `{p['verdict']}` "
                     f"{stat[col]['passed']}/{len(codes)}：{p['message']}")
            if fails:
                L.append(f"  - 未达判据：{', '.join(fails)}")
        ncn = sorted({c.source for c in cols if not BY_CODE[c.source].commercial_use})
        if ncn:
            L.append(f"- ⚠️ 商用限制：{', '.join(ncn)} 禁商用——站点先按非商用做，"
                     "真要商用时这一维要重算")
        L.append("")

    L += ["## 没进矩阵的", "", "| 源 / 数据集 | 为什么没有这一列 |", "|---|---|"]
    for source, dataset, why in NOT_IN_MATRIX:
        L.append(f"| `{source}` / `{dataset}` | {why} |")
    used = {(c.source, c.dataset) for c in COLUMNS} | {(s, d) for s, d, _ in NOT_IN_MATRIX}
    for key, p in sorted(probes.items()):
        if key in used:
            continue
        L.append(f"| `{key[0]}` / `{key[1]}` | 有逐病 sample 但本矩阵未声明该列——"
                 f"判据不是逐病的，或还没人认领它属于哪一维（verdict=`{p['verdict']}`） |")
    with db.ro() as conn:
        reach_only = db.scalars(
            conn,
            "SELECT s.`code` FROM `source` s"
            " WHERE EXISTS (SELECT 1 FROM `source_probe_log` p WHERE p.`source_id`=s.`id`)"
            "   AND NOT EXISTS (SELECT 1 FROM `source_probe_log` p"
            "                   WHERE p.`source_id`=s.`id` AND p.`dataset_code`<>'reach')"
            " ORDER BY s.`code`")
        never = db.scalars(
            conn,
            "SELECT s.`code` FROM `source` s"
            " WHERE NOT EXISTS (SELECT 1 FROM `source_probe_log` p WHERE p.`source_id`=s.`id`)"
            " ORDER BY s.`code`")
    if reach_only:
        L.append(f"| 只有可达性探针：{', '.join(reach_only)} | 只答了「连不连得上」，"
                 "没答「取回来的东西够不够填这一维」 |")
    if never:
        L.append(f"| 从未探针过：{', '.join(never)} | 源登记表里有，实测记录里没有 |")

    L += ["", "---", "",
          "这张表只裁定「哪些维有源、覆盖到哪」，不裁定「页面长什么样」。",
          "建业务表前逐列读判据与许可；`–` 与没进矩阵的条目要回 `probe --code <源>` 补测，"
          "不要在这份文档里手填格子。", ""]
    return "\n".join(L)


def write_doc(path: Path = DOC_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(), encoding="utf-8", newline="\n")
    return path
