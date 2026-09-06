"""原始响应归档到 data/raw/<source_code>/<key>/<name>，附 sha256。

归一化规则改了要能重放，不必再去敲源站额度；覆盖度报告里"这个源当时到底给了什么"
也只能靠这份归档复现。`key` 用上游版本或采集日，不用固定日期——
GBD/SEER 这类年度发布的源，同一版本重跑十次不该产生十份互相矛盾的证据。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import DATA_RAW


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def target_path(source_code: str, key: str, name: str) -> Path:
    return DATA_RAW / source_code / key / name


def archive(source_code: str, key: str, name: str, body: str | bytes) -> Path:
    path = target_path(source_code, key, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = body.encode("utf-8", "replace") if isinstance(body, str) else body
    path.write_bytes(data)
    return path


def latest(source_code: str, key: str, name: str) -> Path | None:
    path = target_path(source_code, key, name)
    return path if path.exists() else None
