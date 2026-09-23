"""Typed configuration, read once from the environment.

The only place the environment is read, so a mistyped variable fails at startup.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# The identity every request gets when auth is off. Here rather than in
# api/deps.py because the worker needs it too.
DEV_USER_ID = "dev-user"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://app:app@localhost:5434/app"

    # How long an idle worker waits for a NOTIFY before sweeping again. Not job
    # latency: a NOTIFY wakes it at once.
    worker_idle_seconds: float = 5.0

    # A claimed task older than this is assumed orphaned by a dead worker. Must
    # exceed the longest real task, or the sweeper steals live work.
    task_stale_seconds: int = 900

    # Touched each loop pass, so the healthcheck can tell a wedged worker from a
    # running one. S108: a fixed path inside the worker's own container.
    worker_heartbeat_path: Path = Path("/tmp/worker-alive")  # noqa: S108

    # Retry backoff: doubles from the base, capped at the max. Whether a failure
    # is retryable is the handler's call (`TaskOutcome.retryable`).
    task_retry_base_seconds: float = 5.0
    task_retry_max_seconds: float = 120.0

    # The largest single payment, in minor units.
    max_transfer_minor: int = 1_000_000_00

    # The most recipients in one run. A run is one transaction, so its size is
    # how long the funding lock is held.
    max_run_size: int = 100

    # Seed demo recipients and an opening balance. The chart of accounts is always seeded.
    seed_demo_data: bool = True

    # How often reconciliation runs. Short so the demo visibly self-heals.
    reconcile_interval_seconds: int = 30

    # ------------------------------------------------------------- provider
    #
    # The mock provider's behavior, read only by `provider/mock.py`. The three
    # failure hooks default to off.

    # How long a payment stays pending before the provider settles it. Not zero,
    # so the in-flight states are exercised.
    provider_settle_after_seconds: int = 5

    # Every send fails permanently, as a rejected integration would.
    provider_reject_all: bool = False

    # Every send fails retryably. Simulates a network partition or an outage.
    provider_unreachable: bool = False

    # Sends are accepted, then fail at settlement.
    provider_fail_settlement: bool = False

    # ---------------------------------------------------------------- auth
    #
    # An empty issuer turns auth off and every request is the dev user. Any OIDC
    # provider works; see `api/deps.py`.
    #
    #   oidc_issuer   https://your-tenant.example.com   (no trailing slash)
    #   oidc_audience the API identifier the provider puts in `aud`
    oidc_issuer: str = ""
    oidc_audience: str = ""

    # Defaults to the OIDC discovery convention.
    oidc_jwks_url: str = ""

    @property
    def auth_enabled(self) -> bool:
        return bool(self.oidc_issuer.strip())

    @property
    def jwks_url(self) -> str:
        if self.oidc_jwks_url:
            return self.oidc_jwks_url
        return f"{self.oidc_issuer.rstrip('/')}/.well-known/jwks.json"

    log_level: Literal["debug", "info", "warning", "error"] = "info"
    log_format: Literal["console", "json"] = "console"

    # Comma-separated rather than a list: pydantic-settings would parse a list
    # field from the environment as JSON.
    cors_origins: str = "http://localhost:3000"

    @property
    def database_dsn(self) -> str:
        """The URL without `+asyncpg`, for the direct asyncpg connection LISTEN needs."""
        return self.database_url.replace("+asyncpg", "", 1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
