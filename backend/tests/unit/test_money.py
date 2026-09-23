from app.money import format_money


def test_dollars_read_as_dollars():
    assert format_money(123_456, "USD") == "$1,234.56"
    assert format_money(5, "USD") == "$0.05"
    assert format_money(100_000_000, "USD") == "$1,000,000.00"


def test_other_currencies_lead_with_their_code():
    assert format_money(250_000, "KES") == "KES 2,500.00"


def test_negative_amounts_keep_their_sign_outside_the_symbol():
    assert format_money(-1_050, "USD") == "-$10.50"
