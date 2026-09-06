"""配置：仓库根 .env 是唯一凭据源，后端与 etl 共用一份，不复制第二份。

进程环境优先于文件，这样计划任务/临时改库不需要动 .env。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
DATA_RAW = ROOT / "data" / "raw"
DATA_EXPORTS = ROOT / "data" / "exports"

_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "PROXY_URL")


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
    proxy_url: str | None

    @property
    def url(self) -> str:
        # 密码里的 # ? / % 不转义会静默截断连接串，然后报一个看不懂的鉴权失败
        return (
            f"mysql+pymysql://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}?charset=utf8mb4"
        )


def load_settings() -> Settings:
    env = _parse_env_file(ENV_FILE)
    for k in _KEYS:  # 进程环境覆盖文件，不改 .env 也能临时指到别的库
        if os.environ.get(k):
            env[k] = os.environ[k]
    missing = [k for k in ("DB_HOST", "DB_USER", "DB_PASSWORD") if not env.get(k)]
    if missing:
        raise ConfigError(f"缺少 {missing}：请在 {ENV_FILE} 中提供（参考 .env.example）")
    try:
        port = int(env.get("DB_PORT") or 3306)
    except ValueError as e:
        raise ConfigError(f"DB_PORT 不是整数：{env['DB_PORT']}") from e
    return Settings(
        host=env["DB_HOST"],
        port=port,
        database=env.get("DB_NAME") or "db_ot",
        user=env["DB_USER"],
        password=env["DB_PASSWORD"],
        proxy_url=env.get("PROXY_URL") or None,
    )
