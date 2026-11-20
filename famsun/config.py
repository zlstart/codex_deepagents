"""Service settings. Secrets are never written to the session workspace."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "on", "yes"}:
        return True
    if value.lower() in {"0", "false", "off", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


def positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be a positive number")
    return value


def model_base_url() -> str:
    url = (
        os.getenv("LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    )
    parts = urlsplit(url.strip().rstrip("/"))
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "LLM_BASE_URL must be an HTTP(S) endpoint without embedded credentials or query"
        )
    path = parts.path.removesuffix("/chat/completions").removesuffix("/responses")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def public_origin() -> str | None:
    value = os.getenv("FAMSUN_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if not value:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "FAMSUN_PUBLIC_ORIGIN must contain only the public scheme, host and optional port"
        )
    return value


def volume_options() -> dict[str, str]:
    value = json.loads(os.getenv("FAMSUN_VOLUME_OPTIONS") or "{}")
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError(
            "FAMSUN_VOLUME_OPTIONS must be a JSON object of string options for the Docker volume driver"
        )
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("FAMSUN_DATA_DIR", "var")).resolve()
    )
    static_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("FAMSUN_STATIC_DIR", "web/dist")
        ).resolve()
    )
    llm_api_key: str = field(
        repr=False,
        default_factory=lambda: (
            os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        ),
    )
    llm_base_url: str = field(default_factory=model_base_url)
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "")
    )
    sandbox_image: str = field(
        default_factory=lambda: os.getenv(
            "FAMSUN_SANDBOX_IMAGE", "famsun-sandbox:local"
        )
    )
    sandbox_timeout: int = field(
        default_factory=lambda: positive_int("FAMSUN_SANDBOX_TIMEOUT", 120)
    )
    graph_recursion_limit: int = field(
        default_factory=lambda: positive_int("FAMSUN_GRAPH_RECURSION_LIMIT", 160)
    )
    run_timeout: int = field(
        default_factory=lambda: positive_int("FAMSUN_RUN_TIMEOUT", 900)
    )
    max_parallel_runs: int = field(
        default_factory=lambda: positive_int("FAMSUN_MAX_PARALLEL_RUNS", 4)
    )
    allow_unenforced_cgroups: bool = field(
        default_factory=lambda: flag("FAMSUN_ALLOW_UNENFORCED_CGROUPS", False)
    )
    volume_driver: str = field(
        default_factory=lambda: os.getenv("FAMSUN_VOLUME_DRIVER", "local")
    )
    volume_options: dict[str, str] = field(default_factory=volume_options, repr=False)
    public_origin: str | None = field(default_factory=public_origin)
    secure_cookies: bool = field(
        default_factory=lambda: flag("FAMSUN_SECURE_COOKIES", True)
    )
    allow_signup: bool = field(
        default_factory=lambda: flag("FAMSUN_ALLOW_SIGNUP", False)
    )
    mcp_enabled: bool = field(default_factory=lambda: flag("FAMSUN_MCP_ENABLED", True))

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite3"
