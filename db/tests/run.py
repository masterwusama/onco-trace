#!/usr/bin/env python
"""db 跑测器：迁移应用 + SQL 脚本执行 + 断言判定。

不依赖 mysql.exe（它不在 Git Bash 的 PATH 里，靠命令行取数在本机跑不通）。

    python db/tests/run.py migrate              应用 db/migrations 下未执行的迁移
    python db/tests/run.py apply <file.sql>     直接执行脚本（无事务，用于 schema/seed）
    python db/tests/run.py test  <file.sql>     单连接顺序执行，扫 PASS/FAIL，有 FAIL 则退出码 1
    python db/tests/run.py status               查看迁移、表行数与 P0 门禁
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，中文断言输出会变乱码
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

import pymysql

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "db" / "migrations"

# P0 的四张表；业务表进来后加到这里，status 才有意义
TABLES = ("source", "dataset_release", "source_probe_log", "etl_job_log", "db_migration")


def load_env() -> dict:
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    missing = [k for k in ("DB_HOST", "DB_USER", "DB_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit(f".env 缺少 {missing}")
    return env


def connect(env: dict) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT", 3306)),
        user=env["DB_USER"],
        password=env["DB_PASSWORD"],
        database=env.get("DB_NAME", "db_ot"),
        charset="utf8mb4",
        autocommit=False,
    )


def split_sql(text: str) -> list[str]:
    """按 ; 切分，但跳过字符串/反引号/注释里的分号。"""
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if quote == "'" and c == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                if i + 1 < n and text[i + 1] == quote:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            quote = c
            buf.append(c)
            i += 1
            continue
        if text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i)
            i = n if j < 0 else j + 2
            continue
        if c == ";":
            s = "".join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    s = "".join(buf).strip()
    if s:
        stmts.append(s)
    return stmts


def ensure_migration_table(cur) -> None:
    cur.execute(
        "CREATE TABLE IF NOT EXISTS `db_migration` ("
        "`name` varchar(128) NOT NULL, `applied_at` datetime NOT NULL,"
        "PRIMARY KEY (`name`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        " COLLATE=utf8mb4_0900_ai_ci"
    )


def applied_names(cur) -> set[str]:
    ensure_migration_table(cur)
    cur.execute("SELECT `name` FROM `db_migration`")
    return {r[0] for r in cur.fetchall()}


def render(cols, rows) -> None:
    if not rows:
        print("     (0 rows)")
        return
    widths = [len(str(c)) for c in cols]
    text = [[("" if v is None else str(v)) for v in r] for r in rows]
    for r in text:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(v))
    head = "  " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols))
    print(head)
    print("  " + "-+-".join("-" * w for w in widths))
    for r in text:
        print("  " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(r)))
    print(f"     ({len(rows)} rows)")


def run_script(cur, path: Path, echo: bool = False):
    """执行脚本，返回 (发现的 PASS 数, FAIL 数, 错误信息列表)。"""
    n_pass = n_fail = 0
    errors: list[str] = []
    for stmt in split_sql(path.read_text(encoding="utf-8")):
        cur.execute(stmt)
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            for r in rows:
                for v in r:
                    sv = str(v)
                    if "FAIL" in sv:
                        n_fail += 1
                    elif "PASS" in sv:
                        n_pass += 1
            if echo:
                render(cols, rows)
    return n_pass, n_fail, errors


def cmd_migrate(conn) -> int:
    cur = conn.cursor()
    done = applied_names(cur)
    pending = sorted(p for p in MIGRATIONS.glob("*.sql") if p.name not in done)
    if not pending:
        print(f"已是最新（{len(done)} 条迁移已应用）")
        return 0
    for p in pending:
        print(f"应用 {p.name} ...")
        try:
            for stmt in split_sql(p.read_text(encoding="utf-8")):
                cur.execute(stmt)
            cur.execute(
                "INSERT INTO `db_migration` (`name`,`applied_at`) VALUES (%s, NOW())",
                (p.name,),
            )
            conn.commit()
            print("  OK")
        except Exception as e:
            conn.rollback()
            print(f"  失败：{e}")
            return 1
    return 0


def cmd_status(conn) -> int:
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    have = {r[0] for r in cur.fetchall()}
    done = sorted(applied_names(cur))
    print("迁移：" + (", ".join(done) if done else "无"))
    for t in TABLES:
        if t not in have:
            print(f"  {t:<18} 缺失 —— 先跑 python db/tests/run.py apply db/schema.sql")
            continue
        cur.execute(f"SELECT COUNT(*) FROM `{t}`")
        print(f"  {t:<18} {cur.fetchone()[0]:>8}")
    cur.execute("SHOW FULL TABLES WHERE Table_type='VIEW'")
    print("视图：" + (", ".join(r[0] for r in cur.fetchall()) or "无"))

    # P0 门禁之一：登记进来的源必须写清授权边界，空的 legal_note 等于还没查过 ToS
    if "source" in have:
        cur.execute(
            "SELECT COUNT(*) FROM `source` WHERE `legal_note` IS NULL OR TRIM(`legal_note`)=''"
        )
        blank = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM `source` WHERE `status`='candidate'")
        cand = cur.fetchone()[0]
        print(f"P0 门禁：legal_note 空缺 {blank} 条（必须为 0）；待探针裁定 {cand} 条")
        if blank:
            return 1
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    env = load_env()
    conn = connect(env)
    try:
        if cmd == "migrate":
            return cmd_migrate(conn)
        if cmd == "status":
            return cmd_status(conn)
        if cmd in ("apply", "test"):
            if not rest:
                print(f"{cmd} 需要 <file.sql>")
                return 1
            path = Path(rest[0])
            if not path.exists():
                print(f"找不到 {path}")
                return 1
            cur = conn.cursor()
            n_pass, n_fail, _ = run_script(cur, path, echo=(cmd == "test"))
            conn.commit()
            print(f"{path.name}: PASS {n_pass} / FAIL {n_fail}")
            return 1 if n_fail else 0
        print(f"未知子命令 {cmd}")
        print(__doc__)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
