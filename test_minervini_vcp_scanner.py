import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import minervini_vcp_scanner as scanner


REPO = Path(__file__).resolve().parent
NIFTY_CSV = REPO / "ind_nifty500list.csv"


def short_config(**overrides) -> scanner.VCPScannerConfig:
    values = dict(
        ema_fast=3,
        ema_mid=5,
        ema_slow=8,
        ema_150=10,
        ema_200=12,
        cci_period=5,
        atr_period=5,
        ema200_lookback=3,
        week52_bars=35,
        volume_ema=5,
        base_bars=25,
        pivot_left=2,
        pivot_right=2,
        min_contractions=2,
        combine_mode="all",
        min_sections=2,
    )
    values.update(overrides)
    return scanner.VCPScannerConfig(**values)


def trend_row(**values) -> dict:
    row = {
        "Open": 100.0,
        "High": 110.0,
        "Low": 95.0,
        "Close": 108.0,
        "Volume": 500_000.0,
        "EMA50": 102.0,
        "EMA150": 98.0,
        "EMA200": 90.0,
        "EMA200_1M_AGO": 85.0,
        "EMA_VOL20": 1_000_000.0,
        "ATR": 2.0,
        "LOW_52W": 70.0,
        "HIGH_52W": 120.0,
    }
    row.update(values)
    return row


def build_vcp_base_frame(config: scanner.VCPScannerConfig) -> pd.DataFrame:
    """Synthetic strong uptrend ending in two tightening VCP contractions."""
    periods = config.minimum_history + 10
    index = pd.bdate_range("2024-01-02", periods=periods)
    warmup = periods - config.base_bars
    warmup_close = np.linspace(60.0, 104.0, warmup)
    warmup_lows = warmup_close - 1.0
    if warmup:
        warmup_lows[0] = 58.0

    base_pattern = [
        (104.5, 106.0, 104.0, 105.5, 1_100_000),
        (105.5, 107.0, 105.0, 106.5, 1_000_000),
        (106.5, 108.0, 106.0, 107.5, 950_000),   # pivot high 1
        (107.5, 107.8, 105.5, 106.0, 900_000),
        (106.0, 106.5, 103.5, 104.0, 850_000),
        (104.0, 104.5, 101.0, 101.5, 800_000),
        (101.5, 102.0, 99.0, 99.5, 750_000),     # pivot low 1 (~7.9%)
        (99.5, 101.0, 99.0, 100.5, 700_000),
        (100.5, 102.0, 100.0, 101.5, 650_000),
        (101.5, 103.0, 101.0, 102.5, 600_000),
        (102.5, 104.0, 102.0, 103.5, 550_000),
        (103.5, 105.0, 103.0, 104.5, 500_000),   # pivot high 2
        (104.5, 104.7, 103.8, 104.0, 480_000),
        (104.0, 104.2, 103.2, 103.5, 460_000),
        (103.5, 103.8, 102.8, 103.0, 440_000),
        (103.0, 103.2, 102.0, 102.3, 420_000),   # pivot low 2 (~2.6%), tight ranges
        (102.3, 103.5, 102.1, 103.0, 400_000),
        (103.0, 104.0, 102.8, 103.8, 380_000),
        (103.8, 104.5, 103.5, 104.2, 360_000),
        (104.2, 105.0, 104.0, 104.8, 340_000),
        (104.8, 105.2, 104.5, 105.0, 320_000),
        (105.0, 106.0, 104.8, 105.8, 300_000),
        (105.8, 107.0, 105.5, 106.8, 280_000),
        (106.8, 108.0, 106.5, 107.8, 260_000),
        (107.8, 111.0, 107.5, 110.8, 220_000),
    ]
    base_pattern = base_pattern[: config.base_bars]

    rows = []
    for idx, close in enumerate(warmup_close):
        rows.append(
            {
                "Open": close - 0.4,
                "High": close + 0.8,
                "Low": warmup_lows[idx],
                "Close": close,
                "Volume": 2_000_000.0,
            }
        )
    for open_, high, low, close, volume in base_pattern:
        rows.append(
            {
                "Open": open_,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": float(volume),
            }
        )
    return pd.DataFrame(rows, index=index)


