"""Mark Minervini Trend Template with Volume + Virgin/Narrow CPR daily scanner.

Sections
--------
1. **Minervini_Volume** — Stage-2 Trend Template with volume >= EMA(volume, 20),
   matching the Chartink "Mark Minervini Trend Template with Volume" scan.

2. **Virgin_CPR_Buy** — Price stayed above the prior session pivot (virgin CPR
   above) and today prints a bullish CPR-top breakout with volume confirmation.

3. **Narrow_CPR_Breakout** — Today's CPR width is narrow versus price and price
   closes above the CPR top on a bullish, above-average volume day.

The companion file ``chartink_minervini_volume_cpr_scan.txt`` documents equivalent
Chartink custom-scan conditions.
"""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nimblr_minervini_cpr_scanner import (
    CombinedScannerConfig,
    SectionResult,
    _condition,
    _finite,
    calculate_indicators as _base_calculate_indicators,
    display_symbol,
    extract_company_names,
    evaluate_minervini,
    extract_tickers,
    history_from_chart,
    print_skip_summary,
    read_input_table,
    reset_yahoo_http_session,
    write_results,
)

DEFAULT_INPUT = "ind_nifty500list.csv"
DEFAULT_OUTPUT = "Minervini_Volume_CPR_Scan.xlsx"


@dataclass(frozen=True)
class VolumeCPRScannerConfig(CombinedScannerConfig):
    """Minervini volume + virgin/narrow CPR parameters."""

    narrow_cpr_max_width_pct: float = 0.005
    virgin_use_full_cpr: bool = False
    require_previous_high_breakout: bool = False
    combine_mode: str = "all"
    min_sections: int = 3

    @property
    def minimum_history(self) -> int:
        return super().minimum_history


def calculate_indicators(df: pd.DataFrame, config: VolumeCPRScannerConfig) -> pd.DataFrame:
    """Add CPR width columns on top of the shared indicator set."""
    frame = _base_calculate_indicators(df, config)
    width = frame["CPR_TOP"] - frame["CPR_BOTTOM"]
    frame["CPR_WIDTH"] = width
    frame["CPR_WIDTH_PCT"] = width / frame["Close"].replace(0, np.nan)
    return frame


def _virgin_above(frame: pd.DataFrame, index: int, config: VolumeCPRScannerConfig) -> tuple[bool, str]:
    """Return whether price stayed above the prior session CPR reference."""
    if index < 1:
        return False, "insufficient history"
    curr = frame.iloc[index]
    prev = frame.iloc[index - 1]
    if config.virgin_use_full_cpr:
        level = prev.get("CPR_TOP")
        label = "prior CPR top"
    else:
        level = prev.get("PIVOT")
        label = "prior pivot"
    passed = (
        _finite(curr["Low"])
        and _finite(level)
        and float(curr["Low"]) > float(level)
    )
    detail = f"low={curr.get('Low', np.nan):.2f} vs {label}={level:.2f}" if _finite(level) else "n/a"
    return passed, detail


def _cpr_breakout_conditions(
    frame: pd.DataFrame,
    index: int,
    config: VolumeCPRScannerConfig,
) -> list:
    """Shared bullish CPR breakout checks used by virgin and narrow sections."""
    curr = frame.iloc[index]
    prev = frame.iloc[index - 1]
    close_cross = (
        _finite(prev["Close"])
        and _finite(prev["CPR_TOP"])
        and _finite(curr["Close"])
        and _finite(curr["CPR_TOP"])
        and prev["Close"] <= prev["CPR_TOP"]
        and curr["Close"] > curr["CPR_TOP"]
    )
    conditions = [
        _condition(
            "Close crossed above CPR top",
            close_cross,
            f"prev_close={prev.get('Close', np.nan):.2f} prev_top={prev.get('CPR_TOP', np.nan):.2f} "
            f"close={curr.get('Close', np.nan):.2f} top={curr.get('CPR_TOP', np.nan):.2f}",
        ),
        _condition(
            "Bullish close",
            _finite(curr["Close"]) and _finite(curr["Open"]) and curr["Close"] > curr["Open"],
            f"open={curr.get('Open', np.nan):.2f} close={curr.get('Close', np.nan):.2f}",
        ),
        _condition(
            "Volume >= EMA(volume, 20)",
            _finite(curr["Volume"])
            and _finite(curr["EMA_VOL20"])
            and curr["Volume"] >= curr["EMA_VOL20"],
            f"{curr.get('Volume', np.nan):.0f} vs {curr.get('EMA_VOL20', np.nan):.0f}",
        ),
    ]
    if config.require_previous_high_breakout:
        conditions.append(
            _condition(
                "Close above previous high",
                _finite(curr["Close"]) and _finite(prev["High"]) and curr["Close"] > prev["High"],
                f"{curr.get('Close', np.nan):.2f} vs {prev.get('High', np.nan):.2f}",
            )
        )
    return conditions


