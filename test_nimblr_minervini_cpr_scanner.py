import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import nimblr_minervini_cpr_scanner as scanner


REPO = Path(__file__).resolve().parent
NIFTY_CSV = REPO / "ind_nifty500list.csv"


def short_config(**overrides) -> scanner.CombinedScannerConfig:
    values = dict(
        ema_fast=3,
        ema_mid=5,
        ema_slow=8,
        ema_150=10,
        ema_200=12,
        cci_period=5,
        atr_period=5,
        ema200_lookback=3,
        week52_bars=10,
        volume_ema=5,
        combine_mode="all",
        min_sections=3,
    )
    values.update(overrides)
    return scanner.CombinedScannerConfig(**values)


def indicator_row(**values) -> dict:
    row = {
        "Open": 100.0,
        "High": 110.0,
        "Low": 95.0,
        "Close": 108.0,
        "Volume": 2_000_000.0,
        "EMA10": 106.0,
        "EMA20": 104.0,
        "EMA50": 100.0,
        "EMA150": 96.0,
        "EMA200": 90.0,
        "EMA200_1M_AGO": 85.0,
        "EMA_VOL20": 1_000_000.0,
        "CCI": 120.0,
        "ATR": 2.0,
        "ATR_BREAKOUT": 108.0,
        "PIVOT": 100.0,
        "CPR_TOP": 101.0,
        "CPR_BOTTOM": 99.0,
        "BODY": 8.0,
        "RANGE": 15.0,
        "BULLISH_BODY": True,
        "LOW_52W": 70.0,
        "HIGH_52W": 120.0,
    }
    row.update(values)
    return row


def two_bar_frame(prev: dict, curr: dict, extra: list[dict] | None = None) -> pd.DataFrame:
    rows = extra or []
    rows = list(rows)
    rows.extend([prev, curr])
    index = pd.bdate_range("2026-01-02", periods=len(rows))
    return pd.DataFrame(rows, index=index)


def uptrend_ohlcv(periods: int = 40, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=periods)
    close = start + np.arange(periods) * step
    high = close + 1.5
    low = close - 1.5
    open_ = close - 0.8
    volume = np.full(periods, 1_000_000.0)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


def passing_ohlcv(
    config: scanner.CombinedScannerConfig,
    extra_sessions: int = 0,
) -> pd.DataFrame:
    """Build daily bars that satisfy Nimblr + Minervini + CPR after indicators are applied.

    The last original session is the signal bar. ``extra_sessions`` appends later
    trading days so backtests can buy the next open and hold to a later close.
    """
    periods = max(config.minimum_history + 10, 32)
    frame = uptrend_ohlcv(periods=periods, start=80.0, step=0.4)
    base = float(frame.iloc[-4]["Close"])
    # Wide prior day so yesterday's close can sit at the CPR top, then a CCI/CPR breakout.
    frame.iloc[-3] = {
        "Open": base,
        "High": base + 12.0,
        "Low": base - 12.0,
        "Close": base - 1.0,
        "Volume": 800_000.0,
    }
    frame.iloc[-2] = {
        "Open": base - 2.0,
        "High": base + 0.3,
        "Low": base - 2.2,
        "Close": base,
        "Volume": 2_000_000.0,
    }
    frame.iloc[-1] = {
        "Open": base + 0.5,
        "High": base + 40.0,
        "Low": base + 0.3,
        "Close": base + 38.0,
        "Volume": 4_000_000.0,
    }
    if extra_sessions <= 0:
        return frame
    last_close = float(frame.iloc[-1]["Close"])
    rows = []
    for step in range(1, extra_sessions + 1):
        close = last_close + step
        rows.append(
            {
                "Open": close - 0.5,
                "High": close + 0.8,
                "Low": close - 0.8,
                "Close": close,
                "Volume": 1_500_000.0,
            }
        )
    extra = pd.DataFrame(
        rows,
        index=pd.bdate_range(frame.index[-1] + pd.Timedelta(days=1), periods=extra_sessions),
    )
    return pd.concat([frame, extra])


class ExcelEngineSelectionTests(unittest.TestCase):
    def test_engine_is_resolved_from_common_excel_suffixes(self) -> None:
        self.assertEqual(scanner.excel_engine_for_path("list.xlsx"), "openpyxl")
        self.assertEqual(scanner.excel_engine_for_path("list.xls"), "xlrd")
        self.assertIsNone(scanner.excel_engine_for_path("list.csv"))

    def test_write_engine_defaults_to_openpyxl_for_xlsx(self) -> None:
        self.assertEqual(
            scanner.excel_engine_for_path("out.xlsx", mode="writer"),
            "openpyxl",
        )


