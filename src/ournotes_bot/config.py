# L3
# Input: 环境变量、可选 dotenv 路径、安装位置和当前工作目录。
# Output: frozen Settings、独立主资料／谱面／分数表／榜线来源开关及历史路径；Secret 不进入 repr；load_dotenv 返回 None。
# Pos: Application 的配置与运行路径边界；见 ../L2-Application.md。
# Effects/Dependencies: 读取环境及 dotenv；开放平台接受 MOENOTES_OPEN_SECRET 或 BDON_OPENPLATFORM，前者非空优先，密钥不进入repr。

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_SOURCE_CANDIDATE = Path(__file__).resolve().parents[2]
SOURCE_ROOT = _SOURCE_CANDIDATE if (_SOURCE_CANDIDATE / "pyproject.toml").is_file() else None
CONFIG_ROOT = SOURCE_ROOT or Path.cwd()

# QQ allows at most five passive replies per inbound message id. Because a
# sixth send is rejected outright, one slot is kept aside for the over-limit
# notice, so a message can execute at most four commands.
QQ_PASSIVE_REPLY_LIMIT = 5
MAX_MULTI_COMMAND_LIMIT = QQ_PASSIVE_REPLY_LIMIT - 1

# QQ unified its API hostname on api.bot.qq.com but still advertises the retired
# api.sgroup.qq.com as the websocket gateway. The unified host serves the same
# gateway, so the advertised address is rewritten; set the variable to empty to
# keep whatever the server sends.
DEFAULT_QQ_GATEWAY_HOST = "api.bot.qq.com"

# How long a batch waits for its turn to send before giving up and sending
# anyway, so one wedged batch cannot silence every later reply.
REPLY_ORDER_TIMEOUT_SECONDS = 60.0

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disable", "disabled"})


def read_flag(name: str, default: bool) -> bool:
    """Read a boolean setting; an unrecognised value keeps the default.

    Accepts 1/0, true/false, yes/no and on/off. Anything else (including a
    typo) falls back to `default` rather than silently flipping the behaviour.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    return default


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
    qq_gateway_host: str = DEFAULT_QQ_GATEWAY_HOST
    # Send replies in arrival order. On by default: unordered replies read as
    # an answer to the wrong question. Turn it off to favour latency instead.
    reply_order: bool = True
    update_notices: bool = True
    ai_metrics_file: Path | None = None
    cutoff_history_enabled: bool = True
    cutoff_history_file: Path | None = None
    cutoff_sampling_enabled: bool = False
    cutoff_sampling_servers: tuple[str, ...] = ("jp", "tw", "kr", "en")
    cutoff_sampling_interval: int = 300
    cutoff_history_min_free_mb: int = 512
    meta_source: str = "moenotes"
    cutoff_source: str = "tracker"
    moenotes_open_secret: str = field(default="", repr=False)
    moenotes_open_history_file: Path | None = None
    chart_source: str = "moenotes"
    data_source: str = "yume"

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
        configured_metrics = os.getenv("OURNOTES_AI_METRICS_FILE", "").strip()
        metrics_file = (Path(configured_metrics) if configured_metrics
                        else raw_cache.with_name("ai-metrics.json"))
        if configured_metrics and not metrics_file.is_absolute():
            metrics_file = CONFIG_ROOT / metrics_file
        configured_history = os.getenv("OURNOTES_CUTOFF_HISTORY_FILE", "").strip()
        history_file = Path(configured_history) if configured_history else raw_cache.with_name("moenotes-history-v1.sqlite3")
        if configured_history and not history_file.is_absolute():
            history_file = CONFIG_ROOT / history_file
        servers = tuple(dict.fromkeys("tw" if s.strip().lower() == "hk" else s.strip().lower()
                                      for s in os.getenv("OURNOTES_CUTOFF_SAMPLING_SERVERS", "jp,hk,kr,en").split(",")))
        if not servers or any(s not in {"jp", "tw", "kr", "en"} for s in servers):
            raise ValueError("OURNOTES_CUTOFF_SAMPLING_SERVERS must use jp,hk,kr,en")
        meta_source = os.getenv("OURNOTES_META_SOURCE", "moenotes").strip()
        cutoff_source = os.getenv("OURNOTES_CUTOFF_SOURCE", "tracker").strip()
        chart_source = os.getenv("OURNOTES_CHART_SOURCE", "moenotes").strip()
        data_source = os.getenv("OURNOTES_DATA_SOURCE", "yume").strip()
        if data_source not in {"yume", "haneoka"}:
            raise ValueError("unsupported main data source")
        if chart_source not in {"moenotes", "haneoka"}:
            raise ValueError("unsupported chart source")
        if meta_source not in {"moenotes", "haneoka"} or cutoff_source not in {"tracker", "open"}:
            raise ValueError("unsupported music/ranking source")
        configured_open_history = os.getenv("OURNOTES_OPEN_HISTORY_FILE", "").strip()
        open_history = Path(configured_open_history) if configured_open_history else raw_cache.with_name("moenotes-open-history-v2.sqlite3")
        if configured_open_history and not open_history.is_absolute():
            open_history = CONFIG_ROOT / open_history
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
            qq_gateway_host=os.getenv("OURNOTES_QQ_GATEWAY_HOST",
                                      DEFAULT_QQ_GATEWAY_HOST).strip(),
            reply_order=read_flag("OURNOTES_REPLY_ORDER", True),
            update_notices=read_flag("OURNOTES_UPDATE_NOTICES", True),
            ai_metrics_file=metrics_file,
            cutoff_history_enabled=read_flag("OURNOTES_CUTOFF_HISTORY", True),
            cutoff_history_file=history_file,
            cutoff_sampling_enabled=read_flag("OURNOTES_CUTOFF_SAMPLING", False),
            cutoff_sampling_servers=servers,
            cutoff_sampling_interval=max(60, int(os.getenv("OURNOTES_CUTOFF_SAMPLING_INTERVAL", "300"))),
            cutoff_history_min_free_mb=max(0, int(os.getenv("OURNOTES_CUTOFF_MIN_FREE_MB", "512"))),
            meta_source=meta_source,
            cutoff_source=cutoff_source,
            moenotes_open_secret=(os.getenv("MOENOTES_OPEN_SECRET", "").strip()
                                  or os.getenv("BDON_OPENPLATFORM", "").strip()),
            moenotes_open_history_file=open_history,
            chart_source=chart_source,
            data_source=data_source,
        )
