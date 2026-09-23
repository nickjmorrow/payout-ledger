"""Which `PaymentProvider` this process uses.

A function, so importing `app` never constructs a provider. A real one would
read credentials at construction.
"""

from functools import lru_cache

from app.provider.base import PaymentProvider


@lru_cache(maxsize=1)
def get_provider() -> PaymentProvider:
    from app.provider.mock import MockProvider  # noqa: PLC0415

    return MockProvider()