class PivotTests(unittest.TestCase):
    def test_pullback_depths_require_alternating_high_low(self) -> None:
        pivots = [
            scanner.PivotPoint(1, "H", 110.0),
            scanner.PivotPoint(3, "L", 100.0),
            scanner.PivotPoint(6, "H", 112.0),
            scanner.PivotPoint(8, "L", 105.0),
        ]
        depths = scanner._pullback_depths(pivots)
        self.assertEqual(len(depths), 2)
        self.assertAlmostEqual(depths[0][2], (110.0 - 100.0) / 110.0 * 100.0)
        self.assertLess(depths[1][2], depths[0][2])


class StageTests(unittest.TestCase):
    def test_trend_template_passes_on_strong_uptrend_row(self) -> None:
        frame = pd.DataFrame([trend_row()], index=pd.bdate_range("2026-01-02", periods=1))
        result = scanner.evaluate_trend_template(frame, 0, short_config())
        self.assertTrue(result.passed)

    def test_trend_template_fails_when_below_ema200(self) -> None:
        frame = pd.DataFrame(
            [trend_row(Close=80.0, EMA200=90.0)],
            index=pd.bdate_range("2026-01-02", periods=1),
        )
        result = scanner.evaluate_trend_template(frame, 0, short_config())
        self.assertFalse(result.passed)

    def test_vcp_passes_on_tightening_pullbacks(self) -> None:
        config = short_config()
        raw = build_vcp_base_frame(config)
        frame = scanner.calculate_indicators(raw, config)
        result = scanner.evaluate_vcp(frame, len(frame) - 1, config)
        self.assertTrue(result.passed, result.failed_names)

    def test_evaluate_scan_requires_both_stages(self) -> None:
        config = short_config()
        raw = build_vcp_base_frame(config)
        frame = scanner.calculate_indicators(raw, config)
        snapshot = scanner.evaluate_scan(frame, config)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertTrue(snapshot["Stage2_Trend"])
        self.assertTrue(snapshot["VCP"])
        self.assertTrue(snapshot["Qualified"])


class CliTests(unittest.TestCase):
    def test_main_writes_xlsx_from_csv_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "tickers.csv"
            out = Path(tmp) / "Minervini_VCP_Scan.xlsx"
            pd.DataFrame(
                {
                    "Ticker": ["TCS", "INFY"],
                    "Company Name": ["Tata Consultancy Services Ltd.", "Infosys Ltd."],
                }
            ).to_csv(src, index=False)

            def fake_analyze(symbol: str, config: scanner.VCPScannerConfig, history=None):
                return {
                    "Ticker": symbol,
                    "Date": "2026-08-30",
                    "Close": 100.0,
                    "EMA50": 95.0,
                    "EMA150": 90.0,
                    "EMA200": 85.0,
                    "ATR": 2.0,
                    "Low_52W": 70.0,
                    "High_52W": 110.0,
                    "Volume": 500_000.0,
                    "Volume_EMA20": 800_000.0,
                    "Contractions": 3,
                    "Latest_Pullback_%": 5.0,
                    "Base_Position": 0.8,
                    "Stage2_Trend": True,
                    "VCP": True,
                    "Sections_Passed": 2,
                    "Qualified": True,
                    "Failed_Conditions": "",
                }

            with patch.object(scanner, "analyze_symbol", side_effect=fake_analyze):
                with patch("sys.argv", ["minervini_vcp_scanner.py", "--input", str(src), "--output", str(out)]):
                    captured = io.StringIO()
                    with patch("sys.stdout", captured):
                        scanner.main()

            self.assertTrue(out.exists())
            saved = pd.read_excel(out, engine="openpyxl")
            self.assertEqual(list(saved["Ticker"]), ["TCS", "INFY"])
            self.assertEqual(list(saved["Company Name"]), ["Tata Consultancy Services Ltd.", "Infosys Ltd."])
            self.assertIn("qualified setup", captured.getvalue().lower())

    def test_repo_nifty_csv_is_readable(self) -> None:
        self.assertTrue(NIFTY_CSV.exists())
        tickers = scanner.extract_tickers(scanner.read_input_table(NIFTY_CSV, engine="csv"))
        self.assertGreater(len(tickers), 100)
        self.assertIn("TCS", tickers)


if __name__ == "__main__":
    unittest.main()
