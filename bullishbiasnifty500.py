import argparse
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

EXCEL_READ_ENGINES = {
    ".xlsx": "openpyxl",
    ".xlsm": "openpyxl",
    ".xltx": "openpyxl",
    ".xltm": "openpyxl",
    ".xls": "xlrd",
    ".xlsb": "pyxlsb",
    ".ods": "odf",
}
EXCEL_WRITE_ENGINES = {
    ".xlsx": "openpyxl",
    ".xlsm": "openpyxl",
    ".xlsb": "pyxlsb",
    ".ods": "odf",
}
TEXT_ENGINES = {"csv", "html", "htm"}
TICKER_COLUMNS = ("Ticker", "ticker", "Symbol", "symbol", "SYMBOL")
OLE_COMPOUND_SIGNATURE = b"\xd0\xcf\x11\xe0"
ZIP_SIGNATURE = b"PK\x03\x04"


@dataclass(frozen=True)
class BullishScannerConfig:
    min_adx: float = 20.0
    cci_period: int = 20
    adx_period: int = 14
    ema_fast: int = 9
    ema_medium: int = 18
    ema_slow: int = 50
    ema_macro: int = 200
    lookback_period: str = "1y"


def calculate_indicators(df: pd.DataFrame, config: BullishScannerConfig) -> pd.DataFrame:
    """Calculates EMAs, CCI, and ADX for given price data."""
    df = df.copy()

    # 1. Exponential Moving Averages (EMAs)
    df["EMA9"] = df["Close"].ewm(span=config.ema_fast, adjust=False).mean()
    df["EMA18"] = df["Close"].ewm(span=config.ema_medium, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=config.ema_slow, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=config.ema_macro, adjust=False).mean()

    # 2. Commodity Channel Index (CCI)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    sma_tp = tp.rolling(window=config.cci_period).mean()
    mad = tp.rolling(window=config.cci_period).apply(
        lambda x: np.abs(x - x.mean()).mean(), raw=True
    )
    df["CCI"] = (tp - sma_tp) / (0.015 * mad)

    # 3. Average Directional Index (ADX)
    high_diff = df["High"].diff()
    low_diff = -df["Low"].diff()

    pos_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    neg_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)

    tr1 = df["High"] - df["Low"]
    tr2 = np.abs(df["High"] - df["Close"].shift(1))
    tr3 = np.abs(df["Low"] - df["Close"].shift(1))
    tr = pd.DataFrame({"tr1": tr1, "tr2": tr2, "tr3": tr3}).max(axis=1)

    atr = tr.rolling(window=config.adx_period).mean()
    pos_di = 100 * (pd.Series(pos_dm, index=df.index).rolling(window=config.adx_period).mean() / atr)
    neg_di = 100 * (pd.Series(neg_dm, index=df.index).rolling(window=config.adx_period).mean() / atr)

    dx = 100 * (np.abs(pos_di - neg_di) / (pos_di + neg_di))
    df["ADX"] = dx.rolling(window=config.adx_period).mean()

    return df


def evaluate_bullish_bias(symbol: str, df: pd.DataFrame, config: BullishScannerConfig) -> dict[str, Any] | None:
    """Evaluates systematic bullish criteria on historical price data."""
    if len(df) < config.ema_macro:
        return None

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Rule 1: EMA Alignment & Stack Order (Price > 9 > 18 > 50 > 200)
    price_above_emas = (
        curr["Close"] > curr["EMA9"]
        and curr["EMA9"] > curr["EMA18"]
        and curr["EMA18"] > curr["EMA50"]
        and curr["EMA50"] > curr["EMA200"]
    )

    # Anchor condition: 200 EMA is flat or sloping upward
    ema200_sloping_up = curr["EMA200"] >= df.iloc[-5]["EMA200"]
    ema_aligned = price_above_emas and ema200_sloping_up

    # Rule 2: CCI Momentum Trigger & Sustained Check
    cci_trigger = (prev["CCI"] <= 100 and curr["CCI"] > 100) or (curr["CCI"] > 100)

    # Rule 3: Bullish Candle Check
    body_size = abs(curr["Close"] - curr["Open"])
    candle_range = curr["High"] - curr["Low"]
    is_bullish_candle = (curr["Close"] > curr["Open"]) and (body_size / candle_range >= 0.5 if candle_range > 0 else True)
    riding_ema9 = curr["Low"] >= curr["EMA9"] or curr["Close"] > curr["EMA9"]

    # Rule 4: ADX Filter
    adx_pass = curr["ADX"] >= config.min_adx

    # Combined Strategy Signal Validation
    is_confirmed = ema_aligned and cci_trigger and is_bullish_candle and riding_ema9 and adx_pass

    if not is_confirmed:
        return None

    return {
        "Ticker": symbol,
        "Close Price": round(curr["Close"], 2),
        "EMA9": round(curr["EMA9"], 2),
        "EMA18": round(curr["EMA18"], 2),
        "EMA50": round(curr["EMA50"], 2),
        "EMA200": round(curr["EMA200"], 2),
        "CCI": round(curr["CCI"], 2),
        "ADX": round(curr["ADX"], 2),
        "Aggressive SL (18 EMA)": round(curr["EMA18"], 2),
        "Swing SL (50 EMA)": round(curr["EMA50"], 2),
        "Status": "Confirmed Bullish",
    }


