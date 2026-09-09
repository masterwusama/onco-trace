"""服务层入口：`python -m onco_api <子命令>`（在 api/ 目录下跑，ops\\api.ps1 已替你 cd）。

    serve [--host X] [--port N] [--reload]   起只读服务，默认取 .env 的 API_HOST / API_PORT
    routes                                   列出已注册的 GET 路由

`routes` 是给后续批次用的自检：一维一个路由模块，加完维度用它确认都挂上了。
"""
from __future__ import annotations

import argparse
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台默认 GBK

from .app import create_app
from .config import ConfigError, load_settings
from .db import dispose


def _cmd_routes(_args) -> int:
    app = create_app()
    # 走 openapi 而不是 app.routes：这一版 starlette 把 include_router 包成
    # _IncludedRouter 不展平，按 routes 列只会看到内置的 docs 几条
    paths = app.openapi()["paths"]
    for path in sorted(paths):
        for method in sorted(paths[path]):
            params = sorted({p["name"] for op in paths[path].values()
                             for p in op.get("parameters", [])})
            args = f"  [{', '.join(params)}]" if params else ""
            print(f"{method.upper():7} {path}{args}")
    print(f"共 {len(paths)} 条路径")
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    s = load_settings()
    host = args.host or s.api_host
    port = args.port or s.api_port
    print(f"onco-trace API → http://{host}:{port}/api/docs（只读，DB={s.database}）")
    uvicorn.run("onco_api.app:create_app", factory=True, host=host, port=port, reload=args.reload)
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m onco_api", description="onco-trace 服务层")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="起只读服务")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    sp.set_defaults(func=_cmd_serve)
    rp = sub.add_parser("routes", help="列出已注册的 API 路由")
    rp.set_defaults(func=_cmd_routes)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2
    finally:
        dispose()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
