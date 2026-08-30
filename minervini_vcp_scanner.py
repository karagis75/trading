"""Mark Minervini Stage-2 Trend Template + VCP scanner for Nifty 500.

Stage 1 confirms a stock is in a powerful Stage-2 uptrend using the Minervini
Trend Template (without requiring a volume spike, since VCP bases dry up).

Stage 2 looks for a Volatility Contraction Pattern: sequential pullbacks with
tightening depth/range and below-average volume near the upper part of the base.

The companion file ``chartink_minervini_vcp_scan.txt`` documents equivalent
Chartink custom-scan conditions.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from nimblr_minervini_cpr_scanner import (
    CombinedScannerConfig,
    SectionResult,
    _condition,
    _finite,
    calculate_indicators,
    display_symbol,
    extract_company_names,
    extract_tickers,
    fetch_history,
    print_skip_summary,
    read_input_table,
    write_results,
)

DEFAULT_INPUT = "ind_nifty500list.csv"
DEFAULT_OUTPUT = "Minervini_VCP_Scan.xlsx"


@dataclass(frozen=True)
class VCPScannerConfig(CombinedScannerConfig):
    """Trend-template + VCP parameters."""

    base_bars: int = 63
    pivot_left: int = 3
    pivot_right: int = 3
    min_contractions: int = 2
    max_latest_pullback_pct: float = 25.0
    min_base_position: float = 0.65
    require_atr_contraction: bool = True
    require_volume_dryup: bool = True
    combine_mode: str = "all"
    min_sections: int = 2

    @property
    def minimum_history(self) -> int:
        return max(
            super().minimum_history,
            self.base_bars + self.pivot_left + self.pivot_right + 5,
        )


@dataclass
class PivotPoint:
    index: int
    kind: str
    price: float


def _alternate_pivots(raw: list[PivotPoint]) -> list[PivotPoint]:
    if not raw:
        return []
    ordered = sorted(raw, key=lambda item: item.index)
    alt: list[PivotPoint] = []
    for pivot in ordered:
        if not alt:
            alt.append(pivot)
            continue
        prev = alt[-1]
        if prev.kind == pivot.kind:
            if pivot.kind == "H" and pivot.price >= prev.price:
                alt[-1] = pivot
            elif pivot.kind == "L" and pivot.price <= prev.price:
                alt[-1] = pivot
        else:
            alt.append(pivot)
    return alt


def find_pivots(
    high: np.ndarray,
    low: np.ndarray,
    left: int,
    right: int,
) -> list[PivotPoint]:
    raw: list[PivotPoint] = []
    size = len(high)
    for index in range(left, size - right):
        high_window = high[index - left : index + right + 1]
        low_window = low[index - left : index + right + 1]
        if high[index] >= np.max(np.delete(high_window, left)):
            raw.append(PivotPoint(index=index, kind="H", price=float(high[index])))
        if low[index] <= np.min(np.delete(low_window, left)):
            raw.append(PivotPoint(index=index, kind="L", price=float(low[index])))
    return _alternate_pivots(raw)


def _pullback_depths(pivots: list[PivotPoint]) -> list[tuple[int, int, float]]:
    """Return (high_idx, low_idx, depth_pct) for each high-to-low pullback."""
    depths: list[tuple[int, int, float]] = []
    for index in range(len(pivots) - 1):
        left = pivots[index]
        right = pivots[index + 1]
        if left.kind != "H" or right.kind != "L":
            continue
        if left.price <= 0:
            continue
        depth = (left.price - right.price) / left.price * 100.0
        if depth > 0:
            depths.append((left.index, right.index, depth))
    return depths


def _segment_mean_range(frame: pd.DataFrame, start: int, end: int) -> float:
    segment = frame.iloc[start : end + 1]
    if segment.empty:
        return float("nan")
    return float((segment["High"] - segment["Low"]).mean())


def _segment_mean_volume(frame: pd.DataFrame, start: int, end: int) -> float:
    segment = frame.iloc[start : end + 1]
    if segment.empty:
        return float("nan")
    return float(segment["Volume"].mean())


def evaluate_trend_template(
    frame: pd.DataFrame,
    index: int,
    config: VCPScannerConfig,
) -> SectionResult:
    """Stage 1: Minervini Trend Template (Stage-2 uptrend)."""
    curr = frame.iloc[index]
    conditions = [
        _condition(
            "Close >= EMA150",
            _finite(curr["Close"]) and _finite(curr["EMA150"]) and curr["Close"] >= curr["EMA150"],
            f"{curr.get('Close', np.nan):.2f} vs {curr.get('EMA150', np.nan):.2f}",
        ),
        _condition(
            "Close >= EMA200",
            _finite(curr["Close"]) and _finite(curr["EMA200"]) and curr["Close"] >= curr["EMA200"],
            f"{curr.get('Close', np.nan):.2f} vs {curr.get('EMA200', np.nan):.2f}",
        ),
        _condition(
            "EMA150 >= EMA200",
            _finite(curr["EMA150"]) and _finite(curr["EMA200"]) and curr["EMA150"] >= curr["EMA200"],
            f"{curr.get('EMA150', np.nan):.2f} vs {curr.get('EMA200', np.nan):.2f}",
        ),
        _condition(
            "EMA200 rising vs 1 month ago",
            _finite(curr["EMA200"])
            and _finite(curr["EMA200_1M_AGO"])
            and curr["EMA200"] > curr["EMA200_1M_AGO"],
            f"{curr.get('EMA200', np.nan):.2f} vs {curr.get('EMA200_1M_AGO', np.nan):.2f}",
        ),
        _condition(
            "EMA50 > EMA150",
            _finite(curr["EMA50"]) and _finite(curr["EMA150"]) and curr["EMA50"] > curr["EMA150"],
            f"{curr.get('EMA50', np.nan):.2f} vs {curr.get('EMA150', np.nan):.2f}",
        ),
        _condition(
            "EMA50 > EMA200",
            _finite(curr["EMA50"]) and _finite(curr["EMA200"]) and curr["EMA50"] > curr["EMA200"],
            f"{curr.get('EMA50', np.nan):.2f} vs {curr.get('EMA200', np.nan):.2f}",
        ),
        _condition(
            "Close > EMA50",
            _finite(curr["Close"]) and _finite(curr["EMA50"]) and curr["Close"] > curr["EMA50"],
            f"{curr.get('Close', np.nan):.2f} vs {curr.get('EMA50', np.nan):.2f}",
        ),
        _condition(
            "Close >= 1.30 x 52-week low",
            _finite(curr["Close"])
            and _finite(curr["LOW_52W"])
            and curr["Close"] >= config.min_low_multiple * curr["LOW_52W"],
            f"{curr.get('Close', np.nan):.2f} vs {config.min_low_multiple:.2f}*{curr.get('LOW_52W', np.nan):.2f}",
        ),
        _condition(
            "Close >= 75% of 52-week high",
            _finite(curr["Close"])
            and _finite(curr["HIGH_52W"])
            and curr["Close"] >= config.min_high_multiple * curr["HIGH_52W"],
            f"{curr.get('Close', np.nan):.2f} vs {config.min_high_multiple:.2f}*{curr.get('HIGH_52W', np.nan):.2f}",
        ),
    ]
    return SectionResult("Stage2_Trend", all(item.passed for item in conditions), conditions)


def evaluate_vcp(
    frame: pd.DataFrame,
    index: int,
    config: VCPScannerConfig,
) -> SectionResult:
    """Stage 2: tightening contractions with dry volume inside the base."""
    curr = frame.iloc[index]
    start = max(0, index - config.base_bars + 1)
    base = frame.iloc[start : index + 1]
    if len(base) < config.pivot_left + config.pivot_right + 5:
        return SectionResult(
            "VCP",
            False,
            [_condition("Enough base history", False, f"bars={len(base)}")],
        )

    highs = base["High"].to_numpy(dtype=float)
    lows = base["Low"].to_numpy(dtype=float)
    pivots = find_pivots(highs, lows, config.pivot_left, config.pivot_right)
    depths = _pullback_depths(pivots)

    contraction_count = len(depths)
    tightening = False
    depth_detail = "no pullbacks"
    if contraction_count >= config.min_contractions:
        recent = [item[2] for item in depths[-config.min_contractions :]]
        tightening = all(recent[idx] < recent[idx - 1] for idx in range(1, len(recent)))
        depth_detail = " -> ".join(f"{value:.1f}%" for value in recent)

    latest_depth = depths[-1][2] if depths else float("nan")
    latest_pullback_ok = _finite(latest_depth) and latest_depth <= config.max_latest_pullback_pct

    base_high = float(base["High"].max())
    base_low = float(base["Low"].min())
    span = base_high - base_low
    position = (float(curr["Close"]) - base_low) / span if span > 0 else float("nan")
    near_top = _finite(position) and position >= config.min_base_position

    atr_start = float(base["ATR"].iloc[0]) if _finite(base["ATR"].iloc[0]) else float("nan")
    atr_end = float(curr["ATR"]) if _finite(curr["ATR"]) else float("nan")
    atr_contracted = _finite(atr_start) and _finite(atr_end) and atr_end < atr_start

    if depths:
        first_start, first_end, _ = depths[0]
        last_start, last_end, _ = depths[-1]
        first_range = _segment_mean_range(base, first_start, first_end)
        last_range = _segment_mean_range(base, last_start, last_end)
        range_tightening = _finite(first_range) and _finite(last_range) and last_range < first_range
        first_vol = _segment_mean_volume(base, first_start, first_end)
        last_vol = _segment_mean_volume(base, last_start, last_end)
        pullback_vol_dry = _finite(first_vol) and _finite(last_vol) and last_vol < first_vol
    else:
        range_tightening = False
        pullback_vol_dry = False
        first_range = float("nan")
        last_range = float("nan")

    volume_below_ema = (
        _finite(curr["Volume"])
        and _finite(curr["EMA_VOL20"])
        and curr["Volume"] < curr["EMA_VOL20"]
    )
    base_mid = start + len(base) // 2
    early_vol = float(base.iloc[: len(base) // 2]["Volume"].mean())
    late_vol = float(base.iloc[len(base) // 2 :]["Volume"].mean())
    base_volume_dry = _finite(early_vol) and _finite(late_vol) and late_vol < early_vol
    volume_dry = volume_below_ema and (pullback_vol_dry or base_volume_dry)

    conditions = [
        _condition(
            f">= {config.min_contractions} pullbacks in base",
            contraction_count >= config.min_contractions,
            f"count={contraction_count}",
        ),
        _condition(
            "Pullback depths tightening",
            tightening,
            depth_detail,
        ),
        _condition(
            f"Latest pullback <= {config.max_latest_pullback_pct:.0f}%",
            latest_pullback_ok,
            f"{latest_depth:.2f}%" if _finite(latest_depth) else "n/a",
        ),
        _condition(
            "Pullback ranges tightening",
            range_tightening,
            f"first={first_range:.2f} last={last_range:.2f}"
            if _finite(first_range) and _finite(last_range)
            else "n/a",
        ),
        _condition(
            "Price in upper part of base",
            near_top,
            f"position={position:.2f}" if _finite(position) else "n/a",
        ),
    ]
    if config.require_atr_contraction:
        conditions.append(
            _condition(
                "ATR contracted over base",
                atr_contracted,
                f"start={atr_start:.2f} end={atr_end:.2f}"
                if _finite(atr_start) and _finite(atr_end)
                else "n/a",
            )
        )
    if config.require_volume_dryup:
        conditions.append(
            _condition(
                "Volume dried up in base",
                volume_dry,
                f"today={curr.get('Volume', np.nan):.0f} ema20={curr.get('EMA_VOL20', np.nan):.0f}",
            )
        )

    return SectionResult("VCP", all(item.passed for item in conditions), conditions)


def evaluate_scan(
    frame: pd.DataFrame,
    config: VCPScannerConfig,
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

    trend = evaluate_trend_template(frame, loc, config)
    vcp = evaluate_vcp(frame, loc, config)
    sections = [trend, vcp]
    passed_count = sum(1 for section in sections if section.passed)
    qualified = (
        passed_count == 2
        if config.combine_mode == "all"
        else passed_count >= config.min_sections
    )
    failed = [name for section in sections for name in section.failed_names]

    start = max(0, loc - config.base_bars + 1)
    base = frame.iloc[start : loc + 1]
    pivots = find_pivots(
        base["High"].to_numpy(dtype=float),
        base["Low"].to_numpy(dtype=float),
        config.pivot_left,
        config.pivot_right,
    )
    depths = _pullback_depths(pivots)
    latest_depth = depths[-1][2] if depths else np.nan
    base_high = float(base["High"].max())
    base_low = float(base["Low"].min())
    span = base_high - base_low
    position = (float(curr["Close"]) - base_low) / span if span > 0 else np.nan

    return {
        "Date": pd.Timestamp(frame.index[loc]).date().isoformat(),
        "Close": float(curr["Close"]),
        "EMA50": float(curr["EMA50"]) if _finite(curr["EMA50"]) else np.nan,
        "EMA150": float(curr["EMA150"]) if _finite(curr["EMA150"]) else np.nan,
        "EMA200": float(curr["EMA200"]) if _finite(curr["EMA200"]) else np.nan,
        "ATR": float(curr["ATR"]) if _finite(curr["ATR"]) else np.nan,
        "Low_52W": float(curr["LOW_52W"]) if _finite(curr["LOW_52W"]) else np.nan,
        "High_52W": float(curr["HIGH_52W"]) if _finite(curr["HIGH_52W"]) else np.nan,
        "Volume": float(curr["Volume"]) if _finite(curr["Volume"]) else np.nan,
        "Volume_EMA20": float(curr["EMA_VOL20"]) if _finite(curr["EMA_VOL20"]) else np.nan,
        "Contractions": len(depths),
        "Latest_Pullback_%": float(latest_depth) if _finite(latest_depth) else np.nan,
        "Base_Position": float(position) if _finite(position) else np.nan,
        "Stage2_Trend": trend.passed,
        "VCP": vcp.passed,
        "Sections_Passed": passed_count,
        "Qualified": qualified,
        "Failed_Conditions": "; ".join(failed) if failed else "",
    }


def analyze_symbol(
    symbol: str,
    config: VCPScannerConfig,
    history: pd.DataFrame | None = None,
    skipped: list[str] | None = None,
) -> dict[str, Any] | None:
    try:
        frame = history if history is not None else fetch_history(symbol, config)
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
    "ATR",
    "Low_52W",
    "High_52W",
    "Volume",
    "Volume_EMA20",
    "Contractions",
    "Latest_Pullback_%",
    "Base_Position",
    "Stage2_Trend",
    "VCP",
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
        by=["Qualified", "Sections_Passed", "Latest_Pullback_%"],
        ascending=[False, False, True],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan Nifty 500 for Mark Minervini Stage-2 Trend Template stocks "
            "showing a Volatility Contraction Pattern (VCP)."
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
        help="all = both stages required; score = rank by sections passed.",
    )
    parser.add_argument(
        "--include-failures",
        action="store_true",
        help="Write rows that fail one or both stages (useful for debugging).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = VCPScannerConfig(
        combine_mode="all" if args.mode == "all" else "score",
        min_sections=1 if args.mode == "score" else 2,
    )

    try:
        input_table = read_input_table(args.input, engine=args.engine)
        tickers = extract_tickers(input_table)
        company_names = extract_company_names(input_table)
    except Exception as exc:
        print(f"Input error: {exc}")
        return

    print(
        f"Scanning {len(tickers)} stocks for Minervini Stage-2 + VCP "
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


if __name__ == "__main__":
    main()
