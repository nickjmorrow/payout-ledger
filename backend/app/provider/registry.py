"""Which `PaymentProvider` this process uses.

A function rather than a module-level `provider = MockProvider()` so that
importing anything under `app/` does not construct one. That matters the day
the real provider arrives: constructing it will read credentials and open an
HTTP client, and at module scope that would make every test and the migration
step require an API key to import a module they never call.

The import of the concrete provider is inside the function for the same reason,
and it is the only place outside `app/provider/` that names one.
"""

from functools import lru_cache

from app.provider.base import PaymentProvider


@lru_cache(maxsize=1)
def get_provider() -> PaymentProvider:
    from app.provider.mock import MockProvider  # noqa: PLC0415

    return MockProvider()
