#!/usr/bin/env python
"""解析回归跑测器：拿仓库里的 fixture 页重跑探针的解析器，结构事实必须与判据依赖的一致。

    python etl/tests/run.py        # 跑全部；退出码非 0 即有 FAIL（不需要 pytest）

fixture 是上游原样字节（`etl/tests/fixtures/` 在 `.gitattributes` 里按二进制处理，
连换行都不改），所以这一跑兼作漂移检测：SEER 改了表结构，这里先红，
而不是等覆盖度文档里莫名少一页。`manifest.json` 记着每页的 sha256、来源版本
与"为什么是这一页"，跑测器先核字节再核解析结果。

归档该不该进仓库是按体积逐源判的：MONDO 51 MB、GWAS 整包 71 MB 不进，
SEER 四页 340 KB 进——判据分支每支有一页在场上就够。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，中文断言输出会变乱码
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "etl"))

from onco_etl.probes import seer_statfacts as seer  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 四页共享的年度序列事实。SEER 8 那两列是 49 年而不是 50 行——最后一年的 New Cases
# 是 '-'（无观测值），按行数算会把"占位符"当成"有数"。
METRICS = ["Rate of New Cases — SEER 8", "Rate of New Cases — SEER 12",
           "Death Rate — U.S.", "5-Year Relative Survival — SEER 8"]
OBS = {
    "Rate of New Cases — SEER 8 — Observed": (49, "1975", "2023"),
    "Rate of New Cases — SEER 12 — Observed": (32, "1992", "2023"),
    "Death Rate — U.S. — Observed": (50, "1975", "2024"),
    "5-Year Relative Survival — SEER 8 — Observed": (44, "1975", "2018"),
    "5-Year Relative Survival — SEER 8 — Modeled Trend": (49, "1975", "2023"),
}


class Checks:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def eq(self, what: str, got, want) -> None:
        ok = got == want
        self.items.append((what, ok, "" if ok else f"得到 {got!r}，期望 {want!r}"))

    def ok(self, what: str, cond: bool, note: str = "") -> None:
        self.items.append((what, bool(cond), note))


def check_seer(c: Checks) -> None:
    d = FIXTURES / "seer_statfacts"
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    pages = {}
    for name, info in man["files"].items():
        body = (d / name).read_bytes()
        c.eq(f"seer fixture {name} 字节未改写",
             (len(body), hashlib.sha256(body).hexdigest()),
             (info["bytes"], info["sha256"]))
        pages[name[:-5]] = seer.parse_page(body)

    for slug, page in pages.items():
        ys = page["year_series"] or {}
        c.eq(f"seer {slug} 年度序列跨度", (ys.get("n_years"), ys.get("y0"), ys.get("y1")),
             (50, "1975", "2024"))
        c.eq(f"seer {slug} metric 清单", ys.get("metrics"), METRICS)
        obs = ys.get("observed") or {}
        # 8 列 = 4 个 metric × (Observed, Modeled Trend)。少一半说明二级表头没配上，
        # 拟合线就会和观测值挤成同名列
        c.eq(f"seer {slug} 观测/拟合分列", len(obs), 8)
        for col, want in OBS.items():
            got = obs.get(col) or {}
            c.eq(f"seer {slug} {col}",
                 (got.get("n"), got.get("y0"), got.get("y1")), want)

    c.eq("seer lungb 分期档", (pages["lungb"]["stage_survival"] or {}).get("n"), 4)
    c.eq("seer lungb 双性别费率表标签", pages["lungb"]["rate_sexes"], {"Males", "Females"})
    c.eq("seer lungb 种族分组", len(pages["lungb"]["race_groups"]), 6)
    c.eq("seer lungb 年龄档", (pages["lungb"]["age_incidence"] or {}).get("n"), 8)
    c.eq("seer lungb 性别口径", seer.sex_verdict("lung", pages["lungb"]), "ok")

    # 白血病这一支：SEER 没给它发分期表，解析必须真的什么也没找到（None 而不是空表），
    # 探针才走 NO_STAGE 豁免而不是"≥3 档"判据
    c.eq("seer leuks 无分期表（豁免分支）", pages["leuks"]["stage_survival"], None)
    c.ok("seer leuks 在 NO_STAGE 名单里", "leukemia" in seer.NO_STAGE)
    c.eq("seer leuks 性别口径", seer.sex_verdict("leukemia", pages["leuks"]), "ok")

    # 同为血液肿瘤，NHL 却有 Ann Arbor 五档——防的是"按 category='heme' 一律豁免"
    c.eq("seer nhl 分期档（heme 不等于无分期）",
         (pages["nhl"]["stage_survival"] or {}).get("n"), 5)

    # 性别特异癌这一支：页面上没有 <h5> 性别标签、也没有种族费率表，
    # 性别只写在口径行里，所以两条证据链都必须被认到
    c.eq("seer prost 无 <h5> 性别标签", pages["prost"]["rate_sexes"], set())
    c.eq("seer prost 无种族费率表", pages["prost"]["race_groups"], [])
    c.ok("seer prost 口径行带 Males",
         any("All Races, Males" in v for v in pages["prost"]["vintages"]))
    c.eq("seer prost 性别口径", seer.sex_verdict("prostate", pages["prost"]), "ok")
    # 拿男性页去核对女性专属病：必须判成 conflict（targets.py 声明写错了），
    # 不能混进 missing（源没给证据）——两者要去查的边不一样
    c.eq("seer prost 当作女性病核对", seer.sex_verdict("cervix", pages["prost"]), "conflict")


CHECKS = (("seer_statfacts", check_seer),)


def main() -> int:
    c = Checks()
    for _, fn in CHECKS:
        try:
            fn(c)
        except Exception as e:  # noqa: BLE001 - 解析器抛错就是回归失败，不是跑测器坏了
            c.items.append((fn.__name__, False, f"抛异常 {type(e).__name__}: {e}"))
    bad = 0
    for what, ok, note in c.items:
        if not ok:
            bad += 1
        print(("PASS  " if ok else "FAIL  ") + what + (f" —— {note}" if note else ""))
    print(f"\n{len(c.items) - bad}/{len(c.items)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