def evaluate_minervini_volume(
    frame: pd.DataFrame,
    index: int,
    config: VolumeCPRScannerConfig,
) -> SectionResult:
    """Stage-2 Trend Template with above-average volume."""
    result = evaluate_minervini(frame, index, config)
    return SectionResult("Minervini_Volume", result.passed, result.conditions)


def evaluate_virgin_cpr_buy(
    frame: pd.DataFrame,
    index: int,
    config: VolumeCPRScannerConfig,
) -> SectionResult:
    """Virgin CPR above prior pivot/CPR plus bullish CPR-top breakout."""
    virgin_ok, virgin_detail = _virgin_above(frame, index, config)
    ref_label = "prior CPR top" if config.virgin_use_full_cpr else "prior pivot"
    conditions = [
        _condition(
            f"Virgin above {ref_label}",
            virgin_ok,
            virgin_detail,
        ),
        *_cpr_breakout_conditions(frame, index, config),
    ]
    return SectionResult("Virgin_CPR_Buy", all(item.passed for item in conditions), conditions)


def evaluate_narrow_cpr_breakout(
    frame: pd.DataFrame,
    index: int,
    config: VolumeCPRScannerConfig,
) -> SectionResult:
    """Narrow CPR width with bullish CPR-top breakout."""
    curr = frame.iloc[index]
    width_pct = curr.get("CPR_WIDTH_PCT")
    narrow = _finite(width_pct) and float(width_pct) <= config.narrow_cpr_max_width_pct
    conditions = [
        _condition(
            f"CPR width <= {config.narrow_cpr_max_width_pct * 100:.2f}% of close",
            narrow,
            f"width_pct={float(width_pct) * 100:.3f}%" if _finite(width_pct) else "n/a",
        ),
        *_cpr_breakout_conditions(frame, index, config),
    ]
    return SectionResult("Narrow_CPR_Breakout", all(item.passed for item in conditions), conditions)


def evaluate_scan(
    frame: pd.DataFrame,
    config: VolumeCPRScannerConfig,
    index: int | None = None,
) -> dict[str, Any] | None:
    if len(frame) < config.minimum_history:
        return None
    loc = len(frame) - 1 if index is None else index
    if loc < 1 or loc >= len(frame):
        return None

    curr = frame.iloc[loc]
    if not all(_finite(curr[column]) for column in ("Open", "High", "Low", "Close")):
        return None

    minervini = evaluate_minervini_volume(frame, loc, config)
    virgin = evaluate_virgin_cpr_buy(frame, loc, config)
    narrow = evaluate_narrow_cpr_breakout(frame, loc, config)
    sections = [minervini, virgin, narrow]
    passed_count = sum(1 for section in sections if section.passed)
    qualified = (
        passed_count == 3
        if config.combine_mode == "all"
        else passed_count >= config.min_sections
    )
    failed = [name for section in sections for name in section.failed_names]
    next_open = frame.iloc[loc + 1]["Open"] if loc + 1 < len(frame) else np.nan
    stop = curr["CPR_BOTTOM"] if _finite(curr["CPR_BOTTOM"]) else np.nan
    risk = (curr["Close"] - stop) if _finite(stop) else np.nan
    width_pct = curr.get("CPR_WIDTH_PCT")
    virgin_ok, _ = _virgin_above(frame, loc, config)

    return {
        "Date": pd.Timestamp(frame.index[loc]).date().isoformat(),
        "Close": float(curr["Close"]),
        "Open": float(curr["Open"]),
        "High": float(curr["High"]),
        "Low": float(curr["Low"]),
        "Volume": float(curr["Volume"]) if _finite(curr["Volume"]) else np.nan,
        "EMA50": float(curr["EMA50"]) if _finite(curr["EMA50"]) else np.nan,
        "EMA150": float(curr["EMA150"]) if _finite(curr["EMA150"]) else np.nan,
        "EMA200": float(curr["EMA200"]) if _finite(curr["EMA200"]) else np.nan,
        "CPR_Top": float(curr["CPR_TOP"]) if _finite(curr["CPR_TOP"]) else np.nan,
        "CPR_Pivot": float(curr["PIVOT"]) if _finite(curr["PIVOT"]) else np.nan,
        "CPR_Bottom": float(curr["CPR_BOTTOM"]) if _finite(curr["CPR_BOTTOM"]) else np.nan,
        "CPR_Width": float(curr["CPR_WIDTH"]) if _finite(curr["CPR_WIDTH"]) else np.nan,
        "CPR_Width_%": float(width_pct) * 100.0 if _finite(width_pct) else np.nan,
        "Virgin_Above": virgin_ok,
        "Low_52W": float(curr["LOW_52W"]) if _finite(curr["LOW_52W"]) else np.nan,
        "High_52W": float(curr["HIGH_52W"]) if _finite(curr["HIGH_52W"]) else np.nan,
        "Volume_EMA20": float(curr["EMA_VOL20"]) if _finite(curr["EMA_VOL20"]) else np.nan,
        "Next_Open": float(next_open) if _finite(next_open) else np.nan,
        "Suggested_Stop": float(stop) if _finite(stop) else np.nan,
        "Risk_Per_Share": float(risk) if _finite(risk) else np.nan,
        "Minervini_Volume": minervini.passed,
        "Virgin_CPR_Buy": virgin.passed,
        "Narrow_CPR_Breakout": narrow.passed,
        "Sections_Passed": passed_count,
        "Qualified": qualified,
        "Failed_Conditions": "; ".join(failed) if failed else "",
    }


