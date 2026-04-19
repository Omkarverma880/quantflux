"""
Broker abstraction layer.
Wraps KiteConnect into clean methods the strategy engine uses.
Handles paper-trading mode transparently.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from kiteconnect import KiteConnect

from config import settings
from core.auth import get_kite
from core.logger import get_logger

logger = get_logger("broker")

# Lazy import to avoid circular dependency
def _get_risk_manager():
    from core.risk_manager import get_risk_manager
    return get_risk_manager()

# Per-user broker instances (keyed by user_id)
_user_brokers: dict[int, "Broker"] = {}


# ──────────────── Data Models ────────────────

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class ProductType(str, Enum):
    MIS = "MIS"       # Intraday
    CNC = "CNC"       # Delivery
    NRML = "NRML"     # F&O normal


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"
    MCX = "MCX"


@dataclass
class OrderRequest:
    tradingsymbol: str
    exchange: Exchange
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    product: ProductType = ProductType.MIS
    price: float = 0.0
    trigger_price: float = 0.0
    tag: str = ""


@dataclass
class OrderResponse:
    order_id: str
    status: str
    tradingsymbol: str
    side: str
    quantity: int
    price: float
    timestamp: datetime = field(default_factory=datetime.now)
    is_paper: bool = False


@dataclass
class Position:
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: float
    pnl: float
    product: str
    last_price: float = 0.0


@dataclass
class Holding:
    tradingsymbol: str
    exchange: str
    quantity: int
    average_price: float
    last_price: float
    pnl: float


# ──────────────── Broker Class ────────────────

class Broker:
    """Unified broker interface. Switches between live and paper mode."""

    def __init__(self, *, per_user: bool = False, user_id: int = None):
        self._kite: Optional[KiteConnect] = None
        self._per_user: bool = per_user
        self._user_id: int = user_id
        self._paper_orders: list[OrderResponse] = []
        self._paper_order_counter = 0

    @property
    def kite(self) -> KiteConnect:
        if self._kite is None:
            if self._per_user:
                raise RuntimeError("Not logged in to Zerodha. Please login first.")
            self._kite = get_kite()
        return self._kite

    @property
    def is_kite_connected(self) -> bool:
        """Check if this broker has a valid Kite session (without fallback)."""
        return self._kite is not None

    def connect(self) -> "Broker":
        """Ensure we have a live authenticated session."""
        _ = self.kite
        logger.info("Broker connected.")
        return self

    # ── Orders ─────────────────────────────────────────

    def place_order(self, req: OrderRequest) -> OrderResponse:
        """Place an order (paper or live based on settings). Risk-checked."""
        if self._user_id is not None:
            from core.risk_manager import get_user_risk_manager
            risk = get_user_risk_manager(self._user_id)
        else:
            risk = _get_risk_manager()
        approved, reason = risk.pre_order_check(
            req.tradingsymbol, req.quantity, req.price, req.side.value
        )
        if not approved:
            raise RuntimeError(f"Risk check failed: {reason}")

        if settings.PAPER_TRADE:
            resp = self._paper_order(req)
        else:
            resp = self._live_order(req)

        risk.record_trade(req.quantity, req.price, req.side.value)
        return resp

    def _live_order(self, req: OrderRequest) -> OrderResponse:
        if not settings.TRADING_ENABLED:
            raise RuntimeError("TRADING_ENABLED is false. Set it to true in .env")

        # Zerodha disallows MARKET orders without market protection via API.
        # Convert MARKET → LIMIT with a 5% buffer from LTP as a workaround.
        effective_order_type = req.order_type
        effective_price = req.price

        if req.order_type == OrderType.MARKET:
            try:
                instrument = f"{req.exchange.value}:{req.tradingsymbol}"
                ltp_data = self.kite.ltp([instrument])
                ltp = ltp_data[instrument]["last_price"]
                tick = 0.05  # NFO/BFO tick size
                buffer = ltp * 0.05  # 5% buffer
                if req.side == OrderSide.BUY:
                    raw = ltp + buffer
                    # Round UP to nearest tick for BUY
                    effective_price = round(
                        (raw // tick + (1 if raw % tick else 0)) * tick, 2
                    )
                else:
                    raw = max(ltp - buffer, tick)
                    # Round DOWN to nearest tick for SELL
                    effective_price = round((raw // tick) * tick, 2)
                    effective_price = max(effective_price, tick)
                effective_order_type = OrderType.LIMIT
                logger.info(
                    f"Converted MARKET → LIMIT: LTP={ltp}, "
                    f"price={effective_price} ({req.side.value})"
                )
            except Exception as e:
                logger.warning(f"LTP fetch failed, placing MARKET order as-is: {e}")

        logger.info(
            f"LIVE ORDER: {req.side.value} {req.quantity} x {req.tradingsymbol} "
            f"@ {effective_order_type.value} | product={req.product.value}"
        )

        # Ensure price and trigger_price are tick-aligned (0.05 for NFO/BFO)
        tick = 0.05
        if effective_price:
            effective_price = round(round(effective_price / tick) * tick, 2)
        effective_trigger = req.trigger_price
        if effective_trigger:
            effective_trigger = round(round(effective_trigger / tick) * tick, 2)

        order_id = self.kite.place_order(
            variety=self.kite.VARIETY_REGULAR,
            exchange=req.exchange.value,
            tradingsymbol=req.tradingsymbol,
            transaction_type=req.side.value,
            quantity=req.quantity,
            product=req.product.value,
            order_type=effective_order_type.value,
            price=effective_price if effective_price else None,
            trigger_price=effective_trigger if effective_trigger else None,
            tag=req.tag[:20] if req.tag else None,
        )
        logger.info(f"Order placed: {order_id}")
        return OrderResponse(
            order_id=str(order_id),
            status="PLACED",
            tradingsymbol=req.tradingsymbol,
            side=req.side.value,
            quantity=req.quantity,
            price=req.price,
        )

    def _paper_order(self, req: OrderRequest) -> OrderResponse:
        self._paper_order_counter += 1
        oid = f"PAPER-{self._paper_order_counter:06d}"
        resp = OrderResponse(
            order_id=oid,
            status="COMPLETE",
            tradingsymbol=req.tradingsymbol,
            side=req.side.value,
            quantity=req.quantity,
            price=req.price,
            is_paper=True,
        )
        self._paper_orders.append(resp)
        logger.info(
            f"PAPER ORDER: {req.side.value} {req.quantity} x {req.tradingsymbol} → {oid}"
        )
        return resp

    # ── Positions / Holdings ───────────────────────────

    def get_positions(self) -> list[Position]:
        """Get current day positions."""
        raw = self.kite.positions()
        positions = []
        for p in raw.get("day", []):
            positions.append(Position(
                tradingsymbol=p["tradingsymbol"],
                exchange=p["exchange"],
                quantity=p["quantity"],
                average_price=p["average_price"],
                pnl=p["pnl"],
                product=p["product"],
                last_price=p.get("last_price", 0.0),
            ))
        return positions

    def get_holdings(self) -> list[Holding]:
        raw = self.kite.holdings()
        return [
            Holding(
                tradingsymbol=h["tradingsymbol"],
                exchange=h["exchange"],
                quantity=h["quantity"],
                average_price=h["average_price"],
                last_price=h["last_price"],
                pnl=h["pnl"],
            )
            for h in raw
        ]

    # ── Market Data ────────────────────────────────────

    def get_ltp(self, instruments: list[str]) -> dict[str, float]:
        """Get last traded price. instruments like ['NSE:RELIANCE', 'NSE:INFY']"""
        raw = self.kite.ltp(instruments)
        return {k: v["last_price"] for k, v in raw.items()}

    def get_quote(self, instruments: list[str]) -> dict:
        return self.kite.quote(instruments)

    def get_ohlc(self, instruments: list[str]) -> dict:
        return self.kite.ohlc(instruments)

    def get_historical_data(
        self, instrument_token: int, from_date, to_date, interval: str
    ) -> list[dict]:
        """
        interval: minute, 3minute, 5minute, 10minute, 15minute, 30minute,
                  60minute, day, week, month
        """
        return self.kite.historical_data(
            instrument_token, from_date, to_date, interval
        )

    # ── Account ────────────────────────────────────────

    def get_margins(self) -> dict:
        return self.kite.margins()

    def get_orders(self) -> list[dict]:
        return self.kite.orders()

    def modify_order(self, order_id: str, **changes) -> dict:
        clean_changes = {key: value for key, value in changes.items() if value is not None}
        if not clean_changes:
            raise ValueError("At least one field must be provided to modify an order")

        if settings.PAPER_TRADE:
            for order in self._paper_orders:
                if order.order_id == order_id:
                    if "price" in clean_changes:
                        order.price = clean_changes["price"]
                    return {
                        "order_id": order.order_id,
                        "status": "MODIFIED",
                        "price": order.price,
                        "is_paper": True,
                    }
            raise ValueError(f"Paper order not found: {order_id}")

        result = self.kite.modify_order(
            variety=self.kite.VARIETY_REGULAR,
            order_id=order_id,
            **clean_changes,
        )
        return {"order_id": order_id, "result": result}

    def cancel_order(self, order_id: str) -> dict:
        if settings.PAPER_TRADE:
            for index, order in enumerate(self._paper_orders):
                if order.order_id == order_id:
                    self._paper_orders.pop(index)
                    return {"order_id": order_id, "status": "CANCELLED", "is_paper": True}
            raise ValueError(f"Paper order not found: {order_id}")

        result = self.kite.cancel_order(
            variety=self.kite.VARIETY_REGULAR,
            order_id=order_id,
        )
        return {"order_id": order_id, "result": result}

    # ── Instruments ────────────────────────────────────

    def get_instruments(self, exchange: str = "NSE") -> list[dict]:
        return self.kite.instruments(exchange)


# Singleton (legacy — used by strategy routes and trading_routes)
_broker: Optional[Broker] = None


def get_broker() -> Broker:
    global _broker
    if _broker is None:
        _broker = Broker()
    return _broker


def get_user_broker(db, user_id: int) -> Broker:
    """Return a Broker wired to a specific user's KiteConnect session."""
    from core.auth import UserZerodhaAuth

    if user_id in _user_brokers:
        # Refresh kite instance — or clear it if session expired/absent
        try:
            kite = UserZerodhaAuth.get_kite_for_user(db, user_id)
            _user_brokers[user_id]._kite = kite
        except RuntimeError:
            _user_brokers[user_id]._kite = None
        return _user_brokers[user_id]

    broker = Broker(per_user=True, user_id=user_id)
    try:
        kite = UserZerodhaAuth.get_kite_for_user(db, user_id)
        broker._kite = kite
    except RuntimeError:
        pass  # _kite stays None; kite property will raise, not fallback

    _user_brokers[user_id] = broker
    return broker
