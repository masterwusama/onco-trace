"""CLI：`python -m onco_etl <subcommand>`，必须在 etl/ 目录下跑（ops\\etl.ps1 已经替你 cd）。

    seed-sources [--dry-run]        把 sources.py 的候选源写进 source 表，拒绝空 legal_note
    probe-reach [--code X] [--all]  可达性探针：直连/代理三态 + HTTP 状态
    probe [--code X] [--list]
          [--offline]               专项探针：实测行数、18 病覆盖度与字段，够不够填这一维
    probe-status                    每源每份数据集最近一次的裁定，P0 覆盖度矩阵的雏形
    status                          库现状速览

probe-reach 只回答"主机答不答话"，probe 才回答"取回来的东西能不能用"。
两者都写 source_probe_log，靠 dataset_code 区分（reach / sitetype-icdo3 / mondo.obo）。
probe --offline 用 data/raw 里最近一次归档重放，不联网：MONDO 的 .obo 有 51 MB，
解析规则改一行重抓一次要七分钟，而那七分钟里上游什么都没变。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata

# Windows 控制台默认 GBK，中文输出会变乱码
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from . import db, joblog, raw, sources as src_mod, targets
from .clock import now_ts, today
from .config import ROOT
from .fetch import fetch

REACH_CRITERIA = "reach-only：HTTP<400 且非网络层失败"

_SOURCE_COLS = (
    "code",
    "name",
    "org",
    "source_type",
    "dimensions",
    "home_url",
    "download_url",
    "auth",
    "license",
    "commercial_use",
    "attribution_required",
    "legal_note",
    "robots_url",
    "rate_note",
    "fetch_mode",
    "reliability",
    "status",
)


def _w(s: str) -> int:
    """终端显示宽度。中文是全角，len() 只算 1，直接拿去补齐会把整张表弄歪。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def _render(cols: list[str], rows: list[list]) -> None:
    if not rows:
        print("  (0 rows)")
        return
    text = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [_w(c) for c in cols]
    for r in text:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], _w(v))
    print("  " + " | ".join(_pad(c, widths[i]) for i, c in enumerate(cols)))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in text:
        print("  " + " | ".join(_pad(v, widths[i]) for i, v in enumerate(r)))
    print(f"  ({len(rows)} rows)")


def _validate(s: src_mod.Source) -> None:
    """P0 门禁：授权边界没查清的源不许进库。"""
    if not s.legal_note.strip():
        raise SystemExit(f"源 {s.code} 的 legal_note 为空：先查清 robots/ToS/再分发条款再登记")
    if not s.dimensions:
        raise SystemExit(f"源 {s.code} 未声明覆盖维度，探针没法归到矩阵的哪一列")
    if not s.home_url.startswith(("http://", "https://")):
        raise SystemExit(f"源 {s.code} 的 home_url 不是绝对 URL")


def cmd_seed_sources(dry_run: bool) -> int:
    for s in src_mod.SOURCES:
        _validate(s)
    codes = [s.code for s in src_mod.SOURCES]
    if len(set(codes)) != len(codes):
        raise SystemExit("sources.py 里有重复 code")

    sets = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in _SOURCE_COLS if c != "code")
    sql = (
        "INSERT INTO `source` ("
        + ", ".join(f"`{c}`" for c in _SOURCE_COLS)
        + ", `updated_at`) VALUES (:"
        + ", :".join(_SOURCE_COLS)
        + f", :updated_at) ON DUPLICATE KEY UPDATE {sets}, `updated_at`=VALUES(`updated_at`)"
    )

    print(f"候选源 {len(codes)} 个" + ("（dry-run，不写库）" if dry_run else ""))
    if dry_run:
        _render(
            ["code", "type", "dimensions", "license", "download_url"],
            [
                [
                    s.code,
                    s.source_type,
                    ",".join(s.dimensions),
                    s.license or "-",
                    "有" if s.download_url else "待解析",
                ]
                for s in src_mod.SOURCES
            ],
        )
        return 0

    with db.tx() as conn:
        for s in src_mod.SOURCES:
            conn.execute(
                db.text(sql),
                {
                    "code": s.code,
                    "name": s.name,
                    "org": s.org,
                    "source_type": s.source_type,
                    # JSON 列必须自己 dumps：MySQL 8 会把不合法的串静默存成 NULL
                    "dimensions": json.dumps(list(s.dimensions), ensure_ascii=False),
                    "home_url": s.home_url,
                    "download_url": s.download_url,
                    "auth": s.auth,
                    "license": s.license,
                    "commercial_use": int(s.commercial_use),
                    "attribution_required": int(s.attribution_required),
                    "legal_note": s.legal_note,
                    "robots_url": s.robots_url,
                    "rate_note": s.rate_note,
                    "fetch_mode": s.fetch_mode,
                    "reliability": s.reliability,
                    "status": s.status,
                    "updated_at": now_ts(),
                },
            )
    with db.ro() as conn:
        n = db.scalars(conn, "SELECT COUNT(*) FROM `source`")[0]
        blank = db.scalars(
            conn, "SELECT COUNT(*) FROM `source` WHERE TRIM(`legal_note`)=''"
        )[0]
    print(f"已写入，source 共 {n} 行，legal_note 空缺 {blank} 行")
    return 1 if blank else 0


