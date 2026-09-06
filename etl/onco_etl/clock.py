"""本地墙钟。写库的每一个时间戳都从这里出。

本机 UTC+8。用 utcnow() 或带 Z 的串，MySQL 会按字面量存成 UTC 时刻，
跨午夜采到的快照会错位一天——fetched_at 与 probed_at 差一天，
在覆盖度报告上就表现为"数据很新但探针没跑"这类假信号。
"""
from __future__ import annotations

from datetime import date, datetime

TS = "%Y-%m-%d %H:%M:%S"
DAY = "%Y-%m-%d"


def now_ts() -> str:
    return datetime.now().strftime(TS)


def today() -> str:
    return date.today().strftime(DAY)
