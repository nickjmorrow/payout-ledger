"""Amounts as a person reads them, for messages that reach one. Minor units everywhere else."""

SYMBOLS = {"USD": "$"}


def format_money(minor: int, currency: str) -> str:
    """`123456, "USD"` -> `"$1,234.56"`; other currencies lead with their code."""
    major, cents = divmod(abs(minor), 100)
    sign = "-" if minor < 0 else ""
    amount = f"{major:,}.{cents:02d}"
    symbol = SYMBOLS.get(currency)
    return f"{sign}{symbol}{amount}" if symbol else f"{sign}{currency} {amount}"