def fetch_volume_cpr_history(symbol: str, config: VolumeCPRScannerConfig) -> pd.DataFrame:
    """Load daily bars from today's shared Yahoo cache, else the chart API.

    ``minervini-volume-cpr`` runs after the prefetch job and ``minervini-vcp``.
    Hitting ``yf.Ticker().history()`` at that point often fails crumb DNS and
    logs listed Nifty names as delisted. Prefer cached bars; on a miss use
    Yahoo's public chart API instead of yfinance.
    """
    from yahoo_bar_store import get_daily_history

    def live(_symbol: str, _period: str) -> pd.DataFrame:
        last_error: Exception | None = None
        for attempt in range(config.max_retries):
            try:
                frame = history_from_chart(symbol, config.lookback_period)
                if frame is not None and not frame.empty:
                    if config.request_delay:
                        time.sleep(config.request_delay)
                    return frame
                last_error = ValueError(f"empty Yahoo chart data for {symbol}")
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "minervini-volume-cpr Yahoo chart attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    config.max_retries,
                    symbol,
                    exc,
                )
                reset_yahoo_http_session()
            if attempt < config.max_retries - 1:
                time.sleep(config.retry_delay * (2 ** attempt))
        if last_error is not None:
            logging.warning(
                "minervini-volume-cpr could not fetch %s after %d attempt(s): %s",
                symbol,
                config.max_retries,
                last_error,
            )
        if config.request_delay:
            time.sleep(config.request_delay)
        return pd.DataFrame()

    return get_daily_history(
        symbol,
        period=config.lookback_period,
        live_loader=live,
    )


def analyze_symbol(
    symbol: str,
    config: VolumeCPRScannerConfig,
    history: pd.DataFrame | None = None,
    skipped: list[str] | None = None,
) -> dict[str, Any] | None:
    try:
        frame = history if history is not None else fetch_volume_cpr_history(symbol, config)
        if frame.empty or len(frame) < config.minimum_history:
            if skipped is not None:
                skipped.append(display_symbol(symbol))
            else:
                logging.debug("Insufficient historical data for %s", symbol)
            return None
        frame = calculate_indicators(frame, config)
        result = evaluate_scan(frame, config)
        if result is None:
            return None
        result["Ticker"] = display_symbol(symbol)
        return result
    except Exception as exc:
        logging.warning("Error processing ticker %s: %s", symbol, exc)
        return None


def _round_result(row: dict[str, Any]) -> dict[str, Any]:
    rounded = dict(row)
    for key, value in row.items():
        if isinstance(value, float) and np.isfinite(value):
            rounded[key] = round(value, 2)
    return rounded


