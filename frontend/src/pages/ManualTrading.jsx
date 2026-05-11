import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { api } from '../api';
import {
  Activity,
  AlertCircle,
  BadgeIndianRupee,
  Bug,
  CandlestickChart,
  Clock,
  ClipboardList,
  PackageOpen,
  RefreshCw,
  Shield,
  Wallet,
} from 'lucide-react';

const INDEX_OPTIONS = ['NIFTY', 'SENSEX'];
const INDEX_CONFIG = {
  NIFTY: { exchange: 'NFO', fallbackLotSize: 75 },
  SENSEX: { exchange: 'BFO', fallbackLotSize: 20 },
};

const AUTO_REFRESH_MS = 5000; // 5s auto-refresh for positions & orders

function isMarketHours() {
  const now = new Date();
  const h = now.getHours();
  const m = now.getMinutes();
  const mins = h * 60 + m;
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30;
}

async function requestManual(path, options = {}) {
  const token = localStorage.getItem('app_token');
  const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};
  const response = await fetch(`/api/manual${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders,
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail || payload.error || response.statusText);
  }

  return response.json();
}

function Panel({ icon: Icon, title, action, children, className = '' }) {
  return (
    <section className={`card space-y-4 ${className}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="w-9 h-9 rounded-lg bg-surface-3/70 flex items-center justify-center">
            <Icon className="w-4 h-4 text-brand-400" />
          </div>
          <h2 className="text-white font-semibold">{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function StatCard({ icon: Icon, label, value, tone = 'brand' }) {
  const toneMap = {
    brand: 'border-brand-500/20 bg-brand-500/10 text-brand-400',
    green: 'border-green-500/20 bg-green-500/10 text-green-400',
    yellow: 'border-yellow-500/20 bg-yellow-500/10 text-yellow-400',
  };

  return (
    <div className={`rounded-xl border ${toneMap[tone]} p-4`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-gray-500">{label}</div>
          <div className="mt-2 text-xl sm:text-2xl font-bold text-white mono">{value}</div>
        </div>
        <div className="w-10 h-10 rounded-lg bg-surface-3/70 flex items-center justify-center">
          <Icon className="w-5 h-5" />
        </div>
      </div>
    </div>
  );
}

function LabeledField({ label, children, hint }) {
  return (
    <label className="block space-y-1.5">
      <div className="flex items-center gap-2 text-xs font-medium text-gray-400 uppercase tracking-[0.14em]">
        <span>{label}</span>
        {hint ? <span className="text-[11px] normal-case tracking-normal text-gray-500">{hint}</span> : null}
      </div>
      {children}
    </label>
  );
}

function controlClass(extra = '') {
  return `w-full rounded-lg border border-surface-3 bg-surface-2 px-3 py-2.5 text-sm text-white outline-none transition-colors placeholder:text-gray-500 focus:border-brand-500 ${extra}`.trim();
}

function InlineToggle({ name, checked, onChange, children }) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-gray-300">
      <input
        type="checkbox"
        name={name}
        checked={checked}
        onChange={onChange}
        className="h-4 w-4 rounded border-surface-4 bg-surface-2 text-brand-500 focus:ring-brand-500"
      />
      <span>{children}</span>
    </label>
  );
}

function SegmentedNumberField({ numberName, numberValue, unitName, unitValue, onChange, units, placeholder }) {
  return (
    <div className="grid grid-cols-[1fr,76px] gap-2">
      <input
        name={numberName}
        value={numberValue}
        onChange={onChange}
        placeholder={placeholder}
        className={controlClass()}
      />
      <select name={unitName} value={unitValue} onChange={onChange} className={controlClass('px-2')}>
        {units.map((unit) => (
          <option key={unit.value} value={unit.value}>
            {unit.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function StatusMessage({ tone, children }) {
  const toneClasses = {
    success: 'border-green-500/20 bg-green-500/10 text-green-400',
    error: 'border-red-500/20 bg-red-500/10 text-red-400',
    warning: 'border-yellow-500/20 bg-yellow-500/10 text-yellow-400',
    info: 'border-brand-500/20 bg-brand-500/10 text-brand-400',
  };

  return (
    <div className={`rounded-lg border px-3 py-2 text-sm ${toneClasses[tone]}`}>
      {children}
    </div>
  );
}

function EmptyState({ icon: Icon, title, subtitle }) {
  return (
    <div className="flex min-h-[240px] items-center justify-center rounded-xl border border-dashed border-surface-3 bg-surface-2/40">
      <div className="text-center">
        <div className="mx-auto mb-3 w-12 h-12 rounded-xl bg-surface-3/60 flex items-center justify-center">
          <Icon className="w-6 h-6 text-gray-500" />
        </div>
        <div className="text-sm font-medium text-gray-300">{title}</div>
        {subtitle ? <div className="mt-1 text-xs text-gray-500">{subtitle}</div> : null}
      </div>
    </div>
  );
}

function ManualOrderForm({ onOrderAction }) {
  const [form, setForm] = useState({
    mode: 'LIVE',
    auto_atm: false,
    index_name: '',
    tradingsymbol: '',
    exchange: 'NFO',
    option_type: 'CE',
    strike_price: '',
    side: 'BUY',
    quantity: 1,
    order_type: 'MARKET',
    product: 'MIS',
    price: '',
    trigger_price: '',
    trade_amount: '50000',
    sl_type: 'PERCENT',
    stop_loss: '15',
    target_type: 'PERCENT',
    target: '30',
    trailing_type: 'POINTS',
    trailing: '',
    enable_trailing_sl: false,
    move_sl_to_cost: false,
    iceberg_legs: 1,
    tag: 'manual',
  });
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [strikeOptions, setStrikeOptions] = useState([]);
  const [spotPrice, setSpotPrice] = useState(null);
  const [strikesLoading, setStrikesLoading] = useState(false);
  const [nearestExpiry, setNearestExpiry] = useState('');
  const [lotSize, setLotSize] = useState(1);

  // Cache both CE+PE option chains so toggling is instant
  const optionChainCache = useRef({});
  // Track whether user manually edited quantity (suppress auto-calc)
  const userEditedQty = useRef(false);

  const handleChange = (event) => {
    const { name, value, type, checked } = event.target;
    if (name === 'quantity') userEditedQty.current = true;
    // When disabling trailing SL, clear trailing value
    if (name === 'enable_trailing_sl' && !checked) {
      setForm((current) => ({ ...current, enable_trailing_sl: false, trailing: '' }));
      return;
    }
    setForm((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const effectiveSymbol = useMemo(() => {
    if (form.tradingsymbol.trim()) {
      return form.tradingsymbol.trim();
    }
    if (!form.index_name || !form.strike_price || !form.option_type) {
      return '';
    }
    return `${form.index_name} ${form.strike_price} ${form.option_type} (nearest expiry auto)`;
  }, [form.index_name, form.option_type, form.strike_price, form.tradingsymbol]);

  useEffect(() => {
    let active = true;

    const loadStrikeOptions = async () => {
      if (!form.index_name) {
        setStrikeOptions([]);
        setSpotPrice(null);
        optionChainCache.current = {};
        return;
      }

      const config = INDEX_CONFIG[form.index_name];
      if (!config) {
        setStrikeOptions([]);
        setSpotPrice(null);
        setNearestExpiry('');
        optionChainCache.current = {};
        return;
      }

      const optType = form.option_type || 'CE';

      // If we already have this index cached, use it instantly
      const cached = optionChainCache.current[form.index_name];
      if (cached && cached[optType]) {
        const data = cached[optType];
        const nextOptions = (data.strike_options || []).map((strike) => String(strike));
        const atmStrike = String(data.atm_strike || '');

        setSpotPrice(Number(data.spot_price || 0));
        setNearestExpiry(data.nearest_expiry || '');
        setStrikeOptions(nextOptions);
        setLotSize(Number(data.lot_size || 1) || 1);
        setForm((current) => {
          const nextExchange = data.exchange || config.exchange || current.exchange;
          if (current.auto_atm || !nextOptions.includes(String(current.strike_price || ''))) {
            return { ...current, strike_price: atmStrike, exchange: nextExchange };
          }
          if (current.exchange !== nextExchange) {
            return { ...current, exchange: nextExchange };
          }
          return current;
        });
        return;
      }

      setStrikesLoading(true);

      try {
        // Fetch both CE+PE in one call — the backend caches instruments
        const allData = await requestManual(`/option_setup_all?index_name=${encodeURIComponent(form.index_name)}`);
        if (!active) return;

        // Store both chains in cache
        optionChainCache.current[form.index_name] = allData;

        const data = allData[optType];
        if (!data) throw new Error(`No ${optType} data returned`);

        const nextOptions = (data.strike_options || []).map((strike) => String(strike));
        const atmStrike = String(data.atm_strike || '');

        setSpotPrice(Number(data.spot_price || 0));
        setNearestExpiry(data.nearest_expiry || '');
        setStrikeOptions(nextOptions);
        setLotSize(Number(data.lot_size || 1) || 1);
        setForm((current) => {
          const nextExchange = data.exchange || config.exchange || current.exchange;
          if (current.auto_atm || !nextOptions.includes(String(current.strike_price || ''))) {
            return { ...current, strike_price: atmStrike, exchange: nextExchange };
          }
          if (current.exchange !== nextExchange) {
            return { ...current, exchange: nextExchange };
          }
          return current;
        });
      } catch {
        if (!active) return;

        const fallbackCenter = form.strike_price ? Number(form.strike_price) : 0;
        setStrikeOptions(fallbackCenter > 0 ? [String(fallbackCenter)] : []);
        setSpotPrice(null);
        setNearestExpiry('');
      } finally {
        if (active) setStrikesLoading(false);
      }
    };

    loadStrikeOptions();

    return () => {
      active = false;
    };
  }, [form.auto_atm, form.index_name, form.option_type]);

  // Auto-calculate quantity from trade_amount and entry price (or spot-based LTP)
  useEffect(() => {
    if (userEditedQty.current) return; // user manually set quantity — don't override
    const amount = parseFloat(form.trade_amount) || 0;
    const entryPrice = parseFloat(form.price) || spotPrice || 0;
    if (amount <= 0 || entryPrice <= 0 || lotSize <= 0) return;

    // qty = trade_amount / entry_price, rounded down to nearest lot_size multiple
    const rawQty = Math.floor(amount / entryPrice);
    const lots = Math.max(1, Math.floor(rawQty / lotSize));
    const calculatedQty = lots * lotSize;

    if (calculatedQty > 0 && calculatedQty !== parseInt(form.quantity, 10)) {
      setForm((current) => ({ ...current, quantity: calculatedQty }));
    }
  }, [form.trade_amount, form.price, spotPrice, lotSize]);

  // Keep order_type in sync with entry price: blank price = MARKET, any price = LIMIT.
  // SL / SL-M are left untouched so the user can still pick them explicitly.
  useEffect(() => {
    const hasPrice = parseFloat(form.price) > 0;
    setForm((current) => {
      if (hasPrice && current.order_type === 'MARKET') {
        return { ...current, order_type: 'LIMIT' };
      }
      if (!hasPrice && current.order_type === 'LIMIT') {
        return { ...current, order_type: 'MARKET' };
      }
      return current;
    });
  }, [form.price]);

  const handleSubmit = async (event) => {
    event.preventDefault();

    // Confirm before placing LIVE orders
    if (form.mode === 'LIVE') {
      const symbol = form.tradingsymbol.trim() || `${form.index_name} ${form.strike_price} ${form.option_type}`;
      if (!window.confirm(`Place LIVE ${form.side} order for ${symbol} x ${form.quantity}?`)) return;
    }

    setSubmitting(true);
    setResult(null);

    try {
      const payload = {
        ...form,
        tradingsymbol: form.tradingsymbol.trim(),
        trailing: form.enable_trailing_sl ? (form.trailing ? parseFloat(form.trailing) : 0) : 0,
        strike_price: form.strike_price ? parseFloat(form.strike_price) : 0,
        price: form.price ? parseFloat(form.price) : 0,
        trigger_price: form.trigger_price ? parseFloat(form.trigger_price) : 0,
        stop_loss: form.stop_loss ? parseFloat(form.stop_loss) : 0,
        target: form.target ? parseFloat(form.target) : 0,
        quantity: form.quantity ? parseInt(form.quantity, 10) : 1,
        iceberg_legs: form.iceberg_legs ? parseInt(form.iceberg_legs, 10) : 1,
      };
      // Remove frontend-only keys that backend doesn't need
      delete payload.enable_trailing_sl;
      delete payload.trade_amount;

      // Final safeguard: an entry price means LIMIT, blank means MARKET.
      // This guarantees the broker never receives MARKET with a stray price
      // (and never receives LIMIT with no price) regardless of dropdown state.
      if (payload.price > 0 && payload.order_type === 'MARKET') {
        payload.order_type = 'LIMIT';
      } else if (payload.price <= 0 && payload.order_type === 'LIMIT') {
        payload.order_type = 'MARKET';
      }

      if (!payload.tradingsymbol && (!payload.index_name || !payload.strike_price || !payload.option_type)) {
        throw new Error('Select an index, option type and strike, or enter a trading symbol');
      }

      // Validate price fields for non-MARKET order types
      if (payload.order_type === 'LIMIT' && payload.price <= 0) {
        throw new Error('Limit orders require a price greater than 0');
      }
      if ((payload.order_type === 'SL' || payload.order_type === 'SL-M') && payload.trigger_price <= 0) {
        throw new Error('SL orders require a trigger price greater than 0');
      }
      if (payload.order_type === 'SL' && payload.price <= 0) {
        throw new Error('SL orders require a limit price greater than 0');
      }
      if (payload.quantity < 1) {
        throw new Error('Quantity must be at least 1');
      }

      const data = await requestManual('/order', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      const resolvedLabel = data.resolved_expiry
        ? `${data.resolved_tradingsymbol} · expiry ${data.resolved_expiry}`
        : data.resolved_tradingsymbol || payload.tradingsymbol;
      setResult({ tone: 'success', message: `Order placed: ${data.order_ids?.join(', ')}${resolvedLabel ? ` (${resolvedLabel})` : ''}` });
      onOrderAction();
      window.dispatchEvent(new Event('refreshManualPositions'));
      window.dispatchEvent(new Event('refreshManualOpenOrders'));
      window.dispatchEvent(new Event('refreshManualTradeLogs'));
    } catch (error) {
      setResult({ tone: 'error', message: error.message });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
      <div className="flex flex-wrap items-center gap-5 text-sm">
        <span className="text-gray-400 uppercase tracking-[0.14em] text-xs">Mode</span>
        <label className="inline-flex items-center gap-2 text-gray-300">
          <input type="radio" name="mode" value="LIVE" checked={form.mode === 'LIVE'} onChange={handleChange} className="text-brand-500 focus:ring-brand-500" />
          Live
        </label>
        <label className="inline-flex items-center gap-2 text-gray-300">
          <input type="radio" name="mode" value="PAPER" checked={form.mode === 'PAPER'} onChange={handleChange} className="text-brand-500 focus:ring-brand-500" />
          Paper
        </label>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <LabeledField label="Index">
          <select name="index_name" value={form.index_name} onChange={handleChange} className={controlClass()}>
            <option value="">Select index</option>
            {INDEX_OPTIONS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </LabeledField>

        <LabeledField label="Option Type">
          <select name="option_type" value={form.option_type} onChange={handleChange} className={controlClass()}>
            <option value="">Select type</option>
            <option value="CE">Call</option>
            <option value="PE">Put</option>
          </select>
        </LabeledField>

        <LabeledField label="Strike Price">
          <select name="strike_price" value={form.strike_price} onChange={handleChange} className={controlClass()}>
            <option value="">{form.index_name ? (strikesLoading ? 'Loading strikes...' : 'Select strike') : 'Select index first'}</option>
            {strikeOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </LabeledField>

        <LabeledField label="Entry Price">
          <input name="price" value={form.price} onChange={handleChange} placeholder="0.00" type="number" step="0.05" className={controlClass()} />
        </LabeledField>
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-surface-3 bg-surface-2/60 px-3 py-2.5">
        <InlineToggle name="auto_atm" checked={form.auto_atm} onChange={handleChange}>Auto ATM</InlineToggle>
        <InlineToggle name="enable_trailing_sl" checked={form.enable_trailing_sl} onChange={handleChange}>Trailing SL</InlineToggle>
        <InlineToggle name="move_sl_to_cost" checked={form.move_sl_to_cost} onChange={handleChange}>Move SL to Cost</InlineToggle>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
        <div className="md:col-span-5">
          <LabeledField label="Trading Symbol" hint="optional override">
            <input name="tradingsymbol" value={form.tradingsymbol} onChange={handleChange} placeholder="Auto-build from selection or enter symbol" className={controlClass()} />
          </LabeledField>
        </div>
        <div className="md:col-span-2">
          <LabeledField label="Side">
            <select name="side" value={form.side} onChange={handleChange} className={controlClass()}>
              <option value="BUY">Buy</option>
              <option value="SELL">Sell</option>
            </select>
          </LabeledField>
        </div>
        <div className="md:col-span-2">
          <LabeledField label="Quantity" hint={lotSize > 1 ? `lot: ${lotSize}` : ''}>
            <input name="quantity" value={form.quantity} onChange={handleChange} type="number" min="1" className={controlClass()} />
          </LabeledField>
        </div>
        <div className="md:col-span-3">
          <LabeledField label="Trade Amount">
            <input name="trade_amount" value={form.trade_amount} onChange={handleChange} className={controlClass()} />
          </LabeledField>
        </div>
      </div>

      <div className={`grid grid-cols-1 ${form.enable_trailing_sl ? 'md:grid-cols-3' : 'md:grid-cols-2'} gap-3`}>
        <LabeledField label="Stop Loss">
          <SegmentedNumberField
            numberName="stop_loss"
            numberValue={form.stop_loss}
            unitName="sl_type"
            unitValue={form.sl_type}
            onChange={handleChange}
            placeholder="15"
            units={[
              { value: 'PERCENT', label: '%' },
              { value: 'POINTS', label: 'Pts' },
            ]}
          />
        </LabeledField>

        <LabeledField label="Target">
          <SegmentedNumberField
            numberName="target"
            numberValue={form.target}
            unitName="target_type"
            unitValue={form.target_type}
            onChange={handleChange}
            placeholder="30"
            units={[
              { value: 'PERCENT', label: '%' },
              { value: 'POINTS', label: 'Pts' },
            ]}
          />
        </LabeledField>

        {form.enable_trailing_sl ? (
          <LabeledField label="Trailing SL">
            <SegmentedNumberField
              numberName="trailing"
              numberValue={form.trailing}
              unitName="trailing_type"
              unitValue={form.trailing_type}
              onChange={handleChange}
              placeholder="e.g. 5"
              units={[
                { value: 'POINTS', label: 'Pts' },
                { value: 'PERCENT', label: '%' },
              ]}
            />
          </LabeledField>
        ) : null}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <LabeledField label="Order Type">
          <select name="order_type" value={form.order_type} onChange={handleChange} className={controlClass()}>
            <option value="MARKET">Market</option>
            <option value="LIMIT">Limit</option>
            <option value="SL">SL</option>
            <option value="SL-M">SL-M</option>
          </select>
        </LabeledField>
        <LabeledField label="Product">
          <select name="product" value={form.product} onChange={handleChange} className={controlClass()}>
            <option value="MIS">MIS</option>
            <option value="NRML">NRML</option>
          </select>
        </LabeledField>
        <LabeledField label="Trigger Price">
          <input name="trigger_price" value={form.trigger_price} onChange={handleChange} placeholder="0.00" type="number" step="0.05" className={controlClass()} />
        </LabeledField>
        <LabeledField label="Iceberg Legs">
          <input name="iceberg_legs" value={form.iceberg_legs} onChange={handleChange} type="number" min="1" max="10" className={controlClass()} />
        </LabeledField>
      </div>

      <div className="flex flex-wrap items-center gap-4 text-xs text-gray-500">
        {spotPrice ? <span>Spot: <span className="text-gray-300 mono">{spotPrice.toFixed(2)}</span></span> : null}
        {form.index_name && INDEX_CONFIG[form.index_name]?.exchange ? <span>Exchange: <span className="text-gray-300 mono">{INDEX_CONFIG[form.index_name].exchange}</span></span> : null}
        {nearestExpiry ? <span>Nearest Expiry: <span className="text-gray-300 mono">{nearestExpiry}</span></span> : null}
        {lotSize > 1 ? <span>Lot Size: <span className="text-gray-300 mono">{lotSize}</span> · Lots: <span className="text-gray-300 mono">{Math.floor(parseInt(form.quantity, 10) / lotSize) || 0}</span></span> : null}
        {effectiveSymbol ? <span>Selection: <span className="text-gray-300 mono">{effectiveSymbol}</span></span> : null}
      </div>

      {result ? <StatusMessage tone={result.tone}>{result.message}</StatusMessage> : null}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting}
          className="btn-primary min-w-40 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {submitting ? 'Placing...' : 'Place Trade'}
        </button>
      </div>
    </form>
  );
}

function ManualActivePositions({ onAction }) {
  const [positions, setPositions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [squareoffId, setSquareoffId] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [monitorInfo, setMonitorInfo] = useState({});
  const initialLoadDone = useRef(false);

  const fetchPositions = async () => {
    if (!initialLoadDone.current) setLoading(true);
    setError('');
    try {
      const [posData, monData] = await Promise.all([
        requestManual('/positions'),
        requestManual('/monitor/status').catch(() => ({ active_trades: {} })),
      ]);
      setPositions(posData.positions || []);
      setMonitorInfo(monData.active_trades || {});
      setUpdatedAt(new Date());
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setLoading(false);
      initialLoadDone.current = true;
    }
  };

  useEffect(() => {
    fetchPositions();
    const handler = () => fetchPositions();
    window.addEventListener('refreshManualPositions', handler);
    // Auto-refresh during market hours
    const interval = setInterval(() => {
      if (isMarketHours()) fetchPositions();
    }, AUTO_REFRESH_MS);
    return () => {
      window.removeEventListener('refreshManualPositions', handler);
      clearInterval(interval);
    };
  }, []);

  const handleSquareoff = async (tradingsymbol) => {
    if (!window.confirm(`Square off ${tradingsymbol}? This will place a market order to close the position.`)) return;
    setSquareoffId(tradingsymbol);
    try {
      await requestManual('/squareoff', {
        method: 'POST',
        body: JSON.stringify({ tradingsymbol }),
      });
      await fetchPositions();
      onAction();
      window.dispatchEvent(new Event('refreshManualOpenOrders'));
      window.dispatchEvent(new Event('refreshManualTradeLogs'));
    } catch (squareoffError) {
      setError(squareoffError.message);
    } finally {
      setSquareoffId(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">Updated {updatedAt ? updatedAt.toLocaleTimeString() : 'never'}</span>
        <button onClick={fetchPositions} className="btn-ghost py-1.5 px-3 text-xs inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {loading ? <EmptyState icon={PackageOpen} title="Loading positions" subtitle="Fetching live position data" /> : null}
      {!loading && error ? <StatusMessage tone="error">Error loading positions: {error}</StatusMessage> : null}
      {!loading && !error && !positions.length ? <EmptyState icon={PackageOpen} title="No active positions" subtitle="Open positions will appear here" /> : null}

      {!loading && !error && !!positions.length ? (
        <div className="overflow-x-auto rounded-xl border border-surface-3">
          <table className="min-w-full text-sm">
            <thead className="bg-surface-2 text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Symbol</th>
                <th className="px-4 py-3 text-right font-medium">Qty</th>
                <th className="px-4 py-3 text-right font-medium">Avg</th>
                <th className="px-4 py-3 text-right font-medium">LTP</th>
                <th className="px-4 py-3 text-right font-medium">P&amp;L</th>
                <th className="px-4 py-3 text-right font-medium">SL</th>
                <th className="px-4 py-3 text-right font-medium">TGT</th>
                <th className="px-4 py-3 text-center font-medium">Monitor</th>
                <th className="px-4 py-3 text-center font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => {
                const mon = monitorInfo[position.tradingsymbol];
                const monStatus = mon?.status || '';
                const isFailed = monStatus.startsWith('FAILED_EXIT');
                return (
                  <tr key={position.tradingsymbol} className="border-t border-surface-3 bg-surface-1/60">
                    <td className="px-4 py-3 text-gray-200 mono">{position.tradingsymbol}</td>
                    <td className="px-4 py-3 text-right text-gray-300">{position.quantity}</td>
                    <td className="px-4 py-3 text-right text-gray-300">{Number(position.average_price || 0).toFixed(2)}</td>
                    <td className="px-4 py-3 text-right text-gray-300 mono">{Number(position.last_price || 0).toFixed(2)}</td>
                    <td className={`px-4 py-3 text-right font-semibold ${Number(position.pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {Number(position.pnl || 0).toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-right text-yellow-400 mono text-xs">{mon?.sl_price ? Number(mon.sl_price).toFixed(2) : '—'}</td>
                    <td className="px-4 py-3 text-right text-blue-400 mono text-xs">{mon?.tgt_price ? Number(mon.tgt_price).toFixed(2) : '—'}</td>
                    <td className="px-4 py-3 text-center">
                      {isFailed ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-red-400" title={`Exit failed after retries — position unprotected! (${mon.exit_attempts || 0} attempts)`}>
                          <AlertCircle className="w-3.5 h-3.5" /> FAILED
                        </span>
                      ) : monStatus === 'WATCHING' ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium text-green-400" title={mon.last_ltp ? `Last LTP: ${Number(mon.last_ltp).toFixed(2)}` : 'Monitoring active'}>
                          <Shield className="w-3.5 h-3.5" /> Active
                        </span>
                      ) : (
                        <span className="text-xs text-gray-500">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <button
                        onClick={() => handleSquareoff(position.tradingsymbol)}
                        disabled={squareoffId === position.tradingsymbol}
                        className="px-3 py-1.5 rounded-lg bg-red-500/15 text-red-400 border border-red-500/20 hover:bg-red-500/25 disabled:opacity-50"
                      >
                        {squareoffId === position.tradingsymbol ? 'Squaring...' : 'Square Off'}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ManualOpenOrders({ onAction }) {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [cancelId, setCancelId] = useState(null);
  const initialLoadDone = useRef(false);

  const fetchOrders = async () => {
    if (!initialLoadDone.current) setLoading(true);
    setError('');
    try {
      const data = await requestManual('/open_orders');
      setOrders(data.open_orders || []);
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setLoading(false);
      initialLoadDone.current = true;
    }
  };

  useEffect(() => {
    fetchOrders();
    const handler = () => fetchOrders();
    window.addEventListener('refreshManualOpenOrders', handler);
    const interval = setInterval(() => {
      if (isMarketHours()) fetchOrders();
    }, AUTO_REFRESH_MS);
    return () => {
      window.removeEventListener('refreshManualOpenOrders', handler);
      clearInterval(interval);
    };
  }, []);

  const handleCancel = async (orderId) => {
    if (!window.confirm(`Cancel order ${orderId}?`)) return;
    setCancelId(orderId);
    try {
      await requestManual('/order/cancel', {
        method: 'POST',
        body: JSON.stringify({ order_id: orderId }),
      });
      await fetchOrders();
      onAction();
    } catch (cancelError) {
      setError(cancelError.message);
    } finally {
      setCancelId(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="inline-flex items-center gap-2 text-xs text-gray-500">
          <AlertCircle className="w-3.5 h-3.5" />
          Pending and trigger orders
        </div>
        <button onClick={fetchOrders} className="btn-ghost py-1.5 px-3 text-xs inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {loading ? <div className="text-sm text-gray-500">Loading open orders...</div> : null}
      {!loading && error ? <StatusMessage tone="error">Error refreshing open orders: {error}</StatusMessage> : null}
      {!loading && !error && !orders.length ? <EmptyState icon={ClipboardList} title="No open orders" subtitle="Active orders will be shown here" /> : null}

      {!loading && !error && !!orders.length ? (
        <div className="overflow-x-auto rounded-xl border border-surface-3">
          <table className="min-w-full text-sm">
            <thead className="bg-surface-2 text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Order ID</th>
                <th className="px-4 py-3 text-left font-medium">Symbol</th>
                <th className="px-4 py-3 text-right font-medium">Qty</th>
                <th className="px-4 py-3 text-right font-medium">Status</th>
                <th className="px-4 py-3 text-center font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.order_id} className="border-t border-surface-3 bg-surface-1/60">
                  <td className="px-4 py-3 text-gray-300 mono text-xs">{order.order_id}</td>
                  <td className="px-4 py-3 text-gray-200">{order.tradingsymbol}</td>
                  <td className="px-4 py-3 text-right text-gray-300">{order.quantity}</td>
                  <td className="px-4 py-3 text-right text-yellow-400">{order.status}</td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={() => handleCancel(order.order_id)}
                      disabled={cancelId === order.order_id}
                      className="px-3 py-1.5 rounded-lg bg-red-500/15 text-red-400 border border-red-500/20 hover:bg-red-500/25 disabled:opacity-50"
                    >
                      {cancelId === order.order_id ? 'Cancelling...' : 'Cancel'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ManualTradeLogs({ onSummaryChange }) {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const initialLoadDone = useRef(false);

  const fetchLogs = async () => {
    if (!initialLoadDone.current) setLoading(true);
    setError('');
    try {
      const data = await requestManual('/trade_logs');
      const nextLogs = data.logs || [];
      const summary = nextLogs.reduce(
        (accumulator, log) => ({
          tradeCount: accumulator.tradeCount + 1,
          pnl: accumulator.pnl + Number(log.pnl || 0),
          investment: accumulator.investment + Number(log.investment || 0),
        }),
        { tradeCount: 0, pnl: 0, investment: 0 },
      );
      setLogs(nextLogs);
      onSummaryChange(summary);
    } catch (fetchError) {
      setError(fetchError.message);
      onSummaryChange({ tradeCount: 0, pnl: 0, investment: 0 });
    } finally {
      setLoading(false);
      initialLoadDone.current = true;
    }
  };

  useEffect(() => {
    fetchLogs();
    const handler = () => fetchLogs();
    window.addEventListener('refreshManualTradeLogs', handler);
    return () => window.removeEventListener('refreshManualTradeLogs', handler);
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">Executed manual orders and realized P&amp;L</span>
        <button onClick={fetchLogs} className="btn-ghost py-1.5 px-3 text-xs inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {loading ? <div className="text-sm text-gray-500">Loading trade history...</div> : null}
      {!loading && error ? <StatusMessage tone="error">Error loading trade logs: {error}</StatusMessage> : null}
      {!loading && !error && !logs.length ? <EmptyState icon={Wallet} title="No trade logs" subtitle="Completed manual trades will appear here" /> : null}

      {!loading && !error && !!logs.length ? (
        <div className="overflow-x-auto rounded-xl border border-surface-3">
          <table className="min-w-full text-sm">
            <thead className="bg-surface-2 text-gray-400">
              <tr>
                <th className="px-4 py-3 text-left font-medium">Time</th>
                <th className="px-4 py-3 text-left font-medium">Symbol</th>
                <th className="px-4 py-3 text-right font-medium">Qty</th>
                <th className="px-4 py-3 text-right font-medium">Entry</th>
                <th className="px-4 py-3 text-right font-medium">Exit</th>
                <th className="px-4 py-3 text-right font-medium">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log, index) => (
                <tr key={`${log.order_id || log.tradingsymbol || 'trade'}-${index}`} className="border-t border-surface-3 bg-surface-1/60">
                  <td className="px-4 py-3 text-gray-300 mono text-xs">{log.time || log.timestamp || '-'}</td>
                  <td className="px-4 py-3 text-gray-200">{log.tradingsymbol}</td>
                  <td className="px-4 py-3 text-right text-gray-300">{log.quantity}</td>
                  <td className="px-4 py-3 text-right text-gray-300">{log.entry_price ?? '-'}</td>
                  <td className="px-4 py-3 text-right text-gray-300">{log.exit_price ?? '-'}</td>
                  <td className={`px-4 py-3 text-right font-semibold ${Number(log.pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    {Number(log.pnl || 0).toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ManualDebugConsole() {
  const [messages, setMessages] = useState([
    { id: 'boot', tone: 'info', text: 'Manual trading panel initialized', time: new Date().toLocaleTimeString() },
  ]);

  useEffect(() => {
    const intercept = (event) => {
      setMessages((current) => [
        {
          id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          tone: event.detail?.tone || 'info',
          text: event.detail?.text || 'Manual event',
          time: new Date().toLocaleTimeString(),
        },
        ...current,
      ].slice(0, 10));
    };

    window.addEventListener('manualDebug', intercept);
    return () => window.removeEventListener('manualDebug', intercept);
  }, []);

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button onClick={() => setMessages([])} className="btn-ghost py-1.5 px-3 text-xs">Clear</button>
      </div>
      <div className="space-y-2 max-h-[240px] overflow-auto rounded-xl border border-surface-3 bg-surface-2/60 p-3">
        {!messages.length ? <div className="text-xs text-gray-500">Console cleared</div> : null}
        {messages.map((message) => (
          <div key={message.id} className="rounded-lg border border-surface-3 bg-surface-1 px-3 py-2 text-sm">
            <div className="flex items-start justify-between gap-3">
              <span className="text-gray-300">{message.text}</span>
              <span className="text-[11px] text-gray-500 mono">{message.time}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ManualTrading() {
  const [summary, setSummary] = useState({ tradeCount: 0, pnl: 0, investment: 0 });
  const [zerodhaAuth, setZerodhaAuth] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [marketOpen, setMarketOpen] = useState(isMarketHours());
  const [margins, setMargins] = useState({ available: 0, used: 0 });

  const handleOrderAction = () => {
    window.dispatchEvent(new Event('refreshManualTradeLogs'));
    window.dispatchEvent(
      new CustomEvent('manualDebug', {
        detail: { tone: 'info', text: 'Manual trading data refresh requested' },
      }),
    );
  };

  // Use the global auth status — same source as the sidebar
  useEffect(() => {
    let active = true;

    const checkAuth = async () => {
      try {
        const data = await api.getAuthStatus();
        if (active) {
          setZerodhaAuth(data);
          setAuthLoading(false);
        }
      } catch {
        if (active) {
          setZerodhaAuth({ authenticated: false });
          setAuthLoading(false);
        }
      }
    };

    checkAuth();

    // Warm the instrument cache in the background on mount
    requestManual('/preload_instruments', { method: 'POST' }).catch(() => {});

    // Fetch available margin
    const fetchMargins = async () => {
      try {
        const data = await requestManual('/margins');
        setMargins(data);
      } catch { /* ignore if not authenticated yet */ }
    };
    fetchMargins();

    // Re-check when Zerodha login completes
    const onMessage = (e) => {
      if (e.data?.type === 'zerodha_login_success') {
        checkAuth();
        fetchMargins();
      }
    };
    window.addEventListener('message', onMessage);

    // Clear data when Zerodha disconnects
    const onDisconnected = () => { setMargins(null); checkAuth(); };
    window.addEventListener('zerodha_disconnected', onDisconnected);

    // Update market hours indicator every 30s + refresh margins during market hours
    const marketTimer = setInterval(() => {
      setMarketOpen(isMarketHours());
      if (isMarketHours()) fetchMargins();
    }, 30000);

    return () => {
      active = false;
      window.removeEventListener('message', onMessage);
      window.removeEventListener('zerodha_disconnected', onDisconnected);
      clearInterval(marketTimer);
    };
  }, []);

  const isAuthenticated = zerodhaAuth?.authenticated;
  const userName = zerodhaAuth?.profile?.name || zerodhaAuth?.profile?.user_name || zerodhaAuth?.profile?.user_id || '';

  return (
    <div className="p-3 sm:p-6 space-y-4 sm:space-y-6 max-w-[1500px] mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white">Manual Trading</h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">Compact execution workspace for discretionary orders</p>
        </div>
        <div className="flex items-center gap-4">
          {/* Market Status */}
          <div className="text-right">
            <div className="text-xs uppercase tracking-[0.14em] text-gray-500">Market</div>
            <div className={`mt-1 text-sm font-medium flex items-center gap-1.5 justify-end ${marketOpen ? 'text-green-400' : 'text-gray-500'}`}>
              <Clock className="w-3.5 h-3.5" />
              {marketOpen ? 'Open' : 'Closed'}
            </div>
          </div>
          {/* Auth Status */}
          <div className="text-right">
            <div className="text-xs uppercase tracking-[0.14em] text-gray-500">Zerodha</div>
            {authLoading ? (
              <div className="mt-1 text-sm text-gray-400">Checking...</div>
            ) : isAuthenticated ? (
              <div className="mt-1 text-sm flex items-center gap-1.5 justify-end text-green-400">
                <Shield className="w-3.5 h-3.5" />
                <span className="font-medium">{userName || 'Connected'}</span>
              </div>
            ) : (
              <div className="mt-1 text-sm text-red-400">Not logged in</div>
            )}
          </div>
        </div>
      </div>

      {!authLoading && !isAuthenticated ? (
        <StatusMessage tone="error">
          Zerodha is not authenticated. Please login via the sidebar first to use manual trading.
        </StatusMessage>
      ) : null}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard icon={ClipboardList} label="Total Trades" value={summary.tradeCount} tone="brand" />
        <StatCard icon={BadgeIndianRupee} label="Total P&amp;L" value={`₹${summary.pnl.toFixed(2)}`} tone={summary.pnl >= 0 ? 'green' : 'yellow'} />
        <StatCard icon={Wallet} label="Available Margin" value={`₹${Number(margins.available || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} tone="green" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.45fr,1fr] gap-4">
        <Panel icon={CandlestickChart} title="Options Trading">
          <ManualOrderForm onOrderAction={handleOrderAction} />
        </Panel>
        <Panel icon={Activity} title="Active Positions">
          <ManualActivePositions onAction={handleOrderAction} />
        </Panel>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr,1.2fr] gap-4">
        <Panel icon={Bug} title="Debug Console">
          <ManualDebugConsole />
        </Panel>
        <Panel icon={ClipboardList} title="Open Orders">
          <ManualOpenOrders onAction={handleOrderAction} />
        </Panel>
      </div>

      <Panel icon={Wallet} title="Trade Logs & P&L">
        <ManualTradeLogs onSummaryChange={setSummary} />
      </Panel>
    </div>
  );
}