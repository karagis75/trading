#!/usr/bin/env python3
"""Regression tests for trading-script bugfixes."""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_yahoo_ticker_aliases() -> None:
    v8 = load_module("combined_v8", ROOT / "combinedoptionanalyzedv8.py")
    check("NIFTY -> ^NSEI", v8.yahoo_ticker("NIFTY") == "^NSEI")
    check("BANKNIFTY -> ^NSEBANK", v8.yahoo_ticker("BANKNIFTY") == "^NSEBANK")
    check("FINNIFTY -> NIFTY_FIN_SERVICE.NS", v8.yahoo_ticker("FINNIFTY") == "NIFTY_FIN_SERVICE.NS")
    check("equity keeps .NS", v8.yahoo_ticker("INFY") == "INFY.NS")
    check("already suffixed passthrough", v8.yahoo_ticker("RELIANCE.NS") == "RELIANCE.NS")
    check("caret passthrough", v8.yahoo_ticker("^NSEI") == "^NSEI")


def test_india_vix_index_symbol_field() -> None:
    v8 = load_module("combined_v8_vix", ROOT / "combinedoptionanalyzedv8.py")

    class FakeSession:
        pass

    # Monkeypatch get_json via payload path by stubbing get_json on module
    def fake_get_json(session, url, **params):
        return {
            "data": [
                {"indexSymbol": "INDIA VIX", "last": 13.45},
                {"index": "NIFTY 50", "last": 24000},
            ]
        }

    v8.get_json = fake_get_json
    value = v8.fetch_india_vix(FakeSession(), fallback=18.0)
    check("VIX reads indexSymbol", abs(value - 13.45) < 1e-9, str(value))


def test_iron_butterfly_guards() -> None:
    v8 = load_module("combined_v8_bf", ROOT / "combinedoptionanalyzedv8.py")

    def opt(strike: float, bid: float = 10.0, ask: float = 10.5, oi: int = 1000):
        return {
            "strikePrice": strike,
            "bidprice": bid,
            "askPrice": ask,
            "openInterest": oi,
        }

    # Mismatched ATM strikes must be rejected
    bad = v8.build_short_iron_butterfly_opportunity(
        "TEST", 1.0, 12.0, 100.0,
        opt(100), opt(105), opt(110), opt(90),
        5000, "30-Apr-2026", 0.2,
    )
    check("IB rejects mismatched ATM", bad is None)

    # Wing not strictly outside ATM must be rejected
    degenerate = v8.build_short_iron_butterfly_opportunity(
        "TEST", 1.0, 12.0, 100.0,
        opt(100), opt(100), opt(110), opt(100),
        5000, "30-Apr-2026", 0.2,
    )
    check("IB rejects ATM put as wing", degenerate is None)

    good = v8.build_short_iron_butterfly_opportunity(
        "TEST", 1.0, 12.0, 100.0,
        opt(100, bid=3.0, ask=3.2), opt(100, bid=3.0, ask=3.2),
        opt(110, bid=0.5, ask=0.7), opt(90, bid=0.5, ask=0.7),
        5000, "30-Apr-2026", 0.2,
    )
    check("IB accepts equal wings", good is not None and good["Strategy"] == "Short Iron Butterfly", str(good))


def test_neowave_mode_prefix_and_chronology() -> None:
    bullbear = load_module("bullbear_neowave", ROOT / "bullbear_neowave.py")
    src = (ROOT / "bullbear_neowave.py").read_text()
    # mode_prefix must be assigned before the if fib branch uses it in else
    assign_idx = src.find('mode_prefix = "Bear" if is_bearish else "Bull"')
    else_idx = src.find('No confirmed {mode_prefix.lower()} impulse found in this segment.')
    check("mode_prefix defined before else usage", 0 <= assign_idx < else_idx)

    shortterm = load_module("neowave_short", ROOT / "neowave_commodity_shortterm.py")
    # High before low should NOT count as bullish impulse
    pivots = [
        {"kind": "H", "price": 120.0, "index": 1},
        {"kind": "L", "price": 100.0, "index": 2},
        {"kind": "H", "price": 110.0, "index": 3},
        {"kind": "L", "price": 95.0, "index": 4},
    ]
    # macro_high at index 1, macro_low at index 4 — high before low → no bullish
    result = shortterm.latest_impulse(pivots, bearish=False)
    check("shortterm bullish requires low before high", result is None)

    bullish_pivots = [
        {"kind": "L", "price": 90.0, "index": 1},
        {"kind": "H", "price": 100.0, "index": 2},
        {"kind": "L", "price": 95.0, "index": 3},
        {"kind": "H", "price": 130.0, "index": 4},
    ]
    result2 = shortterm.latest_impulse(bullish_pivots, bearish=False)
    check("shortterm bullish when low precedes high", result2 is not None)


def test_zero_mad_indicators() -> None:
    import numpy as np
    import pandas as pd

    bear = load_module("bear_bias", ROOT / "bearisbiasnifty500.py")
    # Flat prices → MAD/ATR can be zero; must not produce Inf
    idx = pd.date_range("2024-01-01", periods=40, freq="B")
    df = pd.DataFrame(
        {
            "Open": [100.0] * 40,
            "High": [100.0] * 40,
            "Low": [100.0] * 40,
            "Close": [100.0] * 40,
        },
        index=idx,
    )
    cfg = bear.BearishScannerConfig()
    out = bear.calculate_indicators(df, cfg)
    check("CCI finite on flat series", np.isfinite(out["CCI"]).all())
    check("ADX finite on flat series", np.isfinite(out["ADX"]).all())


def test_live_yahoo_index_aliases() -> None:
    try:
        import yfinance as yf
    except ImportError:
        check("yfinance installed", False, "missing package")
        return

    v8 = load_module("combined_v8_live", ROOT / "combinedoptionanalyzedv8.py")
    for symbol in ("NIFTY", "BANKNIFTY"):
        ticker = v8.yahoo_ticker(symbol)
        df = yf.Ticker(ticker).history(period="1mo")
        check(f"live Yahoo data for {symbol} ({ticker})", len(df) >= 10, f"rows={len(df)}")


def main() -> int:
    test_yahoo_ticker_aliases()
    test_india_vix_index_symbol_field()
    test_iron_butterfly_guards()
    test_neowave_mode_prefix_and_chronology()
    test_zero_mad_indicators()
    test_live_yahoo_index_aliases()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
