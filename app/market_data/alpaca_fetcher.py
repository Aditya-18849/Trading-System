"""
Alpaca Market Data Fetcher (US Equities & Crypto).

Fetches historical and real-time OHLCV barset data, quotes, and trades
using the official `alpaca-py` SDK (`StockHistoricalDataClient` and `CryptoHistoricalDataClient`).

Supports:
- US Equities and Crypto asset classes with auto-detection.
- Multi-timeframe bar resolution (1Min, 5Min, 15Min, 1Hour, 1Day, etc.).
- Normalized pandas DataFrame output with timezone handling (US/Eastern, UTC, IST).
- Direct persistence integration with PostgreSQL/SQLite `MarketData` model.
- Latest quote / LTP inspection.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Union
import pandas as pd
import pytz

from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    CryptoBarsRequest,
    StockLatestQuoteRequest,
    CryptoLatestQuoteRequest,
    StockLatestTradeRequest,
    CryptoLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.common.exceptions import APIError
from sqlalchemy.orm import Session

from app.models import MarketData

logger = logging.getLogger(__name__)

# Standard Timezones
UTC = timezone.utc
ET_TZ = pytz.timezone("America/New_York")
IST_TZ = pytz.timezone("Asia/Kolkata")

# TimeFrame String Mapping
TIMEFRAME_MAP: Dict[str, TimeFrame] = {
    "1m": TimeFrame.Minute,
    "1min": TimeFrame.Minute,
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "5min": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "15min": TimeFrame(15, TimeFrameUnit.Minute),
    "30m": TimeFrame(30, TimeFrameUnit.Minute),
    "30min": TimeFrame(30, TimeFrameUnit.Minute),
    "1h": TimeFrame.Hour,
    "1hour": TimeFrame.Hour,
    "60m": TimeFrame.Hour,
    "4h": TimeFrame(4, TimeFrameUnit.Hour),
    "1d": TimeFrame.Day,
    "1day": TimeFrame.Day,
    "day": TimeFrame.Day,
}

KNOWN_CRYPTO_PAIRS = {
    "BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "LTC/USD", "AVAX/USD", "LINK/USD",
    "BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "LTCUSD", "AVAXUSD", "LINKUSD",
    "BTC/USDT", "ETH/USDT", "USDT/USD", "USDC/USD"
}


class AlpacaDataFetcher:
    """Service for querying and persisting market data from Alpaca Data API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> None:
        """Initialize the Alpaca Data Clients.

        Args:
            api_key: Alpaca API Key ID (or None to use Alpaca free tier if applicable).
            secret_key: Alpaca API Secret Key.
            db: Optional SQLAlchemy database session for candle persistence.
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.db = db

        effective_api_key = api_key or "PK_TEST_DUMMY"
        effective_secret_key = secret_key or "SK_TEST_DUMMY"

        self.stock_client = StockHistoricalDataClient(
            api_key=effective_api_key,
            secret_key=effective_secret_key,
        )
        self.crypto_client = CryptoHistoricalDataClient(
            api_key=effective_api_key,
            secret_key=effective_secret_key,
        )

    def is_crypto(self, symbol: str) -> bool:
        """Heuristic check to determine whether a symbol is a crypto asset."""
        sym_clean = symbol.strip().upper()
        if "/" in sym_clean:
            return True
        if sym_clean in KNOWN_CRYPTO_PAIRS:
            return True
        if sym_clean.endswith("USD") and len(sym_clean) in (6, 7):
            return True
        return False

    def normalize_symbol(self, symbol: str, is_crypto: bool) -> str:
        """Normalize symbol string according to Alpaca conventions."""
        sym = symbol.strip().upper()
        if is_crypto:
            if "/" not in sym and len(sym) >= 6 and (sym.endswith("USD") or sym.endswith("USDT")):
                # Convert BTCUSD -> BTC/USD
                base = sym[:-3] if sym.endswith("USD") else sym[:-4]
                quote = sym[-3:] if sym.endswith("USD") else sym[-4:]
                return f"{base}/{quote}"
            return sym
        return sym.replace("/", "").replace(" ", "")

    def _parse_timeframe(self, timeframe: Union[str, TimeFrame]) -> TimeFrame:
        """Convert string or TimeFrame instance into Alpaca TimeFrame."""
        if isinstance(timeframe, TimeFrame):
            return timeframe
        tf_key = str(timeframe).lower().strip()
        if tf_key in TIMEFRAME_MAP:
            return TIMEFRAME_MAP[tf_key]
        logger.warning("Unrecognized timeframe '%s', defaulting to 1Min", timeframe)
        return TimeFrame.Minute

    def get_historical_bars(
        self,
        symbol: str,
        timeframe: Union[str, TimeFrame] = "1Min",
        start: Optional[Union[datetime, str]] = None,
        end: Optional[Union[datetime, str]] = None,
        limit: Optional[int] = None,
        is_crypto: Optional[bool] = None,
        tz: str = "America/New_York",
    ) -> pd.DataFrame:
        """Fetch historical OHLCV barset from Alpaca and return a normalized DataFrame.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL', 'SPY', 'BTC/USD').
            timeframe: Bar interval ('1m', '5m', '15m', '1h', '1d' or TimeFrame object).
            start: Start datetime or ISO string (inclusive).
            end: End datetime or ISO string (exclusive/inclusive).
            limit: Maximum number of bars to retrieve.
            is_crypto: Explicit crypto override. If None, auto-detected.
            tz: Target timezone for the returned DataFrame timestamp ('America/New_York', 'UTC', 'Asia/Kolkata').

        Returns:
            Normalized pandas.DataFrame with columns:
            ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'trade_count', 'vwap']
        """
        crypto_flag = self.is_crypto(symbol) if is_crypto is None else is_crypto
        norm_symbol = self.normalize_symbol(symbol, crypto_flag)
        tf = self._parse_timeframe(timeframe)

        # Parse datetime objects ensuring timezone awareness
        parsed_start = None
        if start:
            if isinstance(start, str):
                parsed_start = datetime.fromisoformat(start)
            else:
                parsed_start = start
            if parsed_start.tzinfo is None:
                parsed_start = parsed_start.replace(tzinfo=timezone.utc)

        parsed_end = None
        if end:
            if isinstance(end, str):
                parsed_end = datetime.fromisoformat(end)
            else:
                parsed_end = end
            if parsed_end.tzinfo is None:
                parsed_end = parsed_end.replace(tzinfo=timezone.utc)

        target_tz = pytz.timezone(tz) if isinstance(tz, str) else tz

        try:
            if crypto_flag:
                req = CryptoBarsRequest(
                    symbol_or_symbols=norm_symbol,
                    timeframe=tf,
                    start=parsed_start,
                    end=parsed_end,
                    limit=limit,
                )
                barset = self.crypto_client.get_crypto_bars(req)
            else:
                req = StockBarsRequest(
                    symbol_or_symbols=norm_symbol,
                    timeframe=tf,
                    start=parsed_start,
                    end=parsed_end,
                    limit=limit,
                )
                barset = self.stock_client.get_stock_bars(req)

            bars = barset.data.get(norm_symbol, []) if hasattr(barset, "data") else []
            if not bars and hasattr(barset, "df"):
                df_raw = barset.df
                if not df_raw.empty:
                    df = df_raw.reset_index()
                    if "timestamp" in df.columns:
                        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(target_tz)
                    return df

            if not bars:
                logger.debug("No bar data returned from Alpaca for %s", norm_symbol)
                return pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume", "trade_count", "vwap"]
                )

            records = []
            for bar in bars:
                ts = bar.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_local = ts.astimezone(target_tz)

                records.append({
                    "timestamp": ts_local,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": float(bar.volume),
                    "trade_count": getattr(bar, "trade_count", 0),
                    "vwap": float(getattr(bar, "vwap", 0.0) or 0.0),
                })

            df = pd.DataFrame(records)
            df.sort_values(by="timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df

        except Exception as exc:
            logger.error("Error fetching historical bars from Alpaca for %s: %s", symbol, exc)
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "trade_count", "vwap"]
            )

    def get_latest_price(self, symbol: str, is_crypto: Optional[bool] = None) -> float:
        """Fetch the latest trade price or mid-quote for a given symbol."""
        crypto_flag = self.is_crypto(symbol) if is_crypto is None else is_crypto
        norm_symbol = self.normalize_symbol(symbol, crypto_flag)

        try:
            if crypto_flag:
                req = CryptoLatestTradeRequest(symbol_or_symbols=norm_symbol)
                res = self.crypto_client.get_crypto_latest_trade(req)
                trade = res.get(norm_symbol)
                if trade and trade.price:
                    return float(trade.price)

                # Fallback to latest quote
                q_req = CryptoLatestQuoteRequest(symbol_or_symbols=norm_symbol)
                q_res = self.crypto_client.get_crypto_latest_quote(q_req)
                quote = q_res.get(norm_symbol)
                if quote and quote.ask_price and quote.bid_price:
                    return float((quote.ask_price + quote.bid_price) / 2.0)
            else:
                req = StockLatestTradeRequest(symbol_or_symbols=norm_symbol)
                res = self.stock_client.get_stock_latest_trade(req)
                trade = res.get(norm_symbol)
                if trade and trade.price:
                    return float(trade.price)

                # Fallback to latest quote
                q_req = StockLatestQuoteRequest(symbol_or_symbols=norm_symbol)
                q_res = self.stock_client.get_stock_latest_quote(q_req)
                quote = q_res.get(norm_symbol)
                if quote and quote.ask_price and quote.bid_price:
                    return float((quote.ask_price + quote.bid_price) / 2.0)

        except Exception as exc:
            logger.error("Error fetching latest price for %s: %s", symbol, exc)

        return 0.0

    def get_latest_quote(self, symbol: str, is_crypto: Optional[bool] = None) -> Dict[str, Any]:
        """Fetch latest bid/ask quote for a given symbol."""
        crypto_flag = self.is_crypto(symbol) if is_crypto is None else is_crypto
        norm_symbol = self.normalize_symbol(symbol, crypto_flag)

        try:
            if crypto_flag:
                q_req = CryptoLatestQuoteRequest(symbol_or_symbols=norm_symbol)
                q_res = self.crypto_client.get_crypto_latest_quote(q_req)
                quote = q_res.get(norm_symbol)
            else:
                q_req = StockLatestQuoteRequest(symbol_or_symbols=norm_symbol)
                q_res = self.stock_client.get_stock_latest_quote(q_req)
                quote = q_res.get(norm_symbol)

            if quote:
                return {
                    "symbol": norm_symbol,
                    "bid_price": float(quote.bid_price),
                    "bid_size": float(quote.bid_size),
                    "ask_price": float(quote.ask_price),
                    "ask_size": float(quote.ask_size),
                    "timestamp": quote.timestamp.isoformat() if hasattr(quote, "timestamp") else None,
                }
        except Exception as exc:
            logger.error("Error fetching latest quote for %s: %s", symbol, exc)

        return {}

    def fetch_and_persist(
        self,
        symbol: str,
        interval: str = "1m",
        exchange: str = "US",
        is_crypto: Optional[bool] = None,
        db: Optional[Session] = None,
    ) -> Optional[MarketData]:
        """Fetch latest candle and persist it to the MarketData database table.

        Args:
            symbol: Ticker symbol (e.g., 'AAPL', 'BTC/USD').
            interval: Standard interval identifier ('1m', '5m', '15m', '1h', '1d').
            exchange: Exchange name (e.g., 'NASDAQ', 'NYSE', 'CRYPTO', 'US').
            is_crypto: Whether symbol is crypto.
            db: Database session (uses self.db if omitted).

        Returns:
            The created MarketData instance, or None if skipped/failed.
        """
        session = db or self.db
        if not session:
            logger.warning("No database session provided to fetch_and_persist")
            return None

        crypto_flag = self.is_crypto(symbol) if is_crypto is None else is_crypto
        norm_symbol = self.normalize_symbol(symbol, crypto_flag)

        df = self.get_historical_bars(
            symbol=norm_symbol,
            timeframe=interval,
            limit=5,
            is_crypto=crypto_flag,
            tz="UTC",
        )

        if df.empty:
            logger.debug("No candles returned to persist for %s (%s)", norm_symbol, interval)
            return None

        # Take the most recent candle
        latest = df.iloc[-1]
        candle_ts = latest["timestamp"]
        if hasattr(candle_ts, "to_pydatetime"):
            candle_ts = candle_ts.to_pydatetime()

        try:
            # Check for existing candle
            existing = session.query(MarketData).filter(
                MarketData.symbol == norm_symbol,
                MarketData.exchange == exchange,
                MarketData.interval == interval,
                MarketData.timestamp == candle_ts,
            ).first()

            if existing:
                return existing

            candle = MarketData(
                symbol=norm_symbol,
                exchange=exchange,
                interval=interval,
                open=float(latest["open"]),
                high=float(latest["high"]),
                low=float(latest["low"]),
                close=float(latest["close"]),
                volume=int(latest["volume"]),
                timestamp=candle_ts,
                source="alpaca",
            )
            session.add(candle)
            session.commit()
            logger.debug("Persisted Alpaca candle: %s %s %s @ %s", norm_symbol, exchange, interval, candle_ts)
            return candle

        except Exception as exc:
            logger.error("Failed to persist Alpaca candle for %s: %s", norm_symbol, exc)
            session.rollback()
            return None