def analyze_symbol(symbol: str, config: BullishScannerConfig) -> dict[str, Any] | None:
    """Fetches stock data using yfinance and runs indicator evaluation."""
    # Ensure ticker has appropriate extension for NSE if not provided
    formatted_ticker = symbol if ("." in symbol or symbol.startswith("^")) else f"{symbol}.NS"
    try:
        from yahoo_bar_store import get_daily_history

        def live(_symbol: str, period: str) -> pd.DataFrame:
            ticker = yf.Ticker(formatted_ticker)
            return ticker.history(period=period, interval="1d")

        df = get_daily_history(symbol, period=config.lookback_period, live_loader=live)

        if df.empty or len(df) < config.ema_macro:
            logging.warning(f"Insufficient historical data for {symbol}")
            return None

        df = calculate_indicators(df, config)
        return evaluate_bullish_bias(symbol, df, config)

    except Exception as exc:
        logging.warning(f"Error processing ticker {symbol}: {exc}")
        return None


def excel_engine_for_path(
    path: str | Path,
    engine: str | None = None,
    *,
    mode: str = "reader",
) -> str | None:
    """Return a pandas Excel engine from an explicit value or the file suffix."""
    if engine:
        normalized = engine.strip().lower()
        if normalized in TEXT_ENGINES:
            return None
        return normalized

    suffix = Path(path).suffix.lower()
    mapping = EXCEL_READ_ENGINES if mode == "reader" else EXCEL_WRITE_ENGINES
    return mapping.get(suffix)


def sniff_excel_engine(path: str | Path) -> str | None:
    """Infer an Excel engine from file signatures when the suffix is missing or unknown."""
    source = Path(path)
    with source.open("rb") as handle:
        peek = handle.read(8)

    if peek.startswith(OLE_COMPOUND_SIGNATURE):
        return "xlrd"
    if not peek.startswith(ZIP_SIGNATURE):
        return None

    try:
        with zipfile.ZipFile(source) as archive:
            names = {name.replace("\\", "/").lower() for name in archive.namelist()}
    except zipfile.BadZipFile:
        return None

    if "xl/workbook.bin" in names:
        return "pyxlsb"
    if "content.xml" in names:
        return "odf"
    if "xl/workbook.xml" in names:
        return "openpyxl"
    return None


def _read_first_html_table(path: Path) -> pd.DataFrame:
    tables = pd.read_html(path)
    if not tables:
        raise ValueError(f"No HTML tables found in '{path}'")
    return tables[0]


def _has_ticker_column(df: pd.DataFrame) -> bool:
    return any(column in df.columns for column in TICKER_COLUMNS)


def _try_read_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    return frame if _has_ticker_column(frame) else None


def _try_read_html(path: Path) -> pd.DataFrame | None:
    try:
        frame = _read_first_html_table(path)
    except Exception:
        return None
    return frame if _has_ticker_column(frame) else None