def _classify(res) -> tuple[str, str]:
    if res.reachability == "blocked":
        return "blocked", f"网络层不可达（{'→'.join(res.attempts)}）：{res.note}"
    if res.status is None:
        return "dead", f"未取得状态码：{res.note}"
    if res.status in (404, 410):
        return "dead", f"HTTP {res.status}：入口不存在或已下线，需从 home_url 重新解析"
    if res.status in (401, 403, 451):
        # 这三种不等于源不可用：可能只是反爬或地域策略，处置方式和 404 完全不同
        return "blocked", f"HTTP {res.status}：需鉴权或被反爬/地域策略拦下"
    if res.status == 304:
        return "ok", "HTTP 304 未变更"
    if res.ok:
        size = f"{len(res.body)} 字节（截断上限内）" if res.truncated else f"{len(res.body)} 字节"
        declared = f"，声明体量 {res.declared_bytes}" if res.declared_bytes else ""
        return "ok", f"HTTP {res.status} 可达，{size}{declared}，{res.content_type or '无 Content-Type'}"
    return "partial", f"HTTP {res.status}：{res.note}"


def cmd_probe_reach(codes: list[str] | None, include_all: bool, max_bytes: int, sleep: float) -> int:
    with db.ro() as conn:
        rows = db.rows(
            conn,
            "SELECT `id`,`code`,`home_url`,`download_url`,`status` FROM `source`"
            + ("" if include_all else " WHERE `status` <> 'rejected'")
            + " ORDER BY `id`",
        )
    if not rows:
        print("source 表是空的，先跑 seed-sources")
        return 1
    if codes:
        rows = [r for r in rows if r[1] in set(codes)]
        if not rows:
            print(f"没有匹配的源：{codes}")
            return 1

    job = joblog.start("probe-reach", {"sources": len(rows)})
    out: list[list] = []
    failures: list[str] = []
    try:
        for sid, code, home_url, download_url, _status in rows:
            url = download_url or home_url
            res = fetch(url, max_bytes=max_bytes)
            verdict, message = _classify(res)
            # 归档探针取回的那几十字节：覆盖度报告要能回答"当时源到底给了什么"，
            # 否则事后只能重新去敲源站，而它可能已经改版了
            raw_path = None
            if res.body:
                p = raw.archive(code, f"reach-{today()}", f"{code}.bin", res.body)
                raw_path = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
            with db.tx() as conn:
                conn.execute(
                    db.text(
                        "INSERT INTO `source_probe_log`"
                        " (`source_id`,`dataset_code`,`probed_at`,`http_status`,`reachability`,"
                        "  `latency_ms`,`bytes`,`verdict`,`criteria`,`message`,`raw_path`)"
                        " VALUES (:sid,'reach',:ts,:st,:reach,:ms,:bytes,:verdict,:crit,:msg,:raw)"
                    ),
                    {
                        "sid": sid,
                        "ts": now_ts(),
                        "st": res.status,
                        "reach": res.reachability,
                        "ms": res.latency_ms,
                        "bytes": len(res.body) or None,
                        "verdict": verdict,
                        "crit": REACH_CRITERIA,
                        "msg": f"{url} → {res.final_url or url} | {message}",
                        "raw": raw_path,
                    },
                )
            job.tally(probed=1)
            if verdict in ("blocked", "dead", "partial"):
                job.tally(blocked=1)
                failures.append(f"{code}={verdict}")
            out.append([code, url[:58], res.reachability, res.status or "-", res.latency_ms, verdict])
            print(f"  {code:<24} {res.reachability:<8} {res.status or '-':<4} {verdict}")
            time.sleep(sleep)
    except BaseException as e:  # noqa: BLE001 —— 半途中断也要把已跑的探针收口
        job.note(f"中断：{type(e).__name__}: {e}")
        joblog.finish(job, ok=False)
        raise
    job.note("全部失败项：" + ", ".join(failures) if failures else "全部可达")
    joblog.finish(job, ok=True)
    print()
    _render(["code", "url", "reachability", "http", "ms", "verdict"], out)
    print(f"\n基准病种 {len(targets.TARGETS)} 个；本次只做可达性，行数与覆盖度待专项探针")
    return 0