class NiftyCsvUniverseTests(unittest.TestCase):
    def test_repo_nifty500_csv_loads_tickers(self) -> None:
        self.assertTrue(NIFTY_CSV.exists())
        tickers = scanner.load_universe(NIFTY_CSV, engine="csv")
        self.assertGreater(len(tickers), 400)
        self.assertIn("TCS", tickers)
        self.assertIn("RELIANCE", tickers)
        self.assertEqual(scanner.DEFAULT_INPUT, "ind_nifty500list.csv")

    def test_csv_input_does_not_use_read_excel(self) -> None:
        with patch.object(pd, "read_excel") as read_excel:
            frame = scanner.read_input_table(NIFTY_CSV)

        read_excel.assert_not_called()
        self.assertIn("Ticker", frame.columns)


class SymbolHelperTests(unittest.TestCase):
    def test_yahoo_symbol_appends_ns_for_nse_cash_names(self) -> None:
        self.assertEqual(scanner.yahoo_symbol("TCS"), "TCS.NS")
        self.assertEqual(scanner.yahoo_symbol("tcs"), "TCS.NS")
        self.assertEqual(scanner.yahoo_symbol("360ONE.NS"), "360ONE.NS")
        self.assertEqual(scanner.yahoo_symbol("^NSEI"), "^NSEI")

    def test_display_symbol_strips_ns(self) -> None:
        self.assertEqual(scanner.display_symbol("TCS.NS"), "TCS")
        self.assertEqual(scanner.display_symbol("TCS"), "TCS")


class TimezoneAndEmptyOutputTests(unittest.TestCase):
    def test_normalize_ohlcv_strips_asia_kolkata_timezone(self) -> None:
        index = pd.date_range("2026-08-25", periods=3, freq="D", tz="Asia/Kolkata")
        frame = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1_000.0, 1_100.0, 1_200.0],
            },
            index=index,
        )
        out = scanner.normalize_ohlcv(frame)
        self.assertIsNone(out.index.tz)
        self.assertEqual(out.index[0], pd.Timestamp("2026-08-25"))
        self.assertEqual(out.index[-1], pd.Timestamp("2026-08-27"))

    def test_backtest_does_not_fail_on_tz_aware_yahoo_index(self) -> None:
        config = short_config()
        frame = passing_ohlcv(config, extra_sessions=2)
        frame.index = frame.index.tz_localize("Asia/Kolkata")
        trades = scanner.backtest_symbol(frame, config)
        self.assertGreaterEqual(len(trades), 1)
        self.assertAlmostEqual(trades[-1]["Entry"], float(frame.iloc[-2]["Open"]))

    def test_empty_scan_still_writes_header_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.xlsx"
            scanner.write_results(scanner.format_results([]), path)
            loaded = pd.read_excel(path, engine="openpyxl")
            self.assertEqual(list(loaded.columns)[:3], ["Ticker", "Date", "Close"])
            self.assertEqual(len(loaded), 0)


class NewerListingHistoryTests(unittest.TestCase):
    def test_default_minimum_history_allows_sub_year_listings(self) -> None:
        config = scanner.CombinedScannerConfig()
        self.assertEqual(config.minimum_history, 223)
        self.assertLess(config.minimum_history, 254)

    def test_52_week_uses_six_month_floor_for_newer_listings(self) -> None:
        frame = uptrend_ohlcv(200)
        out = scanner.calculate_indicators(frame, scanner.CombinedScannerConfig())
        last = out.iloc[-1]
        self.assertTrue(np.isfinite(last["HIGH_52W"]))
        self.assertTrue(np.isfinite(last["LOW_52W"]))

    def test_short_history_is_summarized_not_warned(self) -> None:
        config = short_config()
        skipped: list[str] = []
        with self.assertNoLogs("nimblr_minervini_cpr_scanner", level="WARNING"):
            results = scanner.scan_tickers(
                ["TENNIND", "URBANCO"],
                config,
                history_loader=lambda *_args, **_kwargs: pd.DataFrame(),
                skipped=skipped,
            )
        self.assertEqual(results, [])
        self.assertEqual(skipped, ["TENNIND", "URBANCO"])


