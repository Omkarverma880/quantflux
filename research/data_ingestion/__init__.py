"""
Data Ingestion Lab — get any index or option file into the Market Store, check it, see it.

  mapping   recognise columns, parse trading symbols, detect underlying / kind / bar size
  service   staging → preview → validate → commit (background jobs), coverage, explorer,
            partition removal, and import from the Data Downloader

Everything lands in the one shared Market Store (research/market_store), so the OI Lab,
Options Lab and every future model read it the same way. Never places orders.
"""
