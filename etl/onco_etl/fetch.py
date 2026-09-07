"""HTTP 取数：直连 → 代理两段式，结果带 reachability 三态而非布尔。

这台机器直连境外站点不可靠（Wikimedia 全线超时，git push 必须显式带
`-c http.proxy=http://127.0.0.1:1080`）。探针如果把"连不上"记成 False，
覆盖度报告里就分不清"源没了"和"本机网络到不了"——这两种情况处置完全不同：
前者要换源，后者只要给 scheduler 配上代理。

4xx 一律算"可达"：服务器答话了，问题在 URL 或授权，不在网络。
只有 DNS/连接/超时/TLS 这一层失败才记 blocked。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import certifi
import requests
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    ProxyError,
    SSLError,
    Timeout,
    TooManyRedirects,
)

from .config import load_settings

# 不带 UA 会被 ebi.ac.uk / cancer.gov 一类站点按默认策略挡掉，
# 而且挡下来的样子和"源不存在"一模一样，排查时全是噪音
DEFAULT_UA = "onco-trace/0.1 (+https://github.com/masterwusama/onco-trace) probe"

# 连接 10s、读取 90s：GBD 的批量 CSV 与 HPO 的 35MB 注释文件都不是秒回的东西
DEFAULT_TIMEOUT = (10, 90)


@dataclass
class FetchResult:
    url: str
    ok: bool = False
    status: int | None = None
    reachability: str = "error"  # direct | proxy | blocked | error
    latency_ms: int = 0
    body: bytes = b""
    truncated: bool = False
    declared_bytes: int | None = None
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    final_url: str = ""
    note: str = ""
    attempts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def _session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    # trust_env=False 是关键：否则 requests 会自己读环境变量里的 HTTP_PROXY，
    # "直连"这一趟其实走了代理，reachability 三态就退化成永远 direct
    s.trust_env = False
    s.verify = certifi.where()
    s.headers.update({"User-Agent": DEFAULT_UA, "Accept-Encoding": "gzip, deflate"})
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _read(resp: requests.Response, max_bytes: int | None) -> tuple[bytes, bool]:
    if max_bytes is None:
        return resp.content, False
    buf = bytearray()
    for chunk in resp.iter_content(chunk_size=min(max_bytes, 1 << 20)):
        if not chunk:
            continue
        buf.extend(chunk)
        if len(buf) >= max_bytes:
            return bytes(buf), True
    return bytes(buf), False


def _attempt(
    url: str,
    proxy: str | None,
    *,
    method: str,
    timeout,
    headers: dict | None,
    max_bytes: int | None,
    allow_redirects: bool,
    json_body: dict | None = None,
) -> FetchResult:
    tag = "proxy" if proxy else "direct"
    t0 = time.perf_counter()
    res = FetchResult(url=url, attempts=[tag])
    try:
        with _session(proxy) as s:
            resp = s.request(
                method,
                url,
                timeout=timeout,
                headers=headers or None,
                json=json_body,
                stream=max_bytes is not None,
                allow_redirects=allow_redirects,
            )
        body, truncated = _read(resp, max_bytes)
        ms = int((time.perf_counter() - t0) * 1000)
        h = resp.headers
        declared = h.get("Content-Length")
        # 这里必须逐字段赋值：FetchResult 是 dataclass 不是 dict，
        # 写成 res.update(...) 会抛 AttributeError，而它会被下面的兜底 except 吞掉，
        # 表现成"所有源都不可达"——真实结果反而被掩盖，排查时全是假信号
        res.ok = resp.status_code < 400
        res.status = resp.status_code
        res.reachability = tag
        res.latency_ms = ms
        res.body = body
        res.truncated = truncated
        res.declared_bytes = int(declared) if declared and declared.isdigit() else None
        res.content_type = h.get("Content-Type")
        res.etag = h.get("ETag")
        res.last_modified = h.get("Last-Modified")
        res.final_url = resp.url
        res.note = "" if resp.ok else f"HTTP {resp.status_code}"
        return res
    except (ReqConnectionError, Timeout, SSLError, ProxyError) as e:
        res.latency_ms = int((time.perf_counter() - t0) * 1000)
        res.reachability = "blocked"
        res.note = f"{type(e).__name__}: {e}"[:500]
        return res
    except TooManyRedirects as e:
        res.reachability = "error"
        res.note = f"TooManyRedirects: {e}"[:500]
        return res
    except (AttributeError, TypeError, NameError, KeyError, IndexError):
        # 代码 bug 不许被记成"源不可达"：那会往 source_probe_log 里写假数据，
        # 下一个人会去查源站而不是查这几行代码
        raise
    except Exception as e:  # noqa: BLE001 —— 探针不能因为一个源抛怪异常就整批中断
        res.reachability = "error"
        res.note = f"{type(e).__name__}: {e}"[:500]
        return res


def fetch(
    url: str,
    *,
    method: str = "GET",
    max_bytes: int | None = None,
    timeout=DEFAULT_TIMEOUT,
    headers: dict | None = None,
    allow_redirects: bool = True,
    etag: str | None = None,
    last_modified: str | None = None,
    use_proxy_fallback: bool = True,
    json_body: dict | None = None,
) -> FetchResult:
    """取一个 URL。直连失败或 5xx 时才试代理；两段都试过后取"信息量更大"的那个结果。

    `json_body` 是给 OpenTargets 这类只收 POST GraphQL 的入口用的：GET 探不到数据面，
    而"能不能取回这一维"只能按 POST 的响应裁定，所以三态与重试逻辑必须复用同一套。
    """
    cond = dict(headers or {})
    if etag:
        cond["If-None-Match"] = etag
    if last_modified:
        cond["If-Modified-Since"] = last_modified

    direct = _attempt(
        url,
        None,
        method=method,
        timeout=timeout,
        headers=cond or None,
        max_bytes=max_bytes,
        allow_redirects=allow_redirects,
        json_body=json_body,
    )
    # 304 是成功：源没变，正是不必重新解析的信号
    if direct.ok or direct.status == 304 or (direct.status is not None and direct.status < 500):
        return direct

    proxy = load_settings().proxy_url
    if not proxy or not use_proxy_fallback:
        return direct

    via_proxy = _attempt(
        url,
        proxy,
        method=method,
        timeout=timeout,
        headers=cond or None,
        max_bytes=max_bytes,
        allow_redirects=allow_redirects,
        json_body=json_body,
    )
    via_proxy.attempts = direct.attempts + via_proxy.attempts
    # 直连给了具体状态码、代理连不上时，保留状态码那条更有诊断价值
    if direct.status is not None and via_proxy.status is None:
        direct.note = f"{direct.note} | proxy: {via_proxy.note}"
        return direct
    if not via_proxy.note:
        via_proxy.note = f"direct failed: {direct.note}"
    else:
        via_proxy.note = f"direct failed: {direct.note} | {via_proxy.note}"
    return via_proxy
