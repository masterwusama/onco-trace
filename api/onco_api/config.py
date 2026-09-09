"""配置：仓库根 .env 是唯一凭据源，后端与采集层读同一份文件。

与 `etl/onco_etl/config.py` 重复了一份约三十行的解析代码，这是 README 约定 1
（两侧互不引用、只通过表结构对话）的代价：宁可重复这三十行，也不要让服务层
import 采集层，换来"改一次 .env 语义要同时动两个包"的隐性耦合。改这里时同看那份，
两边的 DB_* 必须解释成同一个意思。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

_DB_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
_API_KEYS = ("API_HOST", "API_PORT", "API_CORS_ORIGINS")

# 前端 dev server 的默认来源（5174 是本站的开发端口，见 frontend/vite.config.js）。
# 生产是同源（FastAPI 托管 dist），不需要 CORS；日常开发走 vite 的 /api 代理，
# 浏览器眼里也是同源，这份清单其实用不上——留着只给"直接开 dist 之外的端口"那种情形。
# 这里只放本机端口，不放通配符：带 credentials 的 `*` 会被浏览器直接拒掉。
DEFAULT_CORS = "http://localhost:5174,http://127.0.0.1:5174"


class ConfigError(RuntimeError):
    pass


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    database: str
    user: str
    password: str
    api_host: str
    api_port: int
    cors_origins: tuple[str, ...]

    @property
    def url(self) -> str:
        # 密码里的 # ? / % 不转义会静默截断连接串，然后报一个看不懂的鉴权失败
        return (
            f"mysql+pymysql://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        )


def load_settings() -> Settings:
    env = _parse_env_file(ENV_FILE)
    for k in _DB_KEYS + _API_KEYS:  # 进程环境覆盖文件，不改 .env 也能临时指到别的库
        if os.environ.get(k):
            env[k] = os.environ[k]
    missing = [k for k in ("DB_HOST", "DB_USER", "DB_PASSWORD") if not env.get(k)]
    if missing:
        raise ConfigError(f"缺少 {missing}：请在 {ENV_FILE} 中提供（参考 .env.example）")

    def _int(key: str, default: str) -> int:
        raw = env.get(key) or default
        try:
            return int(raw)
        except ValueError as e:
            raise ConfigError(f"{key} 不是整数：{raw}") from e

    origins = tuple(
        o.strip() for o in (env.get("API_CORS_ORIGINS") or DEFAULT_CORS).replace(";", ",").split(",")
        if o.strip()
    )
    return Settings(
        host=env["DB_HOST"],
        port=_int("DB_PORT", "3306"),
        database=env.get("DB_NAME") or "db_ot",
        user=env["DB_USER"],
        password=env["DB_PASSWORD"],
        api_host=env.get("API_HOST") or "127.0.0.1",
        # 8001 不是随手挑的：本机 8000 长期是另一个项目的端口，默认值指过去等于埋一个抢端口
        api_port=_int("API_PORT", "8001"),
        cors_origins=origins,
    )
