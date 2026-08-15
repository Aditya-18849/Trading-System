"""Kite Connect market data fetching utilities.

Extends the KiteAdapter with quote, OHLC, and historical data methods.
"""
from app.broker.kite_data import KiteDataFetcher

__all__ = ["KiteDataFetcher"]