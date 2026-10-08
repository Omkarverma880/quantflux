import React from 'react';
import { BookOpen, GitMerge, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import { Note, Section } from './ui';

/**
 * The information panel: what each indicator looks for, what the two say together, how a signal
 * becomes a trade, and what the numbers on the results page do and do not mean.
 */
export default function Rules({ meta, params, execution }) {
  const e = meta?.explain || {};
  const p = params || meta?.defaults?.params || {};
  const ex = execution || meta?.defaults?.execution || {};
  const short = String(ex?.action || 'BUY').toUpperCase() === 'SELL';
  // the wing is quoted in index points so it reads like the thing you would actually place
  const wing = ex?.hedge_offset
    ? <> {ex.moneyness ? `${ex.moneyness} strike${ex.moneyness > 1 ? 's' : ''} out` : 'at the money'}, hedged {ex.hedge_offset * 50} points further out</>
    : null;

  return (
    <div className="space-y-4">
      <Section title="Where this comes from">
        <div className="text-[12.5px] text-gray-300 space-y-1.5">
          <p>
            Two TradingView indicators describe the same candle from different angles. This lab keeps both,
            translated line for line, and lets you require one, the other, or both at once.
          </p>
          {(meta?.sources || []).map((s, i) => (
            <div key={i} className="flex items-start gap-1.5">
              <BookOpen className="w-3.5 h-3.5 text-gray-500 shrink-0 mt-0.5" />
              <span className="text-[12px] text-gray-400">{s}</span>
            </div>
          ))}
        </div>
      </Section>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Script one · the long-tail rejection">
          <ol className="space-y-2 list-decimal pl-4">
            {(e.rejection || []).map((t, i) => <li key={i} className="text-[12.5px] text-gray-300">{t}</li>)}
          </ol>
          <div className="mt-2 text-[11.5px] text-gray-500">
            In words: price pushed past where it has been for the last {p.lookback} bars, then was thrown
            back so hard that most of the candle is tail. Someone big rejected that level.
          </div>
        </Section>

        <Section title="Script two · the body and its filters">
          <ol className="space-y-2 list-decimal pl-4">
            {(e.shape || []).map((t, i) => <li key={i} className="text-[12.5px] text-gray-300">{t}</li>)}
          </ol>
          <div className="mt-2 text-[11.5px] text-gray-500">
            In words: the candle closed at the opposite end from where it stretched, it was big enough to
            matter, and it happened at a local extreme rather than in the middle of a range.
          </div>
        </Section>
      </div>

      <Section title="What the two say together">
        <div className="flex items-start gap-2">
          <GitMerge className="w-4 h-4 text-brand-400 shrink-0 mt-0.5" />
          <div className="text-[12.5px] text-gray-300">
            <b className="text-white">{meta?.modes?.[p.mode] || e.mode_text}</b>
            <div className="text-[11.5px] text-gray-500 mt-1">
              "Both" is the strictest and fires least often — the tail rule (a tail of {p.tail_pct}% of the
              range) is already stricter than the body rule ({Math.round(100 * (1 - (p.fib_level || 0.4)))}% of
              the range), so in practice "both" is the tail rule plus the ATR, swing, colour and EMA filters.
              "Either" roughly doubles the signals and loosens what counts.
            </div>
          </div>
        </div>
      </Section>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="How a signal becomes a trade">
          <div className="space-y-2 text-[12.5px] text-gray-300">
            <div className="flex items-start gap-1.5">
              <TrendingUp className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
              <span>
                A hammer {short
                  ? <>sells the <b>put</b>{wing}, and wins if the index simply does not fall.</>
                  : <>buys the <b>call</b> of the nearest expiry.</>}
              </span>
            </div>
            <div className="flex items-start gap-1.5">
              <TrendingDown className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
              <span>
                An inverted hammer or shooting star {short
                  ? <>sells the <b>call</b>{wing}, and wins if the index simply does not rally.</>
                  : <>buys the at-the-money <b>put</b>.</>}
              </span>
            </div>
            <div className="text-[12px] text-gray-400">
              {short ? (
                <>
                  Close once the credit has decayed {ex.target_pct}%
                  {ex.stop_pct ? <>, or if it expands {ex.stop_pct}%</>
                    : <>; there is no stop, so the hedge is the only thing limiting the loss</>}
                  {ex.max_hold_min ? <>, after {ex.max_hold_min} minutes</> : null}
                  , or at square-off — whichever comes first.
                </>
              ) : (
                <>
                  Exit at {ex.target_pct}% of the premium gained, {ex.stop_pct}% lost,
                  after {ex.max_hold_min} minutes, or at square-off — whichever comes first.
                </>
              )}
              {' '}At most {ex.max_trades_per_day} trades a day, one at a time.
              {ex.min_dte ? ` Nothing closer than ${ex.min_dte} day${ex.min_dte > 1 ? 's' : ''} to expiry.` : ''}
            </div>
            {short && !ex.hedge_offset && (
              <div className="text-[12px] text-amber-300">
                With no hedge this is a naked short: the credit is the most it can make, and the
                loss has no defined limit.
              </div>
            )}
          </div>
        </Section>

        <Section title="What keeps the numbers honest">
          <div className="space-y-1.5">
            {[
              'The decision is taken on the candle\'s last minute, because that is when its close is known — a five-minute candle stamped 10:15 is decided at 10:19.',
              'The fill is the next minute\'s real option open, never a price inside the candle.',
              'Every leg must have actually traded just before the fill; otherwise the signal is recorded as skipped rather than priced from nothing.',
              'Zerodha\'s own charges apply to every trade, plus slippage on each fill.',
              'Index points and option rupees are reported separately and never added together.',
            ].map((t, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                <span className="text-[12px] text-gray-300">{t}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Note tone="warn">
        A backtest is not a forecast. Tuning these settings until the past looks good is the fastest way to
        fool yourself: check that a setting still works on a period you did not tune it on, and prefer a rule
        that survives small changes to one that only works at exact numbers.
      </Note>
    </div>
  );
}
