"""Finite decimal amounts. JSON keeps compatible numeric values at the boundary."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def decimal_amount(value, default=None):
    try:
        if isinstance(value, bool):
            raise ValueError("Boolean is not an amount")
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("Amount must be finite")
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        if default is not None:
            return Decimal(str(default))
        raise ValueError(f"Invalid amount: {value}") from None


def money_sum(values):
    return float(sum((decimal_amount(v) for v in values), Decimal(0)))


def format_money(amount, currency="CNY"):
    """Format display amounts consistently across reports and briefings."""
    symbol = "¥" if currency == "CNY" else f"{currency} "
    return f"{symbol}{float(amount):,.2f}"