RESULT_COLUMNS = [
    "Ticker",
    "Company Name",
    "Date",
    "Close",
    "EMA50",
    "EMA150",
    "EMA200",
    "CPR_Top",
    "CPR_Pivot",
    "CPR_Bottom",
    "CPR_Width",
    "CPR_Width_%",
    "Virgin_Above",
    "Low_52W",
    "High_52W",
    "Volume",
    "Volume_EMA20",
    "Next_Open",
    "Suggested_Stop",
    "Risk_Per_Share",
    "Minervini_Volume",
    "Virgin_CPR_Buy",
    "Narrow_CPR_Breakout",
    "Sections_Passed",
    "Qualified",
    "Failed_Conditions",
]


def format_results(results: list[dict[str, Any]]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    frame = pd.DataFrame([_round_result(row) for row in results])
    ordered = [column for column in RESULT_COLUMNS if column in frame.columns]
    extra = [column for column in frame.columns if column not in ordered]
    return frame.loc[:, ordered + extra].sort_values(
        by=["Qualified", "Sections_Passed", "CPR_Width_%"],
        ascending=[False, False, True],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan Nifty 500 for Mark Minervini Trend Template with Volume, "
            "Virgin CPR daily buy, and Narrow CPR high-probability breakout."
        )
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Ticker universe CSV/Excel path.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output path for scan results.")
    parser.add_argument(
        "--engine",
        default=None,
        help="Input file engine: openpyxl, xlrd, pyxlsb, odf, csv, or html.",
    )
    parser.add_argument(
        "--mode",
        choices=("all", "score"),
        default="all",
        help="all = all three sections required; score = rank by sections passed.",
    )
    parser.add_argument(
        "--min-sections",
        type=int,
        default=2,
        help="Minimum sections for score mode (default: 2).",
    )
    parser.add_argument(
        "--narrow-cpr-max-width-pct",
        type=float,
        default=0.005,
        help="Max CPR width as fraction of close for narrow CPR (default: 0.005 = 0.5%%).",
    )
    parser.add_argument(
        "--virgin-use-full-cpr",
        action="store_true",
        help="Require low above prior CPR top instead of prior pivot only.",
    )
    parser.add_argument(
        "--require-previous-high-breakout",
        action="store_true",
        help="Also require today's close above yesterday's high for CPR breakouts.",
    )
    parser.add_argument(
        "--include-failures",
        action="store_true",
        help="Write rows that fail one or more sections (useful for debugging).",
    )
    parser.add_argument("--limit", type=int, default=0, help="Scan only the first N tickers (0 = all).")
    parser.add_argument(
        "--lookback",
        default="2y",
        help="Yahoo daily lookback period for this scanner (default: 2y).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Yahoo chart retries per ticker (default: 3).",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.0,
        help="Seconds to wait after each ticker fetch (default: 0).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    # Drop any crumb/session left over from minervini-vcp in the same daily run.
    reset_yahoo_http_session()
    config = VolumeCPRScannerConfig(
        combine_mode="all" if args.mode == "all" else "score",
        min_sections=args.min_sections if args.mode == "score" else 3,
        narrow_cpr_max_width_pct=args.narrow_cpr_max_width_pct,
        virgin_use_full_cpr=args.virgin_use_full_cpr,
        require_previous_high_breakout=args.require_previous_high_breakout,
        lookback_period=args.lookback,
        max_retries=args.max_retries,
        request_delay=args.request_delay,
    )

    try:
        input_table = read_input_table(args.input, engine=args.engine)
        tickers = extract_tickers(input_table)
        company_names = extract_company_names(input_table)
    except Exception as exc:
        print(f"Input error: {exc}")
        return 1
    if args.limit and args.limit > 0:
        tickers = tickers[: args.limit]

    print(
        f"Scanning {len(tickers)} stocks for Minervini Volume + Virgin/Narrow CPR "
        f"(mode={config.combine_mode})..."
    )

    results: list[dict[str, Any]] = []
    skipped: list[str] = []
    for ticker in tickers:
        snapshot = analyze_symbol(ticker, config, skipped=skipped)
        if snapshot is None:
            continue
        if snapshot["Qualified"] or args.include_failures:
            snapshot["Company Name"] = company_names.get(snapshot["Ticker"], "")
            results.append(snapshot)
    output = format_results(results)
    write_results(output, args.output)
    qualified = int(output["Qualified"].sum()) if not output.empty and "Qualified" in output.columns else 0
    print_skip_summary(skipped, config.minimum_history)
    print(
        f"Scan complete. Found {qualified} qualified setup(s) "
        f"({len(output)} row(s) written). Saved to '{args.output}'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
