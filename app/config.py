"""Settings read from the environment.

Read lazily through ``settings()`` so tests can change env vars and call
``reload_settings()``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader (no extra dependency). Real env vars win."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    qloo_api_key: str
    qloo_base: str
    qloo_fixtures: bool
    qloo_concurrency: int
    llm_model: str
    anthropic_api_key: str
    openai_api_key: str
    mem0_api_key: str
    resend_api_key: str
    mail_from: str
    webhook_url: str
    db_path: str
    watch_interval_seconds: int
    watcher_enabled: bool
    seed_demo: bool
    public_url: str
    try_rate_per_minute: int
    map_tile_url: str
    map_tile_attribution: str

    @property
    def qloo_mode(self) -> str:
        return "fixture" if (self.qloo_fixtures or not self.qloo_api_key) else "live"


@lru_cache(maxsize=1)
def settings() -> Settings:
    _load_dotenv()
    env = os.environ.get
    return Settings(
        qloo_api_key=env("QLOO_API_KEY", "").strip(),
        qloo_base=env("QLOO_BASE", "https://hackathon.api.qloo.com").rstrip("/"),
        qloo_fixtures=_flag("QLOO_FIXTURES"),
        qloo_concurrency=max(1, int(env("QLOO_CONCURRENCY", "4") or 4)),
        llm_model=env("LLM_MODEL", "").strip(),
        anthropic_api_key=env("ANTHROPIC_API_KEY", "").strip(),
        openai_api_key=env("OPENAI_API_KEY", "").strip(),
        mem0_api_key=env("MEM0_API_KEY", "").strip(),
        resend_api_key=env("RESEND_API_KEY", "").strip(),
        mail_from=env("MAIL_FROM", "Convivium <agent@example.com>"),
        webhook_url=env("WEBHOOK_URL", "").strip(),
        db_path=env("CONVIVIUM_DB", str(ROOT / "convivium.db")),
        watch_interval_seconds=max(30, int(env("WATCH_INTERVAL_SECONDS", "900") or 900)),
        watcher_enabled=_flag("WATCHER_ENABLED", "1"),
        seed_demo=_flag("SEED_DEMO", "1"),
        public_url=env("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        try_rate_per_minute=int(env("TRY_RATE_PER_MINUTE", "6") or 6),
        map_tile_url=env("MAP_TILE_URL", "").strip(),
        map_tile_attribution=env("MAP_TILE_ATTRIBUTION", "").strip(),
    )


def reload_settings() -> Settings:
    settings.cache_clear()
    return settings()
