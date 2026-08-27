"""Shared helpers for the standalone trading scanners."""


def normalize_nse_ticker(value: object) -> str:
    """Return a Yahoo-compatible ticker, including numeric Excel symbols."""
    symbol = str(value).strip().upper()
    if symbol.endswith(".0") and symbol[:-2].isdigit():
        symbol = symbol[:-2]
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}.NS"