def read_input_table(path: str | Path, engine: str | None = None) -> pd.DataFrame:
    """Load a ticker universe from Excel, CSV, or HTML using an explicit or inferred engine."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Input file not found: {source}")

    requested = (engine or "").strip().lower() or None
    suffix = source.suffix.lower()

    if requested == "csv" or (requested is None and suffix == ".csv"):
        return pd.read_csv(source)

    if requested in {"html", "htm"} or (requested is None and suffix in {".html", ".htm"}):
        return _read_first_html_table(source)

    excel_engine = excel_engine_for_path(source, requested)
    if excel_engine is None:
        excel_engine = sniff_excel_engine(source)

    if excel_engine:
        try:
            return pd.read_excel(source, engine=excel_engine)
        except Exception as exc:
            if requested:
                raise ValueError(
                    f"Failed to read '{source}' with engine '{excel_engine}': {exc}"
                ) from exc
            # NSE-style .xls files are often HTML tables, not BIFF workbooks.
            fallback = _try_read_html(source)
            if fallback is None:
                fallback = _try_read_csv(source)
            if fallback is not None:
                return fallback
            raise ValueError(
                "Excel file format cannot be determined, you must specify an engine "
                f"manually. Failed to read '{source}' with engine '{excel_engine}': {exc}"
            ) from exc

    fallback = _try_read_csv(source)
    if fallback is None:
        fallback = _try_read_html(source)
    if fallback is not None:
        return fallback

    raise ValueError(
        "Excel file format cannot be determined, you must specify an engine manually. "
        f"Pass --engine (openpyxl, xlrd, pyxlsb, odf, csv, html) for '{source}'."
    )


def extract_tickers(df: pd.DataFrame) -> list[str]:
    """Return ticker symbols from common column names used by NSE lists."""
    for column in TICKER_COLUMNS:
        if column in df.columns:
            values = df[column].dropna().astype(str).str.strip()
            return [value for value in values if value and value.lower() != "nan"]

    raise KeyError(
        "Input file must contain a 'Ticker' or 'Symbol' column. "
        f"Found columns: {list(df.columns)}"
    )


def write_results(df: pd.DataFrame, path: str | Path, engine: str | None = None) -> None:
    """Write scan results using a CSV writer or an explicit Excel engine."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    requested = (engine or "").strip().lower() or None
    suffix = destination.suffix.lower()

    if requested == "csv" or suffix == ".csv":
        df.to_csv(destination, index=False)
        return

    excel_engine = excel_engine_for_path(destination, requested, mode="writer") or "openpyxl"
    df.to_excel(destination, index=False, engine=excel_engine)


BULLISH_RESULT_COLUMNS = [
    "Ticker",
    "Close Price",
    "EMA9",
    "EMA18",
    "EMA50",
    "EMA200",
    "CCI",
    "ADX",
    "Aggressive SL (18 EMA)",
    "Swing SL (50 EMA)",
    "Status",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tickers from Excel file for Bullish EMA/CCI/ADX Setup.")
    parser.add_argument("--input", default="NSE_Stocks_List_20251230_1617.xlsx", help="Path to input Excel or CSV file.")
    parser.add_argument("--output", default="Bullish_Bias_Analysis.xlsx", help="Output path for results.")
    parser.add_argument(
        "--engine",
        default=None,
        help=(
            "Input file engine: openpyxl (.xlsx/.xlsm), xlrd (.xls), pyxlsb (.xlsb), "
            "odf (.ods), csv, or html. Auto-detected from the file when omitted."
        ),
    )
    args = parser.parse_args()

    config = BullishScannerConfig()

    try:
        df_input = read_input_table(args.input, engine=args.engine)
        tickers = extract_tickers(df_input)
    except Exception as e:
        print(f"Excel Error: {e}")
        return

    results = []
    print(f"Scanning {len(tickers)} stocks for Bullish Structure (EMAs + CCI + ADX)...")

    for ticker in tickers:
        data = analyze_symbol(ticker, config)
        if data:
            results.append(data)

    output_path = Path(args.output)
    if not results:
        print("No qualifying bullish setups found.")
        write_results(pd.DataFrame(columns=BULLISH_RESULT_COLUMNS), output_path)
        print(f"Scan complete. Found 0 bullish setup(s). Saved to '{output_path}'.")
        return

    df_results = pd.DataFrame(results).sort_values(by=["ADX", "CCI"], ascending=False)
    write_results(df_results, output_path)

    print(f"Scan complete. Found {len(results)} bullish setup(s). Saved to '{output_path}'.")


if __name__ == "__main__":
    main()