class IndicatorMathTests(unittest.TestCase):
    def test_ema_matches_pandas_ewm(self) -> None:
        close = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        expected = close.ewm(span=3, adjust=False).mean()
        pd.testing.assert_series_equal(scanner._ema(close, 3), expected)

    def test_cci_uses_typical_price_sma_and_mad(self) -> None:
        frame = pd.DataFrame(
            {
                "Open": [10, 11, 12, 13, 14],
                "High": [11, 12, 13, 14, 16],
                "Low": [9, 10, 11, 12, 13],
                "Close": [10.5, 11.5, 12.5, 13.5, 15.5],
                "Volume": [100] * 5,
            },
            index=pd.bdate_range("2026-01-02", periods=5),
        )
        config = short_config(cci_period=3)
        out = scanner.calculate_indicators(frame, config)
        last = out.iloc[-1]
        typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
        window = typical.iloc[-3:]
        sma = window.mean()
        mad = np.mean(np.abs(window - sma))
        expected = (typical.iloc[-1] - sma) / (0.015 * mad)
        self.assertAlmostEqual(float(last["CCI"]), float(expected), places=6)

    def test_cpr_uses_previous_session_high_low_close(self) -> None:
        frame = pd.DataFrame(
            {
                "Open": [100.0, 108.0],
                "High": [110.0, 120.0],
                "Low": [90.0, 107.0],
                "Close": [105.0, 118.0],
                "Volume": [1_000.0, 2_000.0],
            },
            index=pd.bdate_range("2026-01-05", periods=2),
        )
        out = scanner.calculate_indicators(frame, short_config())
        prev_high, prev_low, prev_close = 110.0, 90.0, 105.0
        pivot = (prev_high + prev_low + prev_close) / 3.0
        bc = (prev_high + prev_low) / 2.0
        tc = (2.0 * pivot) - bc
        self.assertAlmostEqual(float(out.iloc[-1]["PIVOT"]), pivot)
        self.assertAlmostEqual(float(out.iloc[-1]["CPR_TOP"]), max(tc, bc))
        self.assertAlmostEqual(float(out.iloc[-1]["CPR_BOTTOM"]), min(tc, bc))

    def test_atr_breakout_is_ema10_plus_multiplier_times_atr(self) -> None:
        frame = uptrend_ohlcv(20)
        config = short_config(atr_breakout_multiplier=1.5)
        out = scanner.calculate_indicators(frame, config)
        last = out.iloc[-1]
        self.assertAlmostEqual(
            float(last["ATR_BREAKOUT"]),
            float(last["EMA10"] + 1.5 * last["ATR"]),
            places=6,
        )


class SectionEvaluationTests(unittest.TestCase):
    def test_nimblr_passes_on_cci_cross_stacked_emas_and_bodies(self) -> None:
        prev = indicator_row(CCI=90.0, BULLISH_BODY=True, Close=107.0, ATR_BREAKOUT=106.0)
        curr = indicator_row(CCI=130.0, BULLISH_BODY=True, Close=112.0, ATR_BREAKOUT=110.0)
        result = scanner.evaluate_nimblr(two_bar_frame(prev, curr), 1, short_config())
        self.assertTrue(result.passed, result.failed_names)

    def test_nimblr_fails_when_cci_does_not_cross(self) -> None:
        prev = indicator_row(CCI=110.0, BULLISH_BODY=True)
        curr = indicator_row(CCI=140.0, BULLISH_BODY=True, Close=112.0, ATR_BREAKOUT=110.0)
        result = scanner.evaluate_nimblr(two_bar_frame(prev, curr), 1, short_config())
        self.assertFalse(result.passed)
        self.assertIn("CCI(34) crossed above 100", result.failed_names)

    def test_minervini_passes_trend_template_with_volume(self) -> None:
        curr = indicator_row()
        result = scanner.evaluate_minervini(two_bar_frame(indicator_row(), curr), 1, short_config())
        self.assertTrue(result.passed, result.failed_names)

    def test_minervini_requires_close_near_52_week_high(self) -> None:
        curr = indicator_row(Close=80.0, HIGH_52W=120.0, LOW_52W=50.0)
        result = scanner.evaluate_minervini(two_bar_frame(indicator_row(), curr), 1, short_config())
        self.assertFalse(result.passed)
        self.assertIn("Close >= 75% of 52-week high", result.failed_names)

    def test_cpr_passes_on_close_cross_of_cpr_top(self) -> None:
        prev = indicator_row(Close=100.0, CPR_TOP=101.0, Open=99.0)
        curr = indicator_row(Close=108.0, CPR_TOP=102.0, Open=103.0, Volume=2_000_000.0)
        result = scanner.evaluate_cpr(two_bar_frame(prev, curr), 1, short_config())
        self.assertTrue(result.passed, result.failed_names)

    def test_cpr_optional_previous_high_breakout(self) -> None:
        prev = indicator_row(Close=100.0, CPR_TOP=101.0, High=109.0)
        curr = indicator_row(Close=108.0, CPR_TOP=102.0, Open=103.0)
        config = short_config(require_previous_high_breakout=True)
        result = scanner.evaluate_cpr(two_bar_frame(prev, curr), 1, config)
        self.assertFalse(result.passed)
        self.assertIn("Close above previous high", result.failed_names)


