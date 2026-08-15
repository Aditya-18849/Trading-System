"""
Kite Connect Market Data Fetcher.

Provides quote, OHLC, and historical candle data fetching capabilities
using the official kiteconnect Python SDK.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

import pandas as pd
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class KiteDataFetcher:
    """Market data fetcher for Zerodha Kite Connect.

    Extends the broker adapter with data fetching capabilities
    without modifying the existing order placement logic.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
    ) -> None:
        """Initialize the Kite Connect data fetcher.

        Args:
            api_key: Kite Connect API key.
            api_secret: Kite Connect API secret.
            access_token: Valid access token obtained via daily login flow.
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        logger.info("KiteDataFetcher initialized for api_key=%s", api_key)

    # ------------------------------------------------------------------------
    # Quote / LTP
    # ------------------------------------------------------------------------

    def get_quote(self, symbols: List[str], exchange: str = "NSE") -> Dict[str, Dict]:
        """Fetch full quote data for multiple symbols.

        Args:
            symbols: List of trading symbols (e.g., ["RELIANCE", "TCS"]).
            exchange: Exchange segment (default "NSE").

        Returns:
            Dict mapping "EXCHANGE:SYMBOL" to quote data including:
            - last_price, ohlc (open, high, low, close), volume, etc.
        """
        try:
            instrument_keys = [f"{exchange}:{sym}" for sym in symbols]
            data = self.kite.quote(instrument_keys)
            logger.debug("Quote fetched for %d symbols", len(symbols))
            return data
        except Exception as e:
            logger.exception("get_quote FAILED for symbols=%s", symbols)
            raise

    def get_ltp(self, symbols: List[str], exchange: str = "NSE") -> Dict[str, float]:
        """Fetch last traded price for multiple symbols.

        Args:
            symbols: List of trading symbols.
            exchange: Exchange segment (default "NSE").

        Returns:
            Dict mapping "EXCHANGE:SYMBOL" to last traded price.
        """
        try:
            instrument_keys = [f"{exchange}:{sym}" for sym in symbols]
            data = self.kite.ltp(instrument_keys)
            result = {k: v["last_price"] for k, v in data.items()}
            logger.debug("LTP fetched for %d symbols", len(symbols))
            return result
        except Exception as e:
            logger.exception("get_ltp FAILED for symbols=%s", symbols)
            raise

    def get_ohlc(self, symbols: List[str], exchange: str = "NSE") -> Dict[str, Dict]:
        """Fetch OHLC data for multiple symbols.

        Args:
            symbols: List of trading symbols.
            exchange: Exchange segment (default "NSE").

        Returns:
            Dict mapping "EXCHANGE:SYMBOL" to OHLC data.
        """
        try:
            instrument_keys = [f"{exchange}:{sym}" for sym in symbols]
            data = self.kite.ohlc(instrument_keys)
            logger.debug("OHLC fetched for %d symbols", len(symbols))
            return data
        except Exception as e:
            logger.exception("get_ohlc FAILED for symbols=%s", symbols)
            raise

    # ------------------------------------------------------------------------
    # Historical Data
    # ------------------------------------------------------------------------

    def get_historical_data(
        self,
        instrument_token: int,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> pd.DataFrame:
        """Fetch historical candle data for a single instrument.

        Args:
            instrument_token: Numeric instrument token from Kite.
            from_date: Start date (inclusive).
            to_date: End date (inclusive).
            interval: Candle interval ("minute", "3minute", "5minute", "10minute",
                     "15minute", "30minute", "60minute", "day").
            continuous: Whether to fetch continuous data for futures.
            oi: Whether to include open interest (for F&O).

        Returns:
            DataFrame with columns: date, open, high, low, close, volume.
            Date column is timezone-aware (IST).
        """
        try:
            # Kite expects dates in YYYY-MM-DD format
            from_str = from_date.strftime("%Y-%m-%d")
            to_str = to_date.strftime("%Y-%m-%d")

            data = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_str,
                to_date=to_str,
                interval=interval,
                continuous=continuous,
                oi=oi,
            )

            if not data:
                logger.warning(
                    "No historical data returned for token=%d interval=%s",
                    instrument_token,
                    interval,
                )
                return pd.DataFrame()

            df = pd.DataFrame(data)
            df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # Ensure IST timezone
            if df["timestamp"].dt.tz is None:
                df["timestamp"] = df["timestamp"].dt.tz_localize("Asia/Kolkata")
            else:
                df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Kolkata")

            logger.debug(
                "Historical data fetched: token=%d rows=%d interval=%s",
                instrument_token,
                len(df),
                interval,
            )
            return df

        except Exception as e:
            logger.exception(
                "get_historical_data FAILED token=%d interval=%s",
                instrument_token,
                interval,
            )
            raise

    def get_historical_data_batch(
        self,
        instrument_tokens: List[int],
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> Dict[int, pd.DataFrame]:
        """Fetch historical data for multiple instruments.

        Note: Kite API requires sequential calls per instrument token.
        This method batches the requests with rate limiting.

        Args:
            instrument_tokens: List of instrument tokens.
            from_date: Start date.
            to_date: End date.
            interval: Candle interval.

        Returns:
            Dict mapping instrument_token to DataFrame.
        """
        import time

        result = {}
        for token in instrument_tokens:
            try:
                df = self.get_historical_data(token, from_date, to_date, interval)
                result[token] = df
                time.sleep(0.1)  # Rate limit: ~10 req/sec
            except Exception as e:
                logger.error(
                    "Failed to fetch historical for token=%d: %s", token, e
                )
                result[token] = pd.DataFrame()
        return result

    # ------------------------------------------------------------------------
    # Instrument Resolution
    # ------------------------------------------------------------------------

    def get_instruments(self, exchange: str = "NSE") -> List[Dict]:
        """Fetch full instrument list for an exchange.

        Args:
            exchange: Exchange segment ("NSE", "NFO", "BSE", "BFO", etc.).

        Returns:
            List of instrument dicts with tradingsymbol, instrument_token, etc.
        """
        try:
            instruments = self.kite.instruments(exchange)
            logger.info("Fetched %d instruments for %s", len(instruments), exchange)
            return instruments
        except Exception as e:
            logger.exception("get_instruments FAILED for exchange=%s", exchange)
            raise

    def resolve_instrument_tokens(
        self, symbols: List[str], exchange: str = "NSE"
    ) -> Dict[str, int]:
        """Resolve trading symbols to instrument tokens.

        Args:
            symbols: List of trading symbols.
            exchange: Exchange segment.

        Returns:
            Dict mapping symbol to instrument_token.
        """
        try:
            instruments = self.kite.instruments(exchange)
            symbol_to_token = {
                inst["tradingsymbol"]: inst["instrument_token"]
                for inst in instruments
            }
            result = {}
            for sym in symbols:
                token = symbol_to_token.get(sym.upper())
                if token:
                    result[sym.upper()] = token
                else:
                    logger.warning(
                        "Instrument token not found for %s:%s", exchange, sym
                    )
            return result
        except Exception as e:
            logger.exception("resolve_instrument_tokens FAILED for %s", symbols)
            raise

    # ------------------------------------------------------------------------
    # Convenience Methods for Strategy Engine
    # ------------------------------------------------------------------------

    def get_latest_candles(
        self,
        symbol: str,
        exchange: str = "NSE",
        interval: str = "5minute",
        lookback_days: int = 5,
    ) -> pd.DataFrame:
        """Fetch recent candles for a symbol (convenience method).

        Args:
            symbol: Trading symbol.
            exchange: Exchange segment.
            interval: Candle interval.
            lookback_days: How many days of history to fetch.

        Returns:
            DataFrame with OHLCV data.
        """
        # Resolve token
        tokens = self.resolve_instrument_tokens([symbol], exchange)
        token = tokens.get(symbol.upper())
        if not token:
            raise ValueError(f"Could not resolve instrument token for {symbol}")

        to_date = datetime.now()
        from_date = to_date - timedelta(days=lookback_days)

        return self.get_historical_data(token, from_date, to_date, interval)

    def get_multiple_latest_candles(
        self,
        symbols: List[str],
        exchange: str = "NSE",
        interval: str = "5minute",
        lookback_days: int = 5,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch recent candles for multiple symbols.

        Args:
            symbols: List of trading symbols.
            exchange: Exchange segment.
            interval: Candle interval.
            lookback_days: How many days of history to fetch.

        Returns:
            Dict mapping symbol to DataFrame.
        """
        tokens = self.resolve_instrument_tokens(symbols, exchange)
        to_date = datetime.now()
        from_date = to_date - timedelta(days=lookback_days)

        result = {}
        for sym in symbols:
            token = tokens.get(sym.upper())
            if token:
                try:
                    result[sym.upper()] = self.get_historical_data(
                        token, from_date, to_date, interval
                    )
                except Exception as e:
                    logger.error("Failed to fetch candles for %s: %s", sym, e)
                    result[sym.upper()] = pd.DataFrame()
            else:
                result[sym.upper()] = pd.DataFrame()
        return result