def cmd_probe_status() -> int:
    """每个源的每份数据集最近一次裁定。

    一行一个 (source, dataset_code) 而不是一个 source：同一个源可以既有可达性探针
    又有专项探针（mondo 的 reach 与 mondo.obo），两者的裁定不是一回事，
    压成一行就会把"能连上"和"数据够用"混为一谈。
    """
    with db.ro() as conn:
        rows = db.rows(
            conn,
            "SELECT s.`code`, p.`dataset_code`, p.`verdict`,"
            "       CONCAT(IFNULL(p.`diseases_covered`,'-'),'/',IFNULL(p.`diseases_total`,'-')),"
            "       IFNULL(p.`rows_seen`,'-'), p.`probed_at`, p.`reachability`, p.`http_status`,"
            "       LEFT(p.`message`, 80)"
            " FROM `source_probe_log` p JOIN `source` s ON s.`id` = p.`source_id`"
            " WHERE p.`id` = ("
            "   SELECT MAX(p2.`id`) FROM `source_probe_log` p2"
            "   WHERE p2.`source_id` = p.`source_id`"
            "     AND p2.`dataset_code` = p.`dataset_code`)"
            " ORDER BY s.`code`, p.`dataset_code`",
        )
        never = db.scalars(
            conn,
            "SELECT s.`code` FROM `source` s"
            " WHERE NOT EXISTS (SELECT 1 FROM `source_probe_log` p WHERE p.`source_id` = s.`id`)"
            " ORDER BY s.`code`",
        )
    _render(
        ["code", "dataset", "verdict", "cov", "rows", "probed_at", "reach", "http", "message"],
        [list(r) for r in rows],
    )
    if never:
        print(f"\n从未探针过：{', '.join(never)}")
    return 0


def cmd_status() -> int:
    with db.ro() as conn:
        for t in ("source", "dataset_release", "source_probe_log", "etl_job_log", "db_migration"):
            n = db.scalars(conn, f"SELECT COUNT(*) FROM `{t}`")[0]
            print(f"  {t:<18} {n:>8}")
        blank = db.scalars(
            conn, "SELECT COUNT(*) FROM `source` WHERE TRIM(`legal_note`)=''"
        )[0]
        print(f"\nP0 门禁：legal_note 空缺 {blank} 条（必须为 0）")
        rows = db.rows(
            conn,
            "SELECT `verdict`, COUNT(*) FROM `source_probe_log`"
            " GROUP BY `verdict` ORDER BY COUNT(*) DESC",
        )
        if rows:
            print("探针裁定分布：" + ", ".join(f"{v}={c}" for v, c in rows))
    return 1 if blank else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m onco_etl", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("seed-sources", help="登记候选源")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("probe-reach", help="可达性探针")
    p.add_argument("--code", action="append", help="只跑指定源，可重复")
    p.add_argument("--all", action="store_true", help="连 status=rejected 的一起跑")
    p.add_argument("--max-bytes", type=int, default=65536, help="只读这么多字节，够判可达性")
    p.add_argument("--sleep", type=float, default=1.0, help="源之间的间隔秒数，别把人家打疼")

    p = sub.add_parser("probe", help="专项探针：实测行数、覆盖度与字段")
    p.add_argument("--code", action="append", help="只跑指定探针，可重复")
    p.add_argument("--sleep", type=float, default=1.0)
    p.add_argument("--list", action="store_true", help="列出已实现的探针")
    p.add_argument(
        "--offline",
        action="store_true",
        help="用 data/raw 里最近一次归档重放，不联网。解析规则改一行不必重下 51 MB",
    )

    sub.add_parser("probe-status", help="每源每份数据集最近一次探针裁定")
    sub.add_parser("status", help="库现状速览")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "seed-sources":
            return cmd_seed_sources(a.dry_run)
        if a.cmd == "probe-reach":
            return cmd_probe_reach(a.code, a.all, a.max_bytes, a.sleep)
        if a.cmd == "probe":
            # 延迟导入：专项探针会拖进 openpyxl 一类重依赖，
            # status 这种轻命令不该为它付启动成本
            from . import probes

            if a.list:
                print("已实现探针：" + ", ".join(probes.available()))
                return 0
            return probes.run(a.code, a.sleep, a.offline)
        if a.cmd == "probe-status":
            return cmd_probe_status()
        return cmd_status()
    finally:
        db.dispose()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
