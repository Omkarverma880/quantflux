"""
Research → Data Downloader.

A modular, read-only historical-data downloader built on the EXISTING Zerodha
Broker, database, config and storage. It downloads/normalizes/validates OHLCV
(+OI) for indices, equities, futures and options into reusable Parquet/CSV
datasets for future ML / backtesting research. It never places orders and is
isolated from live trading execution.
"""
from research.data_downloader.service import DataDownloader

__all__ = ["DataDownloader"]
