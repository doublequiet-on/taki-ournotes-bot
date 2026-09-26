from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_SOURCE_CANDIDATE = Path(__file__).resolve().parents[2]
SOURCE_ROOT = _SOURCE_CANDIDATE if (_SOURCE_CANDIDATE / "pyproject.toml").is_file() else None
CONFIG_ROOT = SOURCE_ROOT or Path.cwd()

# QQ allows at most five passive replies per inbound message id. Because a
# sixth send is rejected outright, one slot is kept aside for the over-limit
# notice, so a message can execute at most four commands.
QQ_PASSIVE_REPLY_LIMIT = 5
MAX_MULTI_COMMAND_LIMIT = QQ_PASSIVE_REPLY_LIMIT - 1


def runtime_data_dir() -> Path:
    """Keep installed copies out of the package's potentially read-only directory."""
    if SOURCE_ROOT:
        return SOURCE_ROOT / "data"
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "ournotes-qq-bot"


def load_dotenv(path: Path | None = None) -> None:
    """Load a small .env file without adding another dependency."""
    env_path = path or CONFIG_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    app_id: str
    app_secret: str
    data_base: str
    cache_file: Path
    cache_ttl_hours: float
    ai_api_key: str = ""
    ai_model: str = "deepseek-chat"
    ai_base_url: str = "https://api.deepseek.com"
    ai_daily_limit: int = 100
    query_concurrency: int = 2
    query_queue_limit: int = 4
    ai_quota_file: Path | None = None
    multi_command_limit: int = MAX_MULTI_COMMAND_LIMIT

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        configured_cache = os.getenv("OURNOTES_CACHE_FILE", "").strip()
        raw_cache = Path(configured_cache) if configured_cache else runtime_data_dir() / "ournotes-cache.json"
        if configured_cache and not raw_cache.is_absolute():
            raw_cache = CONFIG_ROOT / raw_cache
        configured_quota = os.getenv("OURNOTES_AI_QUOTA_FILE", "").strip()
        quota_file = Path(configured_quota) if configured_quota else raw_cache.with_name("ai-quota.json")
        if configured_quota and not quota_file.is_absolute():
            quota_file = CONFIG_ROOT / quota_file
        return cls(
            app_id=os.getenv("QQ_APP_ID", "").strip(),
            app_secret=os.getenv("QQ_APP_SECRET", "").strip(),
            data_base="https://bdon.yatta.moe",
            cache_file=raw_cache,
            cache_ttl_hours=float(os.getenv("OURNOTES_CACHE_TTL_HOURS", "6")),
            ai_api_key=os.getenv("AI_API_KEY", "").strip(),
            ai_model=os.getenv("AI_MODEL", "deepseek-chat").strip(),
            ai_base_url=os.getenv("AI_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            ai_daily_limit=max(0, int(os.getenv("AI_DAILY_LIMIT", "100"))),
            query_concurrency=max(1, int(os.getenv("OURNOTES_QUERY_CONCURRENCY", "2"))),
            query_queue_limit=max(0, int(os.getenv("OURNOTES_QUERY_QUEUE_LIMIT", "4"))),
            ai_quota_file=quota_file,
            multi_command_limit=min(MAX_MULTI_COMMAND_LIMIT,
                                    max(1, int(os.getenv("OURNOTES_MULTI_COMMAND_LIMIT",
                                                         str(MAX_MULTI_COMMAND_LIMIT))))),
        )