class CombinedAndLookaheadTests(unittest.TestCase):
    def _passing_history(self) -> tuple[scanner.CombinedScannerConfig, pd.DataFrame]:
        config = short_config()
        filler = [indicator_row(Close=90.0 + i, EMA200=80.0 + i * 0.2) for i in range(config.minimum_history)]
        prev = indicator_row(CCI=80.0, Close=100.0, CPR_TOP=101.0, Open=99.0, BULLISH_BODY=True)
        curr = indicator_row(
            CCI=140.0,
            Close=112.0,
            Open=104.0,
            ATR_BREAKOUT=110.0,
            CPR_TOP=102.0,
            BULLISH_BODY=True,
            Volume=2_000_000.0,
        )
        nxt = indicator_row(Open=113.5, Close=114.0)
        frame = two_bar_frame(prev, curr, extra=filler)
        frame = pd.concat([frame, two_bar_frame(nxt, nxt).iloc[[0]]])
        frame.index = pd.bdate_range("2024-01-02", periods=len(frame))
        return config, frame

    def test_all_mode_requires_every_section(self) -> None:
        config, frame = self._passing_history()
        snapshot = scanner.evaluate_combined(frame, config, index=len(frame) - 2)
        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot["Nimblr"])
        self.assertTrue(snapshot["Minervini"])
        self.assertTrue(snapshot["CPR"])
        self.assertTrue(snapshot["Qualified"])
        self.assertEqual(snapshot["Sections_Passed"], 3)
        self.assertAlmostEqual(snapshot["Next_Open"], 113.5)

    def test_score_mode_can_qualify_on_partial_sections(self) -> None:
        config, frame = self._passing_history()
        frame.iloc[-2, frame.columns.get_loc("CCI")] = 40.0
        all_mode = scanner.evaluate_combined(frame, config, index=len(frame) - 2)
        self.assertFalse(all_mode["Qualified"])
        score = scanner.evaluate_combined(
            frame,
            short_config(combine_mode="score", min_sections=2),
            index=len(frame) - 2,
        )
        self.assertGreaterEqual(score["Sections_Passed"], 2)
        self.assertTrue(score["Qualified"])

    def test_future_bars_do_not_change_signal_qualification(self) -> None:
        config, frame = self._passing_history()
        index = len(frame) - 2
        before = scanner.evaluate_combined(frame, config, index=index)
        mutated = frame.copy()
        mutated.iloc[-1, mutated.columns.get_loc("Close")] = 9_999.0
        mutated.iloc[-1, mutated.columns.get_loc("High")] = 10_000.0
        mutated.iloc[-1, mutated.columns.get_loc("Open")] = 9_000.0
        after = scanner.evaluate_combined(mutated, config, index=index)
        self.assertEqual(before["Qualified"], after["Qualified"])
        self.assertEqual(before["Nimblr"], after["Nimblr"])
        self.assertEqual(before["Minervini"], after["Minervini"])
        self.assertEqual(before["CPR"], after["CPR"])
        self.assertEqual(before["Close"], after["Close"])
        self.assertNotEqual(before["Next_Open"], after["Next_Open"])

    def test_backtest_buys_next_open_not_signal_close(self) -> None:
        config = short_config()
        frame = passing_ohlcv(config, extra_sessions=2)
        trades = scanner.backtest_symbol(frame, config)
        self.assertGreaterEqual(len(trades), 1)
        last = trades[-1]
        signal_index = list(frame.index.date).index(pd.Timestamp(last["Signal_Date"]).date())
        self.assertAlmostEqual(last["Entry"], float(frame.iloc[signal_index + 1]["Open"]))
        self.assertNotAlmostEqual(last["Entry"], float(frame.iloc[signal_index]["Close"]))
        self.assertAlmostEqual(last["Exit"], float(frame.iloc[-1]["Close"]))
        self.assertEqual(last["Exit_Date"], pd.Timestamp(frame.index[-1]).date().isoformat())

    def test_backtest_summary_counts_one_share_pnl(self) -> None:
        summary = scanner.summarize_backtest(
            [
                {"Entry": 100.0, "Exit": 110.0, "PnL": 10.0},
                {"Entry": 50.0, "Exit": 40.0, "PnL": -10.0},
            ]
        )
        self.assertEqual(summary["Trades"], 2)
        self.assertEqual(summary["Wins"], 1)
        self.assertEqual(summary["Losses"], 1)
        self.assertAlmostEqual(summary["PnL"], 0.0)
        self.assertAlmostEqual(summary["Cost"], 150.0)


