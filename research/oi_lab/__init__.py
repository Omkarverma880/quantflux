"""
OI Lab — an X-ray of index option open interest for intraday decisions.

  indices    which indices are covered and how their contracts are found
  features   the ONE definition of the chain features, shared by live and history
  analytics  per-strike writer battle, support/resistance zones, flow, bias
  history    3-year NIFTY study from the Market Store: wall-hold odds, model, analogs
  model      numpy logistic regression + honest out-of-sample metrics
  setups     entry-zone engine combining live structure with historical odds
  live       broker snapshot + rate-limited intraday OI tape

Read-only research. Never places orders.
"""
