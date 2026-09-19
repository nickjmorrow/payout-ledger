"""Typed configuration, read once from the environment.

Everything that varies by environment comes from here. Importing `settings`
anywhere is fine; calling `os.getenv` anywhere else is not — a typo in an env
var name should fail at startup with a clear error, not at 2am with a None.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# The identity every request gets when auth is switched off.
#
# Not a Settings field, because it is not environment-dependent: it is the one
# user that exists when `OIDC_ISSUER` is unset. It lives here rather than beside
# the auth seam in `api/deps.py` because the worker needs it too — work the
# worker starts on its own still has to belong to somebody, and a process that
# serves no HTTP should not import the HTTP layer to find out who.
DEV_USER_ID = "dev-user"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://app:app@localhost:5434/app"

    # How long the worker waits on a LISTEN before looking around anyway. It is
    # woken by NOTIFY the instant a task is enqueued, so this is not the
    # latency of a job — it is how often dead workers get noticed when nothing
    # else is happening.
    worker_idle_seconds: float = 5.0

    # A task claimed and then not finished within this long is assumed to belong
    # to a worker that died, and goes back on the queue. Must comfortably exceed
    # your longest real task, or the sweeper will steal live work.
    task_stale_seconds: int = 900

    # Touched once per loop pass, so a container healthcheck can tell a worker
    # that is running from one that is wedged. The worker has no HTTP port, and
    # "the process exists" is not liveness — Docker already restarts a process
    # that exits.
    # S108: a fixed path inside the worker's own container, not a shared
    # tmpdir on a multi-user host. Override it if that stops being true.
    worker_heartbeat_path: Path = Path("/tmp/worker-alive")  # noqa: S108

    # Backoff for a task that failed for a reason worth trying again — a
    # provider timeout, a transient network fault. Doubles per attempt from the
    # base, capped at the max. Which failures qualify is the handler's call:
    # see `TaskOutcome.retryable` in worker/handlers.py.
    task_retry_base_seconds: float = 5.0
    task_retry_max_seconds: float = 120.0

    # A ceiling on one disbursement, in minor units. Unbounded input is an
    # unbounded payment, and the number that matters is the one picked on
    # purpose rather than the one the provider happens to accept. Refused as a
    # 422 the client can show, long before any money moves.
    max_transfer_minor: int = 1_000_000_00

    # ------------------------------------------------------------- provider
    #
    # The mock provider's behaviour. All three failure hooks default to off:
    # they exist so the chaos pass has somewhere to plug in, and are not
    # themselves that pass. Nothing in the application reads them — only
    # `provider/mock.py`, which is pretending to be somebody else's company.

    # How long a payment sits `pending` before the provider settles it. Not
    # zero, deliberately: a provider that succeeds synchronously never
    # exercises the in-flight states the whole async design exists to handle.
    provider_settle_after_seconds: int = 5

    # Every send fails permanently. Simulates a rejected integration — the
    # kind of failure where retrying cannot help.
    provider_reject_all: bool = False

    # Every send fails retryably. Simulates a network partition or an outage.
    provider_unreachable: bool = False

    # Sends are accepted, then fail at settlement. The nastiest of the three,
    # because the money looked like it was on its way.
    provider_fail_settlement: bool = False

    # ---------------------------------------------------------------- auth
    #
    # Empty issuer means auth is OFF and every request is the dev user — which
    # is what keeps `docker compose up` working with no accounts anywhere. Set
    # these and the same endpoints start requiring a token.
    #
    # Deliberately not tied to a vendor. Every serious provider speaks OIDC, so
    # the verification is identical and switching between Clerk, WorkOS, Logto,
    # Auth0 or a self-hosted issuer is these two values.
    #
    #   oidc_issuer   https://your-tenant.example.com   (no trailing slash)
    #   oidc_audience the API identifier the provider puts in `aud`
    oidc_issuer: str = ""
    oidc_audience: str = ""

    # Defaults to the OIDC discovery convention. Override only if your provider
    # puts its keys somewhere else.
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

    # Comma-separated, not a list.
    #
    # pydantic-settings parses a `list[str]` field from the environment as
    # JSON, so `CORS_ORIGINS=http://localhost:3000` is a startup crash and
    # `CORS_ORIGINS=["http://localhost:3000"]` is what it actually wants. That
    # is a miserable thing to write in a compose file, so the field is a plain
    # string and the split happens here. Same trap applies to any list- or
    # dict-typed setting you add.
    cors_origins: str = "http://localhost:3000"

    @property
    def database_dsn(self) -> str:
        """The URL without SQLAlchemy's `+driver` suffix.

        asyncpg is reached two ways here: through SQLAlchemy for everything
        normal, and directly for LISTEN/NOTIFY, which SQLAlchemy has no API for.
        The direct path wants a plain libpq URL.
        """
        return self.database_url.replace("+asyncpg", "", 1)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