class AnalyzeAndCliTests(unittest.TestCase):
    def test_analyze_symbol_qualifies_crafted_history(self) -> None:
        config = short_config()
        history = passing_ohlcv(config)
        result = scanner.analyze_symbol("TCS", config, history=history)
        self.assertIsNotNone(result)
        self.assertEqual(result["Ticker"], "TCS")
        self.assertTrue(result["Qualified"], result.get("Failed_Conditions"))
        self.assertTrue(result["Nimblr"])
        self.assertTrue(result["Minervini"])
        self.assertTrue(result["CPR"])

    def test_scan_tickers_respects_include_failures(self) -> None:
        config = short_config()
        history = uptrend_ohlcv(40, step=0.05)
        results = scanner.scan_tickers(
            ["FAILCO"],
            config,
            include_failures=True,
            history_loader=lambda *_args, **_kwargs: history,
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["Qualified"])
        self.assertTrue(results[0]["Failed_Conditions"])

    def test_main_reads_nifty_csv_and_writes_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "ind_nifty500list.csv"
            out = Path(tmp) / "Nimblr_Minervini_CPR_Scan.xlsx"
            pd.DataFrame(
                {
                    "Company Name": ["Tata Consultancy Services Ltd."],
                    "Industry": ["IT"],
                    "Ticker": ["TCS"],
                    "Series": ["EQ"],
                    "ISIN Code": ["INE467B01029"],
                }
            ).to_csv(src, index=False)

            hit = {
                "Ticker": "TCS",
                "Date": "2026-08-28",
                "Close": 3000.0,
                "CCI": 120.0,
                "EMA10": 2900.0,
                "EMA20": 2800.0,
                "EMA50": 2700.0,
                "EMA150": 2500.0,
                "EMA200": 2400.0,
                "ATR": 40.0,
                "ATR_Breakout": 2940.0,
                "CPR_Top": 2980.0,
                "CPR_Pivot": 2970.0,
                "CPR_Bottom": 2960.0,
                "Low_52W": 1800.0,
                "High_52W": 3200.0,
                "Volume": 1_000_000.0,
                "Volume_EMA20": 800_000.0,
                "Next_Open": 3010.0,
                "Suggested_Stop": 2960.0,
                "Risk_Per_Share": 40.0,
                "Nimblr": True,
                "Minervini": True,
                "CPR": True,
                "Sections_Passed": 3,
                "Qualified": True,
                "Failed_Conditions": "",
            }

            with patch.object(scanner, "analyze_symbol", return_value=hit):
                captured = io.StringIO()
                with patch("sys.stdout", captured):
                    code = scanner.main(
                        ["--input", str(src), "--output", str(out), "--engine", "csv"]
                    )

            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            saved = pd.read_excel(out, engine="openpyxl")
            self.assertEqual(list(saved["Ticker"]), ["TCS"])
            self.assertTrue(bool(saved.loc[0, "Qualified"]))
            self.assertIn("Scanning 1 Nifty 500 names", captured.getvalue())
            self.assertIn(str(src), captured.getvalue())

    def test_default_cli_input_is_ind_nifty500list_csv(self) -> None:
        args = scanner.parse_args([])
        self.assertEqual(args.input, "ind_nifty500list.csv")
        self.assertEqual(args.mode, "scan")
        self.assertEqual(args.combine_mode, "all")


