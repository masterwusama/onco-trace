"""etl_job_log：运行史的唯一来源，也是采集崩掉后唯一留存的现场。

三条规矩，每条都对应一种"看起来没事、其实断了"：

1. `running` 行**立刻单独提交**。跟着数据事务走的话，进程被强杀后日志里什么都不剩，
   表现成"这个 job 从来没跑过"，而真实情况是跑了一半死了。
2. 收口状态用另一笔事务写。数据回滚不该把"我失败了"这条记录一起回滚掉。
3. `written` 只在真往业务表落数的任务里有意义。probe 这类不落数的任务传 None，
   填 0 会被"success 且 written=0"的红牌规则误判成源改版面。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import db
from .clock import now_ts

STALE_HOURS = 3

DEFAULT_STATS = {"probed": 0, "written": 0, "skipped": 0, "blocked": 0}


@dataclass
class Job:
    name: str
    id: int
    stats: dict = field(default_factory=dict)
    message: str | None = None

    def tally(self, **kw) -> "Job":
        """计数用 += 语义：分段累加，调用方不需要自己维护中间量。"""
        for k, v in kw.items():
            self.stats[k] = self.stats.get(k, 0) + v
        return self

    def set(self, **kw) -> "Job":
        self.stats.update(kw)
        return self

    def note(self, msg: str) -> "Job":
        self.message = msg if not self.message else f"{self.message} | {msg}"
        return self


def _write(
    job_id: int,
    status: str,
    stats: dict | None,
    message: str | None,
    *,
    touch_stats: bool = True,
) -> None:
    sets = ["`status`=:st", "`finished_at`=:ts", "`message`=:msg"]
    bind: dict = {"st": status, "ts": now_ts(), "msg": message, "id": job_id}
    if touch_stats:
        # JSON 列必须自己 dumps：MySQL 8 会把"看着像但不合法"的串静默存成 NULL
        bind["stats"] = json.dumps(stats or {}, ensure_ascii=False, default=str)
        sets.append("`stats`=:stats")
    with db.tx() as conn:
        conn.execute(
            db.text(f"UPDATE `etl_job_log` SET {', '.join(sets)} WHERE `id`=:id"), bind
        )


def start(name: str, stats: dict | None = None) -> Job:
    with db.tx() as conn:
        r = conn.execute(
            db.text(
                "INSERT INTO `etl_job_log` (`job_name`,`started_at`,`status`,`stats`)"
                " VALUES (:n,:ts,'running',:stats)"
            ),
            {
                "n": name,
                "ts": now_ts(),
                "stats": json.dumps(stats or dict(DEFAULT_STATS), ensure_ascii=False),
            },
        )
        return Job(name=name, id=int(r.lastrowid), stats=stats or dict(DEFAULT_STATS))


def finish(job: Job, *, ok: bool = True) -> None:
    _write(job.id, "success" if ok else "failed", job.stats, job.message)


def reclaim_stale() -> list[str]:
    """把卡住的 running 标 failed：任务被强杀时不会自己收口，状态页上会永远挂着黄牌。"""
    with db.ro() as conn:
        stuck = db.rows(
            conn,
            "SELECT `id`,`job_name` FROM `etl_job_log`"
            " WHERE `status`='running' AND `started_at` < NOW() - INTERVAL :h HOUR",
            {"h": STALE_HOURS},
        )
    for job_id, _name in stuck:
        # 不碰 stats：死任务留下的最后一个计数是唯一能看出它跑到哪一步的东西
        _write(int(job_id), "failed", None, "超过 3 小时未收口，按失败处理", touch_stats=False)
    return [f"{n}#{i}" for i, n in stuck]
