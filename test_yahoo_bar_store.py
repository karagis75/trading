import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

import prefetch_yahoo_ohlcv as prefetch
import yahoo_bar_store as store
from scanner_history.db import connect


def sample_bars(periods: int = 30, end: str = "2026-08-28") -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=periods)
    close = 100.0 + pd.Series(range(periods), index=index)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": 1_000.0,
        },
        index=index,
    )


class YahooBarStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "cache.sqlite3"
        self.conn = connect(self.db)
        self.now_patch = patch.object(store, "_now_ist", return_value=pd.Timestamp("2026-08-30"))
        self.now_patch.start()

    def tearDown(self) -> None:
        self.now_patch.stop()
        store.reset_shared_connection()
        self.conn.close()
        self.tmp.cleanup()

    def test_second_reader_does_not_call_live_loader(self) -> None:
        history = sample_bars(40)
        live = Mock(return_value=history)
        first = store.get_daily_history(
            "TCS",
            period="2mo",
            live_loader=live,
            connection=self.conn,
            fetch_date=date(2026, 8, 30),
        )
        second = store.get_daily_history(
            "TCS",
            period="1mo",
            live_loader=live,
            connection=self.conn,
            fetch_date=date(2026, 8, 30),
        )
        self.assertEqual(len(first), 40)
        self.assertGreater(len(second), 0)
        self.assertEqual(live.call_count, 1)

    def test_prefetch_then_scanner_reads_same_rows(self) -> None:
        history = sample_bars(20)
        stats = store.prefetch_symbols(
            ["360ONE", "ABB"],
            period="1mo",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda symbol, _period: history,
        )
        self.assertEqual(stats["success"], 2)
        self.assertEqual(stats["bars"], 40)
        live = Mock(side_effect=AssertionError("Yahoo must not be called"))
        loaded = store.get_daily_history(
            "360ONE",
            period="1mo",
            live_loader=live,
            connection=self.conn,
            fetch_date=date(2026, 8, 30),
        )
        self.assertEqual(len(loaded), 20)
        self.assertAlmostEqual(float(loaded["Close"].iloc[-1]), float(history["Close"].iloc[-1]))
        live.assert_not_called()

    def test_short_cached_window_does_not_satisfy_longer_lookback(self) -> None:
        short = sample_bars(8, end="2026-08-28")
        store.upsert_bars(self.conn, "INFY", short)
        live_history = sample_bars(60, end="2026-08-28")
        live = Mock(return_value=live_history)
        result = store.get_daily_history(
            "INFY",
            period="3mo",
            live_loader=live,
            connection=self.conn,
            fetch_date=date(2026, 8, 30),
            persist=False,
        )
        live.assert_called_once()
        self.assertEqual(len(result), 60)

    def test_scheduled_minervini_jobs_reuse_prefetched_bars(self) -> None:
        import minervini_volume_cpr_scanner as volume
        import nimblr_minervini_cpr_scanner as nimblr

        history = sample_bars(40)
        store.prefetch_symbols(
            ["ABB"],
            period="2y",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        real_history = store.get_daily_history

        def history_for_prefetch_day(symbol, period="2y", **kwargs):
            kwargs.setdefault("fetch_date", date(2026, 8, 30))
            return real_history(symbol, period, **kwargs)

        with patch.dict(os.environ, {"TRADING_YAHOO_CACHE_DB": str(self.db)}):
            with patch.object(store, "get_daily_history", side_effect=history_for_prefetch_day):
                with patch.object(nimblr, "_download_yahoo_history") as yf_live:
                    with patch.object(volume, "history_from_chart") as chart_live:
                        vcp = nimblr.fetch_history("ABB", nimblr.CombinedScannerConfig(lookback_period="2y"))
                        cpr = volume.fetch_volume_cpr_history(
                            "ABB",
                            volume.VolumeCPRScannerConfig(lookback_period="2y", max_retries=1, retry_delay=0.0),
                        )
        self.assertEqual(len(vcp), 40)
        self.assertEqual(len(cpr), 40)
        yf_live.assert_not_called()
        chart_live.assert_not_called()

    def test_display_symbol_normalizes_ns_suffix(self) -> None:
        self.assertEqual(store.display_symbol("TCS.NS"), "TCS")
        self.assertEqual(store.yahoo_symbol("TCS"), "TCS.NS")

    def test_display_database_url_redacts_password(self) -> None:
        self.assertEqual(
            store.display_database_url(
                "postgresql://trading_app:secret@localhost:5432/trading_history"
            ),
            "postgresql://trading_app:***@localhost:5432/trading_history",
        )

    def test_refresh_period_is_gap_plus_overlap_not_full_lookback(self) -> None:
        self.assertEqual(
            store.refresh_lookback_period(None, fetch_date=date(2026, 8, 31), full_period="2y"),
            "2y",
        )
        self.assertEqual(
            store.refresh_lookback_period(
                date(2026, 8, 28), fetch_date=date(2026, 8, 31), full_period="2y"
            ),
            "8d",
        )
        self.assertEqual(
            store.refresh_lookback_period(
                date(2026, 4, 1), fetch_date=date(2026, 8, 31), full_period="2y"
            ),
            "2y",
        )

    def test_incremental_prefetch_keeps_existing_bars_and_requests_gap(self) -> None:
        history = sample_bars(40, end="2026-08-28")
        store.prefetch_symbols(
            ["TCS"],
            period="2y",
            fetch_date=date(2026, 8, 28),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        requested: list[str] = []

        def incremental_loader(_symbol: str, period: str) -> pd.DataFrame:
            requested.append(period)
            return sample_bars(3, end="2026-08-31")

        stats = store.prefetch_symbols(
            ["TCS", "NEWCO"],
            period="2y",
            fetch_date=date(2026, 8, 31),
            connection=self.conn,
            live_loader=incremental_loader,
        )
        self.assertEqual(requested, ["8d", "2y"])
        self.assertEqual(stats["incremental"], 1)
        self.assertEqual(stats["full"], 1)
        loaded = store.load_cached_history(self.conn, "TCS", ignore_cutoff=True)
        self.assertGreaterEqual(len(loaded), 40)
        self.assertEqual(pd.Timestamp(loaded.index.max()).date(), date(2026, 8, 31))
        self.assertEqual(store.latest_cached_bar(self.conn, "TCS"), date(2026, 8, 31))

    def test_prefetched_short_listing_does_not_call_live(self) -> None:
        short = sample_bars(8, end="2026-08-28")
        store.prefetch_symbols(
            ["EMMVEE"],
            period="2y",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: short,
        )
        live = Mock(side_effect=AssertionError("Yahoo must not be called"))
        loaded = store.get_daily_history(
            "EMMVEE",
            period="1y",
            live_loader=live,
            connection=self.conn,
            fetch_date=date(2026, 8, 30),
        )
        self.assertEqual(len(loaded), 8)
        live.assert_not_called()
        self.assertEqual(store.last_history_source(), "cache")

    def test_prefetch_day_blocks_live_for_symbols_without_rows(self) -> None:
        store.prefetch_symbols(
            ["TCS"],
            period="1mo",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: sample_bars(20),
        )
        live = Mock(side_effect=AssertionError("Yahoo must not be called"))
        loaded = store.get_daily_history(
            "MISSING",
            period="1y",
            live_loader=live,
            connection=self.conn,
            fetch_date=date(2026, 8, 30),
        )
        self.assertTrue(loaded.empty)
        live.assert_not_called()
        self.assertEqual(store.last_history_source(), "empty")

    def test_reuses_one_database_connection_across_tickers(self) -> None:
        history = sample_bars(40)
        store.prefetch_symbols(
            ["TCS", "INFY", "ABB"],
            period="2y",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        store.reset_shared_connection()
        live = Mock(side_effect=AssertionError("Yahoo must not be called"))
        with patch.object(store, "connect", wraps=connect) as mocked:
            for symbol in ("TCS", "INFY", "ABB"):
                frame = store.get_daily_history(
                    symbol,
                    period="2y",
                    live_loader=live,
                    database_url=self.db,
                    fetch_date=date(2026, 8, 30),
                )
                self.assertEqual(len(frame), 40)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(store.cache_stats()["connects"], 1)
        self.assertEqual(store.cache_stats()["live"], 0)
        self.assertEqual(store.cache_stats()["hits"], 3)
        live.assert_not_called()

    def test_preloads_universe_once_after_prefetch(self) -> None:
        history = sample_bars(40)
        store.prefetch_symbols(
            ["TCS", "INFY", "ABB"],
            period="2y",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        live = Mock(side_effect=AssertionError("Yahoo must not be called"))
        with patch.object(store, "load_cached_universe", wraps=store.load_cached_universe) as preload:
            for symbol in ("TCS", "INFY", "ABB"):
                frame = store.get_daily_history(
                    symbol,
                    period="2y",
                    live_loader=live,
                    connection=self.conn,
                    fetch_date=date(2026, 8, 30),
                )
                self.assertEqual(len(frame), 40)
        self.assertEqual(preload.call_count, 1)
        self.assertEqual(store.cache_stats()["preloads"], 1)
        live.assert_not_called()

    def test_cache_replay_does_not_reconnect_per_ticker(self) -> None:
        symbols = [f"S{index:03d}" for index in range(40)]
        history = sample_bars(40)
        store.prefetch_symbols(
            symbols,
            period="2y",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        store.reset_shared_connection()
        connects = {"n": 0}
        real_connect = connect

        def slow_connect(path, **kwargs):
            connects["n"] += 1
            time.sleep(0.02)
            return real_connect(path, **kwargs)

        live = Mock(side_effect=AssertionError("Yahoo must not be called"))
        started = time.perf_counter()
        with patch.object(store, "connect", side_effect=slow_connect):
            for symbol in symbols:
                frame = store.get_daily_history(
                    symbol,
                    period="2y",
                    live_loader=live,
                    database_url=self.db,
                    fetch_date=date(2026, 8, 30),
                )
                self.assertEqual(len(frame), 40)
        elapsed = time.perf_counter() - started
        self.assertEqual(connects["n"], 1)
        self.assertEqual(store.cache_stats()["live"], 0)
        self.assertLess(elapsed, 0.02 * len(symbols) / 2)

    def test_enabled_cache_replay_scanners_do_not_call_yahoo(self) -> None:
        import bearisbiasnifty500 as bearish
        import bullishbiasnifty500 as bullish
        import fib_pinball_common as pinball
        import minervini_volume_cpr_scanner as volume
        import nifty500_xy_intersect as xy
        import nimblr_minervini_cpr_scanner as nimblr
        import rangeboundstocks as rangebound

        history = sample_bars(40)
        store.prefetch_symbols(
            ["ABB"],
            period="2y",
            fetch_date=date(2026, 8, 30),
            connection=self.conn,
            live_loader=lambda _symbol, _period: history,
        )
        store.reset_shared_connection()
        live_error = AssertionError("Yahoo must not be called after prefetch")
        env = {
            "TRADING_YAHOO_CACHE_DB": str(self.db),
            "TRADING_DATABASE_URL": str(self.db),
        }
        real_history = store.get_daily_history

        def history_for_prefetch_day(symbol, period="2y", **kwargs):
            kwargs.setdefault("fetch_date", date(2026, 8, 30))
            return real_history(symbol, period, **kwargs)

        with patch.dict(os.environ, env, clear=False):
          with patch.object(store, "get_daily_history", side_effect=history_for_prefetch_day):
            with patch.object(bullish.yf, "Ticker", side_effect=live_error):
                with patch.object(bearish.yf, "Ticker", side_effect=live_error):
                    with patch.object(rangebound.yf, "Ticker", side_effect=live_error):
                        with patch.object(xy.yf, "download", side_effect=live_error):
                            with patch.object(pinball.yf, "Ticker", side_effect=live_error):
                                with patch.object(
                                    nimblr, "history_from_chart", side_effect=live_error
                                ):
                                    with patch.object(
                                        nimblr,
                                        "_download_yahoo_history",
                                        side_effect=live_error,
                                    ):
                                        with patch.object(
                                            volume,
                                            "history_from_chart",
                                            side_effect=live_error,
                                        ):
                                            bullish.analyze_symbol(
                                                "ABB", bullish.BullishScannerConfig()
                                            )
                                            bearish.analyze_symbol(
                                                "ABB", bearish.BearishScannerConfig()
                                            )
                                            rangebound.analyze_symbol(
                                                "ABB", rangebound.StrangleScannerConfig()
                                            )
                                            xy_bars = xy._download_history(
                                                "ABB", xy.IntersectScannerConfig()
                                            )
                                            vcp = nimblr.fetch_history(
                                                "ABB",
                                                nimblr.CombinedScannerConfig(
                                                    lookback_period="2y"
                                                ),
                                            )
                                            cpr = volume.fetch_volume_cpr_history(
                                                "ABB",
                                                volume.VolumeCPRScannerConfig(
                                                    lookback_period="2y",
                                                    max_retries=1,
                                                    retry_delay=0.0,
                                                ),
                                            )
                                            fib = pinball.fetch_history(
                                                "ABB", pinball.PinballConfig()
                                            )
        self.assertEqual(len(xy_bars), 40)
        self.assertEqual(len(vcp), 40)
        self.assertEqual(len(cpr), 40)
        self.assertEqual(len(fib), 40)
        self.assertEqual(store.cache_stats()["live"], 0)

    def test_prefetch_cli_writes_and_exits_zero(self) -> None:
        history = sample_bars(8)
        universe = Path(self.tmp.name) / "tickers.csv"
        pd.DataFrame({"Ticker": ["TCS"]}).to_csv(universe, index=False)
        with patch.object(store, "default_live_loader", return_value=history):
            code = prefetch.main(
                [
                    "--input",
                    str(universe),
                    "--engine",
                    "csv",
                    "--database",
                    str(self.db),
                    "--lookback",
                    "1mo",
                    "--request-delay",
                    "0",
                    "--fetch-date",
                    "2026-08-30",
                ]
            )
        self.assertEqual(code, 0)
        verify = connect(self.db)
        try:
            loaded = store.load_cached_history(verify, "TCS", period="1mo")
            self.assertEqual(len(loaded), 8)
            self.assertTrue(store.prefetch_succeeded(verify, "TCS", date(2026, 8, 30)))
        finally:
            verify.close()


class YahooBarStoreEnvTests(unittest.TestCase):
    def test_writes_disabled_without_database_env(self) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"TRADING_DATABASE_URL", "TRADING_YAHOO_CACHE_DB", "TRADING_YAHOO_CACHE"}
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(store.cache_writes_enabled())
            self.assertTrue(store.cache_database_url().endswith("scanner_history.sqlite3"))


if __name__ == "__main__":
    unittest.main()