class YahooFetchRecoveryTests(unittest.TestCase):
    def tearDown(self) -> None:
        scanner.reset_yahoo_http_session()

    def test_lookback_seconds_parses_yahoo_period_tokens(self) -> None:
        self.assertEqual(scanner.lookback_seconds("2y"), 2 * 365 * 86400)
        self.assertEqual(scanner.lookback_seconds("3mo"), 3 * 30 * 86400)
        self.assertEqual(scanner.lookback_seconds("5d"), 5 * 86400)
        with self.assertRaises(ValueError):
            scanner.lookback_seconds("max")

    def test_config_rejects_invalid_retry_settings(self) -> None:
        with self.assertRaises(ValueError):
            scanner.CombinedScannerConfig(max_retries=0)
        with self.assertRaises(ValueError):
            scanner.CombinedScannerConfig(retry_delay=-0.1)
        with self.assertRaises(ValueError):
            scanner.CombinedScannerConfig(request_delay=-1)

    def test_frame_from_yahoo_chart_uses_adjclose_when_present(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1_700_000_000, 1_700_086_400],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0, 110.0],
                                    "high": [105.0, 120.0],
                                    "low": [99.0, 108.0],
                                    "close": [102.0, 118.0],
                                    "volume": [1_000, 2_000],
                                }
                            ],
                            "adjclose": [{"adjclose": [101.0, 117.0]}],
                        },
                    }
                ]
            }
        }
        frame = scanner.frame_from_yahoo_chart(payload)
        self.assertEqual(len(frame), 2)
        self.assertAlmostEqual(float(frame["Close"].iloc[-1]), 117.0)
        self.assertAlmostEqual(float(frame["Volume"].iloc[-1]), 2000.0)

    def test_fetch_history_retries_empty_yfinance_then_succeeds(self) -> None:
        history = uptrend_ohlcv(5)
        config = short_config(max_retries=3, retry_delay=0.0)
        with patch.object(
            scanner,
            "_download_yahoo_history",
            side_effect=[pd.DataFrame(), history],
        ) as download:
            result = scanner.fetch_history("360ONE", config)
        self.assertEqual(len(result), len(history))
        self.assertEqual(download.call_count, 2)

    def test_fetch_history_falls_back_to_chart_when_yfinance_is_empty(self) -> None:
        history = uptrend_ohlcv(4)
        config = short_config(max_retries=1, retry_delay=0.0)
        with patch.object(scanner, "_ticker_history", return_value=pd.DataFrame()):
            with patch.object(scanner, "_yf_download_history", return_value=pd.DataFrame()):
                with patch.object(scanner, "history_from_chart", return_value=history) as chart:
                    result = scanner.fetch_history("ABB", config)
        self.assertEqual(list(result["Close"]), list(history["Close"]))
        chart.assert_called_once()
        self.assertEqual(chart.call_args.args[0], "ABB")

    def test_fetch_history_returns_empty_after_retries_exhausted(self) -> None:
        config = short_config(max_retries=2, retry_delay=0.0)
        with patch.object(scanner, "_download_yahoo_history", return_value=pd.DataFrame()) as download:
            result = scanner.fetch_history("3MINDIA", config)
        self.assertTrue(result.empty)
        self.assertEqual(download.call_count, 2)

    def test_yahoo_http_session_is_reused(self) -> None:
        first = scanner.yahoo_http_session()
        second = scanner.yahoo_http_session()
        self.assertIs(first, second)

    def test_history_from_chart_hits_query1_then_query2(self) -> None:
        payload = {"chart": {"result": [{"timestamp": [], "indicators": {"quote": [{}]}}]}}
        session = Mock()
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = payload
        second = Mock()
        second.raise_for_status.return_value = None
        second.json.return_value = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1_700_000_000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [10.0],
                                    "high": [11.0],
                                    "low": [9.0],
                                    "close": [10.5],
                                    "volume": [100],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        session.get.side_effect = [first, second]
        frame = scanner.history_from_chart("TCS", "5d", session=session)
        self.assertEqual(len(frame), 1)
        self.assertEqual(session.get.call_count, 2)
        self.assertIn("query1.finance.yahoo.com", session.get.call_args_list[0].args[0])
        self.assertIn("query2.finance.yahoo.com", session.get.call_args_list[1].args[0])
        self.assertAlmostEqual(float(frame["Close"].iloc[0]), 10.5)


if __name__ == "__main__":
    unittest.main()
