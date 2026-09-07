"""原始响应归档到 data/raw/<source_code>/<key>/<name>，附 sha256。

归一化规则改了要能重放，不必再去敲源站额度；覆盖度报告里"这个源当时到底给了什么"
也只能靠这份归档复现。`key` 用上游版本或采集日，不用固定日期——
GBD/SEER 这类年度发布的源，同一版本重跑十次不该产生十份互相矛盾的证据。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import DATA_RAW, ROOT


def rel(path: Path) -> str:
    """归档路径 → 入库用的仓库相对路径，一律正斜杠。

    Windows 上 `str(Path)` 给的是反斜杠，直接写进 raw_path 列之后，
    换一台机器（或后端按 POSIX 拼路径）就重放不了这份归档。
    """
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


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


def archive_dir(source_code: str, key: str) -> Path:
    """一份数据集是"一组页面"而不是"一个文件"时用这个：只建目录，由调用方往里写。

    SEER Stat Facts 是 18 个 HTML 页，PDQ 与 WHO fact sheet 也是同一形状。
    硬要塞进单文件归档就得自己拼一个 tar，重放时反而多一层解包。
    """
    d = DATA_RAW / source_code / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest(source_code: str, key: str, name: str) -> Path | None:
    path = target_path(source_code, key, name)
    return path if path.exists() else None


def newest_dir(source_code: str, requires: str | None = None) -> Path | None:
    """最近一次归档的版本目录，给多页面数据集的离线重放用。

    `requires` 传了就只能挑"确实装着这份数据集"的目录，别嫌它啰嗦：
    probe-reach 会把可达性探针的原始响应也归档成 `data/raw/<code>/reach-<日期>/<code>.bin`，
    而它通常跑在专项探针之后。纯按 mtime 取最新目录就会挑中这个只有 .bin 的空壳，
    离线重放于是报"缺归档"——SEER 探针更会把 18 页全缺记成 `verdict=dead`，
    在覆盖度日志里留下一条假死讯。
    """
    root = DATA_RAW / source_code
    if not root.is_dir():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if requires:
        dirs = [p for p in dirs if next(p.glob(requires), None)]
    return max(dirs, key=lambda p: p.stat().st_mtime) if dirs else None


def newest(source_code: str, name: str) -> Path | None:
    """跨版本目录找最近一次归档的同名文件。

    离线重放用：MONDO 的 .obo 有 51 MB，解析规则改一行就重下一次要七分钟，
    而这七分钟里源站给的东西完全没变。按 mtime 取最新的那份，等价于
    "拿最后一次真实抓到的证据重跑判定"。
    """
    # 递归而不是单层：上游的 data-version 字面量常带斜杠（'releases/2026-09-01'），
    # 拿它直接当目录名会多嵌一层。mondo.py 现在会先拍平，但这里不该依赖每个调用方都记得拍
    root = DATA_RAW / source_code
    if not root.is_dir():
        return None
    hits = [p for p in root.glob(f"**/{name}") if p.is_file()]
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